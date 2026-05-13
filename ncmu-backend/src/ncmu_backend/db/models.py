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
