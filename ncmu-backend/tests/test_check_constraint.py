"""AC #3 + #4: G-02-5/6 chat_sessions schema 闭环。

G-02-6（AC#3）：CHECK 约束 `user_id IS NOT NULL OR anonymous_session_id IS NOT NULL`
G-02-5（AC#4）：anonymous 分支查询命中 partial index `idx_chat_sessions_anon`
    关键：查询谓词必须**蕴含**索引谓词（user_id IS NULL AND deleted_at IS NULL），
    planner 才会选 partial index。

> **N-5 修订**：使用 sync fixture（pytest-postgresql + sync session），与 TASK-25
  引入的 AsyncSession 区分。
> **C-NEW-2 修订**：用 session-level `SET enable_seqscan = off`（不是 SET LOCAL）
  — commit() 后事务结束，SET LOCAL 仅当前事务有效；session-level 在整个连接
  生命周期生效更稳。
"""
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


def test_chat_session_requires_user_or_anon(db_session):
    """G-02-6: CHECK 约束拒绝 user_id 与 anonymous_session_id 同时为 NULL。"""
    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                "INSERT INTO chat_sessions (dify_app_id, dify_conversation_id) "
                "VALUES ('app-x', 'conv-x')"
            )
        )
        db_session.flush()


def test_chat_session_accepts_anon_only(db_session):
    """G-02-6 互补：仅有 anonymous_session_id 应被接受。"""
    db_session.execute(
        text(
            "INSERT INTO chat_sessions "
            "(anonymous_session_id, dify_app_id, dify_conversation_id) "
            "VALUES ('anon-accept-1', 'app-x', 'conv-accept-1')"
        )
    )
    db_session.commit()
    row_id = db_session.execute(
        text(
            "SELECT id FROM chat_sessions "
            "WHERE anonymous_session_id = 'anon-accept-1'"
        )
    ).scalar_one()
    assert row_id is not None


def test_anonymous_session_partial_index_used(db_session):
    """G-02-5: anonymous 分支查询命中 partial index。

    planner 在小表上会偏好 Seq Scan。两手都要：
      1. 插入 ≥50 行 anonymous 会话让 cost 倾向 index
      2. 关闭 seqscan 让 planner 必须用 index
    然后 EXPLAIN (FORMAT JSON) 递归扫 plan 树找 `Index Name` 节点。
    """
    # 1. 插入 50 行 anonymous 会话
    for i in range(50):
        db_session.execute(
            text(
                "INSERT INTO chat_sessions "
                "(anonymous_session_id, dify_app_id, dify_conversation_id) "
                f"VALUES ('anon-test-{i}', 'app-x', 'conv-{i}')"
            )
        )
    db_session.commit()

    # 2. session-level SET（非 SET LOCAL）— commit 后仍生效（C-NEW-2）
    db_session.execute(text("SET enable_seqscan = off"))

    # 3. 查询谓词必须包含 user_id IS NULL AND deleted_at IS NULL
    #    （与索引谓词严格蕴含；PostgreSQL planner 才会选 partial index）
    plan_json = db_session.execute(
        text(
            "EXPLAIN (FORMAT JSON) "
            "SELECT id FROM chat_sessions "
            "WHERE anonymous_session_id = 'anon-test-1' "
            "  AND user_id IS NULL "
            "  AND deleted_at IS NULL"
        )
    ).scalar()

    # 4. 递归扫描 plan 树找所有 `Index Name` 节点
    def find_index_names(node):
        names = []
        if isinstance(node, dict):
            if "Index Name" in node:
                names.append(node["Index Name"])
            for v in node.values():
                names.extend(find_index_names(v))
        elif isinstance(node, list):
            for item in node:
                names.extend(find_index_names(item))
        return names

    names = find_index_names(plan_json)
    assert "idx_chat_sessions_anon" in names, (
        f"未命中 anon partial index；实际 indexes={names}\nplan={plan_json}"
    )


def test_dev_seed_users_present_after_seed_import(db_session):
    """AC#5: dev_users.sql seed 5 条固定 UUID 用户。

    本测试手动应用 seed（alembic 不自动跑 seed — seed 由运维/dev 手动或容器
    启动后显式导入；Phase 1 dev container 启动后由开发者一次性执行）。
    """
    import pathlib

    seed_path = (
        pathlib.Path(__file__).parent.parent
        / "alembic"
        / "seeds"
        / "dev_users.sql"
    )
    sql = seed_path.read_text(encoding="utf-8")
    # 去掉 -- 开头的注释行，psycopg 直接执行多语句 SQL
    db_session.execute(text(sql))
    db_session.commit()

    count = db_session.execute(
        text("SELECT COUNT(*) FROM users")
    ).scalar_one()
    assert count == 5

    # 张三 UUID 固定（B-4：dev 默认 admin）
    name = db_session.execute(
        text(
            "SELECT name FROM users "
            "WHERE id = 'a0000001-0000-4000-8000-000000000001'"
        )
    ).scalar_one()
    assert name == "张三"
