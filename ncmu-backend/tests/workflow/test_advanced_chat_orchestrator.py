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
    from ncmu_backend.workflow.advanced_chat import AdvancedChatOrchestrator
    from ncmu_backend.workflow.dify_client import DifyStreamClient

    dsc = DifyStreamClient(base_url="http://dify-api:5001", api_key="k")
    return AdvancedChatOrchestrator(dsc)


def _run_inputs():
    return (
        uuid.uuid4(),
        "app-advanced-chat",
        uuid.uuid4(),
        {"query": "hi", "inputs": {}, "conversation_id": ""},
    )


# --------------------------------------------------------------------- #
# (a) AC#3(a) — yields exactly 5 NCMU events
# --------------------------------------------------------------------- #
async def test_yields_five_events_from_seven_row_fixture():
    _FakeAsyncClient.LINES = _load_fixture_lines()
    orch = _make_orchestrator()
    run_id, app_id, user_id, inputs = _run_inputs()
    events = [ev async for ev in orch.run(run_id, app_id, user_id, inputs)]
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
    types = [ev.event_type async for ev in orch.run(run_id, app_id, user_id, inputs)]
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
    events = [ev async for ev in orch.run(run_id, app_id, user_id, inputs)]
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
    events = [ev async for ev in orch.run(run_id, app_id, user_id, inputs)]
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
    events = [ev async for ev in orch.run(run_id, app_id, user_id, inputs)]
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
    events = [ev async for ev in orch.run(run_id, app_id, user_id, inputs)]
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
    dispatcher.register("advanced-chat", AdvancedChatOrchestrator(dsc))

    run_id, app_id, user_id, inputs = _run_inputs()
    events = [
        ev async for ev in dispatcher.dispatch(
            "advanced-chat", run_id, app_id, user_id, inputs
        )
    ]
    assert len(events) >= 5, (
        f"AC#5: dispatcher → orchestrator must yield ≥5 NCMU events; "
        f"got {len(events)}"
    )
    # Sanity: the run_id we passed in is what the orchestrator stamps.
    assert all(ev.run_id == run_id for ev in events)
