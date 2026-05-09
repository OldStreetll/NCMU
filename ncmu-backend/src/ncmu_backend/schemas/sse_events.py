"""NCMU 统一 SSE 事件 Pydantic schema (Phase 2B TASK-68).

Why a unified envelope rather than per-mode shapes:
- The SPA registers ONE event-stream consumer per run; if every mode
  emitted its own raw Dify schema the client would need 4 parallel
  parsers + 4 disjoint TypeScript unions. The envelope flattens this:
  `(event_type, run_id, timestamp, data)` is the only contract the SPA
  has to know.
- `event_type` is a Literal of 7 values so unknown upstream events are
  rejected at schema-validation time; the orchestrator is responsible
  for translating Dify's wider taxonomy onto this fixed set (see H2
  status mappings below).

Why split the data union per event:
- `NodeStartedData` / `NodeFinishedData` are the bread-and-butter of
  workflow / advanced-chat; field-level Pydantic validation catches
  schema drift between Dify v1.13.x revisions early (a renamed key in
  Dify becomes a test failure here, not a runtime KeyError downstream).
- `AgentThoughtData` / `ToolCallData` are agent-chat specific.
- `ping` and `error` use a permissive `dict[str, Any]` body — they're
  control-plane events whose payload shape is volatile and not worth a
  dedicated schema yet.

H2 status mapping (PLAN-FIX-2):
- Dify v1.13.3 WorkflowNodeExecutionStatus values include
  pending/running/succeeded/failed/exception/stopped/paused; NCMU
  collapses to 4 terminal states (succeeded / failed / stopped /
  exception). Orchestrators map paused/scheduled → succeeded so the
  SPA never has to render a transient state as a final outcome.
- WorkflowExecutionStatus likewise: scheduled/running/succeeded/failed/
  stopped/partial-succeeded/paused → 4 terminal values, with
  `partial-succeeded → succeeded`.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal, Union

from pydantic import BaseModel, Field


class NodeStartedData(BaseModel):
    node_id: str
    node_type: str
    title: str | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)


class NodeFinishedData(BaseModel):
    """Per H2 mapping above, status is collapsed to 4 terminal values."""

    node_id: str
    node_type: str
    status: Literal["succeeded", "failed", "stopped", "exception"]
    outputs: dict[str, Any] = Field(default_factory=dict)
    elapsed_ms: int | None = None
    error: str | None = None


class WorkflowFinishedData(BaseModel):
    """Per H2 mapping above, partial-succeeded → succeeded."""

    status: Literal["succeeded", "failed", "stopped", "exception"]
    outputs: dict[str, Any] = Field(default_factory=dict)
    total_elapsed_ms: int | None = None
    error: str | None = None


class AgentThoughtData(BaseModel):
    thought: str
    observation: str | None = None
    tool_name: str | None = None


class ToolCallData(BaseModel):
    tool_name: str
    tool_input: dict[str, Any]
    tool_output: Any | None = None
    status: Literal["calling", "completed", "failed"]


class NcmuSseEvent(BaseModel):
    """The envelope every NCMU workflow SSE frame ships in."""

    event_type: Literal[
        "node_started",
        "node_finished",
        "workflow_finished",
        "agent_thought",
        "tool_call",
        "ping",
        "error",
    ]
    run_id: uuid.UUID
    timestamp: datetime
    data: Union[
        NodeStartedData,
        NodeFinishedData,
        WorkflowFinishedData,
        AgentThoughtData,
        ToolCallData,
        dict[str, Any],
    ]
