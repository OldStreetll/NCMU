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
import yaml
from fastapi import APIRouter, Depends, File, HTTPException, Path, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ncmu_backend.admin.dsl.schemas import DslCandidateOut, DslExportRequest
from ncmu_backend.admin.dsl.services import (
    build_manifest,
    export_zip_filename,
    safe_filename,
    upsert_app_cache,
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


# ===================================================================== #
# TASK-PE-11 — DSL import (single YAML or ZIP)
# ===================================================================== #
_MAX_YAML_BYTES = 5 * 1024 * 1024    # AC#4 — single YAML 5 MB
_MAX_ZIP_BYTES = 20 * 1024 * 1024    # AC#4 — ZIP bundle 20 MB (compressed)
# REWORK-PE-11-INDEP — unbounded-decompression (zip-bomb / fan-out DoS) guards.
# A 20 MB ZIP can deflate to GBs and/or carry thousands of members, so the
# compressed cap above is not enough on its own:
_MAX_ZIP_ENTRY_BYTES = 5 * 1024 * 1024    # per .yaml member, uncompressed
_MAX_ZIP_MEMBERS = 50                      # .yaml/.yml fan-out cap (outbound calls)
_MAX_ZIP_TOTAL_BYTES = 100 * 1024 * 1024   # cumulative decompressed budget
# Dify import statuses that mean "app was created" (spike 2026-05-30).
_OK_IMPORT_STATUSES = {"completed", "completed-with-warnings"}


def _is_zip_upload(file: UploadFile) -> bool:
    name = (file.filename or "").lower()
    if name.endswith(".zip"):
        return True
    if name.endswith((".yaml", ".yml")):
        return False
    return file.content_type in (
        "application/zip",
        "application/x-zip-compressed",
        "multipart/x-zip",
    )


async def _import_one_yaml(
    *,
    filename: str,
    yaml_str: str,
    dify: DifyConsoleClient,
    db: AsyncSession,
    settings: Settings,
    http: httpx.AsyncClient,
) -> dict[str, object]:
    """Import one DSL YAML; returns a classification dict:

    {"ok": True, "app_id", "name"}                          — clean success
    {"ok": False, "app_id"?, "failed": {<field-level row>}} — failure row

    ``app_id`` is present on the failure dict for the PLUGIN_MISSING case —
    the app *was* created in Dify (we never auto-delete; admin installs the
    plugin then re-syncs), so the caller still caches it.
    """
    dify_kw = {
        "http": http,
        "base_url": settings.DIFY_BASE_URL,
        "api_key": settings.DIFY_CONSOLE_API_KEY,
        "tenant_id": settings.DIFY_TENANT_ID,
    }

    # 1. safe_load — injection-safe parse + structural validation.
    try:
        parsed = yaml.safe_load(yaml_str)
    except yaml.YAMLError as exc:
        return {
            "ok": False,
            "failed": {
                "filename": filename,
                "error_code": "YAML_PARSE",
                "error_message": str(exc)[:500],
            },
        }
    if not isinstance(parsed, dict) or not isinstance(parsed.get("app"), dict):
        return {
            "ok": False,
            "failed": {
                "filename": filename,
                "error_code": "YAML_PARSE",
                "error_message": "missing top-level 'app' mapping",
            },
        }
    app_meta = parsed["app"]
    name = app_meta.get("name") or filename
    yaml_mode = app_meta.get("mode") or "chat"

    # 2. import (4xx-tolerant — failed body is data, not an exception).
    resp = await dify.import_app(yaml_content=yaml_str, **dify_kw)
    status_ = resp.get("status")

    # 3. pending → confirm.
    if status_ == "pending" and resp.get("id"):
        resp = await dify.confirm_import(import_id=str(resp["id"]), **dify_kw)
        status_ = resp.get("status")

    app_id = resp.get("app_id")
    if status_ not in _OK_IMPORT_STATUSES or not app_id:
        return {
            "ok": False,
            "failed": {
                "filename": filename,
                "error_code": "DIFY_IMPORT",
                "error_message": resp.get("error") or f"import status={status_}",
            },
        }
    app_id = str(app_id)
    mode = resp.get("app_mode") or yaml_mode

    # 4. app created → cache it (PE-11 ② single-app upsert, no full sync).
    await upsert_app_cache(db, dify_app_id=app_id, name=name, mode=mode)

    # 5. dependency check — missing plugins make this a field-level FAILED row
    #    (AC#5/#6), but the app stays (never auto-deleted; admin installs the
    #    plugin then re-syncs).
    leaked = await dify.check_app_dependencies(app_id=app_id, **dify_kw)
    if leaked:
        return {
            "ok": False,
            "app_id": app_id,
            "failed": {
                "filename": filename,
                "error_code": "PLUGIN_MISSING",
                "app_id": app_id,
                "name": name,
                "plugin_missing": leaked,
                "error_message": (
                    "App created but required plugins are missing; "
                    "install them then re-sync."
                ),
            },
        }
    return {"ok": True, "app_id": app_id, "name": name}


@router.post(
    "/api/v1/ncmu/admin/apps/dsl-import",
    summary="Import App DSL (single YAML or ZIP auto-detect) — partial OK → 200",
)
async def import_dsl(
    file: UploadFile = File(...),
    user: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    http: httpx.AsyncClient = Depends(get_dify_client),
    dify: DifyConsoleClient = Depends(get_dify_console_client),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Single YAML or ZIP → per-App import + check-dependencies + cache sync.

    Partial success is normal: each App is independent, so the response is
    always 200 ``{total, succeeded[], failed[]}`` (per-App failures are
    field-level rows, not a whole-request 4xx). Hard 4xx only for
    request-level faults: oversize (413), corrupt ZIP / bad encoding (400).
    """
    content = await file.read()
    is_zip = _is_zip_upload(file)

    limit = _MAX_ZIP_BYTES if is_zip else _MAX_YAML_BYTES
    if len(content) > limit:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail={
                "code": 1003,
                "message": f"file exceeds {limit // (1024 * 1024)}MB limit",
            },
        )

    succeeded: list[dict[str, object]] = []
    failed: list[dict[str, object]] = []

    # Collect (filename, yaml_str) for processable members. Per-entry faults
    # (oversize / bad encoding) go straight to ``failed`` so one bad member
    # never aborts the batch; request-level DoS guards (member count, total
    # decompressed budget) raise 413.
    entries: list[tuple[str, str]] = []
    total_members = 1
    if is_zip:
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                members = [
                    zi for zi in zf.infolist()
                    if zi.filename.endswith((".yaml", ".yml"))
                ]
                total_members = len(members)
                if total_members == 0:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail={"code": 1003,
                                "message": "ZIP contains no .yaml/.yml files"},
                    )
                # Guard 2 — fan-out cap (each member ⇒ outbound Dify calls).
                if total_members > _MAX_ZIP_MEMBERS:
                    raise HTTPException(
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                        detail={"code": 1003,
                                "message": f"ZIP has too many YAML members "
                                           f"(>{_MAX_ZIP_MEMBERS})"},
                    )
                decompressed_total = 0
                for zi in members:
                    # Guard 1a — cheap header pre-check (skip obvious bombs).
                    if zi.file_size > _MAX_ZIP_ENTRY_BYTES:
                        failed.append({
                            "filename": zi.filename,
                            "error_code": "ENTRY_TOO_LARGE",
                            "error_message": (
                                f"uncompressed size exceeds "
                                f"{_MAX_ZIP_ENTRY_BYTES // (1024 * 1024)}MB"
                            ),
                        })
                        continue
                    # Guard 1b — bounded REAL read: the file_size header can be
                    # forged, so read at most per-entry-limit+1 bytes via the
                    # stream and reject if it overflows (caps memory too).
                    with zf.open(zi) as fh:
                        raw = fh.read(_MAX_ZIP_ENTRY_BYTES + 1)
                    if len(raw) > _MAX_ZIP_ENTRY_BYTES:
                        failed.append({
                            "filename": zi.filename,
                            "error_code": "ENTRY_TOO_LARGE",
                            "error_message": (
                                f"uncompressed size exceeds "
                                f"{_MAX_ZIP_ENTRY_BYTES // (1024 * 1024)}MB"
                            ),
                        })
                        continue
                    # Guard 3 — cumulative decompressed budget (forged-header
                    # backstop across many small-but-lying members).
                    decompressed_total += len(raw)
                    if decompressed_total > _MAX_ZIP_TOTAL_BYTES:
                        raise HTTPException(
                            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                            detail={"code": 1003,
                                    "message": f"ZIP decompressed total exceeds "
                                               f"{_MAX_ZIP_TOTAL_BYTES // (1024 * 1024)}MB"},
                        )
                    try:
                        entries.append((zi.filename, raw.decode("utf-8")))
                    except UnicodeDecodeError:
                        failed.append({
                            "filename": zi.filename,
                            "error_code": "YAML_PARSE",
                            "error_message": "entry is not valid UTF-8",
                        })
        except zipfile.BadZipFile:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": 1003, "message": "invalid ZIP archive"},
            )
    else:
        try:
            entries.append((file.filename or "upload.yaml", content.decode("utf-8")))
        except UnicodeDecodeError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": 1003, "message": "file is not valid UTF-8"},
            )

    for filename, yaml_str in entries:
        result = await _import_one_yaml(
            filename=filename, yaml_str=yaml_str,
            dify=dify, db=db, settings=settings, http=http,
        )
        if result["ok"]:
            succeeded.append({
                "filename": filename,
                "app_id": result["app_id"],
                "name": result["name"],
            })
        else:
            failed.append(result["failed"])

    # Persist the cache upserts done by successful / plugin-missing imports.
    await db.commit()

    if any(r.get("error_code") == "PLUGIN_MISSING" for r in failed):
        log.warning(
            "admin %s DSL-import: %d app(s) created with MISSING PLUGINS",
            user.sub,
            sum(1 for r in failed if r.get("error_code") == "PLUGIN_MISSING"),
        )

    # total = every YAML member seen (processed ∪ rejected-at-collection), so
    # succeeded + failed always sums to total.
    return {"total": total_members, "succeeded": succeeded, "failed": failed}
