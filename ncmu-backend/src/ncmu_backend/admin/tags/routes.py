"""TASK-PE-05 — admin tags CRUD endpoints.

All routes are gated by :func:`require_admin` so the PE-02 invariance
test (``tests/admin/test_require_admin_audit.py``) auto-detects them.

URL surface:

    GET    /api/v1/ncmu/admin/tags              list (with app_count + user_count)
    POST   /api/v1/ncmu/admin/tags              create one
    PATCH  /api/v1/ncmu/admin/tags/{tag_id}     partial update
    DELETE /api/v1/ncmu/admin/tags/{tag_id}     hard-delete (FK CASCADE clears bindings)

Error envelope shape — ``{"detail": {"code": <int>, "message": "..."}}`` —
matches ``fastgpt_readonly/errors.py``, ``auth/deps.py``, and
``admin/users/routes.py`` so the SPA can read every admin error
uniformly.

Error codes used here (Boss 2026-05-29 拍板 A — adjacent to PE-06's
10xx family rather than reusing 1010/1011 which carry user-CRUD
semantics):

  1012  tag name UNIQUE conflict (POST + PATCH 409)
  1013  tag not found by id     (PATCH / DELETE 404)
  1201  admin permission required (raised by ``require_admin``)

Delete semantics — **hard delete** (mirror plan §3 line 929-935): row is
removed and FK ``ondelete=CASCADE`` on ``app_tags.tag_id`` /
``user_tags.tag_id`` sweeps the join rows. The SPA Popconfirm shows
``app_count + user_count`` before the admin confirms so the cascade is
visible, not hidden. This is intentionally different from PE-06's
soft-delete on ``users`` — users keep history rows (FK targets);
``tags`` are admin-visible labels with no audit trail to preserve.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ncmu_backend.admin.tags.schemas import (
    TagAppsOut,
    TagAppsReplaceResult,
    TagBindAppsRequest,
    TagCreate,
    TagOut,
    TagUpdate,
)
from ncmu_backend.admin.tags.services import (
    get_tag_with_counts,
    list_tags_with_counts,
)
from ncmu_backend.auth.deps import CurrentUser, require_admin
from ncmu_backend.db.models import AppTag, DifyApp, Tag
from ncmu_backend.db.session import get_db

router = APIRouter(tags=["admin-tags"])


def _not_found(tag_id: UUID) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": 1013, "message": f"tag {tag_id} not found"},
    )


def _binding_targets_not_found(kind: str, missing: list[str]) -> HTTPException:
    # TASK-PE-08: 1015 — one or more ids in a replace-all binding body do
    # not exist (next free code after PE-07's 1014). ``app_tags`` has no FK
    # to ``dify_apps`` (so a phantom ``dify_app_id`` would silently insert),
    # and the FK to ``tags.id`` would otherwise surface a raw IntegrityError;
    # validating up-front gives the SPA a clean 404 either way.
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={
            "code": 1015,
            "message": f"{kind} not found: {', '.join(missing)}",
        },
    )


def _name_conflict(name: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": 1012, "message": f"tag '{name}' already exists"},
    )


@router.get(
    "/api/v1/ncmu/admin/tags",
    response_model=list[TagOut],
    summary="List all tags with app_count + user_count (newest first)",
)
async def list_tags(
    _: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[TagOut]:
    """Plan §3 PE-05 Step 3 — single LEFT-JOIN list (no pagination yet).

    PE-08/-09 may add a search / pagination wrapper once the tag volume
    grows past dozens, but the v1 admin UX is a single table view of
    all tags so this stays a one-call endpoint.
    """
    return await list_tags_with_counts(db)


@router.post(
    "/api/v1/ncmu/admin/tags",
    response_model=TagOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create one tag (UNIQUE on name; 409 + code 1012 on conflict)",
)
async def create_tag(
    body: TagCreate,
    _: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> TagOut:
    tag = Tag(name=body.name, description=body.description)
    db.add(tag)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise _name_conflict(body.name)
    await db.refresh(tag)
    # Freshly created tag has no bindings yet — counts are deterministically 0/0.
    # Skip the round-trip to ``get_tag_with_counts`` (same shape, known result).
    return TagOut(
        id=tag.id,
        name=tag.name,
        description=tag.description,
        app_count=0,
        user_count=0,
    )


@router.patch(
    "/api/v1/ncmu/admin/tags/{tag_id}",
    response_model=TagOut,
    summary="Partial update — name and/or description (UNIQUE conflict → 409/1012)",
)
async def update_tag(
    tag_id: UUID,
    body: TagUpdate,
    _: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> TagOut:
    tag = await db.get(Tag, tag_id)
    if tag is None:
        raise _not_found(tag_id)

    # Only assign fields actually sent (``exclude_unset=True`` keeps
    # explicit None / empty string distinct from "absent"). Pydantic v2
    # idiom — same as admin.users.routes.update_user.
    updates = body.model_dump(exclude_unset=True)
    # Capture the would-be name BEFORE the try block so the 409 echo
    # doesn't trigger a lazy-load on the post-rollback expired ORM obj
    # (asyncpg + SQLAlchemy ``MissingGreenlet`` if attr touched after
    # the session rolls back during the same request).
    intended_name = updates.get("name", tag.name)
    for field, value in updates.items():
        setattr(tag, field, value)

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise _name_conflict(intended_name)
    await db.refresh(tag)

    # Tag may already have bindings — refetch the JOIN counts so the
    # SPA's row update reflects the real numbers, not stale 0/0.
    result = await get_tag_with_counts(db, tag.id)
    # ``tag`` is still in the session after refresh; counts query can
    # only return None if the row vanished mid-flight — defensive guard
    # mirrors ``admin.users.routes`` style (don't trust hypothetical
    # races, but don't ``raise None.__class__`` if they happen).
    if result is None:
        raise _not_found(tag.id)
    return result


@router.delete(
    "/api/v1/ncmu/admin/tags/{tag_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Hard-delete — cascades to app_tags + user_tags via FK ondelete=CASCADE",
)
async def delete_tag(
    tag_id: UUID,
    _: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Plan §3 PE-05 Step 3 — bulk DELETE + rowcount check.

    Uses Core ``delete()`` instead of ``db.get() + db.delete(obj)`` so
    the round-trip is single-statement. FK ``ondelete=CASCADE`` on
    ``app_tags.tag_id`` and ``user_tags.tag_id`` sweep the binding rows
    in the same statement (one transaction). The SPA Popconfirm already
    surfaced ``app_count + user_count`` so the cascade is documented
    behaviour, not hidden side effect.
    """
    result = await db.execute(delete(Tag).where(Tag.id == tag_id))
    if result.rowcount == 0:
        # No row matched — rollback the (empty) transaction and 404.
        await db.rollback()
        raise _not_found(tag_id)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ===================================================================== #
# TASK-PE-08 — tag ↔ app binding (replace-all). Both directions live with
# their "owning" resource: tag→apps here, app→tags in admin/apps/routes.py.
# ===================================================================== #
@router.get(
    "/api/v1/ncmu/admin/tags/{tag_id}/apps",
    response_model=TagAppsOut,
    summary="List the dify_app_ids currently bound to this tag",
)
async def list_tag_apps(
    tag_id: UUID,
    _: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> TagAppsOut:
    if await db.get(Tag, tag_id) is None:
        raise _not_found(tag_id)
    app_ids = (
        await db.execute(
            select(AppTag.dify_app_id).where(AppTag.tag_id == tag_id)
        )
    ).scalars().all()
    return TagAppsOut(tag_id=str(tag_id), app_ids=list(app_ids))


@router.put(
    "/api/v1/ncmu/admin/tags/{tag_id}/apps",
    response_model=TagAppsReplaceResult,
    summary="Replace-all: set the tag's bound apps to exactly body.app_ids",
)
async def replace_tag_apps(
    tag_id: UUID,
    body: TagBindAppsRequest,
    _: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> TagAppsReplaceResult:
    """Replace-all binding (plan §4 PE-08 Step 1): delete the tag's
    existing ``app_tags`` rows, then insert one per (de-duped) app_id.

    Idempotent — PUT the same body twice → identical final state. ``[]``
    clears all bindings. Validates every app_id exists in ``dify_apps``
    first (1015) since ``app_tags`` has no FK to ``dify_apps`` and a
    phantom id would otherwise silently insert.
    """
    if await db.get(Tag, tag_id) is None:
        raise _not_found(tag_id)

    # De-dupe while preserving order; the composite PK (dify_app_id,
    # tag_id) would raise on a repeated pair within one body.
    app_ids = list(dict.fromkeys(body.app_ids))
    if app_ids:
        existing = set(
            (
                await db.execute(
                    select(DifyApp.dify_app_id).where(
                        DifyApp.dify_app_id.in_(app_ids)
                    )
                )
            ).scalars().all()
        )
        missing = [a for a in app_ids if a not in existing]
        if missing:
            raise _binding_targets_not_found("app(s)", missing)

    await db.execute(delete(AppTag).where(AppTag.tag_id == tag_id))
    for aid in app_ids:
        db.add(AppTag(dify_app_id=aid, tag_id=tag_id))
    await db.commit()
    return TagAppsReplaceResult(tag_id=str(tag_id), app_count=len(app_ids))
