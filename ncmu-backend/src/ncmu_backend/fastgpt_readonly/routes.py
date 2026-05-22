"""Employee-facing read-only KB endpoints (plan §4.3 AC#5 / errata-13 §2.3).

Two routes, both gated by ``apps.services.user_can_access_app`` so the
caller can only see files for Apps they're entitled to chat with (shared
∪ owner / spec §3.3 dual-track).

The download endpoint additionally reverse-looks-up ``file_id`` against
the App's bound KBs (防越权 / plan §4.3 AC#5b) — knowing the App is not
enough; the file must live inside one of that App's datasets.

DB schema note: ``dify_app_kb_bindings`` + ``dify_external_kb_configs``
land in alembic 0002/0003 but have no SQLAlchemy ORM model yet (per
``db/models.py`` header — "Phase 3 回补"). Until then we query them via
``sqlalchemy.text`` directly.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.responses import StreamingResponse
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from ncmu_backend.apps.dify_console_client import DifyConsoleClient
from ncmu_backend.apps.routes import get_dify_console_client
from ncmu_backend.apps.services import user_can_access_app
from ncmu_backend.config import Settings
from ncmu_backend.db.session import get_db
from ncmu_backend.deps import CurrentUser, get_current_user, get_settings
from ncmu_backend.fastgpt_readonly.client import (
    FastGPTReadOnlyClient,
    FileMeta,
)
from ncmu_backend.fastgpt_readonly.errors import (
    FastGPTError,
    translate_to_http_exception,
)
from ncmu_backend.main import get_dify_client

log = logging.getLogger("ncmu_backend.fastgpt_readonly.routes")

router = APIRouter(prefix="/v1/kbs", tags=["fastgpt-readonly"])


# REWORK-INDEP I2: process-singleton FastGPTReadOnlyClient so its
# internal httpx.AsyncClient (and the keep-alive connection pool inside
# it) gets reused across requests. The singleton is closed once in the
# main.py lifespan shutdown hook (see ``aclose_fastgpt_client``).
@lru_cache(maxsize=1)
def _get_fastgpt_client_singleton() -> FastGPTReadOnlyClient:
    settings = get_settings()
    return FastGPTReadOnlyClient(
        base_url=settings.FASTGPT_BASE_URL,
        api_key=settings.FASTGPT_API_KEY,
    )


def _get_fastgpt_client() -> FastGPTReadOnlyClient:
    """FastAPI dependency target — tests override this with a mock.

    The split between the depends-target and the lru-cached singleton
    keeps test injection trivial (override ``_get_fastgpt_client`` →
    mock) while still allowing the prod path to reuse one client.
    """
    return _get_fastgpt_client_singleton()


async def aclose_fastgpt_client() -> None:
    """Close the process-singleton at app shutdown. No-op if it was
    never lazily created (lru_cache CacheInfo currsize == 0)."""
    info = _get_fastgpt_client_singleton.cache_info()
    if info.hits + info.misses == 0:
        return
    client = _get_fastgpt_client_singleton()
    await client.aclose()
    _get_fastgpt_client_singleton.cache_clear()


async def _datasets_for_app(db: AsyncSession, app_id: str) -> list[str]:
    """All ``fastgpt_dataset_id`` strings bound to ``app_id``.

    Returns ``[]`` if no bindings — caller treats that as "no KB files".
    """
    result = await db.execute(
        sql_text(
            "SELECT dec.fastgpt_dataset_id "
            "FROM dify_app_kb_bindings dab "
            "JOIN dify_external_kb_configs dec "
            "  ON dab.external_kb_config_id = dec.id "
            "WHERE dab.dify_app_id = :app_id "
            "  AND dec.fastgpt_dataset_id IS NOT NULL"
        ),
        {"app_id": app_id},
    )
    return [row[0] for row in result.fetchall() if row[0]]


async def _ensure_access(
    *,
    app_id: str,
    user: CurrentUser,
    db: AsyncSession,
    http: httpx.AsyncClient,
    cache: DifyConsoleClient,
    settings: Settings,
) -> None:
    allowed = await user_can_access_app(
        db=db,
        user_id=user.sub,
        app_id=app_id,
        http=http,
        cache=cache,
        settings=settings,
    )
    if not allowed:
        raise HTTPException(status_code=403, detail="无权访问该 App")


@router.get(
    "/{app_id}/files",
    response_model=list[FileMeta],
    summary="List the KB files an authorized user can see for the given App",
)
async def list_kb_files(
    app_id: str = Path(..., min_length=1),
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    http: httpx.AsyncClient = Depends(get_dify_client),
    console: DifyConsoleClient = Depends(get_dify_console_client),
    client: FastGPTReadOnlyClient = Depends(_get_fastgpt_client),
) -> list[FileMeta]:
    await _ensure_access(
        app_id=app_id, user=user, db=db, http=http, cache=console, settings=settings,
    )
    dataset_ids = await _datasets_for_app(db, app_id)
    if not dataset_ids:
        return []

    try:
        files: list[FileMeta] = []
        for dataset_id in dataset_ids:
            collections = await client.list_collections(dataset_id)
            for col in collections:
                files.extend(await client.get_collection_files(col.collection_id))
    except FastGPTError as exc:
        raise translate_to_http_exception(exc) from exc

    log.info(
        "GET /v1/kbs/%s/files user=%s datasets=%d files=%d",
        app_id, user.sub, len(dataset_ids), len(files),
    )
    return files


@router.get(
    "/{app_id}/files/{file_id}/download",
    summary="Stream a single KB file (authorized + reverse-checked against App's KBs)",
)
async def download_kb_file(
    app_id: str = Path(..., min_length=1),
    file_id: str = Path(..., min_length=1),
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    http: httpx.AsyncClient = Depends(get_dify_client),
    console: DifyConsoleClient = Depends(get_dify_console_client),
    client: FastGPTReadOnlyClient = Depends(_get_fastgpt_client),
) -> StreamingResponse:
    await _ensure_access(
        app_id=app_id, user=user, db=db, http=http, cache=console, settings=settings,
    )
    dataset_ids = await _datasets_for_app(db, app_id)
    if not dataset_ids:
        raise HTTPException(status_code=403, detail="无权访问该文件")

    try:
        target: Optional[FileMeta] = None
        for dataset_id in dataset_ids:
            for col in await client.list_collections(dataset_id):
                for meta in await client.get_collection_files(col.collection_id):
                    if meta.file_id == file_id:
                        target = meta
                        break
                if target:
                    break
            if target:
                break

        if target is None:
            raise HTTPException(status_code=403, detail="无权访问该文件")

        filename_header = target.original_filename or file_id
        stream = client.download_file(file_id)

        async def _iter():
            async for chunk in stream:
                yield chunk

        return StreamingResponse(
            _iter(),
            media_type=target.content_type or "application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="{filename_header}"',
            },
        )
    except FastGPTError as exc:
        raise translate_to_http_exception(exc) from exc
