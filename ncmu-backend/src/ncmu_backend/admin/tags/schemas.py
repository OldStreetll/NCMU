"""Pydantic schemas for admin tags CRUD (TASK-PE-05).

Mirrors the live ``tags`` table (db/models.py ``Tag``) and adds two
computed counters — ``app_count`` and ``user_count`` — that the admin
SPA renders in the Popconfirm description (``"将解除 X 个 App 与 Y 个
用户的绑定"``) so the admin sees the cascade-delete blast radius before
confirming.

Both counters are computed by the route layer via a single LEFT JOIN
(``services.py:select_tags_with_counts``), not stored on the row. PE-08
+ PE-09 wire the binding mutation endpoints; PE-05 only reads the
join-table counts for display.
"""
from __future__ import annotations

import uuid
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TagOut(BaseModel):
    """Single tag row as the admin SPA consumes it (list + create/update echo)."""

    model_config = ConfigDict(from_attributes=False)

    id: uuid.UUID
    name: str
    description: Optional[str]
    app_count: int = 0
    user_count: int = 0


class TagCreate(BaseModel):
    """POST body — single tag creation."""

    name: str = Field(min_length=1, max_length=64)
    description: Optional[str] = Field(default=None, max_length=255)


class TagUpdate(BaseModel):
    """PATCH body — every field optional (partial update via ``exclude_unset``)."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=64)
    description: Optional[str] = Field(default=None, max_length=255)

    @field_validator("name")
    @classmethod
    def _reject_explicit_none(cls, v: Optional[str]) -> Optional[str]:
        # PATCH partial-update semantics: omit the ``name`` key to keep the
        # current value. An explicit ``{"name": null}`` would otherwise
        # bypass ``min_length=1`` (Optional[str] lets None through type
        # validation) → ``setattr(tag, "name", None)`` → NOT NULL INSERT
        # → IntegrityError → 409 with misleading 1012 echo (``tag 'None'
        # already exists``). Pydantic v2 only runs validators on
        # explicitly-set values (defaults are skipped unless
        # ``validate_default=True``), so the omit-to-keep path stays
        # unchanged; only the explicit-null path is gated.
        if v is None:
            raise ValueError(
                "name cannot be null; omit the field to keep the existing value"
            )
        return v


# --------------------------------------------------------------------- #
# TASK-PE-08 — tag ↔ app binding (replace-all) schemas.
# --------------------------------------------------------------------- #
class TagBindAppsRequest(BaseModel):
    """PUT /admin/tags/{tag_id}/apps body — replace-all set of bound apps.

    ``app_ids`` are ``dify_apps.dify_app_id`` strings (String(64)), NOT
    UUIDs (Boss 2026-05-29 拍板 — the cache PK is text; plan §4 sketch's
    ``str(aid)`` wrongly assumed UUIDs). ``[]`` clears all bindings for
    the tag (idempotent replace-all). Duplicate ids are de-duped server
    side before insert (the ``app_tags`` composite PK would otherwise
    raise on a repeated pair).
    """

    app_ids: list[str] = Field(default_factory=list)


class TagAppsOut(BaseModel):
    """GET /admin/tags/{tag_id}/apps — the tag's currently-bound app ids."""

    tag_id: str
    app_ids: list[str]


class TagAppsReplaceResult(BaseModel):
    """PUT /admin/tags/{tag_id}/apps echo — final distinct bound count."""

    tag_id: str
    app_count: int
