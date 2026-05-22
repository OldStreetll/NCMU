"""Admin endpoints for personal KB applications (Phase 2C TASK-PC2-A AC#2).

Six endpoints — all gated by ``require_admin``:

- GET  /admin/personal-kb/applications                            (list)
- GET  /admin/personal-kb/applications/{id}                       (detail + files)
- GET  /admin/personal-kb/applications/{id}/files/{file_id}/download
- POST /admin/personal-kb/applications/{id}/claim                 (pending → in_progress)
- POST /admin/personal-kb/applications/{id}/dispatch              (in_progress → done)
- POST /admin/personal-kb/applications/{id}/reject

The ``dispatch`` endpoint is the heart of the admin path — it stitches a
freshly-built FastGPT dataset into the Dify side by writing four rows in
one transaction (3 INSERTs + 1 UPDATE). Failure of any of the writes
rolls the whole set back so we never leave an orphan ``app_owners``
binding behind.
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, status as http_status
from fastapi.responses import StreamingResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ncmu_backend.deps import CurrentUser, get_db, require_admin
from ncmu_backend.personal_kb.application_service import (
    ApplicationNotFoundError,
    InvalidTransitionError,
    claim_application,
    dispatch_application,
    get_application_detail,
    get_application_file,
    list_all_applications,
    reject_application,
)
from ncmu_backend.personal_kb.file_storage import StorageBackend, StorageRef
from ncmu_backend.personal_kb.routes import get_storage_backend
from ncmu_backend.schemas.personal_kb import (
    ApplicationDetailOut,
    ApplicationOut,
    DispatchIn,
    FileMeta,
    RejectIn,
)


log = logging.getLogger("ncmu_backend.personal_kb.admin_routes")

router = APIRouter(prefix="/admin/personal-kb", tags=["personal-kb-admin"])


# --------------------------------------------------------------------- #
# GET /admin/personal-kb/applications — list all
# --------------------------------------------------------------------- #
@router.get(
    "/applications",
    response_model=list[ApplicationOut],
    summary="（admin）列出所有用户的建库申请",
)
async def admin_list_applications(
    status: Optional[str] = None,  # ?status=pending|in_progress|done|rejected|cancelled
    _: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[ApplicationOut]:
    try:
        rows = await list_all_applications(db, status=status)
    except ValueError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return [ApplicationOut.model_validate(r) for r in rows]


# --------------------------------------------------------------------- #
# GET /admin/personal-kb/applications/{id} — detail with files
# --------------------------------------------------------------------- #
@router.get(
    "/applications/{application_id}",
    response_model=ApplicationDetailOut,
    summary="（admin）查看单个申请详情（含文件清单元数据）",
)
async def admin_get_application(
    application_id: uuid.UUID,
    _: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ApplicationDetailOut:
    try:
        app_row, files = await get_application_detail(
            db, application_id=application_id, with_files=True
        )
    except ApplicationNotFoundError:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="申请不存在")
    base = ApplicationOut.model_validate(app_row)
    return ApplicationDetailOut(
        **base.model_dump(),
        files=[FileMeta.model_validate(f) for f in files],
    )


# --------------------------------------------------------------------- #
# GET /admin/personal-kb/applications/{id}/files/{file_id}/download
# --------------------------------------------------------------------- #
@router.get(
    "/applications/{application_id}/files/{file_id}/download",
    summary="（admin）下载原始文档",
)
async def admin_download_file(
    application_id: uuid.UUID,
    file_id: uuid.UUID,
    _: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    storage: StorageBackend = Depends(get_storage_backend),
) -> StreamingResponse:
    try:
        file_row = await get_application_file(
            db, application_id=application_id, file_id=file_id
        )
    except ApplicationNotFoundError:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND, detail="文件不存在"
        )

    ref = StorageRef(backend="localfs", path=file_row.storage_path)
    try:
        stream = storage.open(ref)
    except FileNotFoundError:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="原始文档已被移除",
        )

    # RFC 5987 / UTF-8 filename so Chinese names render correctly in
    # browser save dialogs.
    encoded = quote(file_row.original_filename)
    return StreamingResponse(
        stream,
        media_type=file_row.content_type or "application/octet-stream",
        headers={
            "Content-Disposition": (
                f"attachment; filename={encoded}; "
                f"filename*=UTF-8''{encoded}"
            )
        },
    )


# --------------------------------------------------------------------- #
# POST /admin/personal-kb/applications/{id}/claim
# --------------------------------------------------------------------- #
@router.post(
    "/applications/{application_id}/claim",
    response_model=ApplicationOut,
    summary="（admin）认领申请，开始处理",
)
async def admin_claim_application(
    application_id: uuid.UUID,
    admin_user: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ApplicationOut:
    try:
        app_row = await claim_application(
            db,
            admin_user_id=uuid.UUID(admin_user.sub),
            application_id=application_id,
        )
    except ApplicationNotFoundError:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="申请不存在")
    except InvalidTransitionError:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="仅待处理状态可认领",
        )
    log.info(
        "POST /admin/personal-kb/applications/%s/claim admin=%s",
        application_id, admin_user.sub,
    )
    return ApplicationOut.model_validate(app_row)


# --------------------------------------------------------------------- #
# POST /admin/personal-kb/applications/{id}/dispatch
# --------------------------------------------------------------------- #
@router.post(
    "/applications/{application_id}/dispatch",
    response_model=ApplicationOut,
    summary="（admin）下发申请：建 KB 配置 + 绑定 + owner",
)
async def admin_dispatch_application(
    application_id: uuid.UUID,
    body: DispatchIn,
    admin_user: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ApplicationOut:
    try:
        app_row = await dispatch_application(
            db,
            admin_user_id=uuid.UUID(admin_user.sub),
            application_id=application_id,
            dataset_id=body.dataset_id,
            dify_app_id=body.dify_app_id,
            kb_name_final=body.kb_name_final,
        )
    except ApplicationNotFoundError:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="申请不存在")
    except InvalidTransitionError:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="请先认领申请",
        )
    except IntegrityError as exc:
        # Duplicate ``external_kb_name`` / FK violation — surface a 409
        # so the admin UI can prompt for a different ``kb_name_final``.
        log.warning("dispatch integrity violation: %s", exc)
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="KB 名称冲突或外部资源不可用",
        )
    log.info(
        "POST /admin/personal-kb/applications/%s/dispatch admin=%s dify_app=%s",
        application_id, admin_user.sub, body.dify_app_id,
    )
    return ApplicationOut.model_validate(app_row)


# --------------------------------------------------------------------- #
# POST /admin/personal-kb/applications/{id}/reject
# --------------------------------------------------------------------- #
@router.post(
    "/applications/{application_id}/reject",
    response_model=ApplicationOut,
    summary="（admin）拒绝申请",
)
async def admin_reject_application(
    application_id: uuid.UUID,
    body: RejectIn,
    admin_user: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ApplicationOut:
    try:
        app_row = await reject_application(
            db,
            admin_user_id=uuid.UUID(admin_user.sub),
            application_id=application_id,
            rejection_reason=body.rejection_reason,
        )
    except ApplicationNotFoundError:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="申请不存在")
    except InvalidTransitionError:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="仅待处理 / 处理中状态可拒绝",
        )
    log.info(
        "POST /admin/personal-kb/applications/%s/reject admin=%s",
        application_id, admin_user.sub,
    )
    return ApplicationOut.model_validate(app_row)
