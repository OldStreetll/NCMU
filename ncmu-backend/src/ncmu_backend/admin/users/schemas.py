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


# --------------------------------------------------------------------- #
# TASK-PE-09 — user ↔ tag binding (replace-all / reverse direction) schemas.
# Mirror of PE-08's app↔tag binding schemas (admin/apps/schemas.py).
# --------------------------------------------------------------------- #
class UserBindTagsRequest(BaseModel):
    """PUT /admin/users/{user_id}/tags body — replace-all set of bound tags.

    ``tag_ids`` are ``tags.id`` UUIDs. ``[]`` clears all (idempotent
    replace-all). Duplicates are de-duped server side. ``user_tags.tag_id``
    FKs to ``tags.id``, so nonexistent ids are rejected (404 / 1016) before
    insert rather than surfacing a raw IntegrityError.
    """

    tag_ids: list[uuid.UUID] = Field(default_factory=list)


class UserTagsOut(BaseModel):
    """GET /admin/users/{user_id}/tags — the user's currently-bound tag ids
    (serialized as strings for the SPA Transfer keys)."""

    user_id: str
    tag_ids: list[str]


class UserTagsReplaceResult(BaseModel):
    """PUT /admin/users/{user_id}/tags echo — final distinct bound count."""

    user_id: str
    tag_count: int
