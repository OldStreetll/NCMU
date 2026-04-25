"""Sessions schemas — list / messages / patch (rename) request/response shapes.

TASK-28 endpoints serialise ChatSession ORM rows via these models. We keep
the output narrow (no leaking of `user_id` to the client — the response is
already user-scoped by the JWT) and stable (so the SPA can typegen against
the OpenAPI schema once and not chase Phase-2 columns).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SessionOut(BaseModel):
    """One row of `GET /api/v1/ncmu/sessions`."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    dify_app_id: str = Field(..., description="Dify App UUID this session belongs to")
    dify_conversation_id: str = Field(..., description="Dify conversation UUID")
    title: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class SessionListOut(BaseModel):
    """Envelope for `GET /api/v1/ncmu/sessions` — items + cursor.

    Phase 1 returns at most `limit` items (default 20) ordered by
    `updated_at DESC`. The cursor is the `updated_at` of the last
    returned row, ISO-8601; pass it back as `?cursor=` to page.
    """
    items: list[SessionOut]
    next_cursor: Optional[str] = Field(
        default=None,
        description="ISO-8601 updated_at of the last item (use as ?cursor= for the next page); null when fewer than limit items remain.",
    )


class SessionPatchRequest(BaseModel):
    """`PATCH /api/v1/ncmu/sessions/:id` — only `title` is mutable in Phase 1."""
    title: str = Field(..., min_length=1, max_length=255)


# ---- GET /api/v1/ncmu/sessions/:id/messages ---------------------------
# We pass through whatever Dify returns from /v1/messages with minimal
# wrapping (the SPA already knows Dify's shape via streamChat). Phase 1
# does not transform the messages payload — Phase 2 may add NCMU-side
# annotations (citation enrichment) at which point a typed model
# replaces this raw dict envelope.

class MessagesOut(BaseModel):
    """Envelope for `GET /api/v1/ncmu/sessions/:id/messages` — Dify透传."""
    model_config = ConfigDict(extra="allow")  # passthrough Dify payload

    data: list[dict]
    has_more: bool = False
    limit: int = 20
