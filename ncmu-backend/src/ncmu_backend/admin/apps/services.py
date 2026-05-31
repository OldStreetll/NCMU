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

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ncmu_backend.admin.apps.schemas import AdminAppOut
from ncmu_backend.db.models import AppTag, DifyApp


def _to_out(app: DifyApp, tag_count: int) -> AdminAppOut:
    """Map a ``DifyApp`` ORM row + its bound-tag count → ``AdminAppOut``."""
    return AdminAppOut(
        dify_app_id=app.dify_app_id,
        name=app.name,
        mode=app.mode,
        is_active=app.is_active,
        last_synced_at=app.last_synced_at,
        tag_count=tag_count,
    )


def _apps_with_tag_count_query() -> Select:
    """``select(DifyApp, tag_count)`` with a LEFT JOIN to an ``app_tags``
    count subquery (mirror of admin/tags/services.py ``_tags_with_counts_query``).

    PE-08: replaces PE-07's literal ``tag_count=0`` placeholder. The
    subquery groups ``app_tags`` by ``dify_app_id``; ``coalesce(_, 0)``
    makes apps with no bindings report 0 (not NULL). ``ix`` on
    ``app_tags`` PK (dify_app_id, tag_id) backs the group-by.
    """
    counts = (
        select(AppTag.dify_app_id, func.count().label("cnt"))
        .group_by(AppTag.dify_app_id)
        .subquery("app_tag_counts")
    )
    return (
        select(DifyApp, func.coalesce(counts.c.cnt, 0).label("tag_count"))
        .select_from(DifyApp)
        .outerjoin(counts, counts.c.dify_app_id == DifyApp.dify_app_id)
    )


async def list_admin_apps(
    db: AsyncSession,
    *,
    include_inactive: bool = False,
    search: str | None = None,
) -> list[AdminAppOut]:
    """All cached apps + real bound-tag count, name-ordered.

    - ``include_inactive=False`` (default) hides ``is_active=false`` rows
      so the admin's default view matches what employees can reach.
    - ``search`` does a case-insensitive substring match on ``name``
      (``ILIKE %term%``); blank/whitespace-only terms are ignored.
    """
    q = _apps_with_tag_count_query()
    if not include_inactive:
        q = q.where(DifyApp.is_active.is_(True))
    if search and search.strip():
        q = q.where(DifyApp.name.ilike(f"%{search.strip()}%"))
    rows = (await db.execute(q.order_by(DifyApp.name))).all()
    return [_to_out(row[0], row.tag_count) for row in rows]


async def get_admin_app(db: AsyncSession, app_id: str) -> AdminAppOut | None:
    """Single app by ``dify_app_id`` PK + real tag_count, or None if absent."""
    q = _apps_with_tag_count_query().where(DifyApp.dify_app_id == app_id)
    row = (await db.execute(q)).first()
    if row is None:
        return None
    return _to_out(row[0], row.tag_count)


__all__: Sequence[str] = (
    "list_admin_apps",
    "get_admin_app",
)
