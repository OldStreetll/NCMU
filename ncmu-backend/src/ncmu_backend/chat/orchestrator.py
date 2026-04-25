"""Stream-chat orchestrator: Dify SSE relay + chat_sessions double-write.

The orchestrator is shaped by three plan-level constraints (see plan TASK-27
AC#7.e for the authoritative skeleton):

* StreamingResponse is a single async generator — its body cannot be
  yielded into from two coroutines concurrently. Heartbeat ticker and Dify
  pump therefore push frames into an :class:`asyncio.Queue`, and a single
  consumer drains the queue on the response path.
* Dify v1.13.x emits ``conversation_id`` on every frame; without a guard
  the orchestrator would attempt N inserts and crash on the UNIQUE
  constraint after the first. ``needs_insert`` and ``session_inserted``
  together ensure exactly one INSERT per request.
* On a Dify ``error`` frame the orchestrator follows the α decision (Boss
  2026-04-25 / C-Queue-1): forward the mapped error to the SPA and return
  immediately — do not wait for Dify's own close. The ticker is cancelled
  in the consumer's ``finally`` so we never leak the heartbeat task.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import AsyncIterator
from uuid import UUID

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ncmu_backend.chat.sse import encode_sse_frame, parse_dify_sse
from ncmu_backend.db.models import ChatSession


_SENTINEL = object()


# Dify v1.13.x SSE error-frame `code` values, taken from
# core/app/apps/base_app_generate_response_converter.py:108-143 and
# confirmed against sources/phase1/dify-chat-sample-2026-04-25/. Phase 1
# collapses all six to NCMU 9001; spec §10.5 may later split them out.
_DIFY_ERROR_CODES: frozenset[str] = frozenset({
    "invalid_param",
    "provider_not_initialize",
    "provider_quota_exceeded",
    "model_currently_not_support",
    "completion_request_error",
    "internal_server_error",
})


def _map_dify_error_to_ncmu_code(dify_data: dict) -> int:
    """Project a Dify SSE error payload onto an NCMU error code.

    SCOPE-CHANGE-1 修订（2026-04-25）: kb-adapter's 1001/1002/2001 do NOT
    surface here. TASK-24 实测 confirmed that Dify ``_retriever`` thread
    swallows kb-adapter HTTP errors (ToolSSRFError /
    MaxRetriesExceededError) and the chat-messages SSE main stream sees an
    empty ``retriever_resources`` array instead of an ``event=error`` frame.
    The only six codes ever observed in real Dify SSE error frames are the
    Dify-internal strings above; Phase 1 collapses them all to 9001.
    """
    code = dify_data.get("code")
    if isinstance(code, str) and code in _DIFY_ERROR_CODES:
        return 9001
    return 9001


async def stream_chat(
    *,
    db: AsyncSession,
    user_id: str,
    app_id: str,
    query: str,
    conversation_id: str | None,
    dify_api_base: str,
    dify_app_token: str,
    client: httpx.AsyncClient,
    heartbeat_interval_s: float = 20.0,
) -> AsyncIterator[bytes]:
    """Yield SSE bytes for ``StreamingResponse``.

    The flow:

    1. Resolve the existing :class:`ChatSession` if a ``conversation_id``
       was supplied; emit an inline ``error/9001`` and stop if it does not
       belong to ``user_id``.
    2. Spawn ``pump_dify`` (Dify SSE → queue, with double-write side-effects)
       and ``pump_ticker`` (every ``heartbeat_interval_s`` push a SSE
       comment ``: ping`` to keep nginx from declaring the stream idle).
    3. The consumer drains the queue until it receives ``_SENTINEL``,
       which ``pump_dify`` is required to send in its ``finally`` block.
    4. On exit (normal, error, or client cancel) cancel both tasks and
       gather them with ``return_exceptions=True`` so neither leaks.

    ``user_id`` is the JWT subject (UUID-string). It is parsed once into
    :class:`uuid.UUID` for the SQL layer; if it is malformed the
    orchestrator emits 9001 rather than letting asyncpg raise mid-stream.
    """
    try:
        user_uuid = UUID(user_id)
    except (ValueError, TypeError):
        yield encode_sse_frame("error", {"code": 9001, "message": "invalid user id"})
        return

    existing: ChatSession | None = None
    if conversation_id:
        result = await db.execute(
            select(ChatSession).where(
                ChatSession.dify_conversation_id == conversation_id,
                ChatSession.user_id == user_uuid,
                ChatSession.deleted_at.is_(None),
            )
        )
        existing = result.scalar_one_or_none()
        if existing is None:
            yield encode_sse_frame(
                "error",
                {"code": 9001, "message": "session not found"},
            )
            return

    needs_insert = existing is None
    session_inserted = False
    target_session_id = existing.id if existing is not None else None

    queue: asyncio.Queue = asyncio.Queue()

    async def pump_dify() -> None:
        """Producer 1: drain Dify SSE, double-write, push frames to queue."""
        nonlocal session_inserted, target_session_id
        try:
            payload: dict = {
                "inputs": {},
                "query": query,
                "response_mode": "streaming",
                "user": str(user_uuid),
            }
            if conversation_id:
                payload["conversation_id"] = conversation_id

            headers = {"Authorization": f"Bearer {dify_app_token}"}
            async with client.stream(
                "POST",
                f"{dify_api_base.rstrip('/')}/v1/chat-messages",
                headers=headers,
                json=payload,
            ) as resp:
                if resp.status_code != 200:
                    await queue.put(
                        encode_sse_frame(
                            "error",
                            {
                                "code": 9001,
                                "message": f"dify upstream {resp.status_code}",
                            },
                        )
                    )
                    return

                async for evt in parse_dify_sse(resp.aiter_bytes()):
                    data = evt["data"]
                    if (
                        needs_insert
                        and not session_inserted
                        and isinstance(data, dict)
                        and "conversation_id" in data
                    ):
                        new_conv_id = data["conversation_id"]
                        new_session = ChatSession(
                            id=uuid.uuid4(),
                            user_id=user_uuid,
                            dify_app_id=app_id,
                            dify_conversation_id=new_conv_id,
                            title=query[:60],
                        )
                        try:
                            db.add(new_session)
                            await db.commit()
                            await db.refresh(new_session)
                            session_inserted = True
                            target_session_id = new_session.id
                            await queue.put(
                                encode_sse_frame(
                                    "ncmu.session_created",
                                    {
                                        "session_id": str(new_session.id),
                                        "dify_conversation_id": new_conv_id,
                                    },
                                )
                            )
                        except Exception:
                            await db.rollback()
                            await queue.put(
                                encode_sse_frame(
                                    "error",
                                    {"code": 9001, "message": "ncmu insert failed"},
                                )
                            )
                            return

                    if evt["event"] == "error":
                        ncmu_code = _map_dify_error_to_ncmu_code(data)
                        await queue.put(
                            encode_sse_frame(
                                "error",
                                {
                                    "code": ncmu_code,
                                    "message": data.get("message", "")
                                    if isinstance(data, dict)
                                    else "",
                                },
                            )
                        )
                        # α (Boss 2026-04-25 / C-Queue-1): forward the
                        # mapped error and stop reading Dify; the
                        # ticker + consumer wind down via _SENTINEL.
                        return

                    # Skip Dify's keep-alive `ping` (the orchestrator
                    # owns its own SPA-side ticker; a double-ping would
                    # just waste bandwidth).
                    if evt["event"] == "ping":
                        continue

                    await queue.put(encode_sse_frame(evt["event"], data))
        except asyncio.CancelledError:
            raise
        except Exception:
            await queue.put(
                encode_sse_frame(
                    "error", {"code": 9001, "message": "upstream stream failed"}
                )
            )
        finally:
            if target_session_id is not None:
                try:
                    await db.execute(
                        update(ChatSession)
                        .where(ChatSession.id == target_session_id)
                        .values(updated_at=func.now())
                    )
                    await db.commit()
                except Exception:
                    await db.rollback()
            await queue.put(_SENTINEL)

    async def pump_ticker() -> None:
        """Producer 2: SSE comment frame every ``heartbeat_interval_s``.

        ``: ping`` is an SSE *comment* (RFC: leading ``:``); browsers /
        EventSource silently drop it, but it keeps nginx
        ``proxy_read_timeout`` from declaring the connection idle.
        """
        try:
            while True:
                await asyncio.sleep(heartbeat_interval_s)
                await queue.put(b": ping\n\n")
        except asyncio.CancelledError:
            return

    dify_task = asyncio.create_task(pump_dify())
    ticker_task = asyncio.create_task(pump_ticker())
    try:
        while True:
            item = await queue.get()
            if item is _SENTINEL:
                break
            yield item
    finally:
        ticker_task.cancel()
        dify_task.cancel()
        await asyncio.gather(dify_task, ticker_task, return_exceptions=True)
