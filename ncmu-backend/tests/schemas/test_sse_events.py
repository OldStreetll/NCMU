"""TASK-68 AC#2 — NCMU SSE event Pydantic schema tests (≥5 cases).

Cases:
  (a) NodeStartedData round-trips dump → load 字面一致
  (b) NodeFinishedData status Literal rejects枚举外值（'pending' 必拒）
  (c) WorkflowFinishedData outputs defaults to empty dict
  (d) AgentThoughtData / ToolCallData fields are full
  (e) NcmuSseEvent envelope event_type Literal rejects unknown ('unknown' 必拒)
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError


# --------------------------------------------------------------------- #
# (a) NodeStartedData dump → load round-trip
# --------------------------------------------------------------------- #
def test_node_started_data_round_trip():
    from ncmu_backend.schemas.sse_events import NodeStartedData

    src = NodeStartedData(
        node_id="n1",
        node_type="llm",
        title="LLM call",
        inputs={"q": "hi"},
    )
    dumped = src.model_dump()
    rebuilt = NodeStartedData.model_validate(dumped)
    assert rebuilt == src


# --------------------------------------------------------------------- #
# (b) NodeFinishedData status Literal — 'pending' 不在 4 终态枚举内
# --------------------------------------------------------------------- #
def test_node_finished_data_status_literal_rejects_pending():
    from ncmu_backend.schemas.sse_events import NodeFinishedData

    NodeFinishedData(node_id="n1", node_type="llm", status="succeeded")
    with pytest.raises(ValidationError):
        NodeFinishedData(node_id="n1", node_type="llm", status="pending")


# --------------------------------------------------------------------- #
# (c) WorkflowFinishedData outputs default — 空 dict
# --------------------------------------------------------------------- #
def test_workflow_finished_outputs_default_empty_dict():
    from ncmu_backend.schemas.sse_events import WorkflowFinishedData

    ev = WorkflowFinishedData(status="succeeded")
    assert ev.outputs == {}
    assert ev.total_elapsed_ms is None
    assert ev.error is None


# --------------------------------------------------------------------- #
# (d) AgentThoughtData / ToolCallData 字段完整
# --------------------------------------------------------------------- #
def test_agent_thought_and_tool_call_data_fields():
    from ncmu_backend.schemas.sse_events import AgentThoughtData, ToolCallData

    thought = AgentThoughtData(
        thought="reasoning here",
        observation="search returned 3",
        tool_name="web_search",
    )
    assert thought.thought == "reasoning here"
    assert thought.observation == "search returned 3"
    assert thought.tool_name == "web_search"

    tool = ToolCallData(
        tool_name="calc",
        tool_input={"a": 1, "b": 2},
        tool_output=3,
        status="completed",
    )
    assert tool.tool_name == "calc"
    assert tool.tool_input == {"a": 1, "b": 2}
    assert tool.tool_output == 3
    assert tool.status == "completed"

    # tool status Literal also enforced
    with pytest.raises(ValidationError):
        ToolCallData(
            tool_name="x",
            tool_input={},
            status="unknown",  # not in {calling, completed, failed}
        )


# --------------------------------------------------------------------- #
# (e) NcmuSseEvent envelope event_type Literal — 'unknown' 必拒
# --------------------------------------------------------------------- #
def test_ncmu_sse_event_envelope_event_type_literal_rejects_unknown():
    from ncmu_backend.schemas.sse_events import (
        NcmuSseEvent,
        NodeStartedData,
    )

    # accepted: valid event_type + matching schema body
    NcmuSseEvent(
        event_type="node_started",
        run_id=uuid.uuid4(),
        timestamp=datetime.now(timezone.utc),
        data=NodeStartedData(node_id="n1", node_type="llm"),
    )

    # rejected: 'unknown' not in 7-item Literal
    with pytest.raises(ValidationError):
        NcmuSseEvent(
            event_type="unknown",
            run_id=uuid.uuid4(),
            timestamp=datetime.now(timezone.utc),
            data={},
        )

    # ping / error use dict body — verify those event_type values land
    NcmuSseEvent(
        event_type="ping",
        run_id=uuid.uuid4(),
        timestamp=datetime.now(timezone.utc),
        data={"message": "alive"},
    )
    NcmuSseEvent(
        event_type="error",
        run_id=uuid.uuid4(),
        timestamp=datetime.now(timezone.utc),
        data={"code": 9001, "message": "upstream failed"},
    )
