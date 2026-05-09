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
"""
from __future__ import annotations

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


class AgentChatOrchestrator(BaseOrchestrator):
    """agent-chat (Dify Agent App with tool-calls) orchestrator."""

    mode = "agent-chat"

    async def run(
        self,
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
        async for raw in self._dify.stream("/v1/chat-messages", body):
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
