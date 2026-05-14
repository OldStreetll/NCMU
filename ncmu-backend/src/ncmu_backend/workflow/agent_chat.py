"""TASK-74 (Phase 2B B2): agent-chat orchestrator.

Wraps Dify's ``/v1/chat-messages`` SSE stream when the upstream App is
configured in *agent-chat* mode (mode == "agent-chat"). The endpoint
itself is shared with the chat / advanced-chat modes — the difference
is the event taxonomy: agent-chat additionally emits ``agent_thought``
frames carrying ``thought`` / ``observation`` / ``tool`` / ``tool_input``
fields.

NCMU splits each Dify ``agent_thought`` frame into two NCMU envelopes
(plan §B2): an ``agent_thought`` event for the LLM's reasoning step and,
when a tool was invoked, a separate ``tool_call`` event. The SPA is
already rendering ``tool_call`` for advanced-chat; reusing the same
envelope keeps the client-side renderer single-purpose.

★L2 (plan line 2197-2199): ToolCall.status is decided with
``observation is not None`` — an empty string observation still means
the tool returned (status="completed"), only ``null`` means the call is
still in flight ("calling").

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
    AgentThoughtData,
    NcmuSseEvent,
    ToolCallData,
    WorkflowFinishedData,
)
from ncmu_backend.workflow._base import BaseOrchestrator
from ncmu_backend.workflow.dify_client import DifyStreamClient


class AgentChatOrchestrator(BaseOrchestrator):
    """agent-chat (Dify Agent App with tool-calls) orchestrator."""

    mode = "agent-chat"

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
        accumulated_text = ""
        emitted_terminal = False  # TASK-B: dedup sentinel — at most 1 terminal envelope per run
        cancelled = False  # REWORK-INDEP-I-1: gate finally yield on cancellation path
        try:
            async for raw in dify_client.stream("/v1/chat-messages", body):
                event_name = raw.get("event")
                now = datetime.now(timezone.utc)
                if event_name == "agent_thought":
                    yield NcmuSseEvent(
                        event_type="agent_thought",
                        run_id=run_id,
                        timestamp=now,
                        data=AgentThoughtData(
                            thought=raw.get("thought", ""),
                            observation=raw.get("observation"),
                            tool_name=raw.get("tool"),
                        ),
                    )
                    tool = raw.get("tool")
                    if tool:
                        raw_input = raw.get("tool_input", {})
                        tool_input = (
                            raw_input
                            if isinstance(raw_input, dict)
                            else {"raw": str(raw_input)}
                        )
                        yield NcmuSseEvent(
                            event_type="tool_call",
                            run_id=run_id,
                            timestamp=now,
                            data=ToolCallData(
                                tool_name=tool,
                                tool_input=tool_input,
                                tool_output=raw.get("observation"),
                                status=(
                                    "completed"
                                    if raw.get("observation") is not None
                                    else "calling"
                                ),
                            ),
                        )
                elif event_name == "message":
                    accumulated_text += raw.get("answer", "")
                    continue
                elif event_name == "error":
                    # TASK-B: Dify v1.13.3 event:error frame — top-level fields
                    # (NOT data-nested); see dify-sse-error-frame-contract §1.1.
                    # Placed BEFORE the message_end terminal branch (plan §Phase 2).
                    code = raw.get("code", "")
                    msg = raw.get("message", "")
                    yield NcmuSseEvent(
                        event_type="workflow_finished",
                        run_id=run_id,
                        timestamp=now,
                        data=WorkflowFinishedData(
                            status="failed",
                            outputs={"answer": accumulated_text} if accumulated_text else {},
                            error=f"{code}: {msg}" if code else msg,
                        ),
                    )
                    emitted_terminal = True
                    return
                elif event_name == "message_end":
                    yield NcmuSseEvent(
                        event_type="workflow_finished",
                        run_id=run_id,
                        timestamp=now,
                        data=WorkflowFinishedData(
                            status="succeeded",
                            outputs={"answer": accumulated_text},
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
            if not emitted_terminal:
                yield NcmuSseEvent(
                    event_type="workflow_finished",
                    run_id=run_id,
                    timestamp=datetime.now(timezone.utc),
                    data=WorkflowFinishedData(
                        status="exception",
                        outputs={"answer": accumulated_text} if accumulated_text else {},
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
                        outputs={"answer": accumulated_text} if accumulated_text else {},
                        error="Upstream stream closed without terminal event",
                    ),
                )
                emitted_terminal = True
