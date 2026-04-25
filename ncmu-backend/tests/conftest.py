"""pytest fixtures for Phase 1 TASK-22 schema tests.

A-1 修订：TASK-22 **仅**提供 sync `db_session` fixture（pytest-postgresql 驱动）；
TASK-25 稍后追加 async_db / jwt / respx fixture，同一 conftest.py 同文件不同段，
sync 部分不改。

连接目标：Phase 0 已起的 pg-ncmu（localhost:5432）。每个测试通过 pytest-postgresql
的 `postgresql_noproc` + `postgresql` 组合获得一个独立、自动清理的 ephemeral 测试数据库。
环境变量覆盖：`PYTEST_PG_HOST` / `PYTEST_PG_PORT` / `PYTEST_PG_USER` / `PYTEST_PG_PASSWORD`。
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import pytest
from pytest_postgresql import factories
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


_REPO_ROOT = pathlib.Path(__file__).parent.parent


postgresql_noproc = factories.postgresql_noproc(
    host=os.environ.get("PYTEST_PG_HOST", "localhost"),
    port=int(os.environ.get("PYTEST_PG_PORT", "5432")),
    user=os.environ.get("PYTEST_PG_USER", "ncmu_app"),
    password=os.environ.get("PYTEST_PG_PASSWORD", "CHANGE_ME"),
    dbname="ncmu_test",
)
postgresql = factories.postgresql("postgresql_noproc")


def _run_alembic(url: str, *args: str) -> None:
    """Run `alembic -c alembic.ini <args>` with NCMU_DB_URL set to `url`."""
    env = os.environ.copy()
    env["NCMU_DB_URL"] = url
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *args],
        cwd=_REPO_ROOT,
        env=env,
        check=True,
    )


@pytest.fixture
def test_db_url(postgresql) -> str:
    """Yield a SQLAlchemy URL to a freshly-created ephemeral test database.

    The database is created by pytest-postgresql's DatabaseJanitor before the
    test and dropped after. No schema is applied; callers run alembic as needed.
    """
    info = postgresql.info
    return (
        f"postgresql+psycopg://{info.user}:{info.password}"
        f"@{info.host}:{info.port}/{info.dbname}"
    )


@pytest.fixture
def db_session(test_db_url: str):
    """Post-`alembic upgrade head` SQLAlchemy Session (sync).

    Each test gets a fresh DB with the latest schema applied. Use this fixture
    for schema-layer tests (CHECK constraints, partial indexes, etc.).
    """
    _run_alembic(test_db_url, "upgrade", "head")
    engine = create_engine(test_db_url, future=True)
    try:
        with Session(engine) as session:
            yield session
    finally:
        engine.dispose()
