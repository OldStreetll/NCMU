"""Pydantic schemas for admin App management (TASK-PE-07).

Mirrors the live ``dify_apps`` table (db/models.py ``DifyApp``) as it
actually exists *after* alembic 0010 — NOT the plan §4 Step 1 sketch,
which assumed an ``id`` UUID PK + ``icon`` / ``description`` columns the
cache table never carried (Boss 2026-05-29 拍板: drop icon/description
from the response; PK is ``dify_app_id`` text String(64)).

Real ``dify_apps`` columns surfaced here:
- dify_app_id   text PK (the Dify Console App UUID, stored as String(64))
- name / mode   synced from Dify Console by POST /sync_apps
- is_active     alembic 0010 — admin toggle ("停用")
- last_synced_at alembic 0010 — stamped by POST /sync_apps; NULL = never

``tag_count`` is wired to 0 until PE-08 lands the apps↔tags binding
(same placeholder convention as admin/routes.py ``_AdminStatsOut``).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class AdminAppOut(BaseModel):
    """Single dify_apps row as the admin SPA consumes it (list + detail + PATCH echo)."""

    model_config = ConfigDict(from_attributes=True)

    dify_app_id: str = Field(description="Dify Console App id (cache PK, String(64))")
    name: str
    mode: str = Field(description="chat / agent-chat / advanced-chat / workflow / completion")
    is_active: bool
    last_synced_at: Optional[datetime] = Field(
        default=None, description="上次同步时间（POST /sync_apps 写入）；NULL = 尚未同步"
    )
    tag_count: int = Field(default=0, description="已绑标签数（PE-08 落地前固定 0）")


class AdminAppUpdate(BaseModel):
    """PATCH body — only ``is_active`` is mutable here.

    name / mode / icon are owned by Dify Console (edit there + re-sync);
    this endpoint exists solely to toggle visibility to employees. The
    field is Optional so an empty PATCH is a no-op (no-op returns the row
    unchanged rather than 422).
    """

    is_active: Optional[bool] = Field(default=None)
