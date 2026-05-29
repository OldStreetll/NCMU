"""init tags / app_tags / user_tags tables (Phase 2E TASK-PE-05)

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-29

Phase 2E TASK-PE-05 — 3 new tables for the admin tag-management surface
(plan §3 line 809-866 字面 grounded + Boss 拍板 error code A: 1012/1013):

- tags          (PK uuid + unique name + nullable description + 2 timestamps)
- app_tags      (composite PK dify_app_id + tag_id / FK tags.id ondelete=CASCADE)
- user_tags     (composite PK user_id + tag_id / FK both ondelete=CASCADE)

Plus 2 indexes on the join tables' ``tag_id`` columns so the
``DELETE FROM tags WHERE id = $1`` ON-DELETE-CASCADE sweep is index-
backed (admin UI's "delete tag" path / Popconfirm description shows
``app_count + user_count`` so cascade is the documented behaviour, not a
side effect).

Binding UI deferred to PE-08 (apps↔tags) and PE-09 (users↔tags). PE-05
ships CRUD-only on the ``tags`` table itself; the two join tables land
here so PE-08/-09 don't need follow-up migrations.

Field type alignment:
- app_tags.dify_app_id   = sa.String(length=64)         (mirror dify_apps.dify_app_id / no cast on JOIN)
- user_tags.user_id      = postgresql.UUID(as_uuid=True) (mirror users.id)
- tags.id / *.tag_id     = postgresql.UUID(as_uuid=True) (default postgres uuid pk)
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── tags ──────────────────────────────────────────────────────────────
    op.create_table(
        "tags",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.String(length=64), nullable=False, unique=True),
        sa.Column("description", sa.String(length=255), nullable=True),
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

    # ── app_tags ──────────────────────────────────────────────────────────
    op.create_table(
        "app_tags",
        sa.Column("dify_app_id", sa.String(length=64), nullable=False),
        sa.Column(
            "tag_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tags.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("dify_app_id", "tag_id"),
    )
    op.create_index("ix_app_tags_tag_id", "app_tags", ["tag_id"])

    # ── user_tags ─────────────────────────────────────────────────────────
    op.create_table(
        "user_tags",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tag_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tags.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("user_id", "tag_id"),
    )
    op.create_index("ix_user_tags_tag_id", "user_tags", ["tag_id"])


def downgrade() -> None:
    # Symmetric reverse — drop indexes explicitly before tables for grep
    # parity with 0007's downgrade pattern (PostgreSQL would cascade-drop
    # the index but explicit reads cleaner in audit).
    op.drop_index("ix_user_tags_tag_id", table_name="user_tags")
    op.drop_table("user_tags")

    op.drop_index("ix_app_tags_tag_id", table_name="app_tags")
    op.drop_table("app_tags")

    op.drop_table("tags")
