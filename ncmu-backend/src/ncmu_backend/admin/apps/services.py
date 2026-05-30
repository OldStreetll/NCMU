"""Service helpers for admin App management (TASK-PE-07).

Keeps the ``dify_apps`` query shape in one place so the list path and the
single-row (detail / PATCH echo) path stay consistent. ``tag_count`` is a
literal 0 for now — PE-08 swaps it for a ``app_tags`` LEFT-JOIN count
(mirror of admin/tags/services.py ``_tags_with_counts_query``) without
churning the route layer.

Why ORM ``select(DifyApp)`` (not Core ``text()`` like admin/routes.py's
two debug GETs): the ``DifyApp`` ORM model *does* exist (unlike
dify_external_kb_configs / dify_app_kb_bindings), so the typed path is
the natural fit + ``from_attributes=True`` lets AdminAppOut validate the
ORM row directly.
"""
from __future__ import annotations

from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ncmu_backend.admin.apps.schemas import AdminAppOut
from ncmu_backend.db.models import DifyApp


def _to_out(app: DifyApp) -> AdminAppOut:
    """Map a ``DifyApp`` ORM row → ``AdminAppOut`` (tag_count placeholder 0)."""
    return AdminAppOut(
        dify_app_id=app.dify_app_id,
        name=app.name,
        mode=app.mode,
        is_active=app.is_active,
        last_synced_at=app.last_synced_at,
        tag_count=0,  # PE-08: replace with app_tags JOIN count.
    )


async def list_admin_apps(
    db: AsyncSession,
    *,
    include_inactive: bool = False,
    search: str | None = None,
) -> list[AdminAppOut]:
    """All cached apps, name-ordered.

    - ``include_inactive=False`` (default) hides ``is_active=false`` rows
      so the admin's default view matches what employees can reach.
    - ``search`` does a case-insensitive substring match on ``name``
      (``ILIKE %term%``); blank/whitespace-only terms are ignored.
    """
    q = select(DifyApp)
    if not include_inactive:
        q = q.where(DifyApp.is_active.is_(True))
    if search and search.strip():
        q = q.where(DifyApp.name.ilike(f"%{search.strip()}%"))
    rows = (await db.execute(q.order_by(DifyApp.name))).scalars().all()
    return [_to_out(a) for a in rows]


async def get_admin_app(db: AsyncSession, app_id: str) -> AdminAppOut | None:
    """Single app by ``dify_app_id`` PK, or None if not found."""
    app = await db.get(DifyApp, app_id)
    if app is None:
        return None
    return _to_out(app)


__all__: Sequence[str] = (
    "list_admin_apps",
    "get_admin_app",
)
