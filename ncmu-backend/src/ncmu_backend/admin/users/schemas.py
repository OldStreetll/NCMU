"""Pydantic schemas for admin users CRUD (TASK-PE-06).

Mirrors the live ``users`` table (db/models.py ``User``) but adds the
computed ``is_admin`` field for the SPA — admin membership is derived
from ``settings.admin_user_id_set`` (env-driven whitelist), not stored
on the row. ``tags`` is reserved for PE-09's many-to-many expansion;
PE-06 always emits an empty list.
"""
from __future__ import annotations

import uuid
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class UserOut(BaseModel):
    """Single user row as the admin SPA consumes it."""
    model_config = ConfigDict(from_attributes=False)

    id: uuid.UUID
    name: str
    dingtalk_userid: Optional[str]
    dept_path: Optional[str]
    is_active: bool
    is_admin: bool  # computed: str(id).lower() in settings.admin_user_id_set
    tags: list = Field(default_factory=list)  # PE-09 多对多绑定时 expand


class UserCreate(BaseModel):
    """POST body — single user creation."""
    name: str = Field(min_length=1, max_length=64)
    dingtalk_userid: Optional[str] = Field(default=None, max_length=64)
    dept_path: Optional[str] = Field(default=None, max_length=255)
    tag_ids: list[uuid.UUID] = Field(default_factory=list)  # PE-09 时 wire


class UserUpdate(BaseModel):
    """PATCH body — every field optional."""
    name: Optional[str] = Field(default=None, min_length=1, max_length=64)
    dingtalk_userid: Optional[str] = Field(default=None, max_length=64)
    dept_path: Optional[str] = Field(default=None, max_length=255)
    is_active: Optional[bool] = None


class UserListResponse(BaseModel):
    """Page envelope around ``UserOut`` items."""
    total: int
    items: list[UserOut]
