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
import re
import time
from typing import Any, Callable, Iterable, Optional

import httpx
from fastapi import HTTPException, status


log = logging.getLogger("ncmu_backend.apps.dify_console_client")


# REWORK-PE-10-INDEP #2 — a Dify App id is a UUID-shaped String(64) token.
# ``export_app`` validates against this at its entry as a defence-in-depth
# chokepoint (in addition to the schema/Path validation at the route
# boundary), so no caller can splice ``?``/``&``/``/``/``..`` into the
# outbound Dify Console URL — closing the include_secret audit-bypass and
# path-rewrite vectors the indep panel flagged.
_VALID_DIFY_APP_ID = re.compile(r"^[A-Za-z0-9-]+$")


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
# Shared Dify Console fetch wrapper — used by NEW methods (TASK-BUG-2).
# Existing list_apps_for_user / _fetch are 字面禁区 (C4 / C-INDEP-1 ruling)
# and keep their inline error handling.
# --------------------------------------------------------------------- #
async def _request_dify_json(
    *,
    http: httpx.AsyncClient,
    method: str,
    url: str,
    api_key: str,
    tenant_id: str,
    params: Optional[dict[str, Any]] = None,
) -> Any:
    """httpx wrapper for Dify Console GET — auth headers + error mapping.

    Returns parsed JSON (dict or list). Raises HTTPException(502, ...)
    with same {code, message} shape as `_fetch` for transport / >=400 /
    non-JSON failures.

    ``params`` (optional) is forwarded to httpx so callers pass query args
    as a dict rather than string-concatenating them into ``url`` — httpx
    percent-encodes values, which keeps a flag like ``include_secret`` a
    real, un-spoofable query parameter (REWORK-PE-10-INDEP #2 defence b).
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "X-WORKSPACE-ID": tenant_id,
    }
    try:
        resp = await http.request(method, url, headers=headers, params=params)
    except httpx.HTTPError as exc:
        log.warning("dify_console_client transport error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": 9001,
                "message": f"Dify Console unreachable: {exc.__class__.__name__}",
            },
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
        return resp.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": 9001, "message": "Dify Console response is not JSON"},
        ) from exc


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
        tenant_id: str,
    ) -> list[dict[str, Any]]:
        """Return raw Dify App dicts. Cached per user_id for TTL seconds.

        Caller is responsible for projecting to AppOut + filtering to
        kb_qa via `detect_app_type` — this method only handles fetch +
        cache + error mapping.

        ``tenant_id`` is forwarded to ``_fetch`` so the outbound request
        carries the ``X-WORKSPACE-ID`` header Dify's ADMIN_API_KEY
        bypass requires (path B' / INDEP-FIX-DEPLOY-2 / Dify
        ``libs/login/ext_login.py:56-72``).

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
        raw = await self._fetch(
            http=http, base_url=base_url, api_key=api_key, tenant_id=tenant_id,
        )
        self._cache[user_id] = (raw, now + self._ttl)
        return raw

    async def _fetch(
        self,
        *,
        http: httpx.AsyncClient,
        base_url: str,
        api_key: str,
        tenant_id: str,
    ) -> list[dict[str, Any]]:
        url = f"{base_url.rstrip('/')}/console/api/apps"
        try:
            resp = await http.get(
                url,
                params={"limit": 100, "page": 1},
                headers={
                    "Authorization": f"Bearer {api_key}",
                    # Path B' (INDEP-FIX-DEPLOY-2): Dify's ADMIN_API_KEY
                    # bypass requires X-WORKSPACE-ID — ext_login.py:60
                    # `if workspace_id:` is the entry guard. Without it
                    # the request falls through to console JWT verify
                    # and the admin key is rejected as "Invalid token".
                    "X-WORKSPACE-ID": tenant_id,
                },
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

    # ----- TASK-BUG-2 new endpoint methods (no cache; per-request live) --
    # docker exec evidence (2026-05-26 / Boss T1.1=C ruling):
    #   GET /console/api/apps/{id}/parameters → 404 (path不存在)
    #   GET /console/api/apps/{id}               → 200, body.model_config /
    #                                              body.workflow / .mode literal
    #   GET /console/api/apps/{id}/workflows/draft → 200 for mode∈{workflow,
    #                                              advanced-chat}, body.graph.nodes
    # Bearer admin_key + X-WORKSPACE-ID auth pattern reused from _fetch.

    async def fetch_app_detail(
        self,
        *,
        app_id: str,
        http: httpx.AsyncClient,
        base_url: str,
        api_key: str,
        tenant_id: str,
    ) -> dict[str, Any]:
        """GET /console/api/apps/{app_id} → raw Dify app detail dict.

        Caller reads `.mode` to dispatch + `.model_config.user_input_form`
        (chat / completion / agent-chat) or hops to `fetch_workflow_draft`
        (workflow / advanced-chat).
        """
        url = f"{base_url.rstrip('/')}/console/api/apps/{app_id}"
        payload = await _request_dify_json(
            http=http, method="GET", url=url, api_key=api_key, tenant_id=tenant_id,
        )
        return payload if isinstance(payload, dict) else {}

    async def fetch_workflow_draft(
        self,
        *,
        app_id: str,
        http: httpx.AsyncClient,
        base_url: str,
        api_key: str,
        tenant_id: str,
    ) -> dict[str, Any]:
        """GET /console/api/apps/{app_id}/workflows/draft → raw draft dict.

        Caller walks `body.graph.nodes[]` looking for `data.type == "start"`
        to extract `data.variables` (flat ParameterSchema-shaped list).
        """
        url = f"{base_url.rstrip('/')}/console/api/apps/{app_id}/workflows/draft"
        payload = await _request_dify_json(
            http=http, method="GET", url=url, api_key=api_key, tenant_id=tenant_id,
        )
        return payload if isinstance(payload, dict) else {}

    async def export_app(
        self,
        *,
        app_id: str,
        http: httpx.AsyncClient,
        base_url: str,
        api_key: str,
        tenant_id: str,
        include_secret: bool = False,
    ) -> str:
        """GET /console/api/apps/{app_id}/export → DSL YAML string (TASK-PE-10).

        docker exec evidence (2026-05-30 spike, Boss path-A ruling):
          GET /console/api/apps/{id}/export?include_secret=<bool> → 200,
          body is a JSON ENVELOPE ``{"data": "app:\\n  ...\\n"}`` — the YAML
          lives under ``.data``, it is NOT returned as a raw ``application/yaml``
          stream. Verified across all 5 modes (completion / agent-chat /
          advanced-chat / workflow / chat) incl. a CJK-named App.

        ``include_secret`` is forwarded as a query flag; unknown ``app_id``
        upstream-404s and surfaces here as ``HTTPException(502, code=1003)``
        via ``_request_dify_json``'s ``>=400`` branch (Dify ``app_not_found``).
        """
        # REWORK-PE-10-INDEP #2 — chokepoint guard: reject any app_id that
        # could break out of the path segment (``?``/``&``/``/``/``..``)
        # *before* it touches the outbound URL. 400 (not 502) — this is a
        # caller/input fault, not a Dify upstream failure.
        if not _VALID_DIFY_APP_ID.match(app_id):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": 1003,
                    "message": "invalid app_id (expected [A-Za-z0-9-])",
                },
            )
        url = f"{base_url.rstrip('/')}/console/api/apps/{app_id}/export"
        # include_secret rides ``params=`` (httpx encodes it) — never
        # string-concatenated, so it cannot be shadowed by an app_id-borne
        # ``?include_secret=...`` (query first-wins) injection.
        payload = await _request_dify_json(
            http=http,
            method="GET",
            url=url,
            api_key=api_key,
            tenant_id=tenant_id,
            params={"include_secret": "true" if include_secret else "false"},
        )
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, str):
            # Defensive: a fork that returns bare YAML or a malformed envelope
            # would otherwise hand the route a non-str body. Treat as upstream
            # contract breakage (9001) rather than silently writing "None".
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "code": 9001,
                    "message": "Dify Console export missing 'data' YAML string",
                },
            )
        return data
