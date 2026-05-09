"""Admin debug endpoints — read-only views over `dify_external_kb_configs`
and `dify_app_kb_bindings`, plus POST /sync_apps (Phase 2B TASK-67a).

Phase 1 scope: GET-only. POST/PUT/DELETE for KB-config / binding admin
is deferred to Phase 3 (which delivers the full admin UI). The two
GET endpoints exist mainly so an operator inspecting a deployed env
can verify the bootstrap seed (ncmu_init.py output) without `psql`.

Phase 2B TASK-67a adds POST /sync_apps so admins can manually populate
the new dify_apps cache table by pulling Dify Console /apps and
UPSERTing each row. dispatcher.resolve_mode (TASK-68) reads from
dify_apps to decide which Dify upstream endpoint a chat request hits.

Why raw `text()` instead of an ORM query for the two GET endpoints:
the ORM models for these two tables don't exist yet — `db/models.py`
only declares User / ChatSession / DifyApp (the dify_external_kb_configs
+ dify_app_kb_bindings ORMs are slated for the same batch that wires
the admin UI, Phase 3). Rather than introduce a forward dep, we hit
those tables with hand-written SQL; the column projections are short
enough that a model wouldn't add much.

Both endpoints depend on `require_admin`, which raises 403 + code 1201
if the JWT subject isn't in `NCMU_ADMIN_USER_IDS`.
"""
from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy import func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ncmu_backend.apps.dify_console_client import DifyConsoleClient
from ncmu_backend.apps.routes import get_dify_console_client
from ncmu_backend.auth.deps import CurrentUser, require_admin
from ncmu_backend.db.models import DifyApp
from ncmu_backend.db.session import get_db
from ncmu_backend.deps import Settings, get_settings
from ncmu_backend.main import get_dify_client
from ncmu_backend.schemas.admin import BindingOut, KbConfigOut, SyncAppsResponse

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


@router.post(
    "/api/v1/ncmu/admin/sync_apps",
    response_model=SyncAppsResponse,
    summary="拉取 Dify Console /apps 全量 + UPSERT 到 dify_apps 缓存表",
)
async def sync_apps(
    user: CurrentUser = Depends(require_admin),
    cache: DifyConsoleClient = Depends(get_dify_console_client),
    http: httpx.AsyncClient = Depends(get_dify_client),
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db),
) -> SyncAppsResponse:
    """Admin pulls Dify Console /apps + UPSERTs each row into dify_apps.

    M-NEW-5: invalidate the per-user TTL cache *before* fetching upstream,
    so a sync immediately following a GET /apps doesn't replay stale data.
    F-FRESH-1: CurrentUser exposes `sub` (JWT subject = user UUID), not
    `id`. Mirrors apps/routes.py:51 `user_id=user.sub`.
    """
    cache.invalidate(user.sub)
    raw = await cache.list_apps_for_user(
        user_id=user.sub,
        http=http,
        base_url=settings.DIFY_BASE_URL,
        api_key=settings.DIFY_CONSOLE_API_KEY,
    )
    upsert_count = 0
    for row in raw:
        dify_id = row.get("id")
        if not dify_id:
            continue
        name = row.get("name", "Untitled")
        mode = row.get("mode", "chat")
        stmt = pg_insert(DifyApp).values(
            dify_app_id=dify_id,
            name=name,
            mode=mode,
        ).on_conflict_do_update(
            index_elements=["dify_app_id"],
            set_={
                "name": name,
                "mode": mode,
                "updated_at": func.now(),
            },
        )
        await db.execute(stmt)
        upsert_count += 1
    await db.commit()
    return SyncAppsResponse(upserted=upsert_count, total_console=len(raw))
