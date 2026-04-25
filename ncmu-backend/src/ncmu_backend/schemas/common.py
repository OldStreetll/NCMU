"""Common response envelopes — shared by every endpoint group."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class ErrorBody(BaseModel):
    """Standard error body — shape `{"code": int, "message": str}`.

    Mirrors FastGPT / kb-adapter response shape so a single error
    handler in the SPA can deal with all upstream errors uniformly.
    """
    code: int = Field(..., description="NCMU error code (4 digits)")
    message: str = Field(..., description="Human-readable description")
    detail: Optional[Any] = Field(default=None, description="Optional debug payload")


class PageMeta(BaseModel):
    """Pagination metadata — TASK-26/27 list endpoints embed this."""
    total: int
    page: int = 1
    page_size: int = 20
