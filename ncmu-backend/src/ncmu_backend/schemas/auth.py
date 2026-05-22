"""Auth schemas — dev-login request/response + user serialisation.

C-3 修订：UserOut + DevLoginResponse must declare
`model_config = ConfigDict(from_attributes=True)` so the routes can do
`UserOut.model_validate(user_orm_instance)` (Pydantic v2 replacement
for v1's `from_orm`). Without this config, ORM-instance validation
raises `ValidationError: Input should be a valid dictionary`.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DevLoginRequest(BaseModel):
    user_id: UUID = Field(
        ..., description="UUID of one of the dev seed users (see dev_users.sql)"
    )


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    dept_path: Optional[str] = None
    is_active: bool = True
    is_admin: bool = Field(
        default=False,
        description=(
            "Derived from settings.admin_user_id_set at dev-login time. "
            "Surfaced to the SPA so it can sync AuthUser.is_admin without "
            "decoding the JWT. Backend authorization is still enforced by "
            "auth/deps.py:get_current_user (admin_user_id_set source-of-"
            "truth, per r3 I-INDEP2-1)."
        ),
    )


class DevLoginResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    jwt: str
    user: UserOut
    expires_at: datetime
