"""TASK-69 AC#1 — ModeDispatcher unit tests.

Pure unit-level (no FastAPI / no real DB) — register / dispatch /
resolve_mode / resolve_token / get_client behaviours are exercised
against in-memory mocks.

TASK-79-BACKEND-ARCH-FIX (2026-05-12): dispatcher now resolves the
per-App ``dify_apps.api_token`` at dispatch time and threads a cached
``DifyStreamClient`` (keyed by token) into orchestrator.run as its
first arg. NULL/empty api_token falls back to the default client (Phase
1 chat App back-compat). New tests at the bottom cover the 4 cases the
brief specifies.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from ncmu_backend.schemas.sse_events import NcmuSseEvent
from ncmu_backend.workflow._base import BaseOrchestrator
from ncmu_backend.workflow.dify_client import DifyStreamClient
from ncmu_backend.workflow.mode_dispatcher import ModeDispatcher


class _FakeOrchestrator(BaseOrchestrator):
    """Yields a single ``ping`` event, captures the ``dify_client`` it was
    handed so the per-App routing tests can assert which token was used.
    """

    mode = "advanced-chat"

    def __init__(self):
        # Skip BaseOrchestrator.__init__ — not strictly needed (no __init__
        # in BaseOrchestrator post-ARCH-FIX-79) but kept explicit for clarity.
        self.last_dify_client: DifyStreamClient | None = None

    async def run(
        self,
        dify_client: DifyStreamClient,
        run_id: uuid.UUID,
        app_id: str,
        user_id: uuid.UUID,
        inputs: dict[str, Any],
    ) -> AsyncIterator[NcmuSseEvent]:
        self.last_dify_client = dify_client
        yield NcmuSseEvent(
            event_type="ping",
            run_id=run_id,
            timestamp=datetime.now(timezone.utc),
            data={"app_id": app_id, "token": dify_client.api_key},
        )


def _make_dispatcher(default_token: str = "k") -> ModeDispatcher:
    """ModeDispatcher seeded with a default DifyStreamClient."""
    return ModeDispatcher(
        DifyStreamClient(base_url="http://dify-api:5001", api_key=default_token)
    )


def _fake_db_with_row(row_value):
    """AsyncMock db whose execute().first() returns ``row_value`` (None or
    a 1-tuple). Used to simulate the dify_apps SELECT result."""
    fake_db = AsyncMock()
    fake_result = MagicMock()
    fake_result.first = MagicMock(return_value=row_value)
    fake_db.execute = AsyncMock(return_value=fake_result)
    return fake_db


# --------------------------------------------------------------------- #
# (a) register + dispatch yields events from the registered orchestrator
# --------------------------------------------------------------------- #
async def test_register_then_dispatch_yields_events():
    dispatcher = _make_dispatcher()
    orch = _FakeOrchestrator()
    dispatcher.register("advanced-chat", orch)

    run_id = uuid.uuid4()
    user_id = uuid.uuid4()
    # dify_apps row returns NULL token → resolve_token falls back to default.
    fake_db = _fake_db_with_row((None,))
    events = []
    async for evt in dispatcher.dispatch(
        fake_db, "advanced-chat", run_id, "app-test-1", user_id, {"q": "hello"}
    ):
        events.append(evt)

    assert len(events) == 1
    assert events[0].event_type == "ping"
    assert events[0].run_id == run_id
    assert events[0].data["app_id"] == "app-test-1"


# --------------------------------------------------------------------- #
# (b) dispatch(mode='chat') raises ValueError
# --------------------------------------------------------------------- #
async def test_dispatch_chat_mode_raises_value_error():
    dispatcher = _make_dispatcher()
    fake_db = _fake_db_with_row((None,))
    with pytest.raises(ValueError, match="mode=chat must use chat orchestrator path"):
        async for _ in dispatcher.dispatch(
            fake_db, "chat", uuid.uuid4(), "app-x", uuid.uuid4(), {}
        ):
            pass


# --------------------------------------------------------------------- #
# (c) dispatch(mode=unknown) raises KeyError with "B2 task pending"
# --------------------------------------------------------------------- #
async def test_dispatch_unregistered_mode_raises_key_error():
    dispatcher = _make_dispatcher()
    fake_db = _fake_db_with_row((None,))
    with pytest.raises(KeyError, match="B2 task pending"):
        async for _ in dispatcher.dispatch(
            fake_db, "advanced-chat", uuid.uuid4(), "app-x", uuid.uuid4(), {}
        ):
            pass


# --------------------------------------------------------------------- #
# (d) resolve_mode for unknown app_id raises ValueError
# --------------------------------------------------------------------- #
async def test_resolve_mode_unknown_app_raises_value_error():
    dispatcher = _make_dispatcher()
    fake_db = _fake_db_with_row(None)  # row not found

    with pytest.raises(ValueError, match="not found"):
        await dispatcher.resolve_mode(fake_db, "nonexistent-app")


# --------------------------------------------------------------------- #
# (e) register same mode twice raises ValueError
# --------------------------------------------------------------------- #
async def test_register_duplicate_raises_value_error():
    dispatcher = _make_dispatcher()
    orch1 = _FakeOrchestrator()
    orch2 = _FakeOrchestrator()
    dispatcher.register("advanced-chat", orch1)
    with pytest.raises(ValueError, match="already registered"):
        dispatcher.register("advanced-chat", orch2)


# =====================================================================
# TASK-79-BACKEND-ARCH-FIX — per-App token dispatcher tests (4 cases
# per Pane 0 brief AC#2).
# =====================================================================

# --------------------------------------------------------------------- #
# (f) brief case (a) — chat App fallback default token: when dify_apps.
#     api_token IS NULL, resolve_token returns the default client's key.
# --------------------------------------------------------------------- #
async def test_resolve_token_null_returns_default(caplog):
    """Per-App row with NULL token → fallback to DIFY_APP_DEFAULT_TOKEN
    (preserves Phase 1 chat App path).

    M-INDEP-R2-1: also asserts the IMP-INDEP-1 ``log.warning`` fires with
    a ``backfill`` hint — regression-locks the silent-degradation guard
    so a future "tidy" pass cannot delete the warning without flipping a
    test.
    """
    dispatcher = _make_dispatcher(default_token="default-chat-token")
    fake_db = _fake_db_with_row((None,))  # api_token NULL

    with caplog.at_level(logging.WARNING, logger="ncmu_backend.workflow.mode_dispatcher"):
        token = await dispatcher.resolve_token(fake_db, "app-chat-kb")
    assert token == "default-chat-token"
    assert any("backfill" in r.message for r in caplog.records), (
        f"expected IMP-INDEP-1 'backfill' warning on NULL fallback; "
        f"got records: {[r.message for r in caplog.records]}"
    )


# --------------------------------------------------------------------- #
# (g) brief case (b) — per-App token: when dify_apps.api_token is set,
#     resolve_token returns that token (NOT the default).
# --------------------------------------------------------------------- #
async def test_resolve_token_returns_per_app_token():
    """When api_token column has a value, resolve_token returns it
    verbatim — this is the path that fixes Mode 2 advanced-chat hang
    (the chatflow App's own token finally reaches Dify)."""
    dispatcher = _make_dispatcher(default_token="default-chat-token")
    fake_db = _fake_db_with_row(("app-advanced-chat-real-token",))

    token = await dispatcher.resolve_token(fake_db, "app-advanced-chat")
    assert token == "app-advanced-chat-real-token"


# --------------------------------------------------------------------- #
# (h) brief case (c) — empty-string token also falls back (defensive
#     handling for accidental UPDATE ... SET api_token=''.
# --------------------------------------------------------------------- #
async def test_resolve_token_empty_string_returns_default():
    dispatcher = _make_dispatcher(default_token="default-fallback")
    fake_db = _fake_db_with_row(("",))  # api_token = '' (whitespace-trimmed)

    token = await dispatcher.resolve_token(fake_db, "app-x")
    assert token == "default-fallback"


# --------------------------------------------------------------------- #
# (i) brief case (d) — per-App cache hit: get_client(token) returns the
#     same DifyStreamClient instance on repeated calls (one client per
#     token, not one per dispatch).
# --------------------------------------------------------------------- #
def test_get_client_caches_per_token():
    dispatcher = _make_dispatcher(default_token="default-token")
    # First call for a new token spawns a client and caches it.
    c1 = dispatcher.get_client("app-token-A")
    c2 = dispatcher.get_client("app-token-A")
    assert c1 is c2, "get_client must return the cached instance on repeated calls"
    # Different token → distinct client instance.
    c3 = dispatcher.get_client("app-token-B")
    assert c3 is not c1
    # The default-token path returns the dispatcher's pre-seeded default client
    # (constructor seeds the cache so get_client(default) doesn't spawn a 2nd
    # httpx.AsyncClient lifecycle for the chat App).
    c_default = dispatcher.get_client("default-token")
    assert c_default is dispatcher._default_client


# --------------------------------------------------------------------- #
# (j) integration: dispatch threads the per-App client into orchestrator
# --------------------------------------------------------------------- #
async def test_dispatch_threads_per_app_client_into_orchestrator():
    """End-to-end: dispatcher.dispatch resolves the per-App token from db,
    pulls the matching cached client, and passes it to orchestrator.run as
    the first arg. _FakeOrchestrator captures .last_dify_client so we can
    assert the orchestrator received the right one."""
    dispatcher = _make_dispatcher(default_token="default-token")
    orch = _FakeOrchestrator()
    dispatcher.register("advanced-chat", orch)
    fake_db = _fake_db_with_row(("per-app-token-xyz",))

    run_id = uuid.uuid4()
    events = []
    async for evt in dispatcher.dispatch(
        fake_db, "advanced-chat", run_id, "app-real",
        uuid.uuid4(), {"q": "hi"},
    ):
        events.append(evt)

    assert orch.last_dify_client is not None
    assert orch.last_dify_client.api_key == "per-app-token-xyz", (
        "orchestrator.run must receive the cached client keyed by the per-App "
        "token, not the default-token client"
    )
    # The cached client should also be reachable via get_client (cache hit).
    assert dispatcher.get_client("per-app-token-xyz") is orch.last_dify_client
    # Sanity: NCMU envelope's data echoes the token (via _FakeOrchestrator yield).
    assert events[0].data["token"] == "per-app-token-xyz"


# =====================================================================
# TASK-A B-NEW-44 — LRU eviction tests
# ---------------------------------------------------------------------
# The dispatcher's per-token client cache now bounds its size to
# ``max_size`` non-default entries + 1 anchored default slot, evicting
# the oldest non-default entry on miss-insert overflow. Three tests
# pin the invariants the spec calls out (eviction triggers / hit
# promotes to MRU / default never evicted).
# =====================================================================


def test_client_cache_evicts_lru_on_overflow():
    """Filling past ``max_size`` non-default entries drops the oldest
    non-default token; the anchored default token is never evicted."""
    default = DifyStreamClient(
        base_url="http://x", api_key="default-token", timeout=10.0
    )
    d = ModeDispatcher(default_client=default, max_size=3)

    # max_size=3 non-default + 1 default 永驻 = 4 total slots.
    # Insert T1, T2, T3 → cache = {default, T1, T2, T3} (len=4, no evict).
    d.get_client("T1")
    d.get_client("T2")
    d.get_client("T3")
    assert "default-token" in d._client_cache
    assert "T1" in d._client_cache
    assert "T2" in d._client_cache
    assert "T3" in d._client_cache

    # Insert T4 → cache reaches len=5 > 4 → evict LRU non-default = T1.
    d.get_client("T4")
    assert "default-token" in d._client_cache, "default 不应被淘汰"
    assert "T1" not in d._client_cache, "T1 应被淘汰（最旧非-default）"
    assert "T2" in d._client_cache
    assert "T3" in d._client_cache
    assert "T4" in d._client_cache


def test_client_cache_hit_moves_to_end():
    """A cache hit promotes the token to MRU; the next eviction
    therefore skips it and drops the new LRU non-default instead."""
    default = DifyStreamClient(
        base_url="http://x", api_key="default-token", timeout=10.0
    )
    d = ModeDispatcher(default_client=default, max_size=3)

    d.get_client("T1")
    d.get_client("T2")
    d.get_client("T3")

    # Touch T1 → promoted to MRU. Insertion-order non-default becomes
    # T2 (LRU) < T3 < T1 (MRU).
    d.get_client("T1")

    # Insert T4 → cache len=5 > 4 → evict LRU non-default = T2.
    d.get_client("T4")
    assert "T1" in d._client_cache, "T1 命中后变 MRU，不应被淘汰"
    assert "T2" not in d._client_cache, "T2 现在是最旧非-default，应被淘汰"
    assert "T3" in d._client_cache
    assert "T4" in d._client_cache


def test_default_token_never_evicted():
    """Heavy overflow leaves the default token still cached and still
    resolving to the exact same client instance the dispatcher was
    seeded with (anchored — eviction never touches it)."""
    default = DifyStreamClient(
        base_url="http://x", api_key="default-token", timeout=10.0
    )
    d = ModeDispatcher(default_client=default, max_size=3)

    # 100 distinct non-default tokens → cache is forced through many
    # eviction passes; default still survives.
    for i in range(100):
        d.get_client(f"T{i}")

    assert "default-token" in d._client_cache
    assert d.get_client("default-token") is default


# =====================================================================
# B-NEW-45 — per-app-id WARN-once TTL suppression for NULL token fallback
# ---------------------------------------------------------------------
# Phase 1 backward-compat (IMP-INDEP-1 / TASK-A) logs ``log.warning`` every
# time ``dify_apps.api_token`` is NULL/empty. Under continuous traffic
# this becomes N non-actionable log lines per minute per app. These
# tests pin the new suppression: the warning fires at most once per
# app_id per TTL window; the underlying fallback behaviour is unchanged.
# =====================================================================


async def test_resolve_token_null_warns_once_within_ttl(caplog):
    """5 repeated NULL-token resolutions for the same app_id emit
    exactly 1 ``backfill`` warning — not 5 — because the second through
    fifth calls fall inside the TTL window of the first."""
    dispatcher = _make_dispatcher(default_token="default-chat-token")
    fake_db = _fake_db_with_row((None,))

    with caplog.at_level(logging.WARNING, logger="ncmu_backend.workflow.mode_dispatcher"):
        for _ in range(5):
            token = await dispatcher.resolve_token(fake_db, "app-chat-kb")
            assert token == "default-chat-token"  # fallback behaviour unchanged

    backfill = [r for r in caplog.records if "backfill" in r.message]
    assert len(backfill) == 1, (
        f"expected exactly 1 'backfill' warning across 5 NULL resolutions "
        f"within TTL; got {len(backfill)}: {[r.message for r in backfill]}"
    )


async def test_resolve_token_null_warns_per_app_id(caplog):
    """Two distinct app_ids each get their own warning — the TTL is keyed
    per app_id, not global. Three calls per app, six total, → 2 warnings."""
    dispatcher = _make_dispatcher(default_token="default-chat-token")
    fake_db = _fake_db_with_row((None,))

    with caplog.at_level(logging.WARNING, logger="ncmu_backend.workflow.mode_dispatcher"):
        for _ in range(3):
            await dispatcher.resolve_token(fake_db, "app-A")
            await dispatcher.resolve_token(fake_db, "app-B")

    backfill = [r for r in caplog.records if "backfill" in r.message]
    # Each app_id appears in its own warning's args (%s, %s twice).
    apps_warned = {r.args[0] for r in backfill}
    assert apps_warned == {"app-A", "app-B"}, (
        f"expected one warning each for app-A + app-B; got args={[r.args for r in backfill]}"
    )
    assert len(backfill) == 2, (
        f"expected 2 warnings (1 per distinct app_id within TTL); got {len(backfill)}"
    )


async def test_resolve_token_null_warns_again_after_ttl_expiry(caplog, monkeypatch):
    """When the TTL window expires (simulated by advancing the monotonic
    clock), the next NULL-token resolution warns again — suppression is
    time-bounded, not permanent."""
    import time
    dispatcher = _make_dispatcher(default_token="default-chat-token")
    fake_db = _fake_db_with_row((None,))

    # Patch the dispatcher's clock source — pinning to a tuple lets us
    # advance "time" deterministically without sleeping.
    fake_now = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: fake_now[0])

    with caplog.at_level(logging.WARNING, logger="ncmu_backend.workflow.mode_dispatcher"):
        await dispatcher.resolve_token(fake_db, "app-X")  # warn #1
        await dispatcher.resolve_token(fake_db, "app-X")  # suppressed
        fake_now[0] += 61.0  # past the 60 s TTL
        await dispatcher.resolve_token(fake_db, "app-X")  # warn #2

    backfill = [r for r in caplog.records if "backfill" in r.message]
    assert len(backfill) == 2, (
        f"expected 2 warnings (1 before TTL expiry, 1 after); got {len(backfill)}"
    )
