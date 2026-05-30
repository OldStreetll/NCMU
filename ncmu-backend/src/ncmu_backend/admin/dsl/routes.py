"""Admin DSL export endpoints (TASK-PE-10).

Routes (all gated by ``Depends(require_admin)`` — enforced + auto-audited
by ``tests/admin/test_require_admin_audit.py`` since every path starts with
``/api/v1/ncmu/admin``):

    GET  /api/v1/ncmu/admin/apps/dsl-candidates  — picker source (cache table)
    GET  /api/v1/ncmu/admin/apps/{app_id}/dsl    — single-App YAML
    POST /api/v1/ncmu/admin/apps/dsl-export       — multi-App ZIP (+ MANIFEST.json)

``dsl-candidates`` is a thin read over the local ``dify_apps`` cache so the
SPA export page has an App list to multi-select; PE-07's admin apps-list
(with ``is_active``) is not on this branch yet, so PE-10 ships its own
minimal source and defaults the page to "select all" (no active filter).

Path/body ``app_id`` values are ``dify_apps.dify_app_id`` strings (the PK)
— NOT a separate UUID (there is no UUID ``id`` column). The Dify export
endpoint keys on this same id, so we proxy it straight through; the DB is
consulted only to resolve a display name for the download filename.
"""
from __future__ import annotations

import io
import json
import logging
import zipfile
from datetime import datetime, timezone
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, Path, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ncmu_backend.admin.dsl.schemas import DslCandidateOut, DslExportRequest
from ncmu_backend.admin.dsl.services import (
    build_manifest,
    export_zip_filename,
    safe_filename,
    zip_entry_name,
)
from ncmu_backend.apps.dify_console_client import DifyConsoleClient
from ncmu_backend.apps.routes import get_dify_console_client
from ncmu_backend.auth.deps import CurrentUser, require_admin
from ncmu_backend.db.models import DifyApp
from ncmu_backend.db.session import get_db
from ncmu_backend.deps import Settings, get_settings
from ncmu_backend.main import get_dify_client

log = logging.getLogger("ncmu_backend.admin.dsl.routes")

router = APIRouter(tags=["admin-dsl"])


def _content_disposition(filename: str, *, ascii_fallback: str) -> str:
    """RFC 6266 dual-form Content-Disposition.

    ``filename`` may carry CJK (中文 App 名兼容); a raw ``filename="..."``
    with non-ASCII bytes is mangled by httpx/browsers, so we emit an ASCII
    fallback plus the percent-encoded ``filename*=UTF-8''...`` that modern
    browsers prefer.
    """
    return (
        f'attachment; filename="{ascii_fallback}"; '
        f"filename*=UTF-8''{quote(filename)}"
    )


@router.get(
    "/api/v1/ncmu/admin/apps/dsl-candidates",
    response_model=list[DslCandidateOut],
    summary="List Apps available for DSL export (local dify_apps cache)",
)
async def list_dsl_candidates(
    _: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[DslCandidateOut]:
    """Picker source for the export page — newest-synced first.

    Reads the local cache only (no live Dify round-trip): the page just
    needs id/name/mode to render the multi-select. An empty list (cache
    never synced) renders an empty table, not an error.
    """
    rows = (
        await db.execute(select(DifyApp).order_by(DifyApp.updated_at.desc()))
    ).scalars().all()
    return [
        DslCandidateOut(dify_app_id=r.dify_app_id, name=r.name, mode=r.mode)
        for r in rows
    ]


@router.get(
    "/api/v1/ncmu/admin/apps/{app_id}/dsl",
    summary="Export one App's DSL as YAML (proxies Dify Console export)",
)
async def export_single_dsl(
    app_id: str = Path(pattern=r"^[A-Za-z0-9-]+$"),
    include_secret: bool = False,
    user: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    http: httpx.AsyncClient = Depends(get_dify_client),
    dify: DifyConsoleClient = Depends(get_dify_console_client),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Single-App YAML. ``app_id`` is the Dify App id (= dify_app_id).

    Unknown id → Dify upstream 404 → ``HTTPException(502, code=1003)`` via
    ``export_app``/``_request_dify_json`` (NOT a local 404 — we never claim
    to know the full App universe; the cache may lag a fresh Dify App).
    """
    if include_secret:
        log.warning(
            "admin %s exported App %s DSL WITH SECRETS", user.sub, app_id
        )
    yaml_str = await dify.export_app(
        app_id=app_id,
        http=http,
        base_url=settings.DIFY_BASE_URL,
        api_key=settings.DIFY_CONSOLE_API_KEY,
        tenant_id=settings.DIFY_TENANT_ID,
        include_secret=include_secret,
    )
    cached = await db.get(DifyApp, app_id)
    display = zip_entry_name(cached.name, app_id) if cached else f"{app_id}.yaml"
    return Response(
        content=yaml_str,
        media_type="application/yaml",
        headers={
            "Content-Disposition": _content_disposition(
                display, ascii_fallback=f"{app_id}.yaml"
            )
        },
    )


@router.post(
    "/api/v1/ncmu/admin/apps/dsl-export",
    summary="Export multiple Apps' DSL as a streamed ZIP (+ MANIFEST.json)",
)
async def export_multi_dsl(
    body: DslExportRequest,
    user: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    http: httpx.AsyncClient = Depends(get_dify_client),
    dify: DifyConsoleClient = Depends(get_dify_console_client),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    """Bundle each selected App's DSL into one ZIP.

    Per-App entry: ``{safe_name}_{dify_app_id}.yaml`` (name resolved from
    the cache for a friendly filename; falls back to the id when the App
    isn't cached — the export itself still works since Dify keys on the id).
    A trailing ``MANIFEST.json`` records the exported ids + secret flag +
    timestamp. Any single App's upstream failure (502) aborts the whole
    request — partial/silently-truncated bundles would mislead the admin.
    """
    now = datetime.now(timezone.utc)
    if body.include_secret:
        log.warning(
            "admin %s exported %d app(s) DSL WITH SECRETS",
            user.sub,
            len(body.app_ids),
        )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for app_id in body.app_ids:
            yaml_str = await dify.export_app(
                app_id=app_id,
                http=http,
                base_url=settings.DIFY_BASE_URL,
                api_key=settings.DIFY_CONSOLE_API_KEY,
                tenant_id=settings.DIFY_TENANT_ID,
                include_secret=body.include_secret,
            )
            cached = await db.get(DifyApp, app_id)
            name = cached.name if cached else app_id
            zf.writestr(zip_entry_name(name, app_id), yaml_str)
        manifest = build_manifest(
            app_ids=body.app_ids,
            include_secret=body.include_secret,
            exported_at_iso=now.isoformat(),
        )
        zf.writestr(
            "MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2)
        )
    buf.seek(0)

    fname = export_zip_filename(now.strftime("%Y%m%d-%H%M"))
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )
