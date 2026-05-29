"""TASK-PE-06 — admin users CRUD endpoints.

All routes are gated by :func:`require_admin` so the PE-02 invariance
test (``tests/admin/test_require_admin_audit.py``) auto-detects them.

URL surface:

    GET    /api/v1/ncmu/admin/users               list + pagination + search
    POST   /api/v1/ncmu/admin/users               create one
    PATCH  /api/v1/ncmu/admin/users/{user_id}     partial update
    DELETE /api/v1/ncmu/admin/users/{user_id}     soft-delete (is_active=False)

Error envelope shape — ``{"detail": {"code": <int>, "message": "..."}}`` —
matches ``fastgpt_readonly/errors.py`` and ``auth/deps.py`` so the SPA
can read every admin error uniformly.

Error codes used here:

  1010  dingtalk_userid already exists (POST + PATCH UNIQUE violation)
  1011  user not found by id          (PATCH / DELETE on missing row)
  1201  admin permission required     (raised by ``require_admin``)
"""
from __future__ import annotations

from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ncmu_backend.admin.users.schemas import (
    UserCreate,
    UserListResponse,
    UserOut,
    UserUpdate,
)
from ncmu_backend.admin.users.services import to_user_out
from ncmu_backend.auth.deps import CurrentUser, require_admin
from ncmu_backend.config import Settings, get_settings
from ncmu_backend.db.models import User
from ncmu_backend.db.session import get_db

router = APIRouter(tags=["admin-users"])


def _not_found(user_id: UUID) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": 1011, "message": f"user {user_id} not found"},
    )


def _dingtalk_conflict() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": 1010, "message": "dingtalk_userid already exists"},
    )


@router.get(
    "/api/v1/ncmu/admin/users",
    response_model=UserListResponse,
    summary="List users (paginated; search by name/dingtalk; default excludes inactive)",
)
async def list_users(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    include_inactive: bool = False,
    search: str | None = None,
    _: CurrentUser = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db),
) -> UserListResponse:
    """Paginated user listing.

    ``include_inactive=False`` is the default so the admin landing
    page's "active users" count line matches what the same admin sees
    here. ``search`` is case-insensitive substring match against
    ``name`` OR ``dingtalk_userid`` (the two fields a real admin would
    type in)."""
    q = select(User)
    if not include_inactive:
        q = q.where(User.is_active.is_(True))
    if search:
        like = f"%{search}%"
        q = q.where(or_(User.name.ilike(like), User.dingtalk_userid.ilike(like)))
    q = q.order_by(User.created_at.desc())

    total = await db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = (
        await db.execute(q.offset((page - 1) * size).limit(size))
    ).scalars().all()

    return UserListResponse(
        total=total,
        items=[to_user_out(u, settings) for u in rows],
    )


@router.post(
    "/api/v1/ncmu/admin/users",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create one user (admin manual single-create; dev-login path unchanged)",
)
async def create_user(
    body: UserCreate,
    _: CurrentUser = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db),
) -> UserOut:
    user = User(
        id=uuid4(),
        name=body.name,
        dingtalk_userid=body.dingtalk_userid,
        dept_path=body.dept_path,
        is_active=True,
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise _dingtalk_conflict()
    await db.refresh(user)
    # tag_ids: PE-09 时实现 (many-to-many binding)
    return to_user_out(user, settings)


@router.patch(
    "/api/v1/ncmu/admin/users/{user_id}",
    response_model=UserOut,
    summary="Partial update — any subset of name/dingtalk_userid/dept_path/is_active",
)
async def update_user(
    user_id: UUID,
    body: UserUpdate,
    _: CurrentUser = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db),
) -> UserOut:
    user = await db.get(User, user_id)
    if user is None:
        raise _not_found(user_id)

    # Only assign fields that were actually provided in the JSON body
    # (``exclude_unset=True`` keeps fields explicitly set to None /
    # empty string distinct from "absent"). Pydantic v2 idiom.
    updates = body.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(user, field, value)

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise _dingtalk_conflict()
    await db.refresh(user)
    return to_user_out(user, settings)


@router.delete(
    "/api/v1/ncmu/admin/users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete — flip is_active=False; row stays so FK and audit are preserved",
)
async def deactivate_user(
    user_id: UUID,
    _: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    user = await db.get(User, user_id)
    if user is None:
        raise _not_found(user_id)
    user.is_active = False
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
