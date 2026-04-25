"""Apps schemas — response shape for `GET /api/v1/ncmu/apps`.

Shape mirrors design §2.2 [2]: SPA receives `[{id, name, type:"kb_qa",
description}, ...]`. Phase 1 only [KB]-typed apps are returned, so `type`
is fixed to `"kb_qa"` (Phase 3 will broaden when other app categories
land in the SPA).
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class AppOut(BaseModel):
    """One Dify App, projected to the slim shape the SPA cares about."""

    id: str = Field(..., description="Dify App UUID (authoritative, from Dify Console)")
    name: str = Field(..., description="App display name; may carry the `[KB]` prefix")
    type: Literal["kb_qa"] = Field(
        default="kb_qa",
        description="NCMU app category — Phase 1 only kb_qa is exposed",
    )
    description: Optional[str] = Field(
        default=None, description="App description from Dify Console"
    )
