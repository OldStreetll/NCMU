"""Admin schemas — read-only views over `dify_external_kb_configs` and
`dify_app_kb_bindings`. Phase 1 admin endpoints are dev/debug surface
only (no write); the schema therefore omits `created_at` / internal
columns the operator doesn't need at this stage.

The ORM models for these two tables don't yet exist (`db/models.py`
will get them in TASK-26's batch per its DEPLOY-1 note). Until then
admin/routes.py queries via SQLAlchemy core `text()`; these pydantic
models validate the `Mapping` rows that fall out of the cursor.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class KbConfigOut(BaseModel):
    """One row of `dify_external_kb_configs` for the admin debug list."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    external_kb_name: str = Field(..., description="Business-facing KB name (e.g. 'hr-handbook')")
    fastgpt_dataset_id: str = Field(..., description="FastGPT dataset._id this NCMU KB maps to")
    api_key_label: str = Field(
        ...,
        description="Audit label for the kb-adapter API key (decoupled from the actual token by Phase 3 mapping).",
    )
    upstream_endpoint: Optional[str] = Field(
        default=None,
        description="Reserved for multi-FastGPT-instance routing; null in Phase 1.",
    )
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class BindingOut(BaseModel):
    """One row of `dify_app_kb_bindings` for the admin debug list."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    dify_app_id: str = Field(..., description="Dify App UUID (no FK in Phase 1; dify_apps cache table deferred)")
    external_kb_config_id: UUID = Field(..., description="FK → dify_external_kb_configs.id")
    bound_at: datetime


class SyncAppsResponse(BaseModel):
    """Response for POST /api/v1/ncmu/admin/sync_apps (TASK-67a).

    `upserted` counts rows that successfully went through the
    INSERT … ON CONFLICT DO UPDATE statement (i.e., rows that had a
    Dify App `id`). `total_console` is the unfiltered Dify Console
    page size — `total_console - upserted` reveals how many rows were
    skipped (typically: missing id).
    """

    upserted: int = Field(description="UPSERT 命中条数（含 INSERT + UPDATE）")
    total_console: int = Field(description="Dify Console 返回的总条数")
