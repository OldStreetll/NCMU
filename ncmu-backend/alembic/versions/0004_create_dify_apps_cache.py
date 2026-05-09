"""create dify_apps cache table

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-09

Phase 2B B1 / TASK-67a: build the dify_apps cache table that
0003 deferred. mode column + workflow_runs are TASK-67b's job.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dify_apps",
        sa.Column("dify_app_id", sa.String(length=64), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("dify_apps")
