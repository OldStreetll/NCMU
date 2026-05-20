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


@pytest.fixture(scope="session", autouse=True)
def _drop_stale_test_database():
    """B-NEW-30: pre-drop any stale ``ncmu_test`` left from crashed prior runs.

    pytest-postgresql's ``DatabaseJanitor`` only drops the test DB if the
    opt-in ``--postgresql-drop-test-database`` CLI flag is passed
    (``store_true`` in ``plugin.py``; default False). When the previous
    pytest invocation died mid-test (Ctrl-C, SIGKILL, network glitch),
    ``ncmu_test`` is left over and the next run errors with
    ``psycopg.errors.DuplicateDatabase`` at the very first fixture setup
    — masking whichever test you actually want to run.

    This session-scoped autouse fixture terminates any leftover backends
    still attached to ``ncmu_test`` and drops the database before the
    session starts. Connection failures (postgres not up yet) are
    swallowed so pytest-postgresql's own retry logic surfaces the real
    error rather than this fixture's symptom.
    """
    import psycopg
    admin_dsn = (
        f"host={os.environ.get('PYTEST_PG_HOST', 'localhost')} "
        f"port={os.environ.get('PYTEST_PG_PORT', '5432')} "
        f"user={os.environ.get('PYTEST_PG_USER', 'ncmu_app')} "
        f"password={os.environ.get('PYTEST_PG_PASSWORD', 'CHANGE_ME')} "
        f"dbname=ncmu"
    )
    try:
        with psycopg.connect(admin_dsn, autocommit=True) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = 'ncmu_test' AND pid <> pg_backend_pid()"
            )
            cur.execute("DROP DATABASE IF EXISTS ncmu_test")
    except psycopg.Error:
        # Best-effort pre-cleanup: swallow ALL psycopg errors so this
        # fixture's environment-dependent failures (postgres-not-up /
        # ncmu-DB-absent / insufficient-privilege) don't block test
        # sessions that don't even use postgres. pytest-postgresql's
        # later fixtures will surface real infra errors with clearer
        # messages.
        pass
    yield


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


# =====================================================================
# TASK-25 — async fixtures (A-1 修订: appended to TASK-22's sync block)
# ---------------------------------------------------------------------
# Sync `db_session` above is for schema tests (alembic / CHECK
# constraints / partial indexes). Business-logic tests (test_auth,
# test_apps, test_sessions, test_admin) must run on the same async
# stack as the production code: AsyncSession + async FastAPI routes
# + httpx.AsyncClient against an ASGI transport.
#
# pytest-asyncio is in `asyncio_mode = "auto"` (pyproject.toml), so
# `async def` test functions don't need an explicit @pytest.mark.asyncio.
# =====================================================================
import asyncio
import uuid

import httpx
import pytest_asyncio
import respx as _respx_lib
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def _to_async_url(sync_url: str) -> str:
    """psycopg sync URL -> asyncpg URL. Mirrors db.session._build_async_url
    but kept tiny so the conftest stays standalone — the production code
    is exercised by direct invocation, not by sharing fixtures back."""
    if sync_url.startswith("postgresql+psycopg://"):
        return sync_url.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return sync_url


@pytest_asyncio.fixture
async def async_db(test_db_url: str):
    """Yield an AsyncSession bound to a freshly-migrated ephemeral DB.

    Each test that takes this fixture gets:
      - a brand-new postgres database (pytest-postgresql)
      - schema applied via `alembic upgrade head` (sync subprocess)
      - 5 dev seed users inserted via dev_users.sql (so test_auth can
        immediately exercise the dev-login flow without bespoke seeding)
    """
    _run_alembic(test_db_url, "upgrade", "head")
    # Apply dev seeds — same path the container's entrypoint.sh uses.
    seed_path = _REPO_ROOT / "alembic" / "seeds" / "dev_users.sql"
    if seed_path.exists():
        # Reuse psycopg (sync) for the one-shot seed — no need to spin
        # up an async engine just for an INSERT.
        import psycopg
        with psycopg.connect(test_db_url.replace("+psycopg", ""), autocommit=True) as c:
            c.execute(seed_path.read_text())

    engine = create_async_engine(_to_async_url(test_db_url))
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def app_client(async_db: AsyncSession, monkeypatch):
    """Yield an httpx.AsyncClient hitting the FastAPI app via ASGI transport.

    Every backend test that exercises HTTP semantics (status codes,
    JSON shape, header checks) takes this fixture. The fixture:
      - sets dev-friendly env vars (NCMU_DB_URL → the ephemeral DB,
        NCMU_ENABLE_DEV_LOGIN=true, deterministic NCMU_JWT_SECRET);
      - imports `ncmu_backend.main:app` AFTER env is patched (so
        Settings() picks up the test values);
      - overrides `get_db` so endpoints share the test's `async_db`
        session (matters for transaction visibility in the same test).
    """
    test_db_url = str(async_db.bind.url).replace("+asyncpg", "")  # readable for env
    monkeypatch.setenv(
        "NCMU_DB_URL",
        test_db_url if test_db_url.startswith("postgresql://") else "postgresql://" + test_db_url.split("://", 1)[1],
    )
    monkeypatch.setenv("NCMU_JWT_SECRET", "test-secret-deterministic-32-bytes-long-xx")
    monkeypatch.setenv("NCMU_ENABLE_DEV_LOGIN", "true")
    monkeypatch.setenv("DEPLOY_PROFILE", "dev")

    # Import-after-monkeypatch so Settings() is built from the test env.
    from ncmu_backend.config import reset_settings_cache
    reset_settings_cache()
    from ncmu_backend.main import app
    from ncmu_backend.db.session import get_db

    async def _override_get_db():
        yield async_db
    app.dependency_overrides[get_db] = _override_get_db

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        yield client

    app.dependency_overrides.clear()
    reset_settings_cache()


@pytest.fixture
def jwt_secret() -> str:
    """Deterministic secret used both inside the app (via NCMU_JWT_SECRET
    env in app_client) and in tests that craft custom tokens."""
    return "test-secret-deterministic-32-bytes-long-xx"


@pytest.fixture
def jwt_token(jwt_secret: str):
    """Pre-signed JWT for protected-endpoint tests in TASK-26/27/28.

    Signs a token for the dev seed admin (张三, UUID a0...0001).
    Tests can call this fixture and then attach `Authorization:
    Bearer <token>` to their requests instead of going through
    /auth/dev-login on every test.
    """
    from ncmu_backend.auth.jwt_utils import sign_jwt
    token, _exp = sign_jwt(
        user_id="a0000001-0000-4000-8000-000000000001",
        name="张三",
        secret=jwt_secret,
    )
    return token


@pytest.fixture
def respx_mock():
    """A respx router context — used by TASK-27 to mock Dify SSE upstream.

    `respx.mock()` wraps the entire httpx call surface for the test;
    routes added inside `with respx_mock as router: router.post(...)`
    intercept all httpx requests during the test.
    """
    return _respx_lib.mock(assert_all_called=False)


# =====================================================================
# TASK-27 — chat-specific fixtures
# ---------------------------------------------------------------------
# The chat orchestrator streams from a httpx.AsyncClient injected via
# `Depends(get_dify_client)`. Real respx struggles with multi-chunk SSE
# in stream-mode, so these tests substitute a tiny in-process fake that
# exposes just `client.stream("POST", url, ...)` returning a context
# manager whose `aiter_bytes()` yields canned bytes (typically taken
# from the real Dify samples archived under
# NCMU-Wiki/sources/phase1/dify-chat-sample-2026-04-25/).
# =====================================================================


class _FakeDifyResponse:
    def __init__(self, sse_bytes: bytes, status_code: int = 200,
                 chunk_size: int = 4096, chunk_delay_s: float = 0.0):
        self.status_code = status_code
        self._bytes = sse_bytes
        self._chunk = chunk_size
        self._delay = chunk_delay_s

    async def aiter_bytes(self):
        if not self._bytes:
            return
        for i in range(0, len(self._bytes), self._chunk):
            if self._delay:
                await asyncio.sleep(self._delay)
            yield self._bytes[i:i + self._chunk]


class _FakeStreamCM:
    def __init__(self, holder: dict):
        self._holder = holder

    async def __aenter__(self):
        if self._holder.get("raise"):
            raise self._holder["raise"]
        return _FakeDifyResponse(
            self._holder["sse_bytes"],
            status_code=self._holder.get("status", 200),
            chunk_size=self._holder.get("chunk_size", 4096),
            chunk_delay_s=self._holder.get("chunk_delay_s", 0.0),
        )

    async def __aexit__(self, *exc_info):
        return False


class _FakeDifyClient:
    """Stand-in for the lifespan-owned ``httpx.AsyncClient`` exposed via
    :func:`ncmu_backend.main.get_dify_client`. Tests mutate
    ``self.holder`` to set what the next ``stream(...)`` call should
    surface; ``calls`` records every invocation for assertions.
    """

    def __init__(self):
        self.holder: dict = {"sse_bytes": b"", "status": 200, "raise": None,
                             "chunk_size": 4096, "chunk_delay_s": 0.0}
        self.calls: list[tuple[str, str, dict]] = []

    def set_sse(self, sse_bytes: bytes, *, status: int = 200,
                chunk_size: int = 4096, chunk_delay_s: float = 0.0) -> None:
        self.holder["sse_bytes"] = sse_bytes
        self.holder["status"] = status
        self.holder["raise"] = None
        self.holder["chunk_size"] = chunk_size
        self.holder["chunk_delay_s"] = chunk_delay_s

    def set_raise(self, exc: BaseException) -> None:
        self.holder["raise"] = exc

    def stream(self, method: str, url: str, **kwargs):
        self.calls.append((method, url, kwargs))
        return _FakeStreamCM(self.holder)


@pytest_asyncio.fixture
async def fake_dify(app_client):
    """Override ``get_dify_client`` so the chat orchestrator uses an
    in-process fake. Yields the :class:`_FakeDifyClient` so the test can
    set the canned SSE payload and inspect call records.

    Depends on ``app_client`` so its dependency_overrides clear cleanly
    after the test (app_client's teardown calls ``app.dependency_overrides.clear()``).
    """
    from ncmu_backend.main import app, get_dify_client

    fake = _FakeDifyClient()
    app.dependency_overrides[get_dify_client] = lambda: fake
    yield fake
