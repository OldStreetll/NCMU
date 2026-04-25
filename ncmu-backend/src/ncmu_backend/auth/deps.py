"""FastAPI dependency: extract + verify JWT, return CurrentUser.

This is the contract TASK-26/27/28 protected endpoints depend on:

    @router.get("/api/v1/ncmu/apps")
    async def list_apps(user: CurrentUser = Depends(get_current_user)):
        ...

`CurrentUser` is a thin pydantic model (sub + name + is_admin),
not the ORM `User`, so endpoints don't need to round-trip to the DB
just to know who's calling.
"""
from __future__ import annotations

from typing import Optional

import jwt
from fastapi import Depends, Header, HTTPException, status
from pydantic import BaseModel

from ncmu_backend.auth.jwt_utils import verify_jwt
from ncmu_backend.config import Settings, get_settings


class CurrentUser(BaseModel):
    sub: str
    name: str
    is_admin: bool


def _extract_bearer(authorization: Optional[str]) -> str:
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": 1102, "message": "missing Authorization header"},
        )
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1]:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": 1103, "message": "Authorization must be Bearer <token>"},
        )
    return parts[1].strip()


async def get_current_user(
    authorization: Optional[str] = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> CurrentUser:
    token = _extract_bearer(authorization)
    try:
        payload = verify_jwt(token, settings.NCMU_JWT_SECRET)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": 1104, "message": "token expired"},
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": 1105, "message": f"invalid token: {exc.__class__.__name__}"},
        )
    sub = payload.get("sub")
    name = payload.get("name")
    if not sub or not name:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": 1106, "message": "token missing sub/name"},
        )
    is_admin = sub.lower() in settings.admin_user_id_set
    return CurrentUser(sub=sub, name=name, is_admin=is_admin)


async def require_admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """Stricter variant — raises 403 unless the JWT subject is in
    NCMU_ADMIN_USER_IDS. TASK-28 admin endpoints depend on this."""
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": 1201, "message": "admin permission required"},
        )
    return user
