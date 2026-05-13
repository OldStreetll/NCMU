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


# =====================================================================
# REWORK-79-BACKEND-SCHEMA — Dify v1.13.3 真 wire 是 float, schema 不抛
# (Bug #1 + #3 类型错位 / mock-vs-real 第 9 次实证).
# =====================================================================

# --------------------------------------------------------------------- #
# (f) WorkflowFinishedData.total_elapsed_ms accepts a real Dify float
#     value (message_end.metadata.usage.latency / workflow_finished.
#     elapsed_time live on the wire as float per
#     dify_graph/model_runtime/entities/llm_entities.py:40,61 +
#     api/core/app/entities/task_entities.py:237,267,403,...).
# --------------------------------------------------------------------- #
def test_workflow_finished_total_elapsed_ms_accepts_float():
    from ncmu_backend.schemas.sse_events import WorkflowFinishedData

    # The exact value observed in the Pane 1 BLOCKED-3 capture
    # (Dify completion-messages message_end.metadata.usage.latency).
    ev = WorkflowFinishedData(
        status="succeeded",
        total_elapsed_ms=1.2286831319797784,
    )
    assert isinstance(ev.total_elapsed_ms, float)
    assert ev.total_elapsed_ms == 1.2286831319797784

    # int input still validates (Pydantic non-strict coerces int → float);
    # protects existing fixtures (test_advanced_chat / completion / workflow)
    # that use round numbers like 1500 / 2400 / 1600.
    ev_int = WorkflowFinishedData(status="succeeded", total_elapsed_ms=2400)
    assert ev_int.total_elapsed_ms == 2400.0

    # None still allowed (sentinel for "Dify didn't ship a latency this round").
    ev_none = WorkflowFinishedData(status="succeeded")
    assert ev_none.total_elapsed_ms is None


# --------------------------------------------------------------------- #
# (g) NodeFinishedData.elapsed_ms accepts a real Dify float (Dify
#     v1.13.3 task_entities.NodeFinishStreamResponse.elapsed_time:
#     float; advanced_chat / workflow orchestrators pipe it verbatim).
# --------------------------------------------------------------------- #
def test_node_finished_elapsed_ms_accepts_float():
    from ncmu_backend.schemas.sse_events import NodeFinishedData

    ev = NodeFinishedData(
        node_id="llm_1",
        node_type="llm",
        status="succeeded",
        elapsed_ms=0.4321,
    )
    assert isinstance(ev.elapsed_ms, float)
    assert ev.elapsed_ms == 0.4321

    # int still validates (mock fixtures backward compat).
    ev_int = NodeFinishedData(
        node_id="http_1", node_type="http", status="succeeded", elapsed_ms=1500,
    )
    assert ev_int.elapsed_ms == 1500.0


# =====================================================================
# REWORK-79-BACKEND-SCHEMA-FIX-2 — NodeStartedData.inputs accepts the
# null Dify v1.13.3 emits when a node has no declared inputs
# (mock-vs-real 第 8.5 次同型 / 防御链 schema layer).
# =====================================================================

# --------------------------------------------------------------------- #
# (h) NodeStartedData(inputs=None) coerces to {} (the literal Dify wire
#     shape — task_entities.py:346 declares
#     ``inputs: Mapping[str, Any] | None = None`` for the node_started
#     family). Without the field_validator, Pydantic strict ``dict`` would
#     reject the None and raise ValidationError.
# --------------------------------------------------------------------- #
def test_node_started_data_inputs_none_coerces_to_empty_dict():
    from ncmu_backend.schemas.sse_events import NodeStartedData

    ev = NodeStartedData(
        node_id="start_1",
        node_type="start",
        title="开始",
        inputs=None,  # ← Dify's wire literal for "no inputs"
    )
    assert ev.inputs == {}
    assert isinstance(ev.inputs, dict)

    # default (omitted) still produces {} (sanity — Field(default_factory=dict)
    # path unchanged).
    ev_default = NodeStartedData(node_id="n1", node_type="llm")
    assert ev_default.inputs == {}


# --------------------------------------------------------------------- #
# (i) NcmuSseEvent envelope with a node_started body carrying
#     ``inputs: null`` does NOT raise ValidationError — mimics the exact
#     orchestrator path (Dify SSE frame → NodeStartedData → NcmuSseEvent).
# --------------------------------------------------------------------- #
def test_ncmu_sse_event_with_node_started_null_inputs():
    from ncmu_backend.schemas.sse_events import NcmuSseEvent, NodeStartedData

    # The shape Dify emits + the way the orchestrator wraps it.
    ev = NcmuSseEvent(
        event_type="node_started",
        run_id=uuid.uuid4(),
        timestamp=datetime.now(timezone.utc),
        data=NodeStartedData(
            node_id="llm_1",
            node_type="llm",
            title="LLM 推理",
            inputs=None,  # Dify v1.13.3 真 wire literal
        ),
    )
    assert isinstance(ev.data, NodeStartedData)
    assert ev.data.inputs == {}


# =====================================================================
# REWORK-79-BACKEND-SCHEMA-FIX-3 — NodeFinishedData.outputs +
# WorkflowFinishedData.outputs accept the null Dify v1.13.3 emits when a
# node / workflow produces no output (same shape as inputs=null, same
# defense pattern; preemptive vs Pane 1 第 6 轮 BLOCKED).
# =====================================================================

# --------------------------------------------------------------------- #
# (j) NodeFinishedData(outputs=None) coerces to {} (Dify wire literal
#     per task_entities.py:235/399/463/571/653/810 —
#     ``outputs: Mapping[str, Any] | None = None``).
# --------------------------------------------------------------------- #
def test_node_finished_data_outputs_none_coerces_to_empty_dict():
    from ncmu_backend.schemas.sse_events import NodeFinishedData

    ev = NodeFinishedData(
        node_id="start_1",
        node_type="start",
        status="succeeded",
        outputs=None,  # ← Dify's wire literal for "node produced no output"
    )
    assert ev.outputs == {}
    assert isinstance(ev.outputs, dict)

    # default (omitted) still produces {} (sanity — Field(default_factory=dict)
    # path unchanged after the validator).
    ev_default = NodeFinishedData(node_id="n1", node_type="llm", status="succeeded")
    assert ev_default.outputs == {}


# --------------------------------------------------------------------- #
# (k) WorkflowFinishedData(outputs=None) coerces to {} (Dify wire literal
#     per task_entities.py:810 WorkflowFinishStreamResponse).
# --------------------------------------------------------------------- #
def test_workflow_finished_data_outputs_none_coerces_to_empty_dict():
    from ncmu_backend.schemas.sse_events import WorkflowFinishedData

    ev = WorkflowFinishedData(
        status="succeeded",
        outputs=None,
    )
    assert ev.outputs == {}
    assert isinstance(ev.outputs, dict)

    # default (omitted) still produces {}.
    ev_default = WorkflowFinishedData(status="failed")
    assert ev_default.outputs == {}


# --------------------------------------------------------------------- #
# (l) NcmuSseEvent envelope with a node_finished body carrying
#     ``outputs: null`` does NOT raise ValidationError — full path test.
# --------------------------------------------------------------------- #
def test_ncmu_sse_event_with_node_finished_null_outputs():
    from ncmu_backend.schemas.sse_events import NcmuSseEvent, NodeFinishedData

    ev = NcmuSseEvent(
        event_type="node_finished",
        run_id=uuid.uuid4(),
        timestamp=datetime.now(timezone.utc),
        data=NodeFinishedData(
            node_id="end_1",
            node_type="end",
            status="succeeded",
            outputs=None,  # Dify v1.13.3 真 wire literal for terminal nodes
            elapsed_ms=0.123,
        ),
    )
    assert isinstance(ev.data, NodeFinishedData)
    assert ev.data.outputs == {}
    assert ev.data.elapsed_ms == 0.123
