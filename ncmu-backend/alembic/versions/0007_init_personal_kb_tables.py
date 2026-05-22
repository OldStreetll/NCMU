"""init personal_kb tables (Phase 2C TASK-PC1)

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-21

Phase 2C TASK-PC1 — 3 new tables for the Personal KB flow (errata-12 §2.2 +
§3.2 字面 grounded):

- app_owners                     (private-App routing / one row per private chat App)
- personal_kb_applications       (5-state machine, Q3-B: pending / in_progress /
                                  done / rejected / cancelled)
- personal_kb_application_files  (1:N original-doc archive / FK ON DELETE CASCADE)

Plus 4 indexes:
- ix_app_owners_user                 (app_owners.owner_user_id)
- ix_personal_kb_app_user_status     (personal_kb_applications.user_id, status)
- ix_pkaf_app                        (personal_kb_application_files.application_id)
- ix_pkaf_sha                        (personal_kb_application_files.sha256)

互斥约束推迟到钉钉接入阶段 (spec §1.5 调和 #4): 当前态 tag_app_mappings 表不存在,
互斥保护没有实施对象; 与 tag_app_mappings + users.tags JSONB alembic 同期落地。
本 migration 不创建 enforce_*_mutex 函数或 trg_*_mutex 行级守卫。

字段类型字面对账 (spec §1.5 调和 #6 / errata-12 §2.2 line 179 自注 "spec 阶段
会落实细节"):
- app_owners.app_id                       = sa.String(length=64)
- personal_kb_applications.dify_app_id    = sa.String(length=64)
两者与既有 dify_apps.dify_app_id (alembic 0004) JOIN 时类型直对齐, 不需强制转换。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── app_owners ────────────────────────────────────────────────────────
    op.create_table(
        "app_owners",
        sa.Column("app_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "owner_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "visibility",
            sa.String(length=20),
            nullable=False,
            server_default="owner_only",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "visibility IN ('owner_only', 'shared', 'public')",
            name="ck_app_owners_visibility",
        ),
    )
    op.create_index(
        "ix_app_owners_user",
        "app_owners",
        ["owner_user_id"],
    )

    # ── personal_kb_applications ──────────────────────────────────────────
    op.create_table(
        "personal_kb_applications",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("kb_name_suggested", sa.String(length=200), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("fastgpt_dataset_id", sa.String(length=50), nullable=True),
        sa.Column(
            "dify_external_kb_config_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dify_external_kb_configs.id"),
            nullable=True,
        ),
        sa.Column("dify_app_id", sa.String(length=64), nullable=True),
        sa.Column(
            "admin_processed_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("admin_processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'in_progress', 'done', 'rejected', 'cancelled')",
            name="ck_personal_kb_applications_status",
        ),
    )
    op.create_index(
        "ix_personal_kb_app_user_status",
        "personal_kb_applications",
        ["user_id", "status"],
    )

    # ── personal_kb_application_files ─────────────────────────────────────
    op.create_table(
        "personal_kb_application_files",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "application_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "personal_kb_applications.id",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column(
            "uploaded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_pkaf_app",
        "personal_kb_application_files",
        ["application_id"],
    )
    op.create_index(
        "ix_pkaf_sha",
        "personal_kb_application_files",
        ["sha256"],
    )


def downgrade() -> None:
    # Drop indexes explicitly for symmetric reverse, then the tables themselves
    # (PostgreSQL would cascade-drop the indexes with the table, but explicit
    # is easier to grep + audit when reviewing migrations against the
    # upgrade plan).
    op.drop_index(
        "ix_pkaf_sha", table_name="personal_kb_application_files"
    )
    op.drop_index(
        "ix_pkaf_app", table_name="personal_kb_application_files"
    )
    op.drop_table("personal_kb_application_files")

    op.drop_index(
        "ix_personal_kb_app_user_status", table_name="personal_kb_applications"
    )
    op.drop_table("personal_kb_applications")

    op.drop_index("ix_app_owners_user", table_name="app_owners")
    op.drop_table("app_owners")
