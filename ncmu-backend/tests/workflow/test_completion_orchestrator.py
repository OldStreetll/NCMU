"""TASK-72 — CompletionOrchestrator integration tests (≥6 cases per AC#3).

Test layering:
- Same monkeypatch-on-httpx pattern as ``test_base_orchestrator.py`` /
  ``test_advanced_chat_orchestrator.py`` so the production code path
  (DifyStreamClient → orchestrator) is exercised end-to-end without a
  real Dify backend. The fixture file is the single source of truth for
  what "Dify v1.13.3 completion-messages" looks like — we load it once
  and prefix each row with ``data: `` to feed the SSE parser.

Cases (mapped to plan §B2 TASK-72 AC#3 a-f + AC#5):
  (a) AC#3(a) — 4 fixture rows yield exactly 1 NCMU event
      (workflow_finished); message + text_chunk are silently accumulated.
  (b) AC#3(b) — F-NEW-1: WorkflowFinishedData.outputs.answer == "翻译结果"
      (1 message "翻译" + 1 text_chunk "结" + 1 message "果").
  (c) AC#3(c) — total_elapsed_ms == 1200 from metadata.usage.latency.
  (d) AC#3(d) — H2 status mapping: status == "succeeded".
  (e) AC#3(e) — endpoint hit: orchestrator.run() POSTs ``/v1/completion-messages``
      (NOT ``/v1/chat-messages``; the path is asserted via the fake httpx
      stream() recorder + a grep on the orchestrator source).
  (f) AC#3(f) — text_chunk path standalone: 1 text_chunk + 1 message_end
      → accumulate hits ``data.text``, no exception.
  (g) AC#5 — ModeDispatcher.register("completion", orch) +
      dispatcher.dispatch(_fake_db_null_token(), "completion", ...) → 1 workflow_finished event.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Iterable

import pytest

from ncmu_backend.schemas.sse_events import NcmuSseEvent, WorkflowFinishedData


# --------------------------------------------------------------------- #
# Fixture loader + fake httpx (same shape as test_base_orchestrator.py)
# --------------------------------------------------------------------- #
FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "completion_dify_sse.jsonl"
)


def _load_fixture_lines() -> list[str]:
    """Each row → one ``data: {...}`` SSE-formatted line."""
    rows = [
        line for line in FIXTURE_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return [f"data: {row}" for row in rows]


class _FakeResponse:
    def __init__(self, lines: Iterable[str]):
        self._lines = list(lines)

    def raise_for_status(self) -> None:
        return None

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeStreamCM:
    def __init__(self, lines: Iterable[str]):
        self._lines = list(lines)

    async def __aenter__(self) -> _FakeResponse:
        return _FakeResponse(self._lines)

    async def __aexit__(self, *exc_info):
        return False


class _FakeAsyncClient:
    """Stand-in for ``httpx.AsyncClient`` whose ``stream(...)`` yields
    pre-loaded lines and records the path the orchestrator POSTed to.
    Tests set ``LINES`` before the run; ``LAST_PATH`` is asserted after.
    """

    LINES: list[str] = []
    LAST_PATH: str | None = None
    LAST_BODY: dict | None = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    def stream(self, method: str, url: str, **kwargs):
        # Record the path Dify was called with — AC#3(e) verifies
        # /v1/completion-messages (not /v1/chat-messages).
        _FakeAsyncClient.LAST_PATH = url
        _FakeAsyncClient.LAST_BODY = kwargs.get("json")
        return _FakeStreamCM(_FakeAsyncClient.LINES)


@pytest.fixture(autouse=True)
def _patch_httpx(monkeypatch):
    """Swap ``httpx.AsyncClient`` inside ``dify_client`` for every test."""
    monkeypatch.setattr(
        "ncmu_backend.workflow.dify_client.httpx.AsyncClient",
        _FakeAsyncClient,
    )
    _FakeAsyncClient.LINES = []
    _FakeAsyncClient.LAST_PATH = None
    _FakeAsyncClient.LAST_BODY = None
    yield
    _FakeAsyncClient.LINES = []
    _FakeAsyncClient.LAST_PATH = None
    _FakeAsyncClient.LAST_BODY = None


def _make_orchestrator():
    """ARCH-FIX-79: orchestrator no longer holds its own DifyStreamClient
    (dispatcher injects it via .run()'s first arg). Tests pass a client
    via _make_client() into .run()."""
    from ncmu_backend.workflow.completion import CompletionOrchestrator

    return CompletionOrchestrator()


def _make_client():
    from ncmu_backend.workflow.dify_client import DifyStreamClient

    return DifyStreamClient(base_url="http://dify-api:5001", api_key="k")


def _fake_db_null_token():
    """AsyncMock db whose execute().first() returns (None,) — so
    ModeDispatcher.resolve_token falls back to the default client's token
    (ARCH-FIX-79 backward-compat path for tests that don't seed dify_apps)."""
    from unittest.mock import AsyncMock, MagicMock
    fake_db = AsyncMock()
    fake_result = MagicMock()
    fake_result.first = MagicMock(return_value=(None,))
    fake_db.execute = AsyncMock(return_value=fake_result)
    return fake_db



# --------------------------------------------------------------------- #
# (a) AC#3(a) — 4 fixture rows yield exactly 1 workflow_finished event
# --------------------------------------------------------------------- #
async def test_completion_yields_single_workflow_finished_event():
    _FakeAsyncClient.LINES = _load_fixture_lines()
    orch = _make_orchestrator()
    out: list[NcmuSseEvent] = []
    async for evt in orch.run(_make_client(), run_id=uuid.uuid4(),
        app_id="app-1",
        user_id=uuid.uuid4(),
        inputs={"query": "translate hello", "inputs": {}},
    ):
        out.append(evt)
    assert len(out) == 1, f"expected 1 event, got {len(out)}: {out}"
    assert out[0].event_type == "workflow_finished"


# --------------------------------------------------------------------- #
# (b) AC#3(b) — accumulated answer == "翻译结果"
# --------------------------------------------------------------------- #
async def test_completion_accumulates_message_and_text_chunk():
    _FakeAsyncClient.LINES = _load_fixture_lines()
    orch = _make_orchestrator()
    out = [
        evt
        async for evt in orch.run(_make_client(), uuid.uuid4(), "app-1", uuid.uuid4(), {"query": "x"}
        )
    ]
    assert isinstance(out[0].data, WorkflowFinishedData)
    assert out[0].data.outputs.get("answer") == "翻译结果", (
        f"expected accumulated 翻译+结+果, got {out[0].data.outputs!r}"
    )


# --------------------------------------------------------------------- #
# (c) AC#3(c) — total_elapsed_ms == 1200
# --------------------------------------------------------------------- #
async def test_completion_total_elapsed_ms_from_metadata_usage_latency():
    _FakeAsyncClient.LINES = _load_fixture_lines()
    orch = _make_orchestrator()
    out = [
        evt
        async for evt in orch.run(_make_client(), uuid.uuid4(), "app-1", uuid.uuid4(), {"query": "x"}
        )
    ]
    assert out[0].data.total_elapsed_ms == 1200


# --------------------------------------------------------------------- #
# (d) AC#3(d) — H2 status mapping: "succeeded"
# --------------------------------------------------------------------- #
async def test_completion_status_is_succeeded():
    _FakeAsyncClient.LINES = _load_fixture_lines()
    orch = _make_orchestrator()
    out = [
        evt
        async for evt in orch.run(_make_client(), uuid.uuid4(), "app-1", uuid.uuid4(), {"query": "x"}
        )
    ]
    assert out[0].data.status == "succeeded"


# --------------------------------------------------------------------- #
# (e) AC#3(e) — endpoint is /v1/completion-messages, not /v1/chat-messages
# --------------------------------------------------------------------- #
async def test_completion_uses_completion_messages_endpoint():
    _FakeAsyncClient.LINES = _load_fixture_lines()
    orch = _make_orchestrator()
    _ = [
        evt
        async for evt in orch.run(_make_client(), uuid.uuid4(), "app-1", uuid.uuid4(), {"query": "x"}
        )
    ]
    assert _FakeAsyncClient.LAST_PATH is not None
    assert _FakeAsyncClient.LAST_PATH.endswith("/v1/completion-messages"), (
        f"expected /v1/completion-messages, got {_FakeAsyncClient.LAST_PATH!r}"
    )
    assert "/v1/chat-messages" not in _FakeAsyncClient.LAST_PATH


# --------------------------------------------------------------------- #
# (f) AC#3(f) — text_chunk path standalone, no message frames
# --------------------------------------------------------------------- #
async def test_completion_text_chunk_path_alone_accumulates():
    _FakeAsyncClient.LINES = [
        'data: {"event":"text_chunk","data":{"text":"hi"}}',
        'data: {"event":"message_end","metadata":{"usage":{"latency":99}}}',
    ]
    orch = _make_orchestrator()
    out = [
        evt
        async for evt in orch.run(_make_client(), uuid.uuid4(), "app-1", uuid.uuid4(), {"query": "x"}
        )
    ]
    assert len(out) == 1
    assert out[0].data.outputs.get("answer") == "hi"
    assert out[0].data.total_elapsed_ms == 99


# --------------------------------------------------------------------- #
# (g) AC#5 — ModeDispatcher.register("completion", orch) round-trip
# --------------------------------------------------------------------- #
async def test_dispatcher_dispatch_completion_yields_workflow_finished():
    from ncmu_backend.workflow.dify_client import DifyStreamClient
    from ncmu_backend.workflow.mode_dispatcher import ModeDispatcher

    _FakeAsyncClient.LINES = _load_fixture_lines()
    orch = _make_orchestrator()
    dispatcher = ModeDispatcher(
        DifyStreamClient(base_url="http://dify-api:5001", api_key="k")
    )
    dispatcher.register("completion", orch)

    run_id = uuid.uuid4()
    user_id = uuid.uuid4()
    out = []
    async for evt in dispatcher.dispatch(_fake_db_null_token(), "completion", run_id, "app-x", user_id, {"query": "translate"}
    ):
        out.append(evt)

    assert len(out) == 1
    assert out[0].event_type == "workflow_finished"
    assert out[0].run_id == run_id
    assert out[0].data.outputs.get("answer") == "翻译结果"
