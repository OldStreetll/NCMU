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


async def _run_orchestrator() -> list[NcmuSseEvent]:
    """Drive AgentChatOrchestrator.run() against the fixture lines."""
    from ncmu_backend.workflow.agent_chat import AgentChatOrchestrator
    from ncmu_backend.workflow.dify_client import DifyStreamClient

    _FakeAsyncClient.LINES = _fixture_as_sse_lines()
    orch = AgentChatOrchestrator(
        DifyStreamClient(base_url="http://dify-api:5001", api_key="k")
    )
    events: list[NcmuSseEvent] = []
    async for evt in orch.run(
        run_id=uuid.uuid4(),
        app_id="app-agent-test-1",
        user_id=uuid.uuid4(),
        inputs={"query": "search NCMU", "conversation_id": ""},
    ):
        events.append(evt)
    return events


@pytest.fixture
async def events() -> list[NcmuSseEvent]:
    return await _run_orchestrator()


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
    dispatcher.register("agent-chat", AgentChatOrchestrator(dify))

    _FakeAsyncClient.LINES = _fixture_as_sse_lines()
    run_id = uuid.uuid4()
    user_id = uuid.uuid4()
    types: list[str] = []
    async for evt in dispatcher.dispatch(
        "agent-chat", run_id, "app-x", user_id, {"query": "q", "conversation_id": ""}
    ):
        types.append(evt.event_type)

    # Routed to AgentChatOrchestrator → same envelope sequence as (a).
    assert types.count("agent_thought") == 2
    assert types.count("tool_call") == 2
    assert types.count("workflow_finished") == 1
