"""TASK-67b AC#12 — DifyApp.mode field smoke tests (≥2 cases).

Cases:
  (a) DifyApp.mode column is declared on the ORM class
  (b) Insert a DifyApp without specifying `mode` → server_default 'chat'
      flows through and is read back

Both depend on alembic 0005 having added the `mode` column to dify_apps;
the `db_session` fixture runs `alembic upgrade head` before yielding,
so the column must be present.
"""
from __future__ import annotations

from sqlalchemy import text


def test_dify_app_mode_column_declared():
    """The ORM class must expose `mode` as a Mapped column."""
    from ncmu_backend.db.models import DifyApp

    cols = {c.name for c in DifyApp.__table__.columns}
    assert "mode" in cols, f"DifyApp.__table__.columns missing 'mode'; got {sorted(cols)}"
    mode_col = DifyApp.__table__.columns["mode"]
    # The column must be NOT NULL with a server_default of 'chat'.
    assert mode_col.nullable is False
    # server_default surfaces as a DefaultClause whose `.arg` is either a
    # plain string ('chat') or a TextClause; either form is acceptable as
    # long as it serialises to 'chat' in the DDL — the (b) case below is
    # the load-bearing assertion that the runtime default really is 'chat'.
    assert mode_col.server_default is not None


def test_dify_app_mode_server_default_is_chat(db_session):
    """Insert a DifyApp without `mode` → read back mode == 'chat'.

    This is the runtime check that alembic 0005 actually applied
    `server_default="chat"` so existing 67a-era rows + future inserts
    that omit the field both end up with the expected default.
    """
    from ncmu_backend.db.models import DifyApp

    row = DifyApp(dify_app_id="app-default-mode", name="Default Mode Test")
    db_session.add(row)
    db_session.commit()

    fetched_mode = db_session.execute(
        text("SELECT mode FROM dify_apps WHERE dify_app_id = :id"),
        {"id": "app-default-mode"},
    ).scalar_one()
    assert fetched_mode == "chat", f"expected default 'chat'; got {fetched_mode!r}"
