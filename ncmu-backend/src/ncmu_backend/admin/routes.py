"""Admin debug endpoints — read-only views over `dify_external_kb_configs`
and `dify_app_kb_bindings`.

Phase 1 scope: GET-only. POST/PUT/DELETE for KB-config / binding admin
is deferred to Phase 3 (which delivers the full admin UI). The two
endpoints here exist mainly so an operator inspecting a deployed env
can verify the bootstrap seed (ncmu_init.py output) without `psql`.

Why raw `text()` instead of an ORM query: the ORM models for these two
tables don't exist yet — `db/models.py` only declares User and
ChatSession (DEPLOY-1 note in that file says the dify_* models are
TASK-26's batch). Rather than introduce a forward dep on TASK-26, we
hit the tables with hand-written SQL; the column projections are short
enough that a model wouldn't add much.

Both endpoints depend on `require_admin`, which raises 403 + code 1201
if the JWT subject isn't in `NCMU_ADMIN_USER_IDS`.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ncmu_backend.auth.deps import CurrentUser, require_admin
from ncmu_backend.db.session import get_db
from ncmu_backend.schemas.admin import BindingOut, KbConfigOut

router = APIRouter(tags=["admin"])


@router.get(
    "/api/v1/ncmu/admin/kb-configs",
    response_model=list[KbConfigOut],
    summary="List dify_external_kb_configs (admin debug; Phase 1 GET only)",
)
async def list_kb_configs(
    _: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[KbConfigOut]:
    result = await db.execute(
        text(
            """
            SELECT id, external_kb_name, fastgpt_dataset_id, api_key_label,
                   upstream_endpoint, notes, created_at, updated_at
              FROM dify_external_kb_configs
             ORDER BY external_kb_name
            """
        )
    )
    rows: list[Any] = result.mappings().all()
    return [KbConfigOut.model_validate(dict(r)) for r in rows]


@router.get(
    "/api/v1/ncmu/admin/dify-app-bindings",
    response_model=list[BindingOut],
    summary="List dify_app_kb_bindings (admin debug; Phase 1 GET only)",
)
async def list_dify_app_bindings(
    _: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[BindingOut]:
    result = await db.execute(
        text(
            """
            SELECT id, dify_app_id, external_kb_config_id, bound_at
              FROM dify_app_kb_bindings
             ORDER BY bound_at DESC
            """
        )
    )
    rows: list[Any] = result.mappings().all()
    return [BindingOut.model_validate(dict(r)) for r in rows]
