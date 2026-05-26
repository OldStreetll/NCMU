"""GET /api/v1/ncmu/apps — list the Apps the current user can chat with.

Phase 2C paradigm shift (spec §3.3 / plan §4.4 PC2-C):
- routes.py is a thin HTTP/projection layer; the dual-track routing
  (shared via DifyConsoleClient ∪ owner via app_owners + dify_apps DB
  query + dedupe) is encapsulated in apps/services.py:list_apps_for_user.
- dify_console_client.py is the字面禁区 (C4 + C-INDEP-1 拍板): its 5-kwarg
  signature and 5-minute lru cache stay untouched.
"""
from __future__ import annotations

import logging
from functools import lru_cache

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ncmu_backend.apps import services
from ncmu_backend.apps.dify_console_client import DifyConsoleClient
from ncmu_backend.config import Settings
from ncmu_backend.db.session import get_db
from ncmu_backend.deps import CurrentUser, get_current_user, get_settings
from ncmu_backend.main import get_dify_client
from ncmu_backend.schemas.apps import AppOut, ParameterOut

log = logging.getLogger("ncmu_backend.apps.routes")

router = APIRouter(tags=["apps"])


@lru_cache(maxsize=1)
def get_dify_console_client() -> DifyConsoleClient:
    """Process-singleton DifyConsoleClient.

    Tests override this dependency to inject a fakeable clock — see
    `tests/conftest.py` `app_client_with_clock` fixture pattern.
    """
    return DifyConsoleClient(ttl_seconds=300)


@router.get(
    "/api/v1/ncmu/apps",
    response_model=list[AppOut],
    summary="List Apps the current user can chat with (shared ∪ owner)",
)
async def list_apps(
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    http: httpx.AsyncClient = Depends(get_dify_client),
    cache: DifyConsoleClient = Depends(get_dify_console_client),
    db: AsyncSession = Depends(get_db),
) -> list[AppOut]:
    merged = await services.list_apps_for_user(
        db=db,
        user_id=user.sub,
        http=http,
        cache=cache,
        settings=settings,
    )
    out: list[AppOut] = [
        AppOut(
            id=str(entry.get("id") or ""),
            name=entry.get("name") or "",
            type="kb_qa",
            mode=entry.get("mode") or "chat",
            description=entry.get("description") or None,
        )
        for entry in merged
    ]
    log.info(
        "GET /api/v1/ncmu/apps user=%s merged=%d returned=%d",
        user.sub, len(merged), len(out),
    )
    return out


@router.get(
    "/api/v1/ncmu/apps/{app_id}/parameters",
    response_model=list[ParameterOut],
    summary="App input form schema (chat user_input_form OR workflow start.variables)",
)
async def get_app_parameters(
    app_id: str,
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    http: httpx.AsyncClient = Depends(get_dify_client),
    cache: DifyConsoleClient = Depends(get_dify_console_client),
    db: AsyncSession = Depends(get_db),
) -> list[ParameterOut]:
    """TASK-BUG-2 — SPA useAppParameters source-of-truth.

    Dispatch (Boss T2.1=A): app.mode literal == "workflow"/"advanced-chat"
    → workflows/draft start.variables; else → model_config.user_input_form.
    Returns flat list[ParameterOut] = SPA ParameterSchema[].

    Permission (reuses services.user_can_access_app): owner OR shared via
    DifyConsoleClient cache; 403 if neither path admits user.
    """
    allowed = await services.user_can_access_app(
        db=db,
        user_id=user.sub,
        app_id=app_id,
        http=http,
        cache=cache,
        settings=settings,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": 1002, "message": "App not accessible by current user"},
        )
    raw = await services.get_app_parameters(
        app_id=app_id, http=http, cache=cache, settings=settings,
    )
    out = [ParameterOut(**entry) for entry in raw]
    log.info(
        "GET /api/v1/ncmu/apps/%s/parameters user=%s returned=%d",
        app_id, user.sub, len(out),
    )
    return out
