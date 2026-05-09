"""TASK-67b AC#11 — WorkflowRun ORM smoke tests (≥3 cases).

Cases:
  (a) WorkflowRun class can be imported + __tablename__ == 'workflow_runs'
  (b) Insert a parent dify_apps row + a workflow_runs row referencing it,
      then read back app_id / user_id / mode / status are 字面一致
  (c) FK enforcement: insert a workflow_runs row whose `app_id` does NOT
      exist in dify_apps → IntegrityError on commit
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError


_ZHANGSAN_UUID = uuid.UUID("a0000001-0000-4000-8000-000000000001")


def _seed_user(db_session) -> uuid.UUID:
    """Insert a User row so workflow_runs.user_id has a valid FK target."""
    from ncmu_backend.db.models import User

    user = User(id=_ZHANGSAN_UUID, name="张三")
    db_session.add(user)
    db_session.commit()
    return _ZHANGSAN_UUID


def test_workflow_run_class_import_and_tablename():
    from ncmu_backend.db.models import WorkflowRun

    assert WorkflowRun.__tablename__ == "workflow_runs"


def test_workflow_run_insert_and_readback(db_session):
    """Insert dify_apps parent + workflow_runs child, then read back fields."""
    from ncmu_backend.db.models import DifyApp, WorkflowRun

    user_id = _seed_user(db_session)

    app = DifyApp(dify_app_id="app-wf-1", name="Workflow App", mode="workflow")
    db_session.add(app)
    db_session.commit()

    run_id = uuid.uuid4()
    run = WorkflowRun(
        run_id=run_id,
        app_id="app-wf-1",
        user_id=user_id,
        mode="workflow",
        status="running",
    )
    db_session.add(run)
    db_session.commit()

    fetched = db_session.get(WorkflowRun, run_id)
    assert fetched is not None
    assert fetched.app_id == "app-wf-1"
    assert fetched.user_id == user_id
    assert fetched.mode == "workflow"
    assert fetched.status == "running"


def test_workflow_run_app_id_fk_enforced(db_session):
    """workflow_runs.app_id must reference an existing dify_apps row.

    Inserting with a non-existent `app_id` should raise IntegrityError on
    commit (FK constraint violation). The user FK target is real so this
    isolates the dify_apps FK as the cause.
    """
    from ncmu_backend.db.models import WorkflowRun

    user_id = _seed_user(db_session)

    run = WorkflowRun(
        run_id=uuid.uuid4(),
        app_id="app-does-not-exist",
        user_id=user_id,
        mode="chat",
        status="running",
    )
    db_session.add(run)
    with pytest.raises(IntegrityError):
        db_session.commit()
