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
    mode: str = Field(
        ...,
        description=(
            "Dify App mode — drives frontend page routing. Values: chat / "
            "advanced-chat / completion / workflow / agent-chat (TASK-69)."
        ),
    )
    description: Optional[str] = Field(
        default=None, description="App description from Dify Console"
    )


class ParameterOut(BaseModel):
    """One input field projected for SPA DynamicInputForm (TASK-BUG-2).

    Mirrors SPA `ParameterSchema` at
    ncmu-spa/src/components/workflow/DynamicInputForm.tsx:3-9 — the SPA
    consumes `list[ParameterOut]` flat from GET /apps/{id}/parameters and
    pipes each entry into one antd Form.Item.
    """

    variable: str = Field(..., description="Form field name (becomes inputs key)")
    label: str = Field(..., description="Display label")
    type: Literal["text", "paragraph", "select", "number"] = Field(
        ..., description="UI widget kind"
    )
    required: bool = Field(default=False, description="Antd Form 'required' rule")
    options: list[str] = Field(
        default_factory=list,
        description="Select options (empty for non-select types)",
    )
