"""TASK-69 AC#2 — workflow CRUD tests (≥6 cases incl. H5 guard 2 cases +
concurrent append + jsonb || atomic).
"""
from __future__ import annotations

import asyncio
import uuid

import pytest_asyncio
from sqlalchemy import text

from ncmu_backend.workflow import crud


# Dev seed admin (alembic seeds dev_users.sql); FK target on workflow_runs.
ADMIN_USER_ID = uuid.UUID("a0000001-0000-4000-8000-000000000001")
APP_ID = "app-test-crud"


@pytest_asyncio.fixture
async def seeded_db(async_db):
    """Async DB with one dify_apps row so workflow_runs FK can land."""
    await async_db.execute(text(
        "INSERT INTO dify_apps (dify_app_id, name, mode) "
        "VALUES (:id, 'Test App', 'workflow')"
    ), {"id": APP_ID})
    await async_db.commit()
    yield async_db


# --------------------------------------------------------------------- #
# (a) create_run inserts row with status='running'
# --------------------------------------------------------------------- #
async def test_create_run_writes_running_row(seeded_db):
    run_id = uuid.uuid4()
    await crud.create_run(seeded_db, run_id, APP_ID, ADMIN_USER_ID, "workflow", {"x": 1})

    status = (await seeded_db.execute(
        text("SELECT status FROM workflow_runs WHERE run_id = :rid"),
        {"rid": run_id},
    )).scalar_one()
    assert status == "running"


# --------------------------------------------------------------------- #
# (b) append_node_event x2 grows node_trace + last entry matches
# --------------------------------------------------------------------- #
async def test_append_node_event_grows_jsonb_array(seeded_db):
    run_id = uuid.uuid4()
    await crud.create_run(seeded_db, run_id, APP_ID, ADMIN_USER_ID, "workflow", {})
    await crud.append_node_event(
        seeded_db, run_id,
        {"event_type": "node_started", "data": {"node_id": "n1"}},
    )
    await crud.append_node_event(
        seeded_db, run_id,
        {"event_type": "node_finished", "data": {"node_id": "n1"}},
    )

    row = (await seeded_db.execute(
        text(
            "SELECT jsonb_array_length(node_trace) AS n, "
            "       node_trace -> 1 ->> 'event_type' AS last_evt "
            "FROM workflow_runs WHERE run_id = :rid"
        ),
        {"rid": run_id},
    )).one()
    assert row.n == 2
    assert row.last_evt == "node_finished"


# --------------------------------------------------------------------- #
# (c) concurrent append: 2 tasks * 5 each → length == 10 (jsonb || atomic)
# --------------------------------------------------------------------- #
async def test_append_node_event_concurrent_atomic(seeded_db, test_db_url):
    """Two coroutines append 5 events each via independent sessions; the
    final node_trace JSONB array must contain all 10. Each coroutine uses
    its own AsyncSession bound to the same ephemeral DB so the `jsonb ||`
    operator is exercised under interleaved transactions.
    """
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    run_id = uuid.uuid4()
    await crud.create_run(seeded_db, run_id, APP_ID, ADMIN_USER_ID, "workflow", {})
    # Commit so other sessions see the row.
    await seeded_db.commit()

    async_url = test_db_url.replace("+psycopg", "+asyncpg") if "+psycopg" in test_db_url \
        else test_db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(async_url)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def appender(label: str):
        async with factory() as s:
            for i in range(5):
                await crud.append_node_event(
                    s, run_id, {"event_type": "ping", "i": i, "src": label}
                )

    await asyncio.gather(appender("a"), appender("b"))
    await engine.dispose()

    n = (await seeded_db.execute(
        text("SELECT jsonb_array_length(node_trace) FROM workflow_runs WHERE run_id = :rid"),
        {"rid": run_id},
    )).scalar_one()
    assert n == 10


# --------------------------------------------------------------------- #
# (d) finalize_run('succeeded') sets finished_at + status
# --------------------------------------------------------------------- #
async def test_finalize_run_succeeded_marks_finished(seeded_db):
    run_id = uuid.uuid4()
    await crud.create_run(seeded_db, run_id, APP_ID, ADMIN_USER_ID, "workflow", {})
    await crud.finalize_run(seeded_db, run_id, "succeeded", outputs={"answer": "ok"})

    row = (await seeded_db.execute(
        text(
            "SELECT status, outputs ->> 'answer' AS ans, "
            "       finished_at IS NOT NULL AS done "
            "FROM workflow_runs WHERE run_id = :rid"
        ),
        {"rid": run_id},
    )).one()
    assert row.status == "succeeded"
    assert row.ans == "ok"
    assert row.done is True


# --------------------------------------------------------------------- #
# (e) H5 guard: cancelled then succeeded → status stays 'cancelled'
# --------------------------------------------------------------------- #
async def test_finalize_run_h5_guard_cancelled_not_overwritten(seeded_db):
    run_id = uuid.uuid4()
    await crud.create_run(seeded_db, run_id, APP_ID, ADMIN_USER_ID, "workflow", {})
    await crud.finalize_run(seeded_db, run_id, "cancelled")
    # Second call must not reach this terminal row.
    await crud.finalize_run(seeded_db, run_id, "succeeded", outputs={"answer": "x"})

    row = (await seeded_db.execute(
        text("SELECT status, outputs FROM workflow_runs WHERE run_id = :rid"),
        {"rid": run_id},
    )).one()
    assert row.status == "cancelled"
    # outputs should still be the create_run default {} (succeeded path skipped).
    assert row.outputs == {}


# --------------------------------------------------------------------- #
# (f) H5 guard 2: db_error then failed(error_msg='abc') → ('db_error', NULL)
# --------------------------------------------------------------------- #
async def test_finalize_run_h5_guard_db_error_not_overwritten(seeded_db):
    run_id = uuid.uuid4()
    await crud.create_run(seeded_db, run_id, APP_ID, ADMIN_USER_ID, "workflow", {})
    await crud.finalize_run(seeded_db, run_id, "db_error")  # no error_msg
    await crud.finalize_run(seeded_db, run_id, "failed", error_msg="abc")

    row = (await seeded_db.execute(
        text("SELECT status, error_msg FROM workflow_runs WHERE run_id = :rid"),
        {"rid": run_id},
    )).one()
    assert row.status == "db_error"
    assert row.error_msg is None


# --------------------------------------------------------------------- #
# (g) extra: list_runs filters by app_id + user_id, sorts started_at DESC
# --------------------------------------------------------------------- #
async def test_list_runs_filters_and_sorts(seeded_db):
    rid_old = uuid.uuid4()
    rid_new = uuid.uuid4()
    await crud.create_run(seeded_db, rid_old, APP_ID, ADMIN_USER_ID, "workflow", {})
    await crud.create_run(seeded_db, rid_new, APP_ID, ADMIN_USER_ID, "workflow", {})
    # Force the old row to have an earlier started_at.
    await seeded_db.execute(
        text("UPDATE workflow_runs SET started_at = NOW() - INTERVAL '1 hour' "
             "WHERE run_id = :rid"),
        {"rid": rid_old},
    )
    await seeded_db.commit()

    runs = await crud.list_runs(seeded_db, APP_ID, ADMIN_USER_ID)
    assert [r.run_id for r in runs[:2]] == [rid_new, rid_old]
