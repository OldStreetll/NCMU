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


class DevLoginResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    jwt: str
    user: UserOut
    expires_at: datetime
