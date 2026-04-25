"""Dify Console API client + 5-minute in-memory cache + detect_app_type.

Why a dedicated module:
- The endpoint logic in `routes.py` stays focused on HTTP semantics
  (auth, response shape) — no caching plumbing.
- `detect_app_type` is exported as a top-level function so its truth
  table is unit-testable without spinning up a FastAPI app.
- The cache holds per-user entries — `防越权`. Phase 1 every authenticated
  user sees the same App list, but Phase 3 will server-side filter by
  permissions; keying by `user_id` now means we won't have to flush a
  global cache when permissions land. Trade-off: ~1 KB per active user.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Iterable, Optional

import httpx
from fastapi import HTTPException, status


log = logging.getLogger("ncmu_backend.apps.dify_console_client")


# --------------------------------------------------------------------- #
# Error code mapping
# --------------------------------------------------------------------- #
def dify_status_to_ncmu(http_status: int) -> int:
    """4xx upstream → 1003 (Dify Console rejected our call);
    5xx upstream → 9001 (Dify upstream broken).

    Both surface to the SPA as HTTP 502 — the backend is healthy, the
    upstream isn't, and the SPA shouldn't retry on a 4xx (auth / config
    bug) the same way it retries on a 5xx (transient).
    """
    if 400 <= http_status < 500:
        return 1003
    return 9001


# --------------------------------------------------------------------- #
# detect_app_type — v3 spec §9.x rule (双判：name prefix OR tag)
# --------------------------------------------------------------------- #
_KB_NAME_PREFIX = "[KB]"
_KB_TAG_NAME = "kb-qa"


def _tag_names(tags: Optional[Iterable[Any]]) -> list[str]:
    """Normalise Dify's tag shape to a flat list of tag names.

    Dify Console returns `tags: [{"id":..., "name":"kb-qa", "type":"app"}, ...]`;
    tests sometimes pass plain strings for clarity. Accept both.
    """
    if not tags:
        return []
    out: list[str] = []
    for t in tags:
        if isinstance(t, str):
            out.append(t)
        elif isinstance(t, dict):
            n = t.get("name")
            if isinstance(n, str):
                out.append(n)
    return out


def detect_app_type(name: str, tags: Optional[Iterable[Any]] = None) -> bool:
    """Return True iff this Dify App is an NCMU [KB] App.

    Truth table (per plan TASK-26 AC#3):
      ("[KB]员工手册", [])             → True   # name prefix
      ("员工手册",     ["kb-qa"])       → True   # tag
      ("[KB]员工手册", ["kb-qa"])       → True   # both
      ("员工手册",     ["normal"])      → False  # neither
    """
    if name.startswith(_KB_NAME_PREFIX):
        return True
    return _KB_TAG_NAME in _tag_names(tags)


# --------------------------------------------------------------------- #
# DifyConsoleClient — wraps httpx + adds per-user TTL cache
# --------------------------------------------------------------------- #
class DifyConsoleClient:
    """Per-user-scoped TTL cache around `GET /console/api/apps`.

    Construction takes:
      ttl_seconds: cache validity window (default 300 = 5 min, plan AC#4/5)
      clock:       monotonic-clock callable; tests substitute a fake
                   clock to fast-forward without `asyncio.sleep`.

    Thread safety: Phase 1 backend is single-process / single-event-loop,
    so a plain dict is fine. Multi-worker uvicorn (Phase 3+) will need
    a shared cache (Redis); that's a deliberate Phase 1 simplification.
    """

    def __init__(
        self,
        *,
        ttl_seconds: int = 300,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        # value = (raw_apps, expires_at_monotonic)
        self._cache: dict[str, tuple[list[dict[str, Any]], float]] = {}
        self._ttl = ttl_seconds
        self._clock = clock

    def invalidate(self, user_id: Optional[str] = None) -> None:
        """Drop one entry (user_id) or the whole cache."""
        if user_id is None:
            self._cache.clear()
        else:
            self._cache.pop(user_id, None)

    async def list_apps_for_user(
        self,
        *,
        user_id: str,
        http: httpx.AsyncClient,
        base_url: str,
        api_key: str,
    ) -> list[dict[str, Any]]:
        """Return raw Dify App dicts. Cached per user_id for TTL seconds.

        Caller is responsible for projecting to AppOut + filtering to
        kb_qa via `detect_app_type` — this method only handles fetch +
        cache + error mapping.

        Raises HTTPException(502, {"code": 1003|9001, ...}) on Dify failure.
        """
        now = self._clock()
        cached = self._cache.get(user_id)
        if cached is not None and cached[1] > now:
            log.info(
                "dify_console_client cache HIT user=%s remaining=%.1fs",
                user_id, cached[1] - now,
            )
            return cached[0]

        log.info("dify_console_client cache MISS user=%s — fetching upstream", user_id)
        raw = await self._fetch(http=http, base_url=base_url, api_key=api_key)
        self._cache[user_id] = (raw, now + self._ttl)
        return raw

    async def _fetch(
        self,
        *,
        http: httpx.AsyncClient,
        base_url: str,
        api_key: str,
    ) -> list[dict[str, Any]]:
        url = f"{base_url.rstrip('/')}/console/api/apps"
        try:
            resp = await http.get(
                url,
                params={"limit": 100, "page": 1},
                headers={"Authorization": f"Bearer {api_key}"},
            )
        except httpx.HTTPError as exc:
            log.warning("dify_console_client transport error: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={"code": 9001, "message": f"Dify Console unreachable: {exc.__class__.__name__}"},
            ) from exc

        if resp.status_code >= 400:
            ncmu_code = dify_status_to_ncmu(resp.status_code)
            log.warning(
                "dify_console_client upstream %s -> ncmu code %s",
                resp.status_code, ncmu_code,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "code": ncmu_code,
                    "message": f"Dify Console returned HTTP {resp.status_code}",
                },
            )

        try:
            payload = resp.json()
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={"code": 9001, "message": "Dify Console response is not JSON"},
            ) from exc

        # Dify wraps the page in {"data": [...], "page":..., "limit":...,
        # "total":..., "has_more":...}. Be lenient if some fork omits the
        # envelope and returns a bare list.
        if isinstance(payload, dict):
            return list(payload.get("data") or [])
        if isinstance(payload, list):
            return payload
        return []
