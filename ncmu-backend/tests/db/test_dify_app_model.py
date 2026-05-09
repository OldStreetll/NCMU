"""TASK-67a AC#9 — DifyApp ORM smoke tests (≥2 cases).

Cases:
  (a) DifyApp class can be imported + __tablename__ == "dify_apps"
  (b) SQLAlchemy session insert 1 row + read back dify_app_id / name 字面一致
"""
from __future__ import annotations


def test_dify_app_class_import_and_tablename():
    from ncmu_backend.db.models import DifyApp

    assert DifyApp.__tablename__ == "dify_apps"


def test_dify_app_insert_and_readback(db_session):
    """db_session fixture runs `alembic upgrade head` against an
    ephemeral postgres → migration 0004 must have created `dify_apps`
    before this point. Insert 1 row, read it back via `Session.get`,
    confirm dify_app_id + name字段字面一致."""
    from ncmu_backend.db.models import DifyApp

    row = DifyApp(dify_app_id="app-test-1", name="Test KB App")
    db_session.add(row)
    db_session.commit()

    fetched = db_session.get(DifyApp, "app-test-1")
    assert fetched is not None
    assert fetched.dify_app_id == "app-test-1"
    assert fetched.name == "Test KB App"
