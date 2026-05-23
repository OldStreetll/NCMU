"""alter dify_external_kb_configs.external_kb_name VARCHAR(64) → VARCHAR(200)

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-22

Phase 2C TASK-PC2-FIX REWORK-C1 — cross-stack 字段长度对齐:

Pydantic schemas/personal_kb.py:110 ``kb_name_final`` Field max_length 64→200
(plan §4.6/§4.7 字面 + errata-12 §2.2 假设 200) 已在 PC2-FIX 落地, 但真存储
列 ``dify_external_kb_configs.external_kb_name`` 在 0002 migration 仍是
``sa.String(64)`` (cross-stack drift). 65-200 字符路径会 Pydantic 通过 →
INSERT → psycopg/asyncpg StringDataRightTruncation (SQLSTATE 22001) → 漏出
sqlalchemy.exc.DBAPIError 500 而非干净的 422.

本 migration 把列扩到 200, 与 Pydantic max_length=200 对齐. 配套 admin
dispatch endpoint 加 ``except DBAPIError`` + SQLSTATE 22001 兜底 (同 commit /
asyncpg dialect 不分类 StringDataRightTruncationError 到 sqlalchemy.exc.DataError
shim / 故捕 DBAPIError base + gate on SQLSTATE 22001 string_data_right_truncation).

字段属性保留 (alembic alter_column 默认行为):
- nullable=False (unchanged)
- unique=True (unchanged / 跨全库唯一约束 / 既有 unique index 自动适配新类型)
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "dify_external_kb_configs",
        "external_kb_name",
        existing_type=sa.String(length=64),
        type_=sa.String(length=200),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "dify_external_kb_configs",
        "external_kb_name",
        existing_type=sa.String(length=200),
        type_=sa.String(length=64),
        existing_nullable=False,
    )
