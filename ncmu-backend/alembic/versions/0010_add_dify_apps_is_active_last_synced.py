"""add dify_apps.is_active + last_synced_at (Phase 2E TASK-PE-07)

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-29

Phase 2E TASK-PE-07 — the admin App-sync surface needs two columns the
Phase 2B ``dify_apps`` cache table never carried (Boss 2026-05-29 拍板
Option A after the PE-07 [INTENT-CHECK] surfaced the gap: plan §4 Step 1
+ spec §5.5.1 both assume these columns but the live model
(db/models.py ``DifyApp``) only had PK + name + mode + api_token + 2
timestamps):

- is_active       BOOLEAN NOT NULL DEFAULT true
      Powers GET /admin/apps?include_inactive=… filtering and the
      PATCH /admin/apps/{id} is_active toggle ("停用" — employee loses
      access without deleting the cache row + its personal_kb / app_tags
      history). Existing rows backfill to ``true`` via the server default.

- last_synced_at  TIMESTAMPTZ NULL
      Stamped by POST /admin/sync_apps on each UPSERT (the 1-line
      SCOPE-CHANGE Boss approved in the same decision) so the admin SPA
      can render "上次同步" on the list page. NULL = never synced since
      the column landed (older rows / fresh deploy before first sync).

Webhook path A (spec §5.5.2) was dropped — the PE-07 Step 0 spike proved
Dify v1.13.3 has no outbound app-lifecycle webhook (only the inbound
WorkflowWebhookTrigger). So this migration carries no webhook-secret /
event-log columns; the cache stays poll-synced (path B).

Both columns are additive + nullable-or-defaulted, so upgrade is safe on
a populated table and downgrade is a clean drop (no data dependency).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "dify_apps",
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "dify_apps",
        sa.Column(
            "last_synced_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("dify_apps", "last_synced_at")
    op.drop_column("dify_apps", "is_active")
