"""Pydantic schemas for admin DSL export (TASK-PE-10).

``app_ids`` carry Dify App ids = ``dify_apps.dify_app_id`` (String(64) PK,
db/models.py ``DifyApp``). There is NO separate UUID ``id`` column on the
cache table — the spike (2026-05-30) confirmed the Dify export endpoint
itself keys on this same id, so the SPA passes ``dify_app_id`` straight
through with no UUID coercion.
"""
from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints


# REWORK-PE-10-INDEP #2 — a Dify App id is a UUID-shaped String(64) token.
# Whitelisting it at the schema boundary stops a malicious ``app_ids`` element
# (e.g. ``real?include_secret=true`` or ``../other``) from being concatenated
# into the outbound Dify Console URL — that would let an attacker (a) flip
# include_secret via a query-param injection that bypasses the route's
# ``log.warning`` audit, or (b) rewrite the request path while still carrying
# the admin key. Non-conforming elements 422 *before* any outbound call.
DifyAppId = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z0-9-]+$", min_length=1, max_length=64)
]


class DslCandidateOut(BaseModel):
    """One row of the export-page multi-select table.

    Sourced from the local ``dify_apps`` cache (populated by admin
    POST /sync_apps), not a live Dify Console call — the page only needs
    id + display name + mode to render the picker. ``is_active`` filtering
    is a PE-07 follow-up (that column is not on this branch yet), so the
    page defaults to selecting *all* candidates.
    """

    dify_app_id: str
    name: str
    mode: str


class DslExportRequest(BaseModel):
    """POST body for the multi-App ZIP export.

    ``app_ids`` must be non-empty — an empty selection is a client bug, not
    a "zip of nothing" request, so we 422 at the boundary rather than
    streaming an empty archive.
    """

    app_ids: list[DifyAppId] = Field(min_length=1)
    include_secret: bool = False
