"""Employee endpoints for personal KB applications.

Three endpoints (Phase 2C TASK-PC2-A AC#1):

- POST   /api/v1/ncmu/personal-kb/applications              (multipart upload)
- GET    /api/v1/ncmu/personal-kb/applications              (list own; ?status= filter)
- DELETE /api/v1/ncmu/personal-kb/applications/{id}         (withdraw a pending app)

Multipart upload streams each file through ``LocalFsBackend.save`` so a
50 MB upload never sits in process RAM — chunks fly straight from the
HTTP wire to the disk write buffer.

File-validation rules (Q2-B 字面):
- at most ``MAX_FILES_PER_APPLICATION`` files per application
- each file at most ``MAX_FILE_SIZE_BYTES`` bytes
- each filename extension must be in ``ALLOWED_EXTENSIONS``

Filenames keep their original on-disk basename for the admin download
endpoint's ``Content-Disposition`` header.
"""
from __future__ import annotations

import hashlib
import logging
import os
import uuid
from pathlib import Path
from typing import AsyncIterator, Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status as http_status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from ncmu_backend.deps import CurrentUser, get_current_user, get_db
from ncmu_backend.schemas.personal_kb import (
    ALLOWED_EXTENSIONS,
    CHUNK_SIZE,
    MAX_FILE_SIZE_BYTES,
    MAX_FILES_PER_APPLICATION,
    ApplicationOut,
)
from ncmu_backend.personal_kb.application_service import (
    ApplicationNotFoundError,
    FileSpec,
    InvalidTransitionError,
    create_application,
    list_user_applications,
    withdraw_application,
)
from ncmu_backend.personal_kb.file_storage import (
    LocalFsBackend,
    StorageBackend,
    StorageRef,
)


log = logging.getLogger("ncmu_backend.personal_kb.routes")

router = APIRouter(prefix="/api/v1/ncmu/personal-kb", tags=["personal-kb"])


# --------------------------------------------------------------------- #
# Storage backend DI — tests override via app.dependency_overrides
# --------------------------------------------------------------------- #
# Aligned with PC2-D plan §4.4 字面 ``/var/lib/ncmu/personal-kb`` so the
# default path stays consistent the moment PC2-D's Settings field
# (``PERSONAL_KB_STORAGE_ROOT``) lands and the env-direct fallback gets
# replaced with ``settings.PERSONAL_KB_STORAGE_ROOT`` (FOLLOWUP-3).
_DEFAULT_STORAGE_ROOT = "/var/lib/ncmu/personal-kb"


def get_storage_backend() -> StorageBackend:
    """FastAPI dependency: build (or fetch a cached) StorageBackend.

    Reads ``PERSONAL_KB_STORAGE_ROOT`` from env each call so a dev /
    test deploy can rotate the directory without restarting. The
    LocalFsBackend ctor is cheap (it doesn't open files), so re-building
    per request is fine.
    """
    root = os.environ.get("PERSONAL_KB_STORAGE_ROOT", _DEFAULT_STORAGE_ROOT)
    return LocalFsBackend(root)


# --------------------------------------------------------------------- #
# POST — create application with multipart upload
# --------------------------------------------------------------------- #
@router.post(
    "/applications",
    response_model=ApplicationOut,
    status_code=http_status.HTTP_201_CREATED,
    summary="提交一个建库申请 + 上传原始文档",
)
async def create_application_endpoint(
    files: list[UploadFile] = File(..., description="原始文档清单"),
    kb_name_suggested: Optional[str] = Form(default=None),
    description: Optional[str] = Form(default=None),
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageBackend = Depends(get_storage_backend),
) -> ApplicationOut:
    # ── Q2-B pre-validation (quantity / per-file size / extension) ──
    if len(files) > MAX_FILES_PER_APPLICATION:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"最多上传 {MAX_FILES_PER_APPLICATION} 个文件",
        )
    for f in files:
        # ``size`` is populated by Starlette from the multipart
        # part's Content-Length — None when the client omits it.
        if f.size is not None and f.size > MAX_FILE_SIZE_BYTES:
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"单文件不能超过 {MAX_FILE_SIZE_BYTES // (1024 * 1024)} MB"
                ),
            )
        ext = Path(f.filename or "").suffix.lstrip(".").lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"不支持的文件格式 .{ext}",
            )

    user_uuid = uuid.UUID(user.sub)
    application_id = uuid.uuid4()
    file_specs: list[FileSpec] = []

    # ── Stage every file on disk first so the storage path lives at
    # <storage_root>/<application_id>/<file_id>_<name>. If any save
    # fails (oversize-late or storage I/O), we sweep ALL the prior
    # blobs we've already written so the upload looks transactional
    # to the client (spec §4.1 cleanup 字面 / C2).
    try:
        for upload in files:
            file_id = uuid.uuid4()
            sha_obj = hashlib.sha256()

            async def _stream(u: UploadFile = upload) -> AsyncIterator[bytes]:
                # Read in fixed-size chunks so a 50 MB body never lands
                # in RAM as a single bytes blob. ``await u.read()``
                # (without size) is the *forbidden* pattern in Q2-B / AC#6.
                while True:
                    chunk = await u.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    sha_obj.update(chunk)
                    yield chunk

            ref = await storage.save(
                application_id, file_id, upload.filename or "unnamed", _stream()
            )
            # ``size`` may have been None pre-upload; count bytes via
            # the filesystem now that the blob has landed.
            size_bytes = (
                upload.size
                if upload.size is not None
                else Path(ref.path).stat().st_size
            )
            if size_bytes > MAX_FILE_SIZE_BYTES:
                # Late discovery — current blob is cleaned by the
                # except-handler below alongside any prior blobs.
                file_specs.append(
                    FileSpec(
                        file_id=file_id,
                        original_filename=upload.filename or "unnamed",
                        storage_path=ref.path,
                        file_size_bytes=size_bytes,
                        content_type=upload.content_type,
                        sha256=sha_obj.hexdigest(),
                    )
                )
                raise HTTPException(
                    status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=(
                        f"单文件不能超过 {MAX_FILE_SIZE_BYTES // (1024 * 1024)} MB"
                    ),
                )
            file_specs.append(
                FileSpec(
                    file_id=file_id,
                    original_filename=upload.filename or "unnamed",
                    storage_path=ref.path,
                    file_size_bytes=size_bytes,
                    content_type=upload.content_type,
                    sha256=sha_obj.hexdigest(),
                )
            )
    except BaseException:
        # Sweep every blob we already wrote — DB has nothing committed
        # yet, so a sweep here leaves zero on-disk orphans.
        for spec in file_specs:
            try:
                await storage.delete(
                    StorageRef(backend="localfs", path=spec.storage_path)
                )
            except Exception:
                log.warning(
                    "cleanup-after-fail: failed to delete %s", spec.storage_path
                )
        raise

    # ── INSERT through the service layer (single source of truth for
    # the row + 1:N files. Service mints a UUID by default; pass our
    # pre-allocated one so the DB row id == the on-disk staging dir.
    app_row = await create_application(
        db,
        user_id=user_uuid,
        kb_name_suggested=kb_name_suggested,
        description=description,
        files=file_specs,
        application_id=application_id,
    )

    log.info(
        "POST /api/v1/ncmu/personal-kb/applications user=%s application_id=%s files=%d",
        user.sub, app_row.id, len(file_specs),
    )
    return ApplicationOut.model_validate(app_row)


# --------------------------------------------------------------------- #
# GET — list current user's applications, optional ?status= filter
# --------------------------------------------------------------------- #
@router.get(
    "/applications",
    response_model=list[ApplicationOut],
    summary="列出当前用户的建库申请",
)
async def list_my_applications(
    status: Optional[str] = None,  # ?status=pending|in_progress|done|rejected|cancelled
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ApplicationOut]:
    try:
        rows = await list_user_applications(
            db,
            user_id=uuid.UUID(user.sub),
            status=status,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return [ApplicationOut.model_validate(r) for r in rows]


# --------------------------------------------------------------------- #
# DELETE — withdraw a pending application
# --------------------------------------------------------------------- #
@router.delete(
    "/applications/{application_id}",
    status_code=http_status.HTTP_204_NO_CONTENT,
    summary="撤回一个待处理的建库申请",
)
async def withdraw_my_application(
    application_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    try:
        await withdraw_application(
            db, user_id=uuid.UUID(user.sub), application_id=application_id
        )
    except ApplicationNotFoundError:
        # Non-owner 也走 404，避免 id 枚举攻击 (spec §3.1 字面).
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="申请不存在",
        )
    except InvalidTransitionError:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="仅待处理状态可撤回",
        )
    log.info(
        "DELETE /api/v1/ncmu/personal-kb/applications/%s user=%s",
        application_id, user.sub,
    )
