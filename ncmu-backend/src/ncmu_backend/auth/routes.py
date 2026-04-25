"""Dev-login + dev-users routes.

Both endpoints raise HTTP 404 when NCMU_ENABLE_DEV_LOGIN is false (per
AC#3 C-3 revision: keep the routes registered so the OpenAPI doc shows
them, but reject at runtime). This makes the prod-mode behaviour visible
to operators (404, not "endpoint not found in router") and keeps the
test surface stable across profiles.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ncmu_backend.auth.jwt_utils import sign_jwt
from ncmu_backend.config import Settings
from ncmu_backend.db.models import User
from ncmu_backend.deps import get_db, get_settings
from ncmu_backend.schemas.auth import (
    DevLoginRequest,
    DevLoginResponse,
    UserOut,
)

router = APIRouter(tags=["auth"])


@router.post(
    "/api/v1/ncmu/auth/dev-login",
    response_model=DevLoginResponse,
    summary="Dev-only login: trade a dev user UUID for a 24h JWT",
)
async def dev_login(
    req: DevLoginRequest,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> DevLoginResponse:
    if not settings.NCMU_ENABLE_DEV_LOGIN:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": 1100, "message": "dev-login disabled"},
        )

    result = await db.execute(
        select(User).where(User.id == req.user_id, User.is_active.is_(True))
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": 1101, "message": "dev user not found"},
        )

    jwt_str, exp = sign_jwt(
        str(user.id),
        user.name,
        settings.NCMU_JWT_SECRET,
        ttl_hours=settings.NCMU_JWT_TTL_HOURS,
    )
    return DevLoginResponse(
        jwt=jwt_str,
        user=UserOut.model_validate(user),
        expires_at=exp,
    )


@router.get(
    "/api/v1/ncmu/dev/users",
    response_model=list[UserOut],
    summary="List the dev seed users (for the dev-only login dropdown)",
)
async def list_dev_users(
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> list[UserOut]:
    if not settings.NCMU_ENABLE_DEV_LOGIN:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": 1100, "message": "dev endpoints disabled"},
        )

    result = await db.execute(
        select(User).where(User.is_active.is_(True)).order_by(User.name)
    )
    users = result.scalars().all()
    return [UserOut.model_validate(u) for u in users]
