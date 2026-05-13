"""add dify_apps.api_token column for per-App Dify authentication

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-12

TASK-79-BACKEND-ARCH-FIX: Dify v1.13.3 binds each API token to one App
(`api_tokens.app_id` FK in the Dify DB; verified against the chatflow /
textgen / workflow / Agent Apps for NCMU Phase 2B). NCMU previously held
a single `DIFY_APP_DEFAULT_TOKEN` env var that resolved to the chat
`[KB]员工手册` App's token — every Phase 2B mode therefore hit that one
App on the Dify side, hanging on advanced-chat (chatflow Dify only
emits pings against a chat-mode App over /v1/chat-messages) and 400-ing
on completion / workflow (chat App has no /v1/completion-messages or
/v1/workflows/run endpoint).

This migration adds a NULLABLE `api_token VARCHAR(64)` column to
`dify_apps` so the dispatcher (workflow/mode_dispatcher.py) can look up
the per-App token at dispatch time and, on NULL, fall back to
`DIFY_APP_DEFAULT_TOKEN` (preserving the Phase 1 chat App path).

Existing rows stay NULL — Boss generates 4 new tokens in the Dify
Console and Pane 0 backfills via a manual SQL UPDATE (out of scope
here; admin /sync_apps does NOT touch this column yet, to keep this
migration contained to the schema change).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "dify_apps",
        sa.Column("api_token", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("dify_apps", "api_token")
