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

import httpx

from ncmu_backend.schemas.sse_events import (
    AgentThoughtData,
    NcmuSseEvent,
    ToolCallData,
    WorkflowFinishedData,
)
from ncmu_backend.workflow._base import BaseOrchestrator
from ncmu_backend.workflow.dify_client import DifyStreamClient


# B-NEW-43 (TASK-CLEAN-3): per-call mitigation delay for Dify Agent app's
# state-machine contention under concurrent batch invocations. Module-level
# constant so test suite can patch it to 0 without literal-value coupling.
_BATCH_MITIGATION_DELAY_S = 0.5


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
        # B-NEW-43 (INDEP-PHASE2B P2 / TASK-CLEAN-3): Dify Agent app's
        # /v1/chat-messages endpoint exhibits state-machine contention when
        # N>=3 concurrent calls share a user_id (observed in Phase 2B B4 e2e
        # batch mode). A fixed pre-stream sleep yields to the event loop so
        # concurrent invocations are naturally staggered. Per-mode user
        # suffix was considered but rejected: it would pollute Dify-side
        # analytics aggregation by raw user_id and break conversation_id
        # continuity for any rows historically created with the bare uuid.
        # Single-call overhead 500ms is acceptable here — agent-chat is a
        # multi-step tool-call mode where single response typically takes
        # 5-30s (1-10% increment, perceptually inert). Placed before the
        # try/except so CancelledError during the wait propagates naturally
        # without engaging the finally yield path (see feedback_async_gen_
        # finally_yield_double_bug).
        await asyncio.sleep(_BATCH_MITIGATION_DELAY_S)
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
                elif event_name in ("message", "agent_message"):
                    # BUG-3: Dify v1.13.3 agent-chat mode emits ``agent_message``
                    # (one char per chunk in ``answer``); chat/completion modes
                    # emit ``message``. Wire shape is identical — ``answer``
                    # carries chunk text — so they share the accumulator branch.
                    # Verified 2026-05-26 via live SSE capture against agent app
                    # 1f05dbfc-1d82-4591-bbae-40c25eaa3d8b (39 agent_message + 0
                    # message frames). Without this, agent-chat mode runs always
                    # land in message_end with accumulated_text == "" and SPA
                    # main bubble stays blank.
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
                    # TASK-D B-NEW-42: surface metadata.usage.latency × 1000.
                    # Plan §AC#4 marks "(4 orchestrator)" uniformity — agent_chat
                    # had no prior total_elapsed_ms assignment; Dify chat-messages
                    # emits LLMUsage.latency (float seconds) on message_end,
                    # mirroring completion.py's existing path. None stays None.
                    _latency = (
                        raw.get("metadata", {}).get("usage", {}).get("latency")
                    )
                    yield NcmuSseEvent(
                        event_type="workflow_finished",
                        run_id=run_id,
                        timestamp=now,
                        data=WorkflowFinishedData(
                            status="succeeded",
                            outputs={"answer": accumulated_text},
                            total_elapsed_ms=(
                                int(_latency * 1000)
                                if _latency is not None
                                else None
                            ),
                        ),
                    )
                    emitted_terminal = True
        except asyncio.CancelledError:
            # TASK-B: client disconnect / SSE cancel → don't yield (pipe is closed).
            # REWORK-INDEP-I-1: set sentinel so finally skips the catch-all yield;
            # see advanced_chat.py for the full rationale.
            cancelled = True
            raise
        except httpx.TimeoutException as exc:
            # TASK-D B-NEW-41: explicit timeout bucket — see advanced_chat.py
            # for the full rationale.
            if not emitted_terminal:
                yield NcmuSseEvent(
                    event_type="workflow_finished",
                    run_id=run_id,
                    timestamp=datetime.now(timezone.utc),
                    data=WorkflowFinishedData(
                        status="timeout",
                        outputs={"answer": accumulated_text} if accumulated_text else {},
                        error=(
                            f"upstream timeout: {exc!r}"
                            if str(exc)
                            else "upstream timeout"
                        ),
                    ),
                )
                emitted_terminal = True
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
