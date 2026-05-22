"""Personal KB sub-package — employee KB-build application + admin dispatch.

Module layout (Phase 2C TASK-PC2-A):
- file_storage.py        StorageBackend Protocol + LocalFsBackend impl
- application_service.py state machine (5 legal transitions, 20 illegal) + DB
- routes.py              employee 3 endpoints (POST / GET list / DELETE)
- admin_routes.py        admin 6 endpoints (list / detail / download / claim /
                         dispatch / reject)

PC1 ORM gap workaround (PC2-A latent fix, 2026-05-21):
``db/models.py`` declares ``PersonalKbApplication.dify_external_kb_config_id``
as ``ForeignKey("dify_external_kb_configs.id")`` but no ORM class for
``dify_external_kb_configs`` (or ``dify_app_kb_bindings``) exists — those
two tables are created by alembic 0002/0003 and the rest of the backend
hits them via raw ``text(...)``. The string-based FK target therefore
can't be resolved at SQLAlchemy mapper-configure time, breaking
``db.add(PersonalKbApplication(...))`` with ``NoReferencedTableError``.

To unblock PC2-A *without* modifying PC1 files, we declare minimal
``Table`` proxies against the same ``Base.metadata`` here — just enough
columns for the FK target to resolve. Alembic remains the source of
truth for the real DDL; these stubs are pure metadata hooks.

Follow-up (out of PC2-A scope): land proper ORM classes for
dify_external_kb_configs / dify_app_kb_bindings in db/models.py and
drop these stubs. Tracked as candidate B-NEW for the Phase 3 cleanup
batch.
"""
from __future__ import annotations

from sqlalchemy import Column, Table
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from ncmu_backend.db.models import Base


# Minimal proxies — only the PK column the FK string targets.
Table(
    "dify_external_kb_configs",
    Base.metadata,
    Column("id", PG_UUID(as_uuid=True), primary_key=True),
    extend_existing=True,
)
Table(
    "dify_app_kb_bindings",
    Base.metadata,
    Column("id", PG_UUID(as_uuid=True), primary_key=True),
    extend_existing=True,
)
