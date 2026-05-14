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

TASK-B 错误防御（B-NEW-40 + B-NEW-46）:
- try/except Exception (排除 CancelledError) 包裹整个 stream 循环
- finally 兜底：若未发终结 envelope 则补发 workflow_finished(exception)
- Dify event:error 帧 → yield workflow_finished(failed) 后 break
- 同一 run 最多 1 个终结 envelope（emitted_terminal sentinel）
"""
from __future__ import annotations

import asyncio
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
from ncmu_backend.workflow.dify_client import DifyStreamClient


class WorkflowOrchestrator(BaseOrchestrator):
    """Translate Dify `/v1/workflows/run` SSE frames → NCMU envelopes."""

    mode = "workflow"

    async def run(
        self,
        dify_client: DifyStreamClient,
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
        emitted_terminal = False  # TASK-B: dedup sentinel — at most 1 terminal envelope per run
        cancelled = False  # REWORK-INDEP-I-1: gate finally yield on cancellation path
        try:
            async for raw in dify_client.stream("/v1/workflows/run", body):
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
                            # REWORK-79-BACKEND-SCHEMA-FIX-2: see
                            # advanced_chat.py for the inputs=null background;
                            # Dify ``/v1/workflows/run`` emits the same shape.
                            inputs=data.get("inputs") or {},
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
                            # REWORK-79-BACKEND-SCHEMA-FIX-3: see advanced_chat.py
                            # for the outputs=null background; Dify
                            # ``/v1/workflows/run`` emits the same shape.
                            outputs=data.get("outputs") or {},
                            elapsed_ms=data.get("elapsed_time"),
                            error=data.get("error"),
                        ),
                    )
                elif event_name == "error":
                    # TASK-B: Dify v1.13.3 event:error frame — top-level fields
                    # (NOT data-nested); see dify-sse-error-frame-contract §1.1.
                    # Placed BEFORE the workflow_finished branch (plan §Phase 4).
                    # Workflow mode has no text accumulator → outputs is always {}.
                    code = raw.get("code", "")
                    msg = raw.get("message", "")
                    yield NcmuSseEvent(
                        event_type="workflow_finished",
                        run_id=run_id,
                        timestamp=now,
                        data=WorkflowFinishedData(
                            status="failed",
                            outputs={},
                            error=f"{code}: {msg}" if code else msg,
                        ),
                    )
                    emitted_terminal = True
                    return
                elif event_name == "workflow_finished":
                    yield NcmuSseEvent(
                        event_type="workflow_finished",
                        run_id=run_id,
                        timestamp=now,
                        data=WorkflowFinishedData(
                            status=data.get("status", "succeeded"),
                            # REWORK-79-BACKEND-SCHEMA-FIX-3: see advanced_chat.py
                            # for the outputs=null background; Dify
                            # ``/v1/workflows/run`` emits the same shape.
                            outputs=data.get("outputs") or {},
                            total_elapsed_ms=data.get("elapsed_time"),
                            error=data.get("error"),
                        ),
                    )
                    emitted_terminal = True
        except asyncio.CancelledError:
            # TASK-B: client disconnect / SSE cancel → don't yield (pipe is closed).
            # REWORK-INDEP-I-1: set sentinel so finally skips the catch-all yield;
            # see advanced_chat.py for the full rationale.
            cancelled = True
            raise
        except Exception as exc:
            # TASK-B: any Python runtime exception → emit terminal envelope.
            # Workflow mode has no text accumulator → outputs is always {}.
            if not emitted_terminal:
                yield NcmuSseEvent(
                    event_type="workflow_finished",
                    run_id=run_id,
                    timestamp=datetime.now(timezone.utc),
                    data=WorkflowFinishedData(
                        status="exception",
                        outputs={},
                        error=f"{type(exc).__name__}: {exc}",
                    ),
                )
                emitted_terminal = True
        finally:
            # TASK-B: catch-all — upstream stream closed without a terminal event.
            # REWORK-INDEP-I-1: ``not cancelled`` guard — see except clause.
            if not emitted_terminal and not cancelled:
                yield NcmuSseEvent(
                    event_type="workflow_finished",
                    run_id=run_id,
                    timestamp=datetime.now(timezone.utc),
                    data=WorkflowFinishedData(
                        status="exception",
                        outputs={},
                        error="Upstream stream closed without terminal event",
                    ),
                )
                emitted_terminal = True
