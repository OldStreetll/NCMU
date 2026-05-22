"""Application service — state machine + DB operations for personal KB.

The state machine is captured by ``LEGAL_TRANSITIONS`` so reviewers can
diff the 5 legal edges against errata-12 §3.1 in a single grep. Any
transition not listed there raises :class:`InvalidTransitionError`
(``ValueError`` subclass) — the routes layer maps that to HTTP 409.

The 5 legal transitions:
    1. pending      → in_progress  (admin claim)
    2. pending      → rejected     (admin reject before claim)
    3. pending      → cancelled    (employee withdraw)
    4. in_progress  → done         (admin dispatch)
    5. in_progress  → rejected     (admin reject after claim)

Terminal states (no outgoing edges): done / rejected / cancelled.

This module never touches the filesystem — routes pre-stage files via
:class:`StorageBackend`, then hand the per-file metadata in as
``FileSpec`` records. Keeps state-machine logic pure (and easily
unit-tested without a real disk).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional
from uuid import UUID, uuid4

from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ncmu_backend.db.models import (
    PersonalKbApplication,
    PersonalKbApplicationFile,
)


# --------------------------------------------------------------------- #
# State machine
# --------------------------------------------------------------------- #
ALL_STATUSES: tuple[str, ...] = (
    "pending", "in_progress", "done", "rejected", "cancelled"
)
TERMINAL_STATUSES: frozenset[str] = frozenset({"done", "rejected", "cancelled"})

LEGAL_TRANSITIONS: frozenset[tuple[str, str]] = frozenset({
    ("pending", "in_progress"),
    ("pending", "rejected"),
    ("pending", "cancelled"),
    ("in_progress", "done"),
    ("in_progress", "rejected"),
})


class InvalidTransitionError(ValueError):
    """Raised when ``(from_status, to_status)`` is not in LEGAL_TRANSITIONS.

    Routes catch this and translate to HTTP 409 with body.detail copied
    from the message.
    """


class ApplicationNotFoundError(LookupError):
    """Raised when an application_id doesn't exist (or user has no access)."""


class ForbiddenError(PermissionError):
    """Raised when a non-owner tries to act on someone else's application."""


def validate_transition(from_status: str, to_status: str) -> None:
    """Assert that ``from_status -> to_status`` is in LEGAL_TRANSITIONS.

    The caller is responsible for picking the right human-friendly detail
    on the resulting HTTP 409 — the message here is generic.
    """
    if (from_status, to_status) not in LEGAL_TRANSITIONS:
        raise InvalidTransitionError(
            f"非法状态迁移: {from_status} -> {to_status}"
        )


# --------------------------------------------------------------------- #
# Value object (routes pass these in after staging files on disk)
# --------------------------------------------------------------------- #
@dataclass(frozen=True)
class FileSpec:
    """Per-file metadata captured by the routes layer after storage save."""

    file_id: UUID
    original_filename: str
    storage_path: str
    file_size_bytes: int
    content_type: Optional[str]
    sha256: Optional[str]


# --------------------------------------------------------------------- #
# Create / list / withdraw — employee surface
# --------------------------------------------------------------------- #
async def create_application(
    db: AsyncSession,
    *,
    user_id: UUID,
    kb_name_suggested: Optional[str],
    description: Optional[str],
    files: Iterable[FileSpec],
    application_id: Optional[UUID] = None,
) -> PersonalKbApplication:
    """INSERT one applications row + N files rows in a single transaction.

    Status starts at 'pending' (DB default). The caller (route layer)
    is expected to have already staged each blob on disk.

    ``application_id`` is optional: callers that pre-allocated the UUID
    (so the on-disk storage path under ``<storage_root>/<id>/`` matches
    the DB row id) pass it through here instead of letting the service
    mint a fresh one. Falls back to ``uuid4()`` when omitted, keeping
    the existing unit-test paths unchanged.
    """
    app_id = application_id if application_id is not None else uuid4()
    app_row = PersonalKbApplication(
        id=app_id,
        user_id=user_id,
        kb_name_suggested=kb_name_suggested,
        description=description,
        status="pending",
    )
    db.add(app_row)
    await db.flush()  # populate app_row.id for FK references

    for spec in files:
        db.add(
            PersonalKbApplicationFile(
                id=spec.file_id,
                application_id=app_row.id,
                original_filename=spec.original_filename,
                storage_path=spec.storage_path,
                file_size_bytes=spec.file_size_bytes,
                content_type=spec.content_type,
                sha256=spec.sha256,
            )
        )
    await db.commit()
    await db.refresh(app_row)
    return app_row


async def list_user_applications(
    db: AsyncSession,
    *,
    user_id: UUID,
    status: Optional[str] = None,
) -> list[PersonalKbApplication]:
    stmt = select(PersonalKbApplication).where(
        PersonalKbApplication.user_id == user_id
    )
    if status:
        if status not in ALL_STATUSES:
            raise ValueError(f"unknown status filter: {status}")
        stmt = stmt.where(PersonalKbApplication.status == status)
    stmt = stmt.order_by(PersonalKbApplication.created_at.desc())
    res = await db.execute(stmt)
    return list(res.scalars().all())


async def withdraw_application(
    db: AsyncSession, *, user_id: UUID, application_id: UUID
) -> PersonalKbApplication:
    """Employee-side cancel. 5-state edge: pending → cancelled.

    - Application missing → ApplicationNotFoundError
    - Application belongs to another user → ApplicationNotFoundError
      (we deliberately don't leak the 403 to prevent enumeration —
      spec §3.1 字面)
    - Status is not 'pending' → InvalidTransitionError (409 at route)
    """
    app_row = await _get_or_404(db, application_id)
    if app_row.user_id != user_id:
        # Treat as not-found to dodge id enumeration attacks.
        raise ApplicationNotFoundError(str(application_id))
    validate_transition(app_row.status, "cancelled")
    app_row.status = "cancelled"
    await db.commit()
    await db.refresh(app_row)
    return app_row


# --------------------------------------------------------------------- #
# Admin surface
# --------------------------------------------------------------------- #
async def list_all_applications(
    db: AsyncSession, *, status: Optional[str] = None
) -> list[PersonalKbApplication]:
    stmt = select(PersonalKbApplication)
    if status:
        if status not in ALL_STATUSES:
            raise ValueError(f"unknown status filter: {status}")
        stmt = stmt.where(PersonalKbApplication.status == status)
    stmt = stmt.order_by(PersonalKbApplication.created_at.desc())
    res = await db.execute(stmt)
    return list(res.scalars().all())


async def get_application_detail(
    db: AsyncSession, *, application_id: UUID, with_files: bool = False
) -> tuple[PersonalKbApplication, list[PersonalKbApplicationFile]]:
    app_row = await _get_or_404(db, application_id)
    files: list[PersonalKbApplicationFile] = []
    if with_files:
        res = await db.execute(
            select(PersonalKbApplicationFile).where(
                PersonalKbApplicationFile.application_id == application_id
            ).order_by(PersonalKbApplicationFile.uploaded_at)
        )
        files = list(res.scalars().all())
    return app_row, files


async def get_application_file(
    db: AsyncSession,
    *,
    application_id: UUID,
    file_id: UUID,
) -> PersonalKbApplicationFile:
    res = await db.execute(
        select(PersonalKbApplicationFile).where(
            PersonalKbApplicationFile.id == file_id,
            PersonalKbApplicationFile.application_id == application_id,
        )
    )
    file_row = res.scalar_one_or_none()
    if file_row is None:
        raise ApplicationNotFoundError(f"file {file_id} on app {application_id}")
    return file_row


async def claim_application(
    db: AsyncSession, *, admin_user_id: UUID, application_id: UUID
) -> PersonalKbApplication:
    """admin claim → 5-state edge: pending → in_progress.

    Side effects on the row:
      status              ← 'in_progress'
      admin_processed_by  ← admin_user_id
      admin_processed_at  ← now()
    """
    app_row = await _get_or_404(db, application_id)
    validate_transition(app_row.status, "in_progress")
    app_row.status = "in_progress"
    app_row.admin_processed_by = admin_user_id
    app_row.admin_processed_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(app_row)
    return app_row


async def reject_application(
    db: AsyncSession,
    *,
    admin_user_id: UUID,
    application_id: UUID,
    rejection_reason: str,
) -> PersonalKbApplication:
    """admin reject → 5-state edges:
      pending → rejected   (reject before claim)
      in_progress → rejected (reject after claim)
    """
    app_row = await _get_or_404(db, application_id)
    validate_transition(app_row.status, "rejected")
    app_row.status = "rejected"
    app_row.rejection_reason = rejection_reason
    # admin_processed_* may already be set from a prior claim — only
    # populate on the first admin touch.
    if app_row.admin_processed_by is None:
        app_row.admin_processed_by = admin_user_id
        app_row.admin_processed_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(app_row)
    return app_row


async def dispatch_application(
    db: AsyncSession,
    *,
    admin_user_id: UUID,
    application_id: UUID,
    dataset_id: str,
    dify_app_id: str,
    kb_name_final: str,
) -> PersonalKbApplication:
    """admin dispatch → 5-state edge: in_progress → done.

    Four SQL operations are wrapped in a single transaction so a unique
    violation (e.g. duplicate ``external_kb_name``) rolls back the whole
    set — no orphan ``app_owners`` row left behind.

    1. INSERT INTO dify_external_kb_configs (..., fastgpt_dataset_id, ...)
    2. INSERT INTO dify_app_kb_bindings (..., external_kb_config_id, dify_app_id)
    3. INSERT INTO app_owners (app_id, owner_user_id, visibility)
    4. UPDATE personal_kb_applications SET status='done', dify_app_id, ...
    """
    app_row = await _get_or_404(db, application_id)
    validate_transition(app_row.status, "done")

    try:
        # 1. dify_external_kb_configs
        kb_config_id = uuid4()
        await db.execute(
            text(
                """
                INSERT INTO dify_external_kb_configs
                       (id, external_kb_name, fastgpt_dataset_id, api_key_label)
                VALUES (:id, :name, :ds_id, :label)
                """
            ),
            {
                "id": kb_config_id,
                "name": kb_name_final,
                "ds_id": dataset_id,
                # spec §4.2 step ① K-6 字面 — the api_key_label is the
                # stable kb-adapter env mapping key, not a per-user value.
                "label": "personal-kb",
            },
        )

        # 2. dify_app_kb_bindings
        binding_id = uuid4()
        await db.execute(
            text(
                """
                INSERT INTO dify_app_kb_bindings
                       (id, dify_app_id, external_kb_config_id)
                VALUES (:id, :app_id, :cfg_id)
                """
            ),
            {
                "id": binding_id,
                "app_id": dify_app_id,
                "cfg_id": kb_config_id,
            },
        )

        # 3. app_owners — Personal KB Apps are owner_only by default.
        await db.execute(
            text(
                """
                INSERT INTO app_owners (app_id, owner_user_id, visibility)
                VALUES (:app_id, :owner, 'owner_only')
                """
            ),
            {"app_id": dify_app_id, "owner": app_row.user_id},
        )

        # 4. applications row — status + cross-refs in one UPDATE.
        await db.execute(
            update(PersonalKbApplication)
            .where(PersonalKbApplication.id == application_id)
            .values(
                status="done",
                dify_app_id=dify_app_id,
                dify_external_kb_config_id=kb_config_id,
                fastgpt_dataset_id=dataset_id,
                admin_processed_by=admin_user_id,
                admin_processed_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise

    await db.refresh(app_row)
    return app_row


# --------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------- #
async def _get_or_404(
    db: AsyncSession, application_id: UUID
) -> PersonalKbApplication:
    res = await db.execute(
        select(PersonalKbApplication).where(
            PersonalKbApplication.id == application_id
        )
    )
    app_row = res.scalar_one_or_none()
    if app_row is None:
        raise ApplicationNotFoundError(str(application_id))
    return app_row
