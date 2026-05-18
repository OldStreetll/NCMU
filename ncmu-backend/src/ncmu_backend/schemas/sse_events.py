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

from pydantic import BaseModel, Field, field_validator


class NodeStartedData(BaseModel):
    """REWORK-79-BACKEND-SCHEMA-FIX-2 (2026-05-12): ``inputs`` accepts the
    literal ``null`` Dify v1.13.3 ships when a node has no inputs. Dify
    declares ``inputs: Mapping[str, Any] | None = None`` in
    ``task_entities.py:346/395/459/575/657`` for the node_started family;
    NCMU schema keeps the attribute strictly ``dict[str, Any]`` so
    downstream code never has to do a ``None`` check, but a model-level
    ``before`` validator coerces inbound ``None`` to an empty dict. Pairs
    with the orchestrator-side ``data.get("inputs") or {}`` coerce in
    ``advanced_chat.py`` + ``workflow.py`` (double defense — schema first,
    boundary second; either alone would suffice but both keep the next
    same-shape bug from leaking).
    """

    node_id: str
    node_type: str
    title: str | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)

    @field_validator("inputs", mode="before")
    @classmethod
    def _coerce_null_inputs(cls, v):
        """Dify wire: node_started.data.inputs may be literal ``null``.
        Pydantic strict ``dict[str, Any]`` would reject; coerce to ``{}``."""
        return {} if v is None else v


class NodeFinishedData(BaseModel):
    """Per H2 mapping above, status is collapsed to 4 terminal values.

    REWORK-79-BACKEND-SCHEMA (2026-05-12): ``elapsed_ms`` is ``float | None``
    not ``int | None``. Dify v1.13.3 ``task_entities.py`` declares
    ``elapsed_time: float`` (line 237/267/403/...); orchestrators forward
    the raw value via ``elapsed_ms=data.get("elapsed_time")`` so Pydantic
    v2's strict ``int_from_float`` validator was rejecting every real
    Dify frame. Name retains ``_ms`` for SPA back-compat — the unit
    semantics (Dify ships seconds; NCMU stores it under an ms-suffixed
    field) is a separate latent bug tracked outside this REWORK.

    REWORK-79-BACKEND-SCHEMA-FIX-3 (2026-05-12): ``outputs`` accepts
    literal ``null`` — Dify v1.13.3 declares
    ``outputs: Mapping[str, Any] | None = None`` for the node_finished
    family (``task_entities.py:235/399/463/571/653/810``). Same defense
    pattern as ``NodeStartedData.inputs``: ``before`` validator coerces
    ``None`` → ``{}`` while keeping the attribute type strict so
    downstream code never has to do a ``None`` check.
    """

    node_id: str
    node_type: str
    status: Literal["succeeded", "failed", "stopped", "exception"]
    outputs: dict[str, Any] = Field(default_factory=dict)
    elapsed_ms: float | None = None
    error: str | None = None

    @field_validator("outputs", mode="before")
    @classmethod
    def _coerce_null_outputs(cls, v):
        """Dify wire: node_finished.data.outputs may be literal ``null``."""
        return {} if v is None else v


class WorkflowFinishedData(BaseModel):
    """Per H2 mapping above, partial-succeeded → succeeded.

    REWORK-79-BACKEND-SCHEMA (2026-05-12): ``total_elapsed_ms`` is
    ``float | None`` not ``int | None`` — Dify v1.13.3 wire emits float
    (e.g. ``1.2286831319797784``). Same root cause as ``NodeFinishedData
    .elapsed_ms``; CompletionOrchestrator additionally pipes
    ``metadata.usage.latency`` here, which ``llm_entities.LLMUsage``
    declares ``latency: float``.

    REWORK-79-BACKEND-SCHEMA-FIX-3 (2026-05-12): ``outputs`` accepts
    literal ``null`` — same shape as ``NodeFinishedData.outputs``
    (Dify ``task_entities.py:810`` ``WorkflowFinishStreamResponse``).
    """

    status: Literal["succeeded", "failed", "stopped", "exception", "timeout"]
    outputs: dict[str, Any] = Field(default_factory=dict)
    total_elapsed_ms: float | None = None
    error: str | None = None

    @field_validator("outputs", mode="before")
    @classmethod
    def _coerce_null_outputs(cls, v):
        """Dify wire: workflow_finished.data.outputs may be literal ``null``."""
        return {} if v is None else v


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
