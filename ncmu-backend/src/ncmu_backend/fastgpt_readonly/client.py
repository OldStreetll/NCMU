"""FastGPT v4.14.x read-only client (errata-13 §2.2 + plan §4.3 AC#1-2).

Hard-coded to GET-only — any write-method httpx call would violate
baseline §3.1 and trip the AC#1 grep self-check (the pattern is the
exact regex documented in plan §4.3 AC#1; it must match nothing under
``fastgpt_readonly/``).

Auth: ``Authorization: Bearer {FASTGPT_API_KEY}`` (plan AC#2 / reuses
the same env var as kb-adapter — baseline §3.1 doesn't grant a new key).

Mock-vs-real grounding (守 feedback_tdd_mock_vs_real_api 第 12 次同型):
- ``list_collections`` response shape ``{"data": {"list": [{"_id", "name", ...}]}}``
  is grounded against kb-adapter ``src/kb_adapter/translator.py:40-48`` (真代码).
- ``get_collection_files`` + ``download_file`` shapes follow FastGPT
  v4.14.x convention; TASK-PC4 真容器 E2E reconciles against live upstream.

Health check (plan §4.3 AC#6 / [INTENT-CHECK T2] path A):
- v4.14.x has no dedicated public health endpoint
  (``/api/common/system/version`` returns Next.js 404 HTML).
- We probe ``/api/core/dataset/collection/list?datasetId=__healthcheck__``
  instead — any non-404 + non-connect-error response means the service is
  up (the auth-protected handler executed; we ignore the auth result).
"""
from __future__ import annotations

import logging
from typing import AsyncIterator, Optional

import httpx
from pydantic import BaseModel

from ncmu_backend.fastgpt_readonly.cache import TTLCache
from ncmu_backend.fastgpt_readonly.errors import (
    FastGPTNotFound,
    FastGPTUnreachable,
    classify_http_status,
)

log = logging.getLogger("ncmu_backend.fastgpt_readonly.client")

HEALTHCHECK_DATASET_ID = "__healthcheck__"
DEFAULT_TIMEOUT_S = 10.0

# Module-level metadata cache shared by every FastGPTReadOnlyClient
# instance in the process — plan §4.3 AC#4 / spec §3.2 Q5-B. Keeping it
# at module scope means a process-singleton client (see routes._get_
# fastgpt_client_singleton) automatically gets request coalescing across
# concurrent /api/v1/ncmu/kbs/* requests, without each route handler re-building
# its own cache. Tests that swap the upstream transport between cases
# MUST call ``_metadata_cache.clear()`` in setup to avoid cross-test
# hits (the conftest autouse fixture below handles this).
_metadata_cache = TTLCache(ttl_seconds=30.0, max_size=256)


class CollectionMeta(BaseModel):
    collection_id: str
    name: str
    file_id: Optional[str] = None


class FileMeta(BaseModel):
    file_id: str
    original_filename: str
    size_bytes: int
    uploaded_at: str
    content_type: str


class FastGPTReadOnlyClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: float = DEFAULT_TIMEOUT_S,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._timeout = timeout
        self._owned_client: Optional[httpx.AsyncClient] = None
        self._injected_client = http_client

    def _client(self) -> httpx.AsyncClient:
        if self._injected_client is not None:
            return self._injected_client
        if self._owned_client is None:
            self._owned_client = httpx.AsyncClient(timeout=self._timeout)
        return self._owned_client

    async def aclose(self) -> None:
        if self._owned_client is not None:
            await self._owned_client.aclose()
            self._owned_client = None

    async def _get(self, path: str, params: Optional[dict] = None) -> httpx.Response:
        url = f"{self._base_url}{path}"
        try:
            return await self._client().get(url, params=params, headers=self._headers)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
            raise FastGPTUnreachable(f"{type(exc).__name__}: {exc}") from exc
        except httpx.HTTPError as exc:
            raise FastGPTUnreachable(f"transport error: {exc}") from exc

    def _raise_for_status(self, resp: httpx.Response, *, on_404_invalidate_key: Optional[str] = None) -> None:
        if resp.status_code < 400:
            return
        snippet = resp.text[:200] if resp.content else None
        exc_cls = classify_http_status(resp.status_code, snippet)
        msg = f"HTTP {resp.status_code} from FastGPT: {snippet!r}"
        if exc_cls is FastGPTNotFound and on_404_invalidate_key is not None:
            log.info("FastGPT 404 — caller should invalidate cache key %r", on_404_invalidate_key)
        raise exc_cls(msg)

    async def list_collections(self, dataset_id: str) -> list[CollectionMeta]:
        cache_key = f"list:{dataset_id}"

        async def _fetch() -> list[CollectionMeta]:
            resp = await self._get(
                "/api/core/dataset/collection/list",
                params={"datasetId": dataset_id},
            )
            try:
                self._raise_for_status(resp, on_404_invalidate_key=cache_key)
            except FastGPTNotFound:
                _metadata_cache.invalidate(cache_key)
                raise
            payload = resp.json()
            items = (payload.get("data") or {}).get("list") or []
            out: list[CollectionMeta] = []
            for item in items:
                cid = item.get("_id") or item.get("id")
                if not cid:
                    continue
                out.append(
                    CollectionMeta(
                        collection_id=str(cid),
                        name=str(item.get("name") or ""),
                        file_id=item.get("fileId") or item.get("file_id"),
                    )
                )
            return out

        return await _metadata_cache.get_or_fetch(cache_key, _fetch)

    async def get_collection_files(self, collection_id: str) -> list[FileMeta]:
        cache_key = f"detail:{collection_id}"

        async def _fetch() -> list[FileMeta]:
            resp = await self._get(
                "/api/core/dataset/collection/detail",
                params={"id": collection_id},
            )
            try:
                self._raise_for_status(resp, on_404_invalidate_key=cache_key)
            except FastGPTNotFound:
                _metadata_cache.invalidate(cache_key)
                raise
            payload = resp.json()
            data = payload.get("data") or {}
            # FastGPT v4.14.x: one collection ≈ one file. The "file" payload
            # usually lives under data.file (single object) or data.files
            # (list); accept either to stay forward-compatible.
            raw_files = data.get("files")
            if raw_files is None:
                single = data.get("file")
                raw_files = [single] if single else [data]
            out: list[FileMeta] = []
            for raw in raw_files:
                if not isinstance(raw, dict):
                    continue
                file_id = raw.get("fileId") or raw.get("_id") or raw.get("id")
                filename = raw.get("filename") or raw.get("name") or data.get("name")
                size = raw.get("size") or raw.get("sizeBytes") or raw.get("size_bytes") or 0
                uploaded = (
                    raw.get("uploadTime")
                    or raw.get("createTime")
                    or data.get("createTime")
                    or ""
                )
                content_type = (
                    raw.get("contentType")
                    or raw.get("mimetype")
                    or raw.get("type")
                    or "application/octet-stream"
                )
                if not file_id:
                    continue
                out.append(
                    FileMeta(
                        file_id=str(file_id),
                        original_filename=str(filename or ""),
                        size_bytes=int(size or 0),
                        uploaded_at=str(uploaded or ""),
                        content_type=str(content_type),
                    )
                )
            return out

        return await _metadata_cache.get_or_fetch(cache_key, _fetch)

    async def download_file(self, file_id: str) -> AsyncIterator[bytes]:
        """Stream raw file bytes — caller iterates and forwards.

        Not cached (plan §4.3 AC#4 / risk §5.6 — large files would blow
        up memory). Uses ``httpx.AsyncClient.stream`` so chunks flow
        through without buffering the whole body.
        """
        url = f"{self._base_url}/api/common/file/read"
        client = self._client()
        try:
            cm = client.stream("GET", url, params={"id": file_id}, headers=self._headers)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise FastGPTUnreachable(f"{type(exc).__name__}: {exc}") from exc
        try:
            resp = await cm.__aenter__()
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
            raise FastGPTUnreachable(f"{type(exc).__name__}: {exc}") from exc
        try:
            if resp.status_code >= 400:
                snippet = (await resp.aread()).decode("utf-8", errors="replace")[:200]
                exc_cls = classify_http_status(resp.status_code, snippet)
                if exc_cls is FastGPTNotFound:
                    log.info("FastGPT 404 — caller should invalidate cache key download:%s", file_id)
                raise exc_cls(f"HTTP {resp.status_code} from FastGPT: {snippet!r}")
            async for chunk in resp.aiter_bytes():
                yield chunk
        finally:
            await cm.__aexit__(None, None, None)

    async def health_check(self) -> dict:
        """Reachability probe (plan AC#6 / INTENT-CHECK T2 path A).

        Returns ``{"alive": True, "status_code": int}`` when the service
        responds with anything other than 404 (a real handler executed —
        even the auth-rejection 500+JSON counts). 404 / connect errors /
        timeouts raise ``FastGPTUnreachable``.
        """
        try:
            resp = await self._client().get(
                f"{self._base_url}/api/core/dataset/collection/list",
                params={"datasetId": HEALTHCHECK_DATASET_ID},
                headers=self._headers,
            )
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
            raise FastGPTUnreachable(f"{type(exc).__name__}: {exc}") from exc
        except httpx.HTTPError as exc:
            raise FastGPTUnreachable(f"transport error: {exc}") from exc
        if resp.status_code == 404:
            raise FastGPTUnreachable(
                f"HTTP 404 — FastGPT endpoint missing (v4.x mismatch?): {resp.text[:200]!r}"
            )
        return {"alive": True, "status_code": resp.status_code}
