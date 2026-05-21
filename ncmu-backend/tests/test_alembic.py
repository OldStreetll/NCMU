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
    "app_owners",
    "personal_kb_applications",
    "personal_kb_application_files",
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

    # 再断言 alembic_version 有 0007（最新 head — Phase 2C TASK-PC1
    # 加 app_owners + personal_kb_applications + personal_kb_application_files；
    # 0006 是 TASK-79-BACKEND-ARCH-FIX dify_apps.api_token；0005 是 Phase 2B
    # TASK-67b workflow_runs + dify_apps.mode）
    version = db_session.execute(
        text("SELECT version_num FROM alembic_version")
    ).scalar_one()
    assert version == "0007", f"预期 alembic_version=0007，实际 {version}"

    # IMP-INDEP-4: 显式列级断言 — 0006 migration 的核心交付物是
    # dify_apps.api_token VARCHAR(64) NULL。版本号匹配不保证列真被加
    # （脚本可能没跑完 / 列名 typo / migration 走错分支等），用
    # information_schema.columns 字面对账列存在。typo 类 bug（如 api_tokens
    # 复数 / api-token 横线）此断言即捕获。
    columns = db_session.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='dify_apps'"
        )
    ).scalars().all()
    assert "api_token" in set(columns), (
        f"预期 dify_apps.api_token 列存在 (0006 migration)，实际列：{sorted(columns)}"
    )


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
