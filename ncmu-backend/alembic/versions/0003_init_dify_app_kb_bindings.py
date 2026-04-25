"""init dify_app_kb_bindings

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-25

Phase 1 TASK-22 — dify_app_kb_bindings。
按 errata-08 偏离 v3.3.1 §14.3.2：
- 偏离 5：主键 (dify_app_id, dify_external_kb_id) 复合 → id UUID + UNIQUE(dify_app_id, external_kb_config_id)
- 偏离 5：移除 dify_app_id → dify_apps(id) 外键（dify_apps 表 Phase 1 不建，缓存推后到 Phase 3）
- 偏离 5：移除 dify_external_kb_id 字段（功能由 external_kb_config_id FK 替代）

Phase 3 必回补：建 dify_apps 缓存表后给 dify_app_id 加 FK（errata-08 衔接清单 #2）。
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dify_app_kb_bindings",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        # Dify App 主键（来自 Dify console）；Phase 1 无 FK（dify_apps 表不存在）
        sa.Column("dify_app_id", sa.String(64), nullable=False),
        sa.Column(
            "external_kb_config_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dify_external_kb_configs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "bound_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "dify_app_id",
            "external_kb_config_id",
            name="uq_dakb_app_config",
        ),
    )
    # dify_app_id 维度查询索引（列出某 App 的所有 KB 绑定）
    op.execute("CREATE INDEX idx_dakb_app ON dify_app_kb_bindings(dify_app_id)")


def downgrade() -> None:
    op.drop_table("dify_app_kb_bindings")
