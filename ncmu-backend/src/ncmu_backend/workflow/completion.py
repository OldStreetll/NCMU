"""Completion (one-shot generation) orchestrator — Phase 2B B2 / TASK-72.

Why a dedicated orchestrator for the completion mode:
- Dify v1.13.3 Completion Apps live behind ``/v1/completion-messages``
  (not ``/v1/chat-messages``) and emit a *flat* event stream — no node
  graph, no message lifecycle. The upstream events that matter are
  ``message`` / ``text_chunk`` (partial token chunks) and ``message_end``.
  NCMU collapses the entire run onto a single ``workflow_finished``
  envelope so the SPA renders the answer in one shot.
- ★H6 修订 (spike §3.6): some Dify Completion Apps push partial tokens
  via ``text_chunk`` instead of (or in addition to) ``message``. This
  orchestrator accumulates both paths into ``accumulated_text`` so the
  final ``WorkflowFinishedData.outputs.answer`` is the literal
  concatenation of every chunk delivered upstream.
- ★H2 status mapping (sse_events.py): NCMU collapses Dify's wider
  workflow status taxonomy onto 4 terminal values; for completion the
  upstream stream closing cleanly with ``message_end`` is "succeeded".
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from ncmu_backend.schemas.sse_events import NcmuSseEvent, WorkflowFinishedData
from ncmu_backend.workflow._base import BaseOrchestrator
from ncmu_backend.workflow.dify_client import DifyStreamClient


class CompletionOrchestrator(BaseOrchestrator):
    """Dify Completion App → NCMU ``workflow_finished`` envelope."""

    mode = "completion"

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
        }
        accumulated_text = ""
        async for raw in dify_client.stream("/v1/completion-messages", body):
            event_name = raw.get("event")
            if event_name == "message":
                # Accumulated answer path — Dify pushes the running answer
                # in one or more ``message`` frames; concatenate verbatim.
                accumulated_text += raw.get("answer", "")
                continue
            if event_name == "text_chunk":
                # ★H6: chunk path — field position candidates are
                # ``raw["data"]["text"]`` (preferred) and ``raw["text"]``.
                chunk = raw.get("data", {}).get("text") or raw.get("text", "")
                accumulated_text += chunk
                continue
            if event_name == "message_end":
                yield NcmuSseEvent(
                    event_type="workflow_finished",
                    run_id=run_id,
                    timestamp=datetime.now(timezone.utc),
                    data=WorkflowFinishedData(
                        status="succeeded",
                        outputs={"answer": accumulated_text},
                        total_elapsed_ms=raw.get("metadata", {})
                        .get("usage", {})
                        .get("latency"),
                    ),
                )
