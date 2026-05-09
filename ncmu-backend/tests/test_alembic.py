"""AC #1 + #2: alembic upgrade head / downgrade base on clean pg-ncmu."""
from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from conftest import _run_alembic


EXPECTED_TABLES = {
    "users",
    "chat_sessions",
    "dify_external_kb_configs",
    "dify_app_kb_bindings",
    "dify_apps",
    "workflow_runs",
    "alembic_version",
}


def _list_public_tables(url: str) -> set[str]:
    engine = create_engine(url, future=True)
    try:
        with Session(engine) as s:
            rows = s.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public'"
                )
            ).scalars().all()
            return set(rows)
    finally:
        engine.dispose()


def test_upgrade_head_creates_all_expected_tables(db_session):
    """AC#1: alembic upgrade head 在干净 pg-ncmu 上成功，3 张迁移按编号顺序应用。

    db_session fixture 已经跑过 upgrade head；这里只断言表齐全。
    """
    rows = db_session.execute(
        text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public'"
        )
    ).scalars().all()
    assert EXPECTED_TABLES.issubset(set(rows)), (
        f"预期表 {EXPECTED_TABLES} 未全部创建；实际：{sorted(rows)}"
    )

    # 再断言 alembic_version 有 0005（最新 head — Phase 2B TASK-67b 加 workflow_runs + dify_apps.mode）
    version = db_session.execute(
        text("SELECT version_num FROM alembic_version")
    ).scalar_one()
    assert version == "0005", f"预期 alembic_version=0005，实际 {version}"


def test_downgrade_base_drops_all_business_tables(test_db_url):
    """AC#2: alembic downgrade base 把 4 张表全部清掉，无残余对象。"""
    _run_alembic(test_db_url, "upgrade", "head")
    _run_alembic(test_db_url, "downgrade", "base")

    tables = _list_public_tables(test_db_url)
    business_tables = {
        "users",
        "chat_sessions",
        "dify_external_kb_configs",
        "dify_app_kb_bindings",
    }
    assert tables.isdisjoint(business_tables), (
        f"downgrade 后残留业务表：{tables & business_tables}"
    )


def test_upgrade_is_idempotent(test_db_url):
    """重入安全：entrypoint.sh 每次容器启动都跑 upgrade head。"""
    _run_alembic(test_db_url, "upgrade", "head")
    # 第二次应为 no-op
    _run_alembic(test_db_url, "upgrade", "head")

    tables = _list_public_tables(test_db_url)
    assert EXPECTED_TABLES.issubset(tables)
