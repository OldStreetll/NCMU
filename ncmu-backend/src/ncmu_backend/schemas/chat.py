"""Request/response schemas for the chat endpoint."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=4000)
    conversation_id: str | None = None
