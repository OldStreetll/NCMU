"""add dify_apps.mode column + workflow_runs table

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-09

Phase 2B B1 / TASK-67b: dify_apps gets a `mode` column (admin sync upsert
mirrors Dify Console's per-app `mode` so dispatcher.resolve_mode in
TASK-68 can answer "is this app chat / advanced-chat / workflow / agent-chat
/ completion" by hitting the local cache instead of round-tripping to
Dify Console). workflow_runs is the single-table audit trail for every
non-chat orchestration request (spec §3.2 Q2=A); JSONB columns let the
node trace and IO payloads stay schema-flexible while still being
queryable via ->/->>.

Default mode='chat' is the no-migration-cost backfill: every dify_apps
row 67a wrote was a Dify "chat" app under the [KB] convention, so the
existing data is correct after the column lands.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) dify_apps.mode — server_default='chat' covers existing rows so the
    #    NOT NULL constraint is satisfied without a separate UPDATE step.
    op.add_column(
        "dify_apps",
        sa.Column(
            "mode",
            sa.String(length=32),
            nullable=False,
            server_default="chat",
        ),
    )

    # 2) workflow_runs — single-table audit log for non-chat orchestration.
    op.create_table(
        "workflow_runs",
        sa.Column("run_id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "app_id",
            sa.String(length=64),
            sa.ForeignKey("dify_apps.dify_app_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column(
            "inputs",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "outputs",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="running",
        ),
        sa.Column(
            "node_trace",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_msg", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'stopped', "
            "'exception', 'timeout', 'cancelled', 'db_error')",
            name="ck_workflow_runs_status",
        ),
    )

    # User-scoped recent-runs query: WHERE app_id = ? AND user_id = ?
    # ORDER BY started_at DESC. The composite index covers all three
    # predicates in one btree.
    op.create_index(
        "ix_workflow_runs_app_user_started",
        "workflow_runs",
        ["app_id", "user_id", sa.text("started_at DESC")],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workflow_runs_app_user_started", table_name="workflow_runs"
    )
    op.drop_table("workflow_runs")
    op.drop_column("dify_apps", "mode")
