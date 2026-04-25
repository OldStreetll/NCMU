"""init users + chat_sessions

Revision ID: 0001
Revises:
Create Date: 2026-04-25

Phase 1 TASK-22 — 第一张迁移脚本。建立：
- users（Phase 1 dev mock + Phase 2 钉钉 ready）
- chat_sessions（Q3 索引镜像；G-02-5/6 schema 闭环）

G-02-5：anonymous 分支走 partial index `idx_chat_sessions_anon`
G-02-6：CHECK 约束 user_id OR anonymous_session_id 必有其一
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # pgcrypto 提供 gen_random_uuid()；若已存在则静默跳过
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        # Phase 2 接入钉钉时填入；Phase 1 dev seed 留 NULL
        sa.Column("dingtalk_userid", sa.String(64), unique=True),
        sa.Column("name", sa.String(64), nullable=False),
        # 钉钉部门路径，Phase 2 同步
        sa.Column("dept_path", sa.String(255)),
        sa.Column(
            "is_active",
            sa.Boolean,
            nullable=False,
            server_default=sa.true(),
        ),
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
    # partial index：仅对已绑钉钉的用户建索引
    op.execute(
        "CREATE INDEX idx_users_dingtalk_userid "
        "ON users(dingtalk_userid) WHERE dingtalk_userid IS NOT NULL"
    )

    op.create_table(
        "chat_sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
        ),
        # guest 路径用（Phase 1 不暴露端点；schema 预留，G-02-5）
        sa.Column("anonymous_session_id", sa.String(64)),
        # Dify App 主键（来自 Dify console，跨版本可能变，VARCHAR 不用 UUID）
        sa.Column("dify_app_id", sa.String(64), nullable=False),
        sa.Column("dify_conversation_id", sa.String(64), nullable=False),
        # 用户可改 / Dify 自动生成
        sa.Column("title", sa.String(255)),
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
        # soft delete
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        # G-02-6 CHECK 约束：user_id 与 anonymous_session_id 必有其一
        sa.CheckConstraint(
            "user_id IS NOT NULL OR anonymous_session_id IS NOT NULL",
            name="ck_chat_sessions_user_or_anon",
        ),
    )
    # dify_conversation_id 全局唯一（orchestrator 首响 INSERT 的 UNIQUE 守卫依赖此）
    op.execute(
        "CREATE UNIQUE INDEX idx_chat_sessions_dify_conv "
        "ON chat_sessions(dify_conversation_id)"
    )
    # 用户会话列表查询（按 updated_at 倒序），排除已软删
    op.execute(
        "CREATE INDEX idx_chat_sessions_user "
        "ON chat_sessions(user_id, updated_at DESC) "
        "WHERE deleted_at IS NULL"
    )
    # G-02-5：anonymous 分支查询命中 partial index
    # 索引谓词 user_id IS NULL AND deleted_at IS NULL；查询谓词必须蕴含才走索引
    op.execute(
        "CREATE INDEX idx_chat_sessions_anon "
        "ON chat_sessions(anonymous_session_id, updated_at DESC) "
        "WHERE user_id IS NULL AND deleted_at IS NULL"
    )


def downgrade() -> None:
    op.drop_table("chat_sessions")
    op.drop_table("users")
    # pgcrypto 为库级 extension，不在 downgrade 里 DROP（可能被其他场景依赖）
