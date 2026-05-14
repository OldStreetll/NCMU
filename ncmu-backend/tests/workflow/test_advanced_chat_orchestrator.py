"""TASK-71 — AdvancedChatOrchestrator integration tests (≥6 cases per AC#3).

Test layering:
- Same monkeypatch-on-httpx pattern as ``test_base_orchestrator.py`` so
  the production code path (DifyStreamClient → orchestrator) is exercised
  end-to-end without a real Dify backend. The fixture file is the single
  source of truth for what "Dify v1.13.3 chatflow" looks like — we load
  it once and prefix each row with ``data: `` to feed the SSE parser.

Cases (mapped to plan §B2 TASK-71 AC#3 a-f + AC#5):
  (a) AC#3(a) — yields exactly 5 NCMU events (2 node_started + 2
      node_finished + 1 workflow_finished); message + message_end are
      silently accumulated, not mapped.
  (b) AC#3(b) — yield order matches fixture order (node 1 start →
      node 1 finish → node 2 start → node 2 finish → workflow_finished).
  (c) AC#3(c) — NodeStartedData fields project literally from the
      data-nested fixture rows (H2 path).
  (d) AC#3(d) F-NEW-1 — workflow_finished.outputs.answer == "hello"
      after accumulating the single ``message`` row.
  (e) AC#3(e) — unknown Dify event_name is skipped without raising.
  (f) AC#3(f) F-NEW-1 inverse — drop message + message_end → no
      ``answer`` key in outputs (setdefault never fires on empty buffer).
  (g) AC#5 — ModeDispatcher.register("advanced-chat", orch) +
      dispatcher.dispatch(...) → ≥5 events through the full router
      (no DB / no routes module, just dispatcher + orchestrator + mock).
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Iterable

import pytest


# --------------------------------------------------------------------- #
# Fixture loader + fake httpx (same shape as test_base_orchestrator.py)
# --------------------------------------------------------------------- #
FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "advanced_chat_dify_sse.jsonl"
)


def _load_fixture_lines() -> list[str]:
    """Each row → one ``data: {...}`` SSE-formatted line."""
    rows = [
        line for line in FIXTURE_PATH.read_text().splitlines() if line.strip()
    ]
    return [f"data: {row}" for row in rows]


def _drop_events(lines: list[str], event_names: set[str]) -> list[str]:
    """Filter out fixture rows whose ``event`` is in ``event_names``."""
    out = []
    for line in lines:
        payload = line[len("data: ") :]
        try:
            doc = json.loads(payload)
        except json.JSONDecodeError:
            out.append(line)
            continue
        if doc.get("event") in event_names:
            continue
        out.append(line)
    return out


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
    LINES: list[str] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    def stream(self, method: str, url: str, **kwargs):
        return _FakeStreamCM(_FakeAsyncClient.LINES)


@pytest.fixture(autouse=True)
def _patch_httpx(monkeypatch):
    monkeypatch.setattr(
        "ncmu_backend.workflow.dify_client.httpx.AsyncClient",
        _FakeAsyncClient,
    )
    _FakeAsyncClient.LINES = []
    yield
    _FakeAsyncClient.LINES = []


def _make_orchestrator():
    """ARCH-FIX-79: orchestrator no longer holds its own DifyStreamClient
    (per-App client is injected via .run()'s first arg). Tests construct
    the client separately via _make_client() and pass to .run()."""
    from ncmu_backend.workflow.advanced_chat import AdvancedChatOrchestrator

    return AdvancedChatOrchestrator()


def _make_client():
    from ncmu_backend.workflow.dify_client import DifyStreamClient

    return DifyStreamClient(base_url="http://dify-api:5001", api_key="k")


def _run_inputs():
    return (
        uuid.uuid4(),
        "app-advanced-chat",
        uuid.uuid4(),
        {"query": "hi", "inputs": {}, "conversation_id": ""},
    )


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
# (a) AC#3(a) — yields exactly 5 NCMU events
# --------------------------------------------------------------------- #
async def test_yields_five_events_from_seven_row_fixture():
    _FakeAsyncClient.LINES = _load_fixture_lines()
    orch = _make_orchestrator()
    run_id, app_id, user_id, inputs = _run_inputs()
    events = [ev async for ev in orch.run(_make_client(), run_id, app_id, user_id, inputs)]
    assert len(events) == 5, (
        f"expected 5 NCMU events (2 node_started + 2 node_finished + 1 "
        f"workflow_finished); message + message_end must be accumulated "
        f"silently, got {len(events)}: {[e.event_type for e in events]}"
    )


# --------------------------------------------------------------------- #
# (b) AC#3(b) — yield order matches fixture order
# --------------------------------------------------------------------- #
async def test_yield_sequence_matches_fixture_order():
    _FakeAsyncClient.LINES = _load_fixture_lines()
    orch = _make_orchestrator()
    run_id, app_id, user_id, inputs = _run_inputs()
    types = [ev.event_type async for ev in orch.run(_make_client(), run_id, app_id, user_id, inputs)]
    assert types == [
        "node_started",
        "node_finished",
        "node_started",
        "node_finished",
        "workflow_finished",
    ]


# --------------------------------------------------------------------- #
# (c) AC#3(c) — NodeStartedData fields project literally from fixture
# --------------------------------------------------------------------- #
async def test_node_started_fields_map_from_data_nest():
    from ncmu_backend.schemas.sse_events import NodeStartedData

    _FakeAsyncClient.LINES = _load_fixture_lines()
    orch = _make_orchestrator()
    run_id, app_id, user_id, inputs = _run_inputs()
    events = [ev async for ev in orch.run(_make_client(), run_id, app_id, user_id, inputs)]
    first_started = events[0]
    assert first_started.event_type == "node_started"
    assert isinstance(first_started.data, NodeStartedData)
    # H2: fields lifted from data.{...}, not the top-level frame
    assert first_started.data.node_id == "start_1"
    assert first_started.data.node_type == "start"
    assert first_started.data.title == "开始"
    assert first_started.data.inputs == {}

    # Second node_started (LLM 推理) — sanity that the loop doesn't
    # collapse fields from the previous event (catches any state leak).
    second_started = events[2]
    assert second_started.data.node_id == "llm_1"
    assert second_started.data.node_type == "llm"
    assert second_started.data.title == "LLM 推理"


# --------------------------------------------------------------------- #
# (d) AC#3(d) F-NEW-1 — outputs.answer == "hello"
# --------------------------------------------------------------------- #
async def test_workflow_finished_merges_accumulated_answer():
    from ncmu_backend.schemas.sse_events import WorkflowFinishedData

    _FakeAsyncClient.LINES = _load_fixture_lines()
    orch = _make_orchestrator()
    run_id, app_id, user_id, inputs = _run_inputs()
    events = [ev async for ev in orch.run(_make_client(), run_id, app_id, user_id, inputs)]
    final = events[-1]
    assert final.event_type == "workflow_finished"
    assert isinstance(final.data, WorkflowFinishedData)
    # Fixture's workflow_finished has outputs={}; accumulated_answer
    # ("hello" from the single message row) merges in via setdefault.
    assert final.data.outputs.get("answer") == "hello", (
        f"F-NEW-1: WorkflowFinishedData.outputs.answer should equal "
        f"the accumulated 'hello'; got outputs={final.data.outputs!r}"
    )
    assert final.data.status == "succeeded"
    assert final.data.total_elapsed_ms == 2400


# --------------------------------------------------------------------- #
# (e) AC#3(e) — unknown Dify event_name skipped, no exception
# --------------------------------------------------------------------- #
async def test_unknown_event_name_is_skipped():
    _FakeAsyncClient.LINES = _load_fixture_lines() + [
        'data: {"event":"unknown","task_id":"t1","data":{}}',
        'data: {"event":"text_chunk","task_id":"t1","data":{}}',
    ]
    orch = _make_orchestrator()
    run_id, app_id, user_id, inputs = _run_inputs()
    # Should still be exactly 5 mapped events; the 2 unknowns drop on the floor.
    events = [ev async for ev in orch.run(_make_client(), run_id, app_id, user_id, inputs)]
    assert len(events) == 5
    assert all(
        ev.event_type
        in {"node_started", "node_finished", "workflow_finished"}
        for ev in events
    )


# --------------------------------------------------------------------- #
# (f) AC#3(f) F-NEW-1 inverse — drop message + message_end → no answer
# --------------------------------------------------------------------- #
async def test_no_message_rows_means_no_answer_key():
    _FakeAsyncClient.LINES = _drop_events(
        _load_fixture_lines(), {"message", "message_end"}
    )
    orch = _make_orchestrator()
    run_id, app_id, user_id, inputs = _run_inputs()
    events = [ev async for ev in orch.run(_make_client(), run_id, app_id, user_id, inputs)]
    final = events[-1]
    assert final.event_type == "workflow_finished"
    # accumulated_answer stayed "" so the `if accumulated_answer` guard
    # blocked setdefault — no "answer" key should appear.
    assert "answer" not in final.data.outputs, (
        f"F-NEW-1 inverse: empty accumulated_answer must NOT seed "
        f"outputs.answer; got outputs={final.data.outputs!r}"
    )


# --------------------------------------------------------------------- #
# (g) AC#5 — ModeDispatcher.register + dispatch → ≥5 events end-to-end
# --------------------------------------------------------------------- #
async def test_dispatcher_registers_and_dispatches_advanced_chat():
    from ncmu_backend.workflow.advanced_chat import AdvancedChatOrchestrator
    from ncmu_backend.workflow.dify_client import DifyStreamClient
    from ncmu_backend.workflow.mode_dispatcher import ModeDispatcher

    _FakeAsyncClient.LINES = _load_fixture_lines()
    dsc = DifyStreamClient(base_url="http://dify-api:5001", api_key="k")
    dispatcher = ModeDispatcher(dsc)
    dispatcher.register("advanced-chat", AdvancedChatOrchestrator())

    run_id, app_id, user_id, inputs = _run_inputs()
    events = [
        ev async for ev in dispatcher.dispatch(_fake_db_null_token(), "advanced-chat", run_id, app_id, user_id, inputs
        )
    ]
    assert len(events) >= 5, (
        f"AC#5: dispatcher → orchestrator must yield ≥5 NCMU events; "
        f"got {len(events)}"
    )
    # Sanity: the run_id we passed in is what the orchestrator stamps.
    assert all(ev.run_id == run_id for ev in events)


# ===================================================================== #
# TASK-B silent-drop tests (spec §3 D3 + plan §Phase 1)
# ===================================================================== #
# Stub DifyStreamClient subclass: bypasses real HTTP — orchestrator calls
# stub.stream() directly and gets pre-parsed dicts. ``raise_at`` controls
# when (and whether) the stream raises:
#   raise_at = None         → never raise (regular yield)
#   raise_at = N < len      → yield frames[0..N-1], then raise (mid-stream)
#   raise_at >= len(frames) → yield all frames, then raise (late exception)
# ===================================================================== #


class _StubDifyClient:
    """Test stub for ``DifyStreamClient`` — yields pre-parsed Dify frames.

    Not a subclass: orchestrators only call ``.stream(path, body)``, so
    duck-typing keeps the stub independent of DifyStreamClient ``__init__``
    requirements. The stub bypasses every HTTP / async-with / SSE-parse
    code path; the test surface is pure orchestrator event mapping.
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


def _load_fixture_frames() -> list[dict]:
    """Parse the JSONL fixture file once and return a list of dicts."""
    return [
        json.loads(line)
        for line in FIXTURE_PATH.read_text().splitlines()
        if line.strip()
    ]


# --------------------------------------------------------------------- #
# C1 — unknown event silently skipped + finally fall-through emits 1
# terminal envelope (plan §Phase 1 1.2 C1)
# --------------------------------------------------------------------- #
async def test_c1_unknown_event_skipped_and_finally_emits_terminal():
    from ncmu_backend.schemas.sse_events import WorkflowFinishedData

    frames = [
        {
            "event": "node_started",
            "task_id": "t1",
            "workflow_run_id": "r1",
            "data": {
                "id": "e1",
                "node_id": "start_1",
                "node_type": "start",
                "title": "开始",
                "inputs": None,
            },
        },
        # Unknown event — must be silent-skipped (no envelope yielded).
        {"event": "future_event", "task_id": "t1", "data": {}},
        {
            "event": "node_finished",
            "task_id": "t1",
            "workflow_run_id": "r1",
            "data": {
                "id": "e2",
                "node_id": "start_1",
                "node_type": "start",
                "status": "succeeded",
                "elapsed_time": 12,
                "outputs": None,
            },
        },
    ]
    stub = _StubDifyClient(frames)
    orch = _make_orchestrator()
    run_id, app_id, user_id, inputs = _run_inputs()
    events = [ev async for ev in orch.run(stub, run_id, app_id, user_id, inputs)]

    # 2 known events mapped + 1 finally terminal (no upstream workflow_finished).
    assert len(events) == 3, (
        f"expected 2 known events + 1 finally terminal = 3; got "
        f"{len(events)}: {[e.event_type for e in events]}"
    )
    assert [e.event_type for e in events] == [
        "node_started",
        "node_finished",
        "workflow_finished",
    ]
    terminal = events[-1]
    assert isinstance(terminal.data, WorkflowFinishedData)
    assert terminal.data.status == "exception"
    assert terminal.data.error == "Upstream stream closed without terminal event"


# --------------------------------------------------------------------- #
# C2 — Dify event:error → exactly 1 workflow_finished(failed) (plan §1.2 C2)
# --------------------------------------------------------------------- #
async def test_c2_dify_event_error_yields_failed_terminal():
    from ncmu_backend.schemas.sse_events import WorkflowFinishedData

    # dify-sse-error-frame-contract §1.1: error frame fields are top-level
    # (NOT data-nested) — code is string, status is int, message is string.
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
        f"expected exactly 1 envelope (error branch returns + finally "
        f"skips because emitted_terminal=True); got {len(events)}: "
        f"{[e.event_type for e in events]}"
    )
    terminal = events[0]
    assert terminal.event_type == "workflow_finished"
    assert isinstance(terminal.data, WorkflowFinishedData)
    # AC#5: status literal "failed" (==, not in / startswith).
    assert terminal.data.status == "failed"
    # error string must carry both Dify code AND message for diagnosis.
    assert "invalid_param" in terminal.data.error
    assert "max_tokens too large" in terminal.data.error


# --------------------------------------------------------------------- #
# C3 — upstream httpx.RequestError mid-stream → finally補 exception terminal
# (plan §1.2 C3)
# --------------------------------------------------------------------- #
async def test_c3_upstream_exception_yields_exception_terminal():
    import httpx

    from ncmu_backend.schemas.sse_events import WorkflowFinishedData

    # Take first 3 frames of the fixture (2 node_started + 1 node_finished
    # mid-pair — orchestrator yields 3 normal envelopes), then raise.
    all_frames = _load_fixture_frames()
    head_frames = all_frames[:3]  # node_started, node_finished, node_started
    stub = _StubDifyClient(
        head_frames,
        raise_at=len(head_frames),  # raise after yielding all 3
        raise_exc=httpx.RequestError("upstream lost"),
    )
    orch = _make_orchestrator()
    run_id, app_id, user_id, inputs = _run_inputs()
    events = [ev async for ev in orch.run(stub, run_id, app_id, user_id, inputs)]

    # 3 normal events + 1 exception terminal = 4.
    assert len(events) == 4, (
        f"expected 3 normal + 1 exception terminal = 4; got "
        f"{len(events)}: {[e.event_type for e in events]}"
    )
    # First 3 are the normal node events from the fixture.
    assert [e.event_type for e in events[:3]] == [
        "node_started",
        "node_finished",
        "node_started",
    ]
    terminal = events[-1]
    assert terminal.event_type == "workflow_finished"
    assert isinstance(terminal.data, WorkflowFinishedData)
    # AC#5: status literal "exception" (== — not "failed" which is for event:error).
    assert terminal.data.status == "exception"
    # error string must carry exception type name + message.
    assert "RequestError" in terminal.data.error
    assert "upstream lost" in terminal.data.error


# --------------------------------------------------------------------- #
# C4 — double-emit guard: late exception AFTER terminal envelope ≠ extra yield
# (spec §AC#4 — dedicated test on this orchestrator only)
# --------------------------------------------------------------------- #
async def test_c4_double_emit_guard_after_workflow_finished():
    from ncmu_backend.schemas.sse_events import WorkflowFinishedData

    # Full 7-frame fixture (terminates with workflow_finished → sets
    # emitted_terminal=True), then raise — the except-block must check
    # emitted_terminal and SKIP yielding (no double terminal envelope).
    all_frames = _load_fixture_frames()
    stub = _StubDifyClient(
        all_frames,
        raise_at=len(all_frames),  # raise after all 7 frames yielded
        raise_exc=RuntimeError("late error"),
    )
    orch = _make_orchestrator()
    run_id, app_id, user_id, inputs = _run_inputs()
    events = [ev async for ev in orch.run(stub, run_id, app_id, user_id, inputs)]

    # Fixture yields: node_started, node_finished, node_started,
    # node_finished, workflow_finished (5 NCMU events — message +
    # message_end accumulate silently). Late RuntimeError must NOT
    # produce a 6th envelope.
    assert len(events) == 5, (
        f"AC#4 double-emit guard: expected exactly 5 envelopes (no extra "
        f"after late RuntimeError); got {len(events)}: "
        f"{[e.event_type for e in events]}"
    )
    terminal = events[-1]
    assert terminal.event_type == "workflow_finished"
    assert isinstance(terminal.data, WorkflowFinishedData)
    # The terminal stays "succeeded" — late exception did NOT downgrade it.
    assert terminal.data.status == "succeeded"
    # F-NEW-1: accumulated message answer merged in.
    assert terminal.data.outputs.get("answer") == "hello"


# --------------------------------------------------------------------- #
# C5 — REWORK-INDEP-I-1: CancelledError must NOT yield a finally envelope
# and must propagate cleanly (regression-lock for the cancelled sentinel).
#
# Without the ``not cancelled`` guard in finally, two bugs trigger together:
#   (1) consumer receives a stray ``workflow_finished(exception)`` envelope
#       — routes.py would then write a misleading finalize_run("exception")
#       to the DB on a path that was simply client disconnect.
#   (2) the yield in finally suppresses the in-flight CancelledError, so it
#       never surfaces to the caller — pytest.raises would NOT trigger.
# This test asserts both fixes in one shot.
# --------------------------------------------------------------------- #
async def test_c5_cancelled_error_does_not_yield_extra_envelope():
    import asyncio

    # First 2 frames yield 2 normal NCMU envelopes; then stub raises
    # asyncio.CancelledError on the 3rd iteration — the orchestrator's
    # ``except CancelledError`` sets the sentinel and re-raises, ``finally``
    # checks ``not cancelled`` and skips its catch-all yield.
    head = _load_fixture_frames()[:2]
    stub = _StubDifyClient(
        head,
        raise_at=len(head),
        raise_exc=asyncio.CancelledError(),
    )
    orch = _make_orchestrator()
    run_id, app_id, user_id, inputs = _run_inputs()

    events = []
    with pytest.raises(asyncio.CancelledError):
        async for ev in orch.run(stub, run_id, app_id, user_id, inputs):
            events.append(ev)

    # AC-REWORK#3 — exactly 2 envelopes (the 2 normal node events); no
    # finally補发. If the guard were missing, len(events) would be 3 AND
    # the pytest.raises block would never fire (CancelledError suppressed).
    assert len(events) == 2, (
        f"REWORK-INDEP-I-1: finally must NOT yield on cancellation path; "
        f"expected exactly 2 normal envelopes; got {len(events)}: "
        f"{[e.event_type for e in events]}"
    )
    assert [e.event_type for e in events] == ["node_started", "node_finished"]
    # No exception/failed terminal leaked into the stream.
    for ev in events:
        if ev.event_type == "workflow_finished":
            assert ev.data.status == "succeeded", (
                "no exception/failed terminal should reach the consumer "
                "on the CancelledError path"
            )
