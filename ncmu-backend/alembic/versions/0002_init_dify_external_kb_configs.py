"""init dify_external_kb_configs

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-25

Phase 1 TASK-22 — dify_external_kb_configs。
按 errata-08 偏离 v3.3.1 §14.3.2：
- 偏离 1：id 从 SERIAL → UUID（与全表 UUID 惯例一致）
- 偏离 2：移除 api_key_encrypted / last_rotated_at / is_active / description 列
  新增 api_key_label（NCMU 侧审计标签，不存秘密）
- 偏离 3：fastgpt_endpoint 重命名为 upstream_endpoint（Phase 1 留空 NULL）
- 偏离 4：fastgpt_dataset_id 从 dify_app_kb_bindings 移到本表
  （归一：同一 KB 的 fastgpt_dataset_id 固定，不应在 bindings 冗余）

Phase 3 必回补：api_key_encrypted + last_rotated_at + is_active 列（errata-08 衔接清单 #1）。
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dify_external_kb_configs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        # 业务命名（如 hr-handbook），跨全库唯一
        sa.Column(
            "external_kb_name",
            sa.String(64),
            nullable=False,
            unique=True,
        ),
        # FastGPT KB 主键（FastGPT dataset._id）
        sa.Column("fastgpt_dataset_id", sa.String(64), nullable=False),
        # KB_ADAPTER_ALLOWED_KEYS 中的 label（A-3 修订：仅 NCMU 侧审计标签，
        # 与 kb-adapter env 里的纯 token 解耦；Phase 3 管理面板录入时建立映射）
        sa.Column("api_key_label", sa.String(64), nullable=False),
        # 多 FastGPT 实例时备用（Phase 1 留空 NULL；偏离 3 重命名自 fastgpt_endpoint）
        sa.Column("upstream_endpoint", sa.String(255)),
        sa.Column("notes", sa.Text),
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
    op.drop_table("dify_external_kb_configs")
