"""advanced-chat (Chatflow) orchestrator — Phase 2B TASK-71.

Endpoint: Dify v1.13.3 ``POST /v1/chat-messages`` (same as the chat hot
path; the difference is purely the upstream app's ``mode`` flag, which
makes Dify emit chatflow node events on top of the message stream).

Two design points worth keeping in mind when reading this file:

H2 (PLAN-FIX-2): every Dify chatflow event nests its payload under
``data.{...}`` — only ``event / task_id / workflow_run_id`` live on the
top-level frame. We mirror Dify's ``api/core/app/entities/task_entities.py``
NodeStart/NodeFinish/WorkflowFinish stream-response Pydantic shapes
(field-for-field). Calling ``raw.get("node_id")`` would silently
return ``None`` for every Dify v1.13.x build — the H2 patch routes us
through ``data = raw.get("data", {})`` first and then keys off that.

F-NEW-1 (PLAN-FIX-3): chat-messages also pushes ``message`` events
carrying partial ``answer`` deltas plus a terminal ``message_end``.
The chat orchestrator (Phase 1) already streams those through to the
SPA verbatim, so re-emitting them here would double-render the answer
in the workflow tab. We instead accumulate the deltas into a local
buffer and merge the joined answer into ``WorkflowFinishedData.outputs``
at the terminal frame — frontend then has both the node trace and the
final answer in a single completion event without subscribing to two
schemas. The merge uses ``setdefault`` so a Dify-supplied
``outputs.answer`` (rare but possible for templated chatflows) always
wins over our accumulated copy.
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
from ncmu_backend.workflow.dify_client import DifyStreamClient


class AdvancedChatOrchestrator(BaseOrchestrator):
    """Translate Dify chat-messages SSE → NCMU SSE for advanced-chat mode."""

    mode = "advanced-chat"

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
            "query": inputs.get("query", ""),
            "user": str(user_id),
            "response_mode": "streaming",
            "conversation_id": inputs.get("conversation_id", ""),
        }
        accumulated_answer = ""  # F-NEW-1: see module docstring
        async for raw in dify_client.stream("/v1/chat-messages", body):
            event_name = raw.get("event")
            now = datetime.now(timezone.utc)
            data = raw.get("data", {})  # H2: every Dify field is data-nested
            if event_name == "node_started":
                yield NcmuSseEvent(
                    event_type="node_started",
                    run_id=run_id,
                    timestamp=now,
                    data=NodeStartedData(
                        node_id=data.get("node_id", ""),
                        node_type=data.get("node_type", ""),
                        title=data.get("title"),
                        # REWORK-79-BACKEND-SCHEMA-FIX-2: Dify v1.13.3 emits
                        # ``inputs: null`` when a node has no inputs (per
                        # task_entities.py:346 NodeStartedStreamResponse).
                        # ``.get(...,{})`` only catches MISSING key; for
                        # an explicit-null value it still returns ``None``.
                        # ``or {}`` defends at the boundary; schema's
                        # field_validator(mode="before") is the redundant
                        # safety net.
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
                        # REWORK-79-BACKEND-SCHEMA-FIX-3: Dify v1.13.3 emits
                        # ``outputs: null`` for nodes that produce no output
                        # (task_entities.py:399 NodeFinishStreamResponse).
                        # ``.get(..., {})`` only catches MISSING key; explicit
                        # null still returns ``None`` → schema would reject.
                        outputs=data.get("outputs") or {},
                        elapsed_ms=data.get("elapsed_time"),
                        error=data.get("error"),
                    ),
                )
            elif event_name == "message":
                # F-NEW-1: ``answer`` lives on the top-level frame for
                # chat-messages, NOT under data.{...}.
                accumulated_answer += raw.get("answer", "")
                continue
            elif event_name == "message_end":
                # F-NEW-1: terminal of the message stream; the workflow_finished
                # event (which follows) is where we actually emit the merged answer.
                continue
            elif event_name == "workflow_finished":
                merged_outputs = dict(data.get("outputs") or {})
                if accumulated_answer:
                    merged_outputs.setdefault("answer", accumulated_answer)
                yield NcmuSseEvent(
                    event_type="workflow_finished",
                    run_id=run_id,
                    timestamp=now,
                    data=WorkflowFinishedData(
                        status=data.get("status", "succeeded"),
                        outputs=merged_outputs,
                        total_elapsed_ms=data.get("elapsed_time"),
                        error=data.get("error"),
                    ),
                )
            # Unknown Dify event_name: silently skip — keeps NCMU resilient
            # to v1.13.x → v1.14.x upstream additions without a fix release.
