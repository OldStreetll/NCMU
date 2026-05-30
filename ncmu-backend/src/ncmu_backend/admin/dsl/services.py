"""Pure helpers for admin DSL export (TASK-PE-10).

Kept free of FastAPI / httpx / DB so the filename-safety + manifest logic
is unit-testable without spinning up the app (mirrors the
``detect_app_type`` split in ``apps/dify_console_client.py``).
"""
from __future__ import annotations

import re
from typing import Any


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
