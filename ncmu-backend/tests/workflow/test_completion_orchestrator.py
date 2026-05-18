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
    # TASK-D B-NEW-42 (Path B): fixture latency in seconds — × 1000 = 99.
    _FakeAsyncClient.LINES = [
        'data: {"event":"text_chunk","data":{"text":"hi"}}',
        'data: {"event":"message_end","metadata":{"usage":{"latency":0.099}}}',
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


# ===================================================================== #
# TASK-B silent-drop tests (spec §3 D3 + plan §Phase 3)
# ===================================================================== #


class _StubDifyClient:
    """Test stub for ``DifyStreamClient`` — yields pre-parsed Dify frames.

    Duck-typed: orchestrators only call ``.stream(path, body)``, so the
    stub bypasses HTTP / async-with / SSE-parse and exposes pure
    orchestrator event-mapping behaviour to the assertions.
    """

    def __init__(self, frames, raise_at=None, raise_exc=None):
        self._frames = list(frames)
        self._raise_at = raise_at
        self._raise_exc = raise_exc
        self.calls: list[tuple[str, dict]] = []

    async def stream(self, path, body):
        self.calls.append((path, body))
        for i, frame in enumerate(self._frames):
            if self._raise_at is not None and i == self._raise_at:
                raise self._raise_exc
            yield frame
        if (
            self._raise_at is not None
            and self._raise_at >= len(self._frames)
        ):
            raise self._raise_exc


def _run_inputs():
    return uuid.uuid4(), "app-completion-test", uuid.uuid4(), {"query": "translate", "inputs": {}}


# --------------------------------------------------------------------- #
# C1 — unknown event silently skipped + finally fall-through emits 1
# terminal envelope (plan §Phase 3 — completion 独有 text_chunk 事件覆盖)
# --------------------------------------------------------------------- #
async def test_c1_unknown_event_skipped_and_finally_emits_terminal():
    # text_chunk exercises completion's chunk-accumulation path; future_event
    # tests silent-skip. Neither emits a terminal → finally補发 1 envelope.
    frames = [
        {"event": "text_chunk", "data": {"text": "hi"}},
        {"event": "future_event", "data": {}},
    ]
    stub = _StubDifyClient(frames)
    orch = _make_orchestrator()
    run_id, app_id, user_id, inputs = _run_inputs()
    events = [ev async for ev in orch.run(stub, run_id, app_id, user_id, inputs)]

    # 0 known-event terminals + 1 finally terminal = 1.
    assert len(events) == 1, (
        f"expected exactly 1 envelope (only finally補发); got "
        f"{len(events)}: {[e.event_type for e in events]}"
    )
    terminal = events[0]
    assert terminal.event_type == "workflow_finished"
    assert isinstance(terminal.data, WorkflowFinishedData)
    assert terminal.data.status == "exception"
    assert terminal.data.error == "Upstream stream closed without terminal event"
    # accumulated_text from text_chunk preserved into outputs.
    assert terminal.data.outputs.get("answer") == "hi"


# --------------------------------------------------------------------- #
# C2 — Dify event:error → exactly 1 workflow_finished(failed)
# --------------------------------------------------------------------- #
async def test_c2_dify_event_error_yields_failed_terminal():
    frames = [
        {
            "event": "error",
            "code": "invalid_param",
            "status": 400,
            "message": "max_tokens too large",
        }
    ]
    stub = _StubDifyClient(frames)
    orch = _make_orchestrator()
    run_id, app_id, user_id, inputs = _run_inputs()
    events = [ev async for ev in orch.run(stub, run_id, app_id, user_id, inputs)]

    assert len(events) == 1, (
        f"expected exactly 1 envelope (error returns + finally skips); "
        f"got {len(events)}: {[e.event_type for e in events]}"
    )
    terminal = events[0]
    assert terminal.event_type == "workflow_finished"
    assert isinstance(terminal.data, WorkflowFinishedData)
    assert terminal.data.status == "failed"
    assert "invalid_param" in terminal.data.error
    assert "max_tokens too large" in terminal.data.error


# --------------------------------------------------------------------- #
# C3 — upstream httpx.RequestError after some accumulation → exception terminal
# carries accumulated answer
# --------------------------------------------------------------------- #
async def test_c3_upstream_exception_yields_exception_terminal():
    import httpx

    # Frames accumulate "翻译" + "结" (no terminal yet) → raise → except
    # yields exception terminal with outputs.answer == "翻译结".
    frames = [
        {"event": "message", "answer": "翻译"},
        {"event": "text_chunk", "data": {"text": "结"}},
    ]
    stub = _StubDifyClient(
        frames,
        raise_at=len(frames),
        raise_exc=httpx.RequestError("upstream lost"),
    )
    orch = _make_orchestrator()
    run_id, app_id, user_id, inputs = _run_inputs()
    events = [ev async for ev in orch.run(stub, run_id, app_id, user_id, inputs)]

    # 0 normal yields (message + text_chunk only accumulate) + 1 exception terminal.
    assert len(events) == 1, (
        f"expected exactly 1 exception terminal; got "
        f"{len(events)}: {[e.event_type for e in events]}"
    )
    terminal = events[0]
    assert terminal.event_type == "workflow_finished"
    assert isinstance(terminal.data, WorkflowFinishedData)
    assert terminal.data.status == "exception"
    assert "RequestError" in terminal.data.error
    assert "upstream lost" in terminal.data.error
    # accumulated_text from both frames preserved.
    assert terminal.data.outputs.get("answer") == "翻译结"


# --------------------------------------------------------------------- #
# C5 — REWORK-INDEP-I-1: CancelledError must NOT yield a finally envelope
# (regression-lock for the cancelled sentinel; see test_advanced_chat C5).
# --------------------------------------------------------------------- #
async def test_c5_cancelled_error_does_not_yield_extra_envelope():
    import asyncio

    # Completion has no envelope-yielding events except message_end /
    # workflow_finished / event:error — message + text_chunk accumulate
    # only. So 1 message frame yields 0 envelopes, then CancelledError
    # on the 2nd iteration. Without the ``not cancelled`` guard, finally
    # would yield 1 stray workflow_finished(exception) and swallow the
    # CancelledError; with the fix, events stay empty AND CancelledError
    # propagates to the caller.
    frames = [{"event": "message", "answer": "x"}]
    stub = _StubDifyClient(
        frames,
        raise_at=len(frames),
        raise_exc=asyncio.CancelledError(),
    )
    orch = _make_orchestrator()
    run_id, app_id, user_id, inputs = _run_inputs()

    events = []
    with pytest.raises(asyncio.CancelledError):
        async for ev in orch.run(stub, run_id, app_id, user_id, inputs):
            events.append(ev)

    assert len(events) == 0, (
        f"REWORK-INDEP-I-1: finally must NOT yield on cancellation path; "
        f"expected 0 envelopes (completion has no non-terminal yields); "
        f"got {len(events)}: {[e.event_type for e in events]}"
    )


# ===================================================================== #
# TASK-D timeout + units tests (B-NEW-41 + B-NEW-42 / plan §AC#5)
# ===================================================================== #


async def test_d_timeout_exception_emits_status_timeout():
    """B-NEW-41 — httpx.TimeoutException → workflow_finished(status='timeout').

    Explicit timeout branch (placed between asyncio.CancelledError and
    the generic Exception handler) translates upstream timeouts into a
    dedicated status='timeout' envelope instead of the generic
    'exception' bucket — aligns with main.py boot-sweeper's
    finalize_run(status='timeout') path.
    """
    import httpx

    stub = _StubDifyClient(
        [],
        raise_at=0,
        raise_exc=httpx.TimeoutException("read timeout"),
    )
    orch = _make_orchestrator()
    run_id, app_id, user_id, inputs = _run_inputs()
    events = [ev async for ev in orch.run(stub, run_id, app_id, user_id, inputs)]

    # (a) exactly 1 envelope — the timeout terminal
    assert len(events) == 1, (
        f"expected exactly 1 timeout terminal envelope; got {len(events)}: "
        f"{[e.event_type for e in events]}"
    )
    # (b) event_type
    assert events[0].event_type == "workflow_finished"
    # (c) status literal "timeout" (==, not in/startswith) — 守
    # feedback_pre_existing_error_strict_validation 字段级断言纪律
    assert events[0].data.status == "timeout", (
        f"B-NEW-41: timeout branch must emit status='timeout' (not "
        f"'exception'); got {events[0].data.status!r}"
    )
    # (d) error field non-empty
    assert events[0].data.error != "", (
        f"timeout terminal must carry a non-empty error string; got "
        f"{events[0].data.error!r}"
    )
    # (e) error string literally contains 'upstream timeout'
    assert "upstream timeout" in events[0].data.error, (
        f"timeout error string must literally contain 'upstream timeout' "
        f"for human readability; got {events[0].data.error!r}"
    )


async def test_d_elapsed_time_multiplied_by_1000():
    """B-NEW-42 — metadata.usage.latency (seconds) → total_elapsed_ms (ms) × 1000.

    Dify v1.13.x emits LLMUsage.latency in seconds (float). The NCMU
    schema retains the ``_ms`` suffix for SPA back-compat; the
    orchestrator now multiplies by 1000 at the boundary so the value
    matches the field name. None remains None (no implicit 0 coercion).
    """
    # Case 1: real seconds value → int(× 1000)
    frames = [
        {
            "event": "message_end",
            "metadata": {"usage": {"latency": 1.2286}},
        }
    ]
    stub = _StubDifyClient(frames)
    orch = _make_orchestrator()
    run_id, app_id, user_id, inputs = _run_inputs()
    events = [ev async for ev in orch.run(stub, run_id, app_id, user_id, inputs)]
    assert len(events) == 1
    assert events[0].data.total_elapsed_ms == 1228, (
        f"B-NEW-42: latency 1.2286s × 1000 truncated to int = 1228; got "
        f"{events[0].data.total_elapsed_ms!r}"
    )

    # Case 2: latency missing → None (no implicit 0)
    frames_none = [{"event": "message_end"}]
    stub_none = _StubDifyClient(frames_none)
    events_none = [
        ev async for ev in orch.run(stub_none, run_id, app_id, user_id, inputs)
    ]
    assert len(events_none) == 1
    assert events_none[0].data.total_elapsed_ms is None, (
        f"B-NEW-42: missing latency must yield None (not 0); got "
        f"{events_none[0].data.total_elapsed_ms!r}"
    )
