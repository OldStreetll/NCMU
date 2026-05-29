"""Service helpers for admin tags CRUD (TASK-PE-05).

The route handlers in :mod:`ncmu_backend.admin.tags.routes` keep most
SQL inline (mirror of ``admin.users.routes``), but the
``app_count + user_count`` projection — which needs a two-subquery
LEFT JOIN to stay one round-trip — lives here so the list path and the
post-mutation refetch path share the same query shape.
"""
from __future__ import annotations

from typing import Sequence
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ncmu_backend.admin.tags.schemas import TagOut
from ncmu_backend.db.models import AppTag, Tag, UserTag


def _tags_with_counts_query() -> Select:
    """Build the LEFT-JOIN query used by both list + single-row refetch.

    Returns a Core ``Select`` with 5 columns: ``Tag.id``, ``Tag.name``,
    ``Tag.description``, ``app_count`` (int, coalesced 0), ``user_count``
    (int, coalesced 0). Two subqueries on the join tables aggregate
    counts per ``tag_id`` so the outer scan over ``tags`` stays a single
    sequential pass (``ix_app_tags_tag_id`` + ``ix_user_tags_tag_id``
    back the subquery group-bys).
    """
    app_counts = (
        select(AppTag.tag_id, func.count().label("cnt"))
        .group_by(AppTag.tag_id)
        .subquery("app_counts")
    )
    user_counts = (
        select(UserTag.tag_id, func.count().label("cnt"))
        .group_by(UserTag.tag_id)
        .subquery("user_counts")
    )
    return (
        select(
            Tag.id,
            Tag.name,
            Tag.description,
            func.coalesce(app_counts.c.cnt, 0).label("app_count"),
            func.coalesce(user_counts.c.cnt, 0).label("user_count"),
        )
        .select_from(Tag)
        .outerjoin(app_counts, app_counts.c.tag_id == Tag.id)
        .outerjoin(user_counts, user_counts.c.tag_id == Tag.id)
    )


async def list_tags_with_counts(db: AsyncSession) -> list[TagOut]:
    """All tags + their bind counts, newest first (``created_at DESC``)."""
    q = _tags_with_counts_query().order_by(Tag.created_at.desc())
    rows = (await db.execute(q)).all()
    return [
        TagOut(
            id=row.id,
            name=row.name,
            description=row.description,
            app_count=row.app_count,
            user_count=row.user_count,
        )
        for row in rows
    ]


async def get_tag_with_counts(db: AsyncSession, tag_id: UUID) -> TagOut | None:
    """Single tag + counts (used by POST/PATCH response echo)."""
    q = _tags_with_counts_query().where(Tag.id == tag_id)
    row = (await db.execute(q)).first()
    if row is None:
        return None
    return TagOut(
        id=row.id,
        name=row.name,
        description=row.description,
        app_count=row.app_count,
        user_count=row.user_count,
    )


__all__: Sequence[str] = (
    "list_tags_with_counts",
    "get_tag_with_counts",
)
