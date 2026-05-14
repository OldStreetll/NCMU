"""GET /api/v1/ncmu/apps — list the [KB] Apps the current user can see.

Picks up the lifespan-owned httpx client via `Depends(get_dify_client)`,
applies a 5-minute per-user cache, filters by `detect_app_type` (name
prefix [KB] OR tag kb-qa), and projects each Dify entry to `AppOut`.
"""
from __future__ import annotations

import logging
from functools import lru_cache

import httpx
from fastapi import APIRouter, Depends

from ncmu_backend.apps.dify_console_client import (
    DifyConsoleClient,
    detect_app_type,
)
from ncmu_backend.config import Settings
from ncmu_backend.deps import CurrentUser, get_current_user, get_settings
from ncmu_backend.main import get_dify_client
from ncmu_backend.schemas.apps import AppOut

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
    summary="List [KB] Apps the current user can chat with",
)
async def list_apps(
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    http: httpx.AsyncClient = Depends(get_dify_client),
    cache: DifyConsoleClient = Depends(get_dify_console_client),
) -> list[AppOut]:
    raw = await cache.list_apps_for_user(
        user_id=user.sub,
        http=http,
        base_url=settings.DIFY_BASE_URL,
        api_key=settings.DIFY_CONSOLE_API_KEY,
        tenant_id=settings.DIFY_TENANT_ID,
    )
    out: list[AppOut] = []
    for entry in raw:
        name = entry.get("name") or ""
        if not detect_app_type(name, entry.get("tags")):
            continue
        out.append(
            AppOut(
                id=str(entry.get("id") or ""),
                name=name,
                type="kb_qa",
                mode=entry.get("mode") or "chat",
                description=entry.get("description") or None,
            )
        )
    log.info(
        "GET /api/v1/ncmu/apps user=%s upstream=%d returned=%d",
        user.sub, len(raw), len(out),
    )
    return out
