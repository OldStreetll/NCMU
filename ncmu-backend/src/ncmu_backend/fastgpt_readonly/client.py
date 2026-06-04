"""FastGPT v4.14.x read-only client (errata-13 §2.2 + plan §4.3 AC#1-2).

Read-only by contract — this client performs NO create/update/delete on
FastGPT (no dataset/collection/file mutation). ``read-only`` is a
*semantic* guarantee, not an HTTP-method one: the real FastGPT
v4.14.10.2 models "list collections" as **POST + JSON body** (the body
just carries query params — datasetId/offset/pageSize), so
``list_collections`` and ``health_check`` use POST against
``/api/core/dataset/collection/list``. ``get_collection_files``
(collection/detail) and ``download_file`` (common/file/read) stay GET.

  ⚠️ FastGPT v4.14.10.2 list 用 POST 实测对账 (2026-06-04, TASK-KBFIX-1).
  The earlier "hard-coded GET-only" baseline (client.py 旧版 §3.1) was
  built on an INCORRECT API assumption — real FastGPT returns HTTP 500
  ``{"code":501002,"unExistDataset"}`` to GET collection/list. Do NOT
  "restore" GET on the strength of "read == GET"; read-only here means
  no writes, and POST-with-body is just how FastGPT transports the query.

Auth: ``Authorization: Bearer {FASTGPT_API_KEY}`` (plan AC#2 / reuses
the same env var as kb-adapter — baseline §3.1 doesn't grant a new key).

Mock-vs-real grounding (守 feedback_tdd_mock_vs_real_api 第 13 次同型):
- ``list_collections`` POST response shape is
  ``{"code":200, "data": {"data": [{"_id","name","type","fileId"?}], "total": N}}``
  — grounded against real FastGPT v4.14.10.2 (2026-06-04 实测对账). The old
  ``data.list`` mock shape never matched the live API (hence the panel bug:
  unit tests green, real KB panel showed 暂无文档).
- ``get_collection_files`` (collection/detail GET ?id=) returns a single
  collection object; a virtual collection (no separate file) falls back to
  using the collection itself as the pseudo-file — 实测 200, unchanged.

Health check (plan §4.3 AC#6):
- v4.14.x has no dedicated public health endpoint
  (``/api/common/system/version`` returns Next.js 404 HTML).
- We probe ``/api/core/dataset/collection/list`` via **POST** with body
  ``{"datasetId":"__healthcheck__","offset":0,"pageSize":1}`` — any
  non-404 + non-connect-error response means the service is up: a real
  handler executed (even a 500 ``unExistDataset`` for the bogus dataset
  proves reachability). 404 = endpoint missing (version mismatch) →
  unreachable; connect/timeout → unreachable.
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
# Page size for the POST collection/list pagination loop. FastGPT caps
# pageSize server-side; 100 keeps round-trips low for typical datasets.
LIST_PAGE_SIZE = 100
# Defensive ceiling so a misbehaving upstream (e.g. total never satisfied)
# can't spin the pagination loop forever. 100 pages * 100 = 10k collections.
_LIST_MAX_PAGES = 100

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

    async def _post(self, path: str, json_body: Optional[dict] = None) -> httpx.Response:
        # Mirror of ``_get`` for FastGPT's POST-with-body read endpoints.
        # FastGPT v4.14.10.2 models "list collections" as POST + JSON body
        # (datasetId/offset/pageSize) — 实测对账 2026-06-04 (TASK-KBFIX-1).
        # This is still a READ: no create/update/delete is ever issued.
        url = f"{self._base_url}{path}"
        try:
            return await self._client().post(url, json=json_body, headers=self._headers)
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
            # POST /api/core/dataset/collection/list with a JSON body — real
            # FastGPT v4.14.10.2 shape (实测对账 2026-06-04). The response
            # list lives at ``data.data`` (NOT ``data.list``) and is paged;
            # walk every page so a dataset with > pageSize collections is
            # returned in full, not just the first page.
            out: list[CollectionMeta] = []
            offset = 0
            for _ in range(_LIST_MAX_PAGES):
                resp = await self._post(
                    "/api/core/dataset/collection/list",
                    json_body={
                        "datasetId": dataset_id,
                        "offset": offset,
                        "pageSize": LIST_PAGE_SIZE,
                    },
                )
                try:
                    self._raise_for_status(resp, on_404_invalidate_key=cache_key)
                except FastGPTNotFound:
                    _metadata_cache.invalidate(cache_key)
                    raise
                payload = resp.json()
                data = payload.get("data") or {}
                items = data.get("data") or []
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
                offset += len(items)
                # ``total`` may be absent or a non-numeric upstream value
                # (e.g. ""). Coerce defensively: anything non-numeric is
                # treated as "unknown total" and we rely on the empty/short
                # -page terminators below (which guarantee termination
                # regardless). Real FastGPT returns an int — this is pure
                # robustness, never raise ValueError on a junk total.
                raw_total = data.get("total")
                try:
                    total = int(raw_total) if raw_total is not None else None
                except (TypeError, ValueError):
                    total = None
                # Stop when: empty page (nothing left), short page (last
                # page), or we've reached the reported total. Any one is a
                # sufficient terminator — total may be absent, so the
                # empty/short-page checks guarantee termination regardless.
                if (
                    not items
                    or len(items) < LIST_PAGE_SIZE
                    or (total is not None and offset >= total)
                ):
                    break
            else:
                # for-loop exhausted _LIST_MAX_PAGES without a natural
                # terminator (upstream kept returning full pages). Do NOT
                # silently return a truncated list — surface it so a caller
                # isn't misled into thinking it got every collection
                # (no-silent-caps; REWORK-KBFIX-1-INDEP ①).
                log.warning(
                    "list_collections hit _LIST_MAX_PAGES=%d cap; collection "
                    "list may be truncated dataset_id=%s pages=%d collected=%d",
                    _LIST_MAX_PAGES, dataset_id, _LIST_MAX_PAGES, len(out),
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
        """Reachability probe (plan AC#6).

        POSTs the bogus ``__healthcheck__`` dataset to collection/list
        (same endpoint + method as ``list_collections`` — 实测对账
        2026-06-04). Returns ``{"alive": True, "status_code": int}`` when
        the service responds with anything other than 404: a real handler
        executed — even a 500 ``{"code":501002,"unExistDataset"}`` for the
        bogus dataset proves the service is **reachable but the dataset is
        absent** (exactly the distinction we want). 404 means the endpoint
        is missing (FastGPT version mismatch); connect errors / timeouts
        arrive as ``FastGPTUnreachable`` from ``_post``. Both → unreachable.
        """
        resp = await self._post(
            "/api/core/dataset/collection/list",
            json_body={
                "datasetId": HEALTHCHECK_DATASET_ID,
                "offset": 0,
                "pageSize": 1,
            },
        )
        if resp.status_code == 404:
            raise FastGPTUnreachable(
                f"HTTP 404 — FastGPT endpoint missing (v4.x mismatch?): {resp.text[:200]!r}"
            )
        return {"alive": True, "status_code": resp.status_code}
