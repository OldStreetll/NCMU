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
        emitted_terminal = False  # TASK-B: dedup sentinel — at most 1 terminal envelope per run
        cancelled = False  # REWORK-INDEP-I-1: gate finally yield on cancellation path
        try:
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
                if event_name == "error":
                    # TASK-B: Dify v1.13.3 event:error frame — top-level fields
                    # (NOT data-nested); see dify-sse-error-frame-contract §1.1.
                    # Placed BEFORE the message_end terminal branch (plan §Phase 3).
                    code = raw.get("code", "")
                    msg = raw.get("message", "")
                    yield NcmuSseEvent(
                        event_type="workflow_finished",
                        run_id=run_id,
                        timestamp=datetime.now(timezone.utc),
                        data=WorkflowFinishedData(
                            status="failed",
                            outputs={"answer": accumulated_text} if accumulated_text else {},
                            error=f"{code}: {msg}" if code else msg,
                        ),
                    )
                    emitted_terminal = True
                    return
                if event_name == "message_end":
                    # TASK-D B-NEW-42: Dify LLMUsage.latency is float seconds;
                    # convert to ms at the boundary so the value matches the
                    # ``_ms`` field name. None stays None (no implicit 0).
                    _latency = (
                        raw.get("metadata", {}).get("usage", {}).get("latency")
                    )
                    yield NcmuSseEvent(
                        event_type="workflow_finished",
                        run_id=run_id,
                        timestamp=datetime.now(timezone.utc),
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
            # TASK-D B-NEW-41: explicit timeout bucket — distinct from the
            # generic 'exception' status so main.py boot-sweeper +
            # SPA render timeout-specific UX. httpx.TimeoutException is
            # the base class for ConnectTimeout / ReadTimeout / WriteTimeout
            # / PoolTimeout — single clause captures all four. Body shape
            # mirrors ``except Exception`` (no re-raise) so the consumer's
            # ``async for`` ends cleanly after receiving the terminal.
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
