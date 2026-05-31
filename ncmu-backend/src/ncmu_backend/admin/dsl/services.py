"""Helpers for admin DSL export (TASK-PE-10) + import (TASK-PE-11).

The filename-safety + manifest helpers are pure (FastAPI/httpx/DB-free) so
they stay unit-testable without the app. ``upsert_app_cache`` (PE-11) is the
one DB-touching helper — kept here rather than inlined in the route so the
import endpoint's body reads as orchestration, not SQL.
"""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ncmu_backend.db.models import DifyApp


# ``\w`` under Python 3 ``re`` (str pattern) is Unicode-aware and already
# matches CJK ideographs, so a single substitution preserves 中文 App names
# while collapsing path separators (``/`` ``\``), dots (blocks ``..`` and
# stray extensions), spaces and other punctuation to ``_``. The explicit
# ``-`` keeps hyphens (common in App names) out of the replacement class.
_UNSAFE = re.compile(r"[^\w\-]", re.UNICODE)


def safe_filename(name: str, *, max_len: int = 80) -> str:
    """Project an arbitrary Dify App name onto a ZIP-entry-safe stem.

    Guarantees: no path separators / traversal, no leading-dot dotfiles,
    bounded length, never empty. CJK characters survive (要求 4 中文 App 名
    兼容). The caller appends ``_{dify_app_id}.yaml`` so collisions between
    two Apps sharing a sanitised name stay distinct.
    """
    cleaned = _UNSAFE.sub("_", name)
    cleaned = cleaned.strip("._")
    if not cleaned:
        cleaned = "app"
    return cleaned[:max_len]


def zip_entry_name(name: str, dify_app_id: str) -> str:
    """Build the per-App ZIP entry: ``{safe_name}_{dify_app_id}.yaml``."""
    return f"{safe_filename(name)}_{dify_app_id}.yaml"


def build_manifest(
    *, app_ids: list[str], include_secret: bool, exported_at_iso: str
) -> dict[str, Any]:
    """MANIFEST.json payload recorded inside the archive.

    Lists the Dify App ids actually exported + the secret flag + an ISO
    timestamp, so a downstream re-import / audit can tell what the bundle
    contains without parsing every YAML.
    """
    return {
        "exported_at": exported_at_iso,
        "app_ids": list(app_ids),
        "include_secret": include_secret,
    }


def export_zip_filename(stamp: str) -> str:
    """Top-level download filename, e.g. ``ncmu-dify-apps-export-20260530-1530.zip``."""
    return f"ncmu-dify-apps-export-{stamp}.zip"


# --------------------------------------------------------------------- #
# TASK-PE-11 — post-import cache sync
# --------------------------------------------------------------------- #
async def upsert_app_cache(
    db: AsyncSession, *, dify_app_id: str, name: str, mode: str
) -> None:
    """UPSERT one freshly-imported app into the ``dify_apps`` cache.

    TASK-PE-11 ② (Boss 2026-05-30): the import response already carries the
    new app_id, so we cache exactly the apps we just created — no full
    ``GET /console/api/apps`` refresh. This deliberately re-uses the same
    ``pg_insert ... on_conflict_do_update`` + ``last_synced_at`` stamping as
    ``admin/routes.py``'s POST /sync_apps (which lives outside this task's
    file range, so the logic is mirrored, not extracted). Dup-debt noted for
    a future shared-helper extraction. ``is_active`` keeps its server_default
    (True) on INSERT, so an imported app is visible/active immediately.
    """
    stmt = (
        pg_insert(DifyApp)
        .values(
            dify_app_id=dify_app_id,
            name=name,
            mode=mode,
            last_synced_at=func.now(),
        )
        .on_conflict_do_update(
            index_elements=["dify_app_id"],
            set_={
                "name": name,
                "mode": mode,
                "updated_at": func.now(),
                "last_synced_at": func.now(),
            },
        )
    )
    await db.execute(stmt)
