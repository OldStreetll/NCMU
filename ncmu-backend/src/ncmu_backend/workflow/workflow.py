"""Workflow (流程编排) orchestrator — Phase 2B B2 TASK-73.

Why a dedicated subclass per Dify mode (instead of one parameterised class):
- Each Dify mode emits its own event taxonomy. `workflow` is pure-DAG
  with no conversation: only `node_started` / `node_finished` /
  `workflow_finished`; no `message` / `message_end`. Encoding the
  mapping in code (vs config) makes Pydantic validation surface field
  drift between Dify v1.13.x revisions immediately at the orchestrator
  layer instead of leaking malformed envelopes downstream.
- Endpoint differs from chat-messages: `/v1/workflows/run` (chat-mode
  uses `/v1/chat-messages`). The base class deliberately doesn't carry
  a default endpoint — every subclass declares the path explicitly so a
  copy-paste typo can't silently hit the wrong upstream.

H2 status mapping (PLAN-FIX-2): Dify can emit non-terminal status values
(`paused` / `scheduled`); NCMU's NodeFinishedData / WorkflowFinishedData
collapse to 4 terminal Literals. The fixture exercises only `succeeded`;
mapping of paused/scheduled → succeeded happens at the schema validation
boundary if future Dify revisions emit them (Pydantic raises and the
default `"succeeded"` here keeps the stream alive for the common case).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from ncmu_backend.schemas.sse_events import (
    NcmuSseEvent,
    NodeFinishedData,
    NodeStartedData,
    WorkflowFinishedData,
)
from ncmu_backend.workflow._base import BaseOrchestrator


class WorkflowOrchestrator(BaseOrchestrator):
    """Translate Dify `/v1/workflows/run` SSE frames → NCMU envelopes."""

    mode = "workflow"

    async def run(
        self,
        run_id: uuid.UUID,
        app_id: str,
        user_id: uuid.UUID,
        inputs: dict[str, Any],
    ) -> AsyncIterator[NcmuSseEvent]:
        body = {
            "inputs": inputs.get("inputs", {}),
            "user": str(user_id),
            "response_mode": "streaming",
        }
        async for raw in self._dify.stream("/v1/workflows/run", body):
            event_name = raw.get("event")
            now = datetime.now(timezone.utc)
            data = raw.get("data", {})

            if event_name == "node_started":
                yield NcmuSseEvent(
                    event_type="node_started",
                    run_id=run_id,
                    timestamp=now,
                    data=NodeStartedData(
                        node_id=data.get("node_id", ""),
                        node_type=data.get("node_type", ""),
                        title=data.get("title"),
                        inputs=data.get("inputs", {}),
                    ),
                )
            elif event_name == "node_finished":
                yield NcmuSseEvent(
                    event_type="node_finished",
                    run_id=run_id,
                    timestamp=now,
                    data=NodeFinishedData(
                        node_id=data.get("node_id", ""),
                        node_type=data.get("node_type", ""),
                        status=data.get("status", "succeeded"),
                        outputs=data.get("outputs", {}),
                        elapsed_ms=data.get("elapsed_time"),
                        error=data.get("error"),
                    ),
                )
            elif event_name == "workflow_finished":
                yield NcmuSseEvent(
                    event_type="workflow_finished",
                    run_id=run_id,
                    timestamp=now,
                    data=WorkflowFinishedData(
                        status=data.get("status", "succeeded"),
                        outputs=data.get("outputs", {}),
                        total_elapsed_ms=data.get("elapsed_time"),
                        error=data.get("error"),
                    ),
                )
