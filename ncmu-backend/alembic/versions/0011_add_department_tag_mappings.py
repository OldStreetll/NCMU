"""add department_tag_mappings table (Phase 2D-A2-同步 TASK-2DA2S-02)

Revision ID: 0011
Revises: 0010
Create Date: 2026-06-01

Phase 2D-A2-同步 TASK-2DA2S-02 — one new table for the DingTalk contact
sync surface (plan §TASK-2DA2S-02 字面 grounded + Boss 拍 dept_id 精确映射):

- department_tag_mappings
    composite PK (dept_id, tag_id) — 一个钉钉部门可映射多个 NCMU tag，
    复合 PK 同时去重（同 (dept_id, tag_id) 不能重复插入）。

Mirrors the ``app_tags`` (0009 / PE-08) composite-PK + FK CASCADE 范式:
- ``dept_id``  = sa.BigInteger 钉钉部门 id（钉钉侧 id，NCMU 无 departments
                 表，故 dept_id 不加 FK）。
- ``tag_id``   = postgresql.UUID(as_uuid=True) FK tags.id ondelete=CASCADE
                 (删 tag 则映射级联删 — 与 app_tags / user_tags 一致)。
- ``created_at`` TIMESTAMPTZ NOT NULL server_default now()（审计列；
                 app_tags 无此列，本表按 plan schema 携带）。

Plus an index on ``tag_id`` so the ``DELETE FROM tags WHERE id = $1``
ON-DELETE-CASCADE sweep is index-backed (mirrors ix_app_tags_tag_id /
ix_user_tags_tag_id in 0009), and to support reverse lookup
(tag → which departments map to it).

Sync service (T03) seeds rows manually (dev SQL); admin CRUD UI deferred
to a later 2D-A2 batch (Boss 拍延后).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "department_tag_mappings",
        sa.Column("dept_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "tag_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tags.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("dept_id", "tag_id"),
    )
    op.create_index(
        "ix_department_tag_mappings_tag_id",
        "department_tag_mappings",
        ["tag_id"],
    )


def downgrade() -> None:
    # Symmetric reverse — drop index explicitly before table for grep
    # parity with 0009's downgrade pattern.
    op.drop_index(
        "ix_department_tag_mappings_tag_id",
        table_name="department_tag_mappings",
    )
    op.drop_table("department_tag_mappings")
