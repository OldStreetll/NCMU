"""TASK-74 AC#3 + AC#5 — AgentChatOrchestrator integration tests.

The orchestrator is exercised against a 5-line JSONL fixture
(``fixtures/agent_chat_dify_sse.jsonl``) that mimics Dify v1.13.3's
``/v1/chat-messages`` SSE stream when the App is in *agent-chat* mode.
Each line becomes a ``data: <json>`` SSE frame; the test stubs
``httpx.AsyncClient`` exactly the way ``test_base_orchestrator.py``
does, so the production parsing path inside ``DifyStreamClient`` is
unmodified.

Coverage map (plan §B2 line 2228-2234):
- (a) yield ≥5 NcmuSseEvent (2 agent_thought + 2 tool_call + 1 workflow_finished)
- (b) AgentThoughtData.thought == "我需要搜索"
- (c) ToolCallData.tool_name == "web_search"
- (d) 2nd ToolCallData.tool_output == "NCMU is..."
- (e) ToolCallData.status: 1st = "calling" (observation null), 2nd = "completed"
- (f) WorkflowFinishedData.outputs["answer"] == accumulated message text
- (g) AC#5: dispatcher.dispatch(mode="agent-chat") routes to this orchestrator
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Iterable

import pytest

from ncmu_backend.schemas.sse_events import (
    AgentThoughtData,
    NcmuSseEvent,
    ToolCallData,
    WorkflowFinishedData,
)


# --------------------------------------------------------------------- #
# Fake httpx.AsyncClient — same shape as test_base_orchestrator.py
# --------------------------------------------------------------------- #
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


# --------------------------------------------------------------------- #
# Load the JSONL fixture once and convert each line into a "data: ..."
# SSE frame, the format DifyStreamClient.stream() expects.
# --------------------------------------------------------------------- #
_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "agent_chat_dify_sse.jsonl"


def _fixture_as_sse_lines() -> list[str]:
    raw = _FIXTURE_PATH.read_text(encoding="utf-8").splitlines()
    return [f"data: {line}" for line in raw if line.strip()]


def _make_client():
    from ncmu_backend.workflow.dify_client import DifyStreamClient
    return DifyStreamClient(base_url="http://dify-api:5001", api_key="k")


async def _run_orchestrator() -> list[NcmuSseEvent]:
    """Drive AgentChatOrchestrator.run() against the fixture lines.

    ARCH-FIX-79: orchestrator is stateless — the DifyStreamClient is now
    passed to .run() as the first arg (dispatcher injection in production)."""
    from ncmu_backend.workflow.agent_chat import AgentChatOrchestrator

    _FakeAsyncClient.LINES = _fixture_as_sse_lines()
    orch = AgentChatOrchestrator()
    events: list[NcmuSseEvent] = []
    async for evt in orch.run(
        _make_client(),
        uuid.uuid4(),
        "app-agent-test-1",
        uuid.uuid4(),
        {"query": "search NCMU", "conversation_id": ""},
    ):
        events.append(evt)
    return events


@pytest.fixture
async def events() -> list[NcmuSseEvent]:
    return await _run_orchestrator()


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
# (a) yield ≥5 NcmuSseEvent — 2 agent_thought + 2 tool_call + 1 finished
# --------------------------------------------------------------------- #
async def test_yields_2_agent_thought_2_tool_call_1_workflow_finished(events):
    types = [e.event_type for e in events]
    assert types.count("agent_thought") == 2, (
        f"expected 2 agent_thought events, got {types}"
    )
    assert types.count("tool_call") == 2, (
        f"expected 2 tool_call events, got {types}"
    )
    assert types.count("workflow_finished") == 1, (
        f"expected 1 workflow_finished event, got {types}"
    )
    assert len(events) >= 5, f"expected ≥5 events, got {len(events)}"


# --------------------------------------------------------------------- #
# (b) first AgentThoughtData.thought == "我需要搜索"
# --------------------------------------------------------------------- #
async def test_first_agent_thought_thought_text(events):
    thoughts = [e for e in events if e.event_type == "agent_thought"]
    assert isinstance(thoughts[0].data, AgentThoughtData)
    assert thoughts[0].data.thought == "我需要搜索"


# --------------------------------------------------------------------- #
# (c) ToolCallData.tool_name == "web_search"
# --------------------------------------------------------------------- #
async def test_tool_call_tool_name_is_web_search(events):
    tool_calls = [e for e in events if e.event_type == "tool_call"]
    assert len(tool_calls) == 2
    for tc in tool_calls:
        assert isinstance(tc.data, ToolCallData)
        assert tc.data.tool_name == "web_search"


# --------------------------------------------------------------------- #
# (d) 2nd ToolCallData.tool_output == "NCMU is..." (observation literal)
# --------------------------------------------------------------------- #
async def test_second_tool_call_tool_output_is_observation(events):
    tool_calls = [e for e in events if e.event_type == "tool_call"]
    assert isinstance(tool_calls[1].data, ToolCallData)
    assert tool_calls[1].data.tool_output == "NCMU is..."


# --------------------------------------------------------------------- #
# (e) status: 1st = "calling" (observation null) / 2nd = "completed"
# --------------------------------------------------------------------- #
async def test_tool_call_status_calling_then_completed(events):
    tool_calls = [e for e in events if e.event_type == "tool_call"]
    assert isinstance(tool_calls[0].data, ToolCallData)
    assert isinstance(tool_calls[1].data, ToolCallData)
    assert tool_calls[0].data.status == "calling", (
        "1st tool_call has observation=null → status must be 'calling'"
    )
    assert tool_calls[1].data.status == "completed", (
        "2nd tool_call has observation='NCMU is...' → status must be 'completed'"
    )
    # First tool_call's tool_output mirrors the null observation.
    assert tool_calls[0].data.tool_output is None


# --------------------------------------------------------------------- #
# (f) WorkflowFinishedData.outputs["answer"] == accumulated message text
# --------------------------------------------------------------------- #
async def test_workflow_finished_accumulates_message_answers(events):
    finished = [e for e in events if e.event_type == "workflow_finished"]
    assert len(finished) == 1
    assert isinstance(finished[0].data, WorkflowFinishedData)
    assert finished[0].data.status == "succeeded"
    assert finished[0].data.outputs["answer"] == "根据搜索，NCMU 是企业 AI 平台。"


# --------------------------------------------------------------------- #
# (g) AC#5 — dispatcher.dispatch(mode="agent-chat") routes here
# --------------------------------------------------------------------- #
async def test_dispatcher_routes_agent_chat_mode():
    from ncmu_backend.workflow.agent_chat import AgentChatOrchestrator
    from ncmu_backend.workflow.dify_client import DifyStreamClient
    from ncmu_backend.workflow.mode_dispatcher import ModeDispatcher

    dify = DifyStreamClient(base_url="http://dify-api:5001", api_key="k")
    dispatcher = ModeDispatcher(dify)
    dispatcher.register("agent-chat", AgentChatOrchestrator())

    _FakeAsyncClient.LINES = _fixture_as_sse_lines()
    run_id = uuid.uuid4()
    user_id = uuid.uuid4()
    types: list[str] = []
    async for evt in dispatcher.dispatch(
        _fake_db_null_token(), "agent-chat", run_id, "app-x", user_id, {"query": "q", "conversation_id": ""}
    ):
        types.append(evt.event_type)

    # Routed to AgentChatOrchestrator → same envelope sequence as (a).
    assert types.count("agent_thought") == 2
    assert types.count("tool_call") == 2
    assert types.count("workflow_finished") == 1


# ===================================================================== #
# TASK-B silent-drop tests (spec §3 D3 + plan §Phase 2)
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


def _load_fixture_frames() -> list[dict]:
    return [
        json.loads(line)
        for line in _FIXTURE_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _make_run_args():
    return uuid.uuid4(), "app-agent-test", uuid.uuid4(), {
        "query": "q",
        "conversation_id": "",
    }


# --------------------------------------------------------------------- #
# C1 — unknown event silently skipped + finally fall-through emits 1
# terminal envelope (plan §Phase 2 — agent_chat 独有 event guard)
# --------------------------------------------------------------------- #
async def test_c1_unknown_event_skipped_and_finally_emits_terminal():
    from ncmu_backend.schemas.sse_events import WorkflowFinishedData
    from ncmu_backend.workflow.agent_chat import AgentChatOrchestrator

    # agent_thought with tool=null → only 1 yield (no tool_call branch).
    # future_event → silently skipped. message → accumulated, no yield.
    frames = [
        {
            "event": "agent_thought",
            "thought": "thinking",
            "tool": None,
            "tool_input": {},
            "observation": None,
        },
        {"event": "future_event", "data": {}},
        {"event": "message", "answer": "hi"},
    ]
    stub = _StubDifyClient(frames)
    orch = AgentChatOrchestrator()
    run_id, app_id, user_id, inputs = _make_run_args()
    events = [ev async for ev in orch.run(stub, run_id, app_id, user_id, inputs)]

    # 1 agent_thought (no tool_call because tool=None) + 1 finally terminal.
    assert len(events) == 2, (
        f"expected 1 agent_thought + 1 finally terminal = 2; got "
        f"{len(events)}: {[e.event_type for e in events]}"
    )
    assert events[0].event_type == "agent_thought"
    terminal = events[-1]
    assert terminal.event_type == "workflow_finished"
    assert isinstance(terminal.data, WorkflowFinishedData)
    assert terminal.data.status == "exception"
    assert terminal.data.error == "Upstream stream closed without terminal event"


# --------------------------------------------------------------------- #
# C2 — Dify event:error → exactly 1 workflow_finished(failed)
# --------------------------------------------------------------------- #
async def test_c2_dify_event_error_yields_failed_terminal():
    from ncmu_backend.schemas.sse_events import WorkflowFinishedData
    from ncmu_backend.workflow.agent_chat import AgentChatOrchestrator

    frames = [
        {
            "event": "error",
            "code": "invalid_param",
            "status": 400,
            "message": "max_tokens too large",
        }
    ]
    stub = _StubDifyClient(frames)
    orch = AgentChatOrchestrator()
    run_id, app_id, user_id, inputs = _make_run_args()
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
# C3 — upstream httpx.RequestError mid-stream → finally補 exception terminal
# --------------------------------------------------------------------- #
async def test_c3_upstream_exception_yields_exception_terminal():
    import httpx

    from ncmu_backend.schemas.sse_events import WorkflowFinishedData
    from ncmu_backend.workflow.agent_chat import AgentChatOrchestrator

    # First 3 fixture frames: agent_thought (tool=web_search, obs=null) →
    # 2 yields (agent_thought + tool_call calling); same for frame 2 with
    # obs filled → 2 yields (agent_thought + tool_call completed); frame
    # 3 is "message" → accumulated, no yield. Total = 4 normal yields.
    head = _load_fixture_frames()[:3]
    stub = _StubDifyClient(
        head,
        raise_at=len(head),
        raise_exc=httpx.RequestError("upstream lost"),
    )
    orch = AgentChatOrchestrator()
    run_id, app_id, user_id, inputs = _make_run_args()
    events = [ev async for ev in orch.run(stub, run_id, app_id, user_id, inputs)]

    # 4 normal envelopes + 1 exception terminal = 5.
    assert len(events) == 5, (
        f"expected 4 normal (2 agent_thought + 2 tool_call) + 1 "
        f"exception terminal = 5; got {len(events)}: "
        f"{[e.event_type for e in events]}"
    )
    assert [e.event_type for e in events[:4]] == [
        "agent_thought",
        "tool_call",
        "agent_thought",
        "tool_call",
    ]
    terminal = events[-1]
    assert terminal.event_type == "workflow_finished"
    assert isinstance(terminal.data, WorkflowFinishedData)
    assert terminal.data.status == "exception"
    assert "RequestError" in terminal.data.error
    assert "upstream lost" in terminal.data.error
    # accumulated_text from frame 3 ("根据搜索") merged into outputs.
    assert terminal.data.outputs.get("answer") == "根据搜索"


# --------------------------------------------------------------------- #
# C5 — REWORK-INDEP-I-1: CancelledError must NOT yield a finally envelope
# (regression-lock for the cancelled sentinel; see test_advanced_chat C5).
# --------------------------------------------------------------------- #
async def test_c5_cancelled_error_does_not_yield_extra_envelope():
    import asyncio

    from ncmu_backend.workflow.agent_chat import AgentChatOrchestrator

    # First 2 fixture frames: 2 agent_thought (each → agent_thought +
    # tool_call) = 4 normal envelopes; then CancelledError on 3rd iter.
    head = _load_fixture_frames()[:2]
    stub = _StubDifyClient(
        head,
        raise_at=len(head),
        raise_exc=asyncio.CancelledError(),
    )
    orch = AgentChatOrchestrator()
    run_id, app_id, user_id, inputs = _make_run_args()

    events = []
    with pytest.raises(asyncio.CancelledError):
        async for ev in orch.run(stub, run_id, app_id, user_id, inputs):
            events.append(ev)

    # Exactly 4 normal envelopes; no finally補发.
    assert len(events) == 4, (
        f"REWORK-INDEP-I-1: finally must NOT yield on cancellation path; "
        f"expected 4 normal envelopes; got {len(events)}: "
        f"{[e.event_type for e in events]}"
    )
    assert [e.event_type for e in events] == [
        "agent_thought",
        "tool_call",
        "agent_thought",
        "tool_call",
    ]
    # No exception/failed workflow_finished envelope leaked.
    for ev in events:
        assert ev.event_type != "workflow_finished"


# ===================================================================== #
# TASK-D timeout + units tests (B-NEW-41 + B-NEW-42 / plan §AC#5)
# ===================================================================== #


async def test_d_timeout_exception_emits_status_timeout():
    """B-NEW-41 — httpx.TimeoutException → workflow_finished(status='timeout').

    Explicit timeout branch (placed between asyncio.CancelledError and the
    generic Exception handler) translates upstream timeouts into a dedicated
    status='timeout' envelope; aligns with main.py boot-sweeper's
    finalize_run(status='timeout') for the late-finalize path.
    """
    import httpx

    from ncmu_backend.workflow.agent_chat import AgentChatOrchestrator

    stub = _StubDifyClient(
        [],
        raise_at=0,
        raise_exc=httpx.TimeoutException("read timeout"),
    )
    orch = AgentChatOrchestrator()
    run_id, app_id, user_id, inputs = _make_run_args()
    events = [ev async for ev in orch.run(stub, run_id, app_id, user_id, inputs)]

    assert len(events) == 1, (
        f"expected exactly 1 timeout terminal envelope; got {len(events)}: "
        f"{[e.event_type for e in events]}"
    )
    assert events[0].event_type == "workflow_finished"
    assert events[0].data.status == "timeout", (
        f"B-NEW-41: timeout branch must emit status='timeout' (not "
        f"'exception'); got {events[0].data.status!r}"
    )
    assert events[0].data.error != "", (
        f"timeout terminal must carry a non-empty error string; got "
        f"{events[0].data.error!r}"
    )
    assert "upstream timeout" in events[0].data.error, (
        f"timeout error string must literally contain 'upstream timeout'; "
        f"got {events[0].data.error!r}"
    )


async def test_d_elapsed_time_multiplied_by_1000():
    """B-NEW-42 — metadata.usage.latency (seconds) → total_elapsed_ms (ms) × 1000.

    Plan §AC#4 marks "(4 orchestrator)" uniformity; agent_chat previously
    had no total_elapsed_ms assignment. This task surfaces
    metadata.usage.latency on message_end (mirroring completion's pattern)
    and applies the × 1000 conversion. None remains None.
    """
    from ncmu_backend.schemas.sse_events import WorkflowFinishedData
    from ncmu_backend.workflow.agent_chat import AgentChatOrchestrator

    # Case 1: real seconds value → int(× 1000)
    frames = [
        {
            "event": "message",
            "answer": "hi",
        },
        {
            "event": "message_end",
            "metadata": {"usage": {"latency": 1.2286}},
        },
    ]
    stub = _StubDifyClient(frames)
    orch = AgentChatOrchestrator()
    run_id, app_id, user_id, inputs = _make_run_args()
    events = [ev async for ev in orch.run(stub, run_id, app_id, user_id, inputs)]
    final = events[-1]
    assert final.event_type == "workflow_finished"
    assert isinstance(final.data, WorkflowFinishedData)
    assert final.data.total_elapsed_ms == 1228, (
        f"B-NEW-42: latency 1.2286s × 1000 truncated to int = 1228; got "
        f"{final.data.total_elapsed_ms!r}"
    )

    # Case 2: latency missing → None (no implicit 0)
    frames_none = [
        {"event": "message", "answer": "hi"},
        {"event": "message_end"},
    ]
    stub_none = _StubDifyClient(frames_none)
    events_none = [
        ev async for ev in orch.run(stub_none, run_id, app_id, user_id, inputs)
    ]
    final_none = events_none[-1]
    assert isinstance(final_none.data, WorkflowFinishedData)
    assert final_none.data.total_elapsed_ms is None, (
        f"B-NEW-42: missing latency must yield None (not 0); got "
        f"{final_none.data.total_elapsed_ms!r}"
    )
