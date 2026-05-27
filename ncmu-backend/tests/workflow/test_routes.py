"""TASK-69 AC#3 — workflow routes HTTP behaviour (≥4 endpoint cases +
H-FRESH-2 client-disconnect cancellation).

Mock strategy:
- ``app_client`` does NOT run lifespan, so ``app.state.workflow_dispatcher``
  is not set — the test installs its own ``ModeDispatcher`` via
  ``app.dependency_overrides[get_dispatcher]``.
- ``dify_apps`` rows are seeded directly via SQL so ``resolve_mode`` finds them.
- H-FRESH-2 cancellation: monkeypatches ``crud.finalize_run`` to record calls,
  then registers a mock orchestrator that raises ``asyncio.CancelledError``
  after the first yield to drive the ``except asyncio.CancelledError`` branch
  in ``event_stream``.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator

import httpx
import pytest_asyncio
import respx
from httpx import Response
from sqlalchemy import text

from ncmu_backend.schemas.sse_events import NcmuSseEvent
from ncmu_backend.workflow._base import BaseOrchestrator
from ncmu_backend.workflow.dify_client import DifyStreamClient
from ncmu_backend.workflow.mode_dispatcher import ModeDispatcher


ADMIN_USER_ID = uuid.UUID("a0000001-0000-4000-8000-000000000001")
APP_ID = "app-test-routes"
APP_ID_CHAT = "app-test-chat-mode"


# --------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------- #
@pytest_asyncio.fixture
async def routes_client(app_client, async_db, monkeypatch):
    """Wrap ``app_client`` so:
      - ``dify_apps`` has 1 ``advanced-chat`` row (APP_ID) + 1 ``chat`` row
        (APP_ID_CHAT) for the mode-routing matrix.
      - ``get_dispatcher`` is overridden with a fresh empty ModeDispatcher.
      - ``services.user_can_access_app`` is mocked to return True so the
        TASK-BUG-5 gate doesn't 403 these mode/routing/SSE-flow tests
        (they pre-date the gate and exercise different behaviour). The
        dedicated gate tests at the bottom of the file use
        ``app_client`` directly with explicit owner/shared seeding.
    """
    from ncmu_backend.apps.routes import get_dify_console_client
    from ncmu_backend.main import app, get_dify_client
    from ncmu_backend.workflow.routes import get_dispatcher

    await async_db.execute(text(
        "INSERT INTO dify_apps (dify_app_id, name, mode) VALUES "
        "(:a, 'Adv', 'advanced-chat'), (:c, 'Chat', 'chat')"
    ), {"a": APP_ID, "c": APP_ID_CHAT})
    await async_db.commit()

    async def _allow_all(**_kw: Any) -> bool:
        return True
    monkeypatch.setattr(
        "ncmu_backend.workflow.routes.services.user_can_access_app",
        _allow_all,
    )
    # TASK-BUG-5 gate added http/cache deps to run_workflow; since
    # user_can_access_app is mocked above, FastAPI still injects these
    # but they're never read. Override with None so get_dify_client
    # doesn't RuntimeError on app.state.dify_client (lifespan not run).
    app.dependency_overrides[get_dify_client] = lambda: None
    app.dependency_overrides[get_dify_console_client] = lambda: None

    dispatcher = ModeDispatcher(
        DifyStreamClient(base_url="http://dify-api:5001", api_key="k")
    )
    app.dependency_overrides[get_dispatcher] = lambda: dispatcher
    yield app_client, dispatcher
    app.dependency_overrides.pop(get_dispatcher, None)
    app.dependency_overrides.pop(get_dify_client, None)
    app.dependency_overrides.pop(get_dify_console_client, None)


def _bearer(jwt_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {jwt_token}"}


# --------------------------------------------------------------------- #
# (a) POST run with non-existent app_id → 404
# --------------------------------------------------------------------- #
async def test_run_unknown_app_returns_404(routes_client, jwt_token):
    client, _ = routes_client
    resp = await client.post(
        "/api/v1/ncmu/workflow/apps/non-existent-app/run",
        json={"inputs": {}},
        headers=_bearer(jwt_token),
    )
    assert resp.status_code == 404


# --------------------------------------------------------------------- #
# (b) POST run with mode=chat → 400 (must use /chat endpoint)
# --------------------------------------------------------------------- #
async def test_run_chat_mode_returns_400(routes_client, jwt_token):
    client, _ = routes_client
    resp = await client.post(
        f"/api/v1/ncmu/workflow/apps/{APP_ID_CHAT}/run",
        json={"inputs": {}},
        headers=_bearer(jwt_token),
    )
    assert resp.status_code == 400
    assert "chat" in resp.text.lower()


# --------------------------------------------------------------------- #
# (c) POST run with B2-unregistered mode → SSE error event code=503
# --------------------------------------------------------------------- #
async def test_run_unregistered_mode_yields_sse_error_503(
    routes_client, jwt_token, monkeypatch,
):
    client, _dispatcher = routes_client
    # Mock finalize_run — the M5 ``async with SessionLocal()`` in the
    # routes.py error branches builds independent AsyncSessions against
    # the production-like NCMU_DB_URL, not the request-overridden
    # ephemeral DB. Verifying SSE wire output is the AC; the DB write is
    # exercised by test_crud.py.
    finalize_calls: list[dict] = []

    async def fake_finalize(db, run_id, status, outputs=None, error_msg=None):
        finalize_calls.append({"run_id": run_id, "status": status,
                               "error_msg": error_msg})

    monkeypatch.setattr(
        "ncmu_backend.workflow.crud.finalize_run", fake_finalize,
    )

    async with client.stream(
        "POST",
        f"/api/v1/ncmu/workflow/apps/{APP_ID}/run",
        json={"inputs": {}},
        headers=_bearer(jwt_token),
    ) as resp:
        assert resp.status_code == 200
        body = b""
        async for chunk in resp.aiter_bytes():
            body += chunk
    text = body.decode("utf-8")
    assert "event: error" in text, text
    assert '"code": 503' in text or '"code":503' in text, text
    # KeyError branch must have flagged the run as failed.
    assert any(c["status"] == "failed" for c in finalize_calls), finalize_calls


# --------------------------------------------------------------------- #
# (d) GET list_runs → 200 + array (empty initially)
# --------------------------------------------------------------------- #
async def test_list_runs_returns_array(routes_client, jwt_token):
    client, _ = routes_client
    resp = await client.get(
        f"/api/v1/ncmu/workflow/apps/{APP_ID}/runs",
        headers=_bearer(jwt_token),
    )
    assert resp.status_code == 200
    assert resp.json() == []


# --------------------------------------------------------------------- #
# (e) GET run with unknown run_id → 404
# --------------------------------------------------------------------- #
async def test_get_run_unknown_returns_404(routes_client, jwt_token):
    client, _ = routes_client
    resp = await client.get(
        "/api/v1/ncmu/workflow/runs/00000000-0000-0000-0000-000000000000",
        headers=_bearer(jwt_token),
    )
    assert resp.status_code == 404


# --------------------------------------------------------------------- #
# (f) DELETE run with unknown run_id → 404
# --------------------------------------------------------------------- #
async def test_delete_run_unknown_returns_404(routes_client, jwt_token):
    client, _ = routes_client
    resp = await client.delete(
        "/api/v1/ncmu/workflow/runs/00000000-0000-0000-0000-000000000000",
        headers=_bearer(jwt_token),
    )
    assert resp.status_code == 404


# --------------------------------------------------------------------- #
# (g) H-FRESH-2 cancellation: orchestrator raises CancelledError →
# event_stream's except branch finalizes 'cancelled' + reraises.
# --------------------------------------------------------------------- #
class _CancellingOrchestrator(BaseOrchestrator):
    """Yields one ping then raises CancelledError to simulate client RST."""

    mode = "advanced-chat"

    def __init__(self):
        pass

    async def run(
        self,
        dify_client: DifyStreamClient,  # ARCH-FIX-79: dispatcher injects per-call
        run_id: uuid.UUID,
        app_id: str,
        user_id: uuid.UUID,
        inputs: dict[str, Any],
    ) -> AsyncIterator[NcmuSseEvent]:
        yield NcmuSseEvent(
            event_type="ping",
            run_id=run_id,
            timestamp=datetime.now(timezone.utc),
            data={"phase": "starting"},
        )
        # Simulate sse_starlette injecting CancelledError into our generator
        # when the client disconnects (athrow under the hood).
        raise asyncio.CancelledError()


async def test_event_stream_h_fresh_2_cancellation_finalizes_cancelled(
    routes_client, jwt_token, monkeypatch,
):
    client, dispatcher = routes_client
    dispatcher.register("advanced-chat", _CancellingOrchestrator())

    finalize_calls: list[dict] = []

    async def fake_finalize(db, run_id, status, outputs=None, error_msg=None):
        finalize_calls.append({
            "run_id": run_id,
            "status": status,
            "error_msg": error_msg,
        })

    monkeypatch.setattr(
        "ncmu_backend.workflow.crud.finalize_run", fake_finalize,
    )

    # Fire POST /run. The orchestrator raises CancelledError after the
    # first yield; the routes.py ``except asyncio.CancelledError`` branch
    # must finalize 'cancelled' then re-raise. Re-raise propagates through
    # StreamingResponse → ASGITransport (raise_app_exceptions=True default
    # in httpx 0.28+), so we wrap in try/except.
    try:
        async with client.stream(
            "POST",
            f"/api/v1/ncmu/workflow/apps/{APP_ID}/run",
            json={"inputs": {}},
            headers=_bearer(jwt_token),
        ) as resp:
            assert resp.status_code == 200
            try:
                async for _ in resp.aiter_bytes():
                    pass
            except (httpx.RemoteProtocolError, httpx.ReadError,
                    asyncio.CancelledError, AssertionError):
                pass
    except (httpx.RemoteProtocolError, httpx.ReadError,
            asyncio.CancelledError, AssertionError):
        pass

    # The except asyncio.CancelledError branch in event_stream must have
    # called finalize_run('cancelled', error_msg='client disconnect').
    cancelled = [c for c in finalize_calls if c["status"] == "cancelled"]
    assert len(cancelled) == 1, finalize_calls
    assert cancelled[0]["error_msg"] == "client disconnect"


# --------------------------------------------------------------------- #
# (h) TASK-BUG-5 — POST /run gate: non-owner non-shared user → 403 +
# detail.code 1002 (字段级断言 / 守 feedback_pre_existing_error_strict_
# validation). Uses raw ``app_client`` instead of ``routes_client`` so
# the real ``services.user_can_access_app`` runs (routes_client mocks it
# allow-all for the routing/mode/SSE tests above).
# --------------------------------------------------------------------- #
APP_FORBIDDEN = "app-bug5-forbidden"


async def test_run_workflow_user_without_access_returns_403(
    app_client, async_db, jwt_token,
):
    from ncmu_backend.apps.dify_console_client import DifyConsoleClient
    from ncmu_backend.apps.routes import get_dify_console_client
    from ncmu_backend.main import app, get_dify_client
    from ncmu_backend.workflow.routes import get_dispatcher

    # Seed dify_apps row so resolve_mode would succeed if reached — but
    # the gate must short-circuit FIRST. No app_owners row → owner path
    # returns False → shared path queried.
    await async_db.execute(text(
        "INSERT INTO dify_apps (dify_app_id, name, mode) "
        "VALUES (:a, 'Forbidden', 'advanced-chat')"
    ), {"a": APP_FORBIDDEN})
    await async_db.commit()

    dcc = DifyConsoleClient(ttl_seconds=300)
    test_http = httpx.AsyncClient(timeout=httpx.Timeout(5.0))
    # FastAPI injects ALL Depends before the handler body — get_dispatcher
    # also needs an override or solve_dependencies raises before our gate
    # logic gets a chance to refuse. Dummy dispatcher is fine; handler
    # body returns 403 before touching it.
    dummy_dispatcher = ModeDispatcher(
        DifyStreamClient(base_url="http://dify-api:5001", api_key="k")
    )
    app.dependency_overrides[get_dify_console_client] = lambda: dcc
    app.dependency_overrides[get_dify_client] = lambda: test_http
    app.dependency_overrides[get_dispatcher] = lambda: dummy_dispatcher

    try:
        with respx.mock(assert_all_called=True) as rx:
            # Shared cache returns empty list → user not entitled via Dify
            # Console → gate returns False → 403.
            rx.get(url__regex=r".*/console/api/apps\?.*").mock(
                return_value=Response(200, json={"data": []})
            )
            resp = await app_client.post(
                f"/api/v1/ncmu/workflow/apps/{APP_FORBIDDEN}/run",
                json={"inputs": {}},
                headers=_bearer(jwt_token),
            )
            # 字段级断言 (守 feedback_pre_existing_error_strict_validation):
            # not just "non-200" — the exact code 1002 contract carries
            # 403 semantics ("App not accessible"), distinct from 1001
            # (auth) / 404 (not found) / 503 (downstream).
            assert resp.status_code == 403
            assert resp.json() == {
                "detail": {
                    "code": 1002,
                    "message": "App not accessible by current user",
                }
            }
    finally:
        app.dependency_overrides.pop(get_dify_console_client, None)
        app.dependency_overrides.pop(get_dify_client, None)
        app.dependency_overrides.pop(get_dispatcher, None)
        await test_http.aclose()


# --------------------------------------------------------------------- #
# (i) TASK-BUG-5 — POST /run gate: owner path short-circuits (DB
# app_owners exists() returns True → cache.list_apps_for_user is NEVER
# called → request proceeds past the gate). Proof: monkeypatch the
# shared cache call to raise AssertionError if reached.
# --------------------------------------------------------------------- #
APP_OWNED_BY_ADMIN = "app-bug5-owned"


async def test_run_workflow_owner_path_short_circuits_shared(
    app_client, async_db, jwt_token, monkeypatch,
):
    from ncmu_backend.main import app
    from ncmu_backend.workflow.routes import get_dispatcher

    await async_db.execute(text(
        "INSERT INTO dify_apps (dify_app_id, name, mode) "
        "VALUES (:a, 'Owned', 'advanced-chat')"
    ), {"a": APP_OWNED_BY_ADMIN})
    await async_db.execute(text(
        "INSERT INTO app_owners (app_id, owner_user_id, visibility) "
        "VALUES (:a, :uid, 'owner_only')"
    ), {"a": APP_OWNED_BY_ADMIN, "uid": str(ADMIN_USER_ID)})
    await async_db.commit()

    async def _shared_must_not_run(self, *args: Any, **kwargs: Any) -> list[dict]:
        raise AssertionError(
            "owner short-circuit failed — shared path reached. "
            "user_can_access_app should have returned True via DB "
            "owner check before hitting DifyConsoleClient."
        )
    monkeypatch.setattr(
        "ncmu_backend.apps.dify_console_client.DifyConsoleClient.list_apps_for_user",
        _shared_must_not_run,
    )

    # Install dispatcher (no orchestrator registered — downstream will
    # KeyError, but that's BEYOND the gate; status 200 = gate passed).
    # Also override get_dify_client + get_dify_console_client so FastAPI
    # solve_dependencies doesn't RuntimeError (no lifespan in tests).
    from ncmu_backend.apps.dify_console_client import DifyConsoleClient
    from ncmu_backend.apps.routes import get_dify_console_client
    from ncmu_backend.main import get_dify_client

    dispatcher = ModeDispatcher(
        DifyStreamClient(base_url="http://dify-api:5001", api_key="k")
    )
    app.dependency_overrides[get_dispatcher] = lambda: dispatcher
    app.dependency_overrides[get_dify_client] = lambda: None
    # Real DifyConsoleClient — but list_apps_for_user is monkey-patched
    # above to raise if called, proving owner-path short-circuit.
    app.dependency_overrides[get_dify_console_client] = lambda: DifyConsoleClient(ttl_seconds=300)

    # Same shape as test_run_unregistered_mode_yields_sse_error_503: the
    # event_stream's ``except KeyError`` branch opens an independent
    # SessionLocal against the production NCMU_DB_URL (not the test
    # ephemeral DB) for the finalize. Mocking finalize_run keeps that
    # AsyncSession lazy so no real asyncpg connect happens — the
    # owner-gate verdict is the AC, finalize bookkeeping is exercised
    # by test_crud.py.
    async def _fake_finalize(_db, _run_id, _status, outputs=None, error_msg=None):
        return None
    monkeypatch.setattr(
        "ncmu_backend.workflow.crud.finalize_run", _fake_finalize,
    )

    try:
        async with app_client.stream(
            "POST",
            f"/api/v1/ncmu/workflow/apps/{APP_OWNED_BY_ADMIN}/run",
            json={"inputs": {}},
            headers=_bearer(jwt_token),
        ) as resp:
            # 200 = SSE stream opened → gate passed. The body will
            # carry the SSE 503 error frame because no orchestrator is
            # registered for advanced-chat, but the gate decision is
            # made BEFORE the stream begins — status_code 200 is the
            # proof. If owner short-circuit failed, _shared_must_not_run
            # would have raised AssertionError → request 500.
            assert resp.status_code == 200
            body = b""
            async for chunk in resp.aiter_bytes():
                body += chunk
            # Sanity: the body should be the KeyError-branch SSE error
            # (not the 1002 gate refusal — that would never have made
            # it past the dispatcher.dispatch call).
            assert b"event: error" in body
            assert b"503" in body
    finally:
        app.dependency_overrides.pop(get_dispatcher, None)
        app.dependency_overrides.pop(get_dify_client, None)
        app.dependency_overrides.pop(get_dify_console_client, None)


# --------------------------------------------------------------------- #
# (j) B-NEW-NEW-A — GET list_runs gate: non-owner non-shared → 403 +
# detail.code 1002. Mirror of (h) for POST /run; closes contract micro-
# asymmetry between workflow's 4 endpoints. Uses raw ``app_client`` so
# the real ``services.user_can_access_app`` runs (routes_client mocks
# it allow-all for the routing/mode/SSE tests above).
# --------------------------------------------------------------------- #
APP_FORBIDDEN_LIST = "app-bnnnew-a-forbidden"


async def test_list_runs_403_when_app_not_accessible(
    app_client, async_db, jwt_token,
):
    from ncmu_backend.apps.dify_console_client import DifyConsoleClient
    from ncmu_backend.apps.routes import get_dify_console_client
    from ncmu_backend.main import app, get_dify_client

    # Seed dify_apps row so the app_id is real — but no app_owners row
    # → owner path returns False → shared path queried → returns False
    # (respx mocks empty data) → gate refuses with 403.
    await async_db.execute(text(
        "INSERT INTO dify_apps (dify_app_id, name, mode) "
        "VALUES (:a, 'Forbidden List', 'advanced-chat')"
    ), {"a": APP_FORBIDDEN_LIST})
    await async_db.commit()

    dcc = DifyConsoleClient(ttl_seconds=300)
    test_http = httpx.AsyncClient(timeout=httpx.Timeout(5.0))
    app.dependency_overrides[get_dify_console_client] = lambda: dcc
    app.dependency_overrides[get_dify_client] = lambda: test_http

    try:
        with respx.mock(assert_all_called=True) as rx:
            rx.get(url__regex=r".*/console/api/apps\?.*").mock(
                return_value=Response(200, json={"data": []})
            )
            resp = await app_client.get(
                f"/api/v1/ncmu/workflow/apps/{APP_FORBIDDEN_LIST}/runs",
                headers=_bearer(jwt_token),
            )
            # 字段级断言 (守 feedback_pre_existing_error_strict_validation):
            # not just "non-200" — exact code 1002 contract carries 403
            # semantics distinct from 1001 (auth) / 404 (not found).
            assert resp.status_code == 403
            assert resp.json() == {
                "detail": {
                    "code": 1002,
                    "message": "App not accessible by current user",
                }
            }
    finally:
        app.dependency_overrides.pop(get_dify_console_client, None)
        app.dependency_overrides.pop(get_dify_client, None)
        await test_http.aclose()


# --------------------------------------------------------------------- #
# (k) B-NEW-NEW-A — GET list_runs gate: owner short-circuits (DB
# app_owners exists() → True → cache.list_apps_for_user is NEVER
# called → list_runs body executes → 200 + empty array). Regression-
# locks the existing list shape; proves owner-path doesn't 403.
# --------------------------------------------------------------------- #
APP_OWNED_FOR_LIST = "app-bnnnew-a-owned"


async def test_list_runs_owner_pass(
    app_client, async_db, jwt_token, monkeypatch,
):
    from ncmu_backend.apps.dify_console_client import DifyConsoleClient
    from ncmu_backend.apps.routes import get_dify_console_client
    from ncmu_backend.main import app, get_dify_client

    await async_db.execute(text(
        "INSERT INTO dify_apps (dify_app_id, name, mode) "
        "VALUES (:a, 'Owned List', 'advanced-chat')"
    ), {"a": APP_OWNED_FOR_LIST})
    await async_db.execute(text(
        "INSERT INTO app_owners (app_id, owner_user_id, visibility) "
        "VALUES (:a, :uid, 'owner_only')"
    ), {"a": APP_OWNED_FOR_LIST, "uid": str(ADMIN_USER_ID)})
    await async_db.commit()

    async def _shared_must_not_run(self, *args: Any, **kwargs: Any) -> list[dict]:
        raise AssertionError(
            "owner short-circuit failed — shared path reached for list_runs. "
            "user_can_access_app should have returned True via DB owner "
            "check before hitting DifyConsoleClient."
        )
    monkeypatch.setattr(
        "ncmu_backend.apps.dify_console_client.DifyConsoleClient.list_apps_for_user",
        _shared_must_not_run,
    )

    # http is unused on the owner short-circuit path but FastAPI still
    # injects it; None is fine because list_apps_for_user is monkey-
    # patched to fail if reached. Real DifyConsoleClient for the cache
    # dep — same proof-shape as test (i) above.
    app.dependency_overrides[get_dify_client] = lambda: None
    app.dependency_overrides[get_dify_console_client] = lambda: DifyConsoleClient(ttl_seconds=300)

    try:
        resp = await app_client.get(
            f"/api/v1/ncmu/workflow/apps/{APP_OWNED_FOR_LIST}/runs",
            headers=_bearer(jwt_token),
        )
        # 200 + [] = gate passed (existing list body executed; no runs
        # seeded so empty array). Regression-locks the pre-gate list
        # shape — if gate accidentally 403s owners, this fails.
        assert resp.status_code == 200
        assert resp.json() == []
    finally:
        app.dependency_overrides.pop(get_dify_client, None)
        app.dependency_overrides.pop(get_dify_console_client, None)
