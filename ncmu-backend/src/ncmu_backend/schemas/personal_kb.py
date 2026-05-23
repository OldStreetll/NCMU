"""Personal KB schemas — Pydantic models + module-level constants.

Lives at the top-level ``schemas/`` package (siblings of ``schemas/apps.py``,
``schemas/admin.py``, ``schemas/auth.py``) so it matches the existing
ncmu-backend pattern — test imports read ``from ncmu_backend.schemas.personal_kb
import ApplicationIn, ApplicationOut, DispatchIn, RejectIn``.

Constants:
- ``ALLOWED_EXTENSIONS`` — Q2-B 8 formats (txt/docx/csv/xlsx/pdf/md/html/pptx).
- ``CHUNK_SIZE`` — multipart streaming chunk for save path (8192 bytes).
- ``MAX_FILES_PER_APPLICATION`` — Q2-B (10).
- ``MAX_FILE_SIZE_BYTES`` — Q2-B (50 MB).
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


ALLOWED_EXTENSIONS: frozenset[str] = frozenset(
    {"txt", "docx", "csv", "xlsx", "pdf", "md", "html", "pptx"}
)
CHUNK_SIZE: int = 8192
MAX_FILES_PER_APPLICATION: int = 10
MAX_FILE_SIZE_BYTES: int = 50 * 1024 * 1024  # 50 MB


ApplicationStatus = Literal[
    "pending", "in_progress", "done", "rejected", "cancelled"
]


class ApplicationIn(BaseModel):
    """Request body of POST /api/v1/ncmu/personal-kb/applications (multipart form fields).

    The actual ``files`` UploadFile list rides separately on the multipart
    request — this model captures the JSON-shaped metadata fields the form
    carries alongside the files.
    """

    kb_name_suggested: Optional[str] = Field(
        default=None, max_length=200,
        description="员工建议的知识库名（admin 可在 dispatch 阶段覆盖）",
    )
    description: Optional[str] = Field(
        default=None,
        description="申请说明（自由文本，admin 中央化建库时参考）",
    )


class FileMeta(BaseModel):
    """Per-file metadata exposed by the admin detail endpoint."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    original_filename: str
    file_size_bytes: int
    content_type: Optional[str] = None
    sha256: Optional[str] = None
    uploaded_at: datetime


class ApplicationOut(BaseModel):
    """Response model for the application endpoints (no files relation).

    Kept slim so ``model_validate(app_row)`` doesn't accidentally trigger
    a SQLAlchemy ``selectin`` lazy-load on the ``files`` relationship —
    that's how async-session callers run into ``MissingGreenlet``. The
    admin detail endpoint composes :class:`ApplicationDetailOut`
    explicitly when it wants the file list.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    kb_name_suggested: Optional[str] = None
    description: Optional[str] = None
    status: ApplicationStatus
    fastgpt_dataset_id: Optional[str] = None
    dify_app_id: Optional[str] = None
    admin_processed_by: Optional[UUID] = None
    admin_processed_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None
    created_at: datetime


class ApplicationDetailOut(ApplicationOut):
    """Admin detail view — includes the eagerly-loaded file metadata list."""

    files: list[FileMeta] = []


class DispatchIn(BaseModel):
    """Request body of POST /api/v1/ncmu/admin/personal-kb/applications/{id}/dispatch."""

    dataset_id: str = Field(
        ..., min_length=1,
        description="FastGPT dataset._id — written to dify_external_kb_configs.fastgpt_dataset_id",
    )
    dify_app_id: str = Field(
        ..., min_length=1, max_length=64,
        description="Dify private chat App id (admin pre-created in Dify Console)",
    )
    kb_name_final: str = Field(
        ..., min_length=1, max_length=200,
        description="Final external_kb_name (cross-table unique constraint enforced by DB)",
    )


class RejectIn(BaseModel):
    """Request body of POST /api/v1/ncmu/admin/personal-kb/applications/{id}/reject."""

    rejection_reason: str = Field(
        ..., min_length=1,
        description="为什么拒绝（员工可见 / 落 personal_kb_applications.rejection_reason）",
    )
