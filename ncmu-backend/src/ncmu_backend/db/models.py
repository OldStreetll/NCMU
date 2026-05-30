"""SQLAlchemy 2.x ORM models — mirror alembic 0001_init_users_chat_sessions.

Tables here MUST stay in sync with the alembic migration. Phase 1 keeps
both hand-written (no autogenerate) so the migration is the single
source of truth; `Base.metadata` is exposed only so future task batches
can switch alembic to autogenerate when the model surface stabilises.

DEPLOY-1 修订：dify_apps / dify_external_kb_configs / dify_app_kb_bindings
ORM models will be added by TASK-26's batch (they reference alembic
0002 + 0003 created by TASK-22).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True
    )
    dingtalk_userid: Mapped[str | None] = mapped_column(
        String(64), unique=True, nullable=True
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    dept_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    chat_sessions: Mapped[list["ChatSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class ChatSession(Base):
    __tablename__ = "chat_sessions"
    __table_args__ = (
        CheckConstraint(
            "user_id IS NOT NULL OR anonymous_session_id IS NOT NULL",
            name="ck_chat_sessions_user_or_anon",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
    )
    anonymous_session_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    dify_app_id: Mapped[str] = mapped_column(String(64), nullable=False)
    dify_conversation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped[User | None] = relationship(back_populates="chat_sessions")


class DifyApp(Base):
    """Dify App cache table — populated by admin POST /sync_apps (TASK-67a).

    mode column lands in TASK-67b alongside workflow_runs (per the spec the
    workflow-runs FK targets dify_apps.dify_app_id, so the cache must exist
    first). Phase 2B B1 keeps this table minimal: PK + display name + audit
    timestamps. No FK from dify_app_kb_bindings yet — that's the Phase 3
    回补 referenced in alembic 0003's docstring.
    """

    __tablename__ = "dify_apps"

    dify_app_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    mode: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="chat"
    )
    api_token: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    # TASK-PE-07 (alembic 0010): admin App-sync surface columns.
    # is_active gates GET /admin/apps?include_inactive + the PATCH toggle
    # ("停用" hides an app from employees without dropping the cache row).
    # last_synced_at is stamped by POST /admin/sync_apps on each UPSERT so
    # the admin list can show "上次同步"; NULL = never synced since 0010.
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class WorkflowRun(Base):
    """Single-table audit trail for non-chat orchestration requests.

    Spec §3.2 Q2=A — keep the run history in one wide JSONB-friendly
    table rather than splitting per-mode. JSONB lets the node-by-node
    trace and the IO payloads evolve without schema churn while still
    being queryable via -> / ->> from runbooks.

    FK app_id → dify_apps(dify_app_id) with ON DELETE CASCADE: if an
    admin removes an app from the cache, its run history goes with it
    (Phase 2B doesn't promise eternal retention — runs are debug aid,
    not source of truth).
    """

    __tablename__ = "workflow_runs"

    run_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True
    )
    app_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("dify_apps.dify_app_id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    inputs: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    outputs: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="running"
    )
    node_trace: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_msg: Mapped[str | None] = mapped_column(Text, nullable=True)


# =====================================================================
# Phase 2C TASK-PC1 — Personal KB tables (mirror alembic 0007)
# ---------------------------------------------------------------------
# 3 tables landed by alembic 0007_init_personal_kb_tables:
#   - app_owners                     private-App routing
#   - personal_kb_applications       5-state machine (Q3-B)
#   - personal_kb_application_files  1:N original-doc archive (FK CASCADE)
# Field-type alignment (spec §1.5 调和 #6): app_owners.app_id and
# personal_kb_applications.dify_app_id are String(64) to JOIN directly
# against dify_apps.dify_app_id without a cast.
# =====================================================================


class AppOwner(Base):
    """Private-App owner mapping (errata-12 §2.2).

    One row per chat App that belongs to a single user (visibility =
    'owner_only'). 'shared' / 'public' values are reserved for later
    phases (Personal KB v2 + team KB); the CHECK constraint accepts
    all three so the column doesn't need a schema change when those
    land.
    """

    __tablename__ = "app_owners"
    __table_args__ = (
        CheckConstraint(
            "visibility IN ('owner_only', 'shared', 'public')",
            name="ck_app_owners_visibility",
        ),
    )

    app_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )
    visibility: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="owner_only"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PersonalKbApplication(Base):
    """Employee KB-build application (errata-12 §3.2 + Q3-B 5-state).

    Status state machine (TASK-PC2-A enforces transitions in
    application_service.py; the schema only constrains the value set):
        pending → in_progress (admin claim)
        pending → rejected     (admin reject before claim)
        pending → cancelled    (employee withdraw)
        in_progress → done     (admin dispatch)
        in_progress → rejected (admin reject after claim)
    Terminal states: done / rejected / cancelled.
    """

    __tablename__ = "personal_kb_applications"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'in_progress', 'done', 'rejected', 'cancelled')",
            name="ck_personal_kb_applications_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )
    kb_name_suggested: Mapped[str | None] = mapped_column(
        String(200), nullable=True
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="pending"
    )
    fastgpt_dataset_id: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )
    dify_external_kb_config_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("dify_external_kb_configs.id"),
        nullable=True,
    )
    dify_app_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    admin_processed_by: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
    )
    admin_processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    files: Mapped[list["PersonalKbApplicationFile"]] = relationship(
        back_populates="application", cascade="all, delete-orphan"
    )


class PersonalKbApplicationFile(Base):
    """Original-doc archive row (one per uploaded file).

    application_id FK uses ON DELETE CASCADE so a withdrawal /
    application-level delete sweeps the archive rows in one statement
    (the on-disk file is the StorageBackend's responsibility — landed
    by TASK-PC2-A).
    """

    __tablename__ = "personal_kb_application_files"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("personal_kb_applications.id", ondelete="CASCADE"),
        nullable=False,
    )
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    application: Mapped["PersonalKbApplication"] = relationship(
        back_populates="files"
    )


# =====================================================================
# Phase 2E TASK-PE-05 — Tag tables (mirror alembic 0009)
# ---------------------------------------------------------------------
# 3 tables for admin tag-management (plan §3 line 868-888 字面):
#   - tags       PK uuid + unique name + nullable description + 2 timestamps
#   - app_tags   composite PK (dify_app_id, tag_id) / FK CASCADE on tags.id
#   - user_tags  composite PK (user_id, tag_id)    / FK CASCADE on both
# Binding routes deferred to PE-08 (apps↔tags) + PE-09 (users↔tags);
# this batch ships CRUD-only on the ``tags`` table itself. Join-table
# ORM classes live here so PE-08/-09 can import without a follow-up
# migration churn.
# =====================================================================


class Tag(Base):
    """Admin-managed tag (PE-05 CRUD; PE-08/-09 wire many-to-many bindings)."""

    __tablename__ = "tags"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AppTag(Base):
    """App ↔ Tag binding (PE-08 will wire admin UI)."""

    __tablename__ = "app_tags"

    dify_app_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tag_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tags.id", ondelete="CASCADE"),
        primary_key=True,
    )


class UserTag(Base):
    """User ↔ Tag binding (PE-09 will wire admin UI + employee filter)."""

    __tablename__ = "user_tags"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tag_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tags.id", ondelete="CASCADE"),
        primary_key=True,
    )
