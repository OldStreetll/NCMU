"""POST /api/v1/ncmu/chat/{app_id} — SSE relay endpoint.

Auto-included by ``ncmu_backend.main`` via the routes-discovery loop, so
this module never needs to be imported elsewhere.
"""
from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ncmu_backend.chat.orchestrator import stream_chat
from ncmu_backend.config import Settings, get_settings
from ncmu_backend.deps import CurrentUser, get_current_user, get_db
from ncmu_backend.main import get_dify_client
from ncmu_backend.schemas.chat import ChatRequest


router = APIRouter(prefix="/api/v1/ncmu", tags=["chat"])


@router.post("/chat/{app_id}")
async def chat(
    app_id: str,
    body: ChatRequest,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    dify_client: httpx.AsyncClient = Depends(get_dify_client),
) -> StreamingResponse:
    """Stream Dify ``/v1/chat-messages`` to the SPA, double-writing
    ``chat_sessions`` on the first frame that carries a ``conversation_id``.
    """
    return StreamingResponse(
        stream_chat(
            db=db,
            user_id=user.sub,
            app_id=app_id,
            query=body.query,
            conversation_id=body.conversation_id,
            dify_api_base=settings.DIFY_BASE_URL,
            dify_app_token=settings.DIFY_APP_DEFAULT_TOKEN,
            client=dify_client,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
