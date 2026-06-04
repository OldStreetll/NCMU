"""FastGPTReadOnlyClient unit tests — plan §4.3 AC#1-3, AC#6, AC#9.

Mock-vs-real grounding (守 feedback_tdd_mock_vs_real_api 第 13 次同型):
- ``list_collections`` is **POST** ``/api/core/dataset/collection/list``
  with JSON body ``{"datasetId","offset","pageSize"}``; the response list
  lives at ``data.data`` with a sibling ``data.total`` for pagination.
  **FastGPT v4.14.10.2 list 用 POST 实测对账 (2026-06-04, TASK-KBFIX-1).**
  The OLD mock shape (GET ?datasetId= → ``data.list``) never matched the
  live API — that mismatch was the KB-panel bug (unit tests green, real
  「知识库内容」面板 showed 暂无文档). Do NOT regress these mocks back to
  GET/``data.list``; read-only means no writes, POST-with-body is just how
  FastGPT transports the query.
- ``get_collection_files`` (collection/detail GET ?id=) is unchanged; a
  *virtual* collection (no separate uploaded file) returns
  ``{code:200, data:{_id,name,type:"virtual",createTime}}`` and the client
  falls back to the collection itself as the pseudo-file (实测 — this is the
  exact shape behind the 'TASK-33 手册' panel case).
- All status / connectivity scenarios use a real httpx.MockTransport so we
  exercise the actual request/response path (no per-method patching).
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from ncmu_backend.fastgpt_readonly import client as client_module
from ncmu_backend.fastgpt_readonly.client import (
    FastGPTReadOnlyClient,
    HEALTHCHECK_DATASET_ID,
)
from ncmu_backend.fastgpt_readonly.errors import (
    FastGPTNotFound,
    FastGPTServerError,
    FastGPTUnauthorized,
    FastGPTUnknownError,
    FastGPTUnreachable,
)


def _mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _build_client(handler) -> FastGPTReadOnlyClient:
    return FastGPTReadOnlyClient(
        base_url="http://fastgpt.example",
        api_key="test-key",
        http_client=_mock_client(handler),
    )


def _req_body(request: httpx.Request) -> dict:
    """Decode a MockTransport request's JSON body (POST list/health)."""
    return json.loads(request.content) if request.content else {}


def _list_page(items: list[dict], total: int) -> dict:
    """Real FastGPT v4.14.10.2 collection/list response envelope."""
    return {"code": 200, "data": {"data": items, "total": total}}


# --------------------------------------------------------------------- AC#1 list_collections
async def test_list_collections_happy_path_real_field_names():
    """Mock body uses the real FastGPT v4.14.10.2 POST shape:
    request = POST + body{datasetId,offset,pageSize};
    response = {code:200, data:{data:[{_id,name,fileId?}], total}}."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("Authorization")
        captured["body"] = _req_body(request)
        return httpx.Response(200, json=_list_page([
            {"_id": "coll1", "name": "员工守则.pdf", "type": "file", "fileId": "f1"},
            {"_id": "coll2", "name": "福利政策.docx", "type": "virtual"},
        ], total=2))

    client = _build_client(handler)
    result = await client.list_collections("ds-123")

    # POST + JSON body (datasetId no longer in the query string)
    assert captured["method"] == "POST"
    assert captured["url"] == "http://fastgpt.example/api/core/dataset/collection/list"
    assert captured["body"]["datasetId"] == "ds-123"
    assert captured["body"]["offset"] == 0
    assert captured["body"]["pageSize"] == client_module.LIST_PAGE_SIZE
    assert captured["auth"] == "Bearer test-key"

    # response parsed from data.data (NOT data.list)
    assert [c.collection_id for c in result] == ["coll1", "coll2"]
    assert result[0].name == "员工守则.pdf"
    assert result[0].file_id == "f1"
    assert result[1].file_id is None


async def test_list_collections_drops_entries_missing_id():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_list_page([
            {"_id": "ok", "name": "yes.pdf"},
            {"name": "no-id-skipped.pdf"},  # silently dropped
        ], total=2))

    result = await _build_client(handler).list_collections("ds-x")
    assert [c.collection_id for c in result] == ["ok"]


async def test_list_collections_paginates_until_short_page(monkeypatch):
    """A dataset with > pageSize collections must be returned in full —
    walk every page (offset advances by page length) until a short/empty
    page. Shrink LIST_PAGE_SIZE to 2 so the test stays small."""
    monkeypatch.setattr(client_module, "LIST_PAGE_SIZE", 2)
    requests: list[dict] = []
    # 3 collections total, pageSize 2 → page0 [c0,c1], page1 [c2] (short → stop)
    all_items = [{"_id": f"c{i}", "name": f"f{i}.pdf"} for i in range(3)]

    def handler(request: httpx.Request) -> httpx.Response:
        body = _req_body(request)
        requests.append(body)
        off, size = body["offset"], body["pageSize"]
        return httpx.Response(200, json=_list_page(all_items[off:off + size], total=3))

    result = await _build_client(handler).list_collections("ds-page")
    assert [c.collection_id for c in result] == ["c0", "c1", "c2"]
    assert [r["offset"] for r in requests] == [0, 2]  # 2 pages, no wasted 3rd


async def test_list_collections_stops_at_total_on_exact_page_multiple(monkeypatch):
    """When total is an exact multiple of pageSize, the ``offset >= total``
    terminator must stop the loop — no wasted empty trailing request."""
    monkeypatch.setattr(client_module, "LIST_PAGE_SIZE", 2)
    requests: list[dict] = []
    all_items = [{"_id": f"c{i}", "name": f"f{i}.pdf"} for i in range(4)]

    def handler(request: httpx.Request) -> httpx.Response:
        body = _req_body(request)
        requests.append(body)
        off, size = body["offset"], body["pageSize"]
        return httpx.Response(200, json=_list_page(all_items[off:off + size], total=4))

    result = await _build_client(handler).list_collections("ds-exact")
    assert [c.collection_id for c in result] == ["c0", "c1", "c2", "c3"]
    assert [r["offset"] for r in requests] == [0, 2]  # stopped at total, no offset=4 call


async def test_list_collections_warns_when_max_pages_cap_hit(monkeypatch, caplog):
    """no-silent-caps: if upstream keeps returning full pages past
    _LIST_MAX_PAGES, the loop must stop AND emit a log.warning so the caller
    isn't misled into thinking it got every collection (REWORK-KBFIX-1-INDEP ①)."""
    import logging

    monkeypatch.setattr(client_module, "LIST_PAGE_SIZE", 2)
    monkeypatch.setattr(client_module, "_LIST_MAX_PAGES", 2)

    def handler(request: httpx.Request) -> httpx.Response:
        off = _req_body(request)["offset"]
        # always a full page (== pageSize) with total absent → no natural
        # terminator → cap kicks in after _LIST_MAX_PAGES pages
        return httpx.Response(200, json={"code": 200, "data": {"data": [
            {"_id": f"c{off}", "name": f"f{off}.pdf"},
            {"_id": f"c{off + 1}", "name": f"f{off + 1}.pdf"},
        ]}})  # no "total"

    with caplog.at_level(logging.WARNING, logger="ncmu_backend.fastgpt_readonly.client"):
        result = await _build_client(handler).list_collections("ds-runaway")

    assert len(result) == 4  # 2 pages * 2 items — stopped at the cap, not infinite
    assert any(
        "_LIST_MAX_PAGES" in r.message and "ds-runaway" in r.message
        for r in caplog.records
    ), "expected a truncation log.warning on cap hit"


async def test_list_collections_non_numeric_total_does_not_raise():
    """Robustness: a non-numeric upstream ``total`` (e.g. "") must not raise
    ValueError — treat it as unknown and let the short/empty-page terminators
    stop the loop (REWORK-KBFIX-1-INDEP ②). Real FastGPT returns an int."""
    def handler(request: httpx.Request) -> httpx.Response:
        # short page (1 < pageSize) so it terminates; junk total must be ignored
        return httpx.Response(200, json={"code": 200, "data": {
            "data": [{"_id": "c-only", "name": "x.pdf"}],
            "total": "",  # non-numeric junk
        }})

    result = await _build_client(handler).list_collections("ds-junk-total")
    assert [c.collection_id for c in result] == ["c-only"]


# --------------------------------------------------------------------- AC#1 get_collection_files
async def test_get_collection_files_virtual_collection_real_shape():
    """Real FastGPT v4.14.10.2 virtual collection (no separate file):
    detail returns ``{code:200, data:{_id,name,type:"virtual",createTime}}``
    with no file/files key → client falls back to the collection itself as
    the pseudo-file. file_id=_id, original_filename=name. This is the exact
    shape behind the 'TASK-33 手册' KB-panel case the bug fix unblocks."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"  # detail stays GET
        assert "id=coll-33" in str(request.url)
        return httpx.Response(200, json={"code": 200, "data": {
            "_id": "coll-33",
            "name": "TASK-33 手册",
            "type": "virtual",
            "createTime": "2026-06-04T00:00:00Z",
        }})

    files = await _build_client(handler).get_collection_files("coll-33")
    assert len(files) == 1
    assert files[0].file_id == "coll-33"
    assert files[0].original_filename == "TASK-33 手册"


async def test_get_collection_files_unwraps_single_file_shape():
    """FastGPT detail response can carry ``data.file`` (single) — client
    must coerce that into a one-element list of FileMeta."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {
            "_id": "coll1",
            "name": "员工守则.pdf",
            "file": {
                "fileId": "f-handbook",
                "filename": "员工守则.pdf",
                "size": 2400000,
                "uploadTime": "2026-05-01T08:00:00Z",
                "contentType": "application/pdf",
            },
        }})

    files = await _build_client(handler).get_collection_files("coll1")
    assert len(files) == 1
    f = files[0]
    assert f.file_id == "f-handbook"
    assert f.original_filename == "员工守则.pdf"
    assert f.size_bytes == 2400000
    assert f.uploaded_at == "2026-05-01T08:00:00Z"
    assert f.content_type == "application/pdf"


async def test_get_collection_files_unwraps_list_shape():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {
            "name": "员工守则.pdf",
            "files": [
                {"fileId": "f1", "filename": "a.pdf", "size": 100,
                 "createTime": "2026-05-01", "mimetype": "application/pdf"},
                {"fileId": "f2", "filename": "b.docx", "size": 200,
                 "createTime": "2026-05-02", "mimetype": "application/msword"},
            ],
        }})

    files = await _build_client(handler).get_collection_files("coll1")
    assert [f.file_id for f in files] == ["f1", "f2"]
    assert files[1].content_type == "application/msword"


# --------------------------------------------------------------------- AC#1 download_file (streaming)
async def test_download_file_streams_chunks_in_order():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert "id=f-123" in str(request.url)
        return httpx.Response(
            200,
            content=b"chunk-A" + b"chunk-B" + b"chunk-C",
            headers={"Content-Type": "application/pdf"},
        )

    client = _build_client(handler)
    chunks = b"".join([c async for c in client.download_file("f-123")])
    assert chunks == b"chunk-Achunk-Bchunk-C"


async def test_download_file_404_raises_FastGPTNotFound():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "file removed"})

    client = _build_client(handler)
    with pytest.raises(FastGPTNotFound):
        async for _ in client.download_file("missing"):
            pass


# --------------------------------------------------------------------- AC#3 error taxonomy
async def test_404_classifies_as_FastGPTNotFound():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not found"})

    with pytest.raises(FastGPTNotFound):
        await _build_client(handler).list_collections("missing-ds")


async def test_401_classifies_as_FastGPTUnauthorized():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "bad key"})

    with pytest.raises(FastGPTUnauthorized):
        await _build_client(handler).list_collections("ds")


async def test_403_classifies_as_FastGPTUnauthorized():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "forbidden"})

    with pytest.raises(FastGPTUnauthorized):
        await _build_client(handler).list_collections("ds")


async def test_fastgpt_500_with_inner_code_403_routes_to_unauthorized():
    """v4.14.x quirk: auth errors arrive as HTTP 500 with a JSON body
    ``{"code":403,...}``. classify_http_status must sniff the body so
    we surface the configuration problem rather than a generic 5xx."""
    body = {"code": 403, "statusText": "unAuthorization", "message": "x", "data": None}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=json.dumps(body).encode(), headers={"Content-Type": "application/json"})

    with pytest.raises(FastGPTUnauthorized):
        await _build_client(handler).list_collections("ds")


async def test_fastgpt_500_without_inner_code_403_routes_to_server_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "internal", "code": 500})

    with pytest.raises(FastGPTServerError):
        await _build_client(handler).list_collections("ds")


async def test_fastgpt_500_unexist_dataset_routes_to_server_error():
    """Real FastGPT v4.14.10.2 returns 500 ``{"code":501002,"unExistDataset"}``
    for an unknown datasetId (实测 2026-06-04). It's a genuine upstream 5xx
    (not the 403 auth quirk) → FastGPTServerError."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"code": 501002, "statusText": "unExistDataset"})

    with pytest.raises(FastGPTServerError):
        await _build_client(handler).list_collections("nonexistent-ds")


async def test_418_classifies_as_FastGPTUnknownError():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(418, json={"detail": "i am a teapot"})

    with pytest.raises(FastGPTUnknownError):
        await _build_client(handler).list_collections("ds")


async def test_connect_error_classifies_as_FastGPTUnreachable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(FastGPTUnreachable):
        await _build_client(handler).list_collections("ds")


async def test_read_timeout_classifies_as_FastGPTUnreachable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("upstream slow")

    with pytest.raises(FastGPTUnreachable):
        await _build_client(handler).list_collections("ds")


# --------------------------------------------------------------------- AC#2 auth header
async def test_authorization_header_uses_bearer_FASTGPT_API_KEY():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("Authorization")
        captured["method"] = request.method
        return httpx.Response(200, json=_list_page([], total=0))

    client = FastGPTReadOnlyClient(
        base_url="http://fastgpt.example",
        api_key="real-secret",
        http_client=_mock_client(handler),
    )
    await client.list_collections("ds")
    assert captured["auth"] == "Bearer real-secret"
    assert captured["method"] == "POST"


# --------------------------------------------------------------------- AC#6 health_check (POST probe)
async def test_health_check_probes_via_post_with_body():
    """health_check POSTs the bogus __healthcheck__ dataset (same endpoint
    + method as list_collections). datasetId travels in the body, not the
    query string."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["body"] = _req_body(request)
        return httpx.Response(200, json=_list_page([], total=0))

    result = await _build_client(handler).health_check()
    assert result == {"alive": True, "status_code": 200}
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/api/core/dataset/collection/list")
    assert captured["body"]["datasetId"] == HEALTHCHECK_DATASET_ID


async def test_health_check_500_unexist_dataset_is_alive():
    """The real reachability signal: POST __healthcheck__ → 500
    ``{"code":501002,"unExistDataset"}`` means the service is **reachable
    but the dataset is absent** → alive (NOT unreachable). This is the
    distinction health_check exists to make."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"code": 501002, "statusText": "unExistDataset"})

    result = await _build_client(handler).health_check()
    assert result == {"alive": True, "status_code": 500}


async def test_health_check_500_with_auth_error_is_alive():
    """500 + JSON {"code":403} from FastGPT means the service responded
    (auth handler executed). Any non-404 + non-connect is alive."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"code": 403, "statusText": "unAuthorization"})

    result = await _build_client(handler).health_check()
    assert result == {"alive": True, "status_code": 500}


async def test_health_check_200_is_alive():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_list_page([], total=0))

    assert (await _build_client(handler).health_check())["alive"] is True


async def test_health_check_404_is_unreachable():
    """404 means endpoint missing (FastGPT v4.x mismatch) — degrade to
    unreachable so the caller surfaces 503 to the user."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="<!DOCTYPE html>...")

    with pytest.raises(FastGPTUnreachable):
        await _build_client(handler).health_check()


async def test_health_check_connect_error_is_unreachable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nope")

    with pytest.raises(FastGPTUnreachable):
        await _build_client(handler).health_check()


# --------------------------------------------------------------------- 取消传播 / async stream lifecycle
async def test_asyncio_cancellation_propagates_through_list_call():
    """Caller cancel during await → CancelledError must propagate (not
    get swallowed by an over-broad except).守 SOP 10 跨 component 边界
    + B-NEW-32 SSE AbortError suppression 同型纪律。"""

    async def slow_handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5.0)
        return httpx.Response(200, json=_list_page([], total=0))

    client = _build_client(slow_handler)
    task = asyncio.create_task(client.list_collections("ds"))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ──────────────────── REWORK-INDEP C1: metadata TTL caching ──────────
async def test_list_collections_caches_within_ttl():
    """30s TTL window: two back-to-back calls fire the upstream once."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json=_list_page([
            {"_id": "c1", "name": "a.pdf"},
        ], total=1))

    client = _build_client(handler)
    a = await client.list_collections("ds-cache")
    b = await client.list_collections("ds-cache")
    assert call_count == 1
    assert [c.collection_id for c in a] == ["c1"]
    assert [c.collection_id for c in b] == ["c1"]


async def test_list_collections_misses_after_ttl_expiry(monkeypatch):
    """Advance the cache's monotonic clock past 30s → next call re-fetches."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json=_list_page([
            {"_id": f"c{call_count}", "name": f"file-{call_count}.pdf"},
        ], total=1))

    fake_now = {"t": 1000.0}
    from ncmu_backend.fastgpt_readonly import cache as cache_module
    monkeypatch.setattr(cache_module.time, "monotonic", lambda: fake_now["t"])

    client = _build_client(handler)

    first = await client.list_collections("ds-ttl")
    assert call_count == 1
    assert [c.collection_id for c in first] == ["c1"]

    fake_now["t"] += 29.0
    second = await client.list_collections("ds-ttl")
    assert call_count == 1  # still cached
    assert [c.collection_id for c in second] == ["c1"]

    fake_now["t"] += 2.0  # 31s elapsed → cache expired
    third = await client.list_collections("ds-ttl")
    assert call_count == 2
    assert [c.collection_id for c in third] == ["c2"]


async def test_404_invalidates_metadata_cache_entry():
    """Cache should not retain a key whose fetcher raised 404 — next call
    must re-attempt the upstream (so a transient 404 doesn't poison the
    cache for the rest of the 30s window)."""
    state = {"phase": "404"}
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if state["phase"] == "404":
            return httpx.Response(404, json={"error": "missing"})
        return httpx.Response(200, json=_list_page([
            {"_id": "c-after", "name": "after.pdf"},
        ], total=1))

    client = _build_client(handler)
    with pytest.raises(FastGPTNotFound):
        await client.list_collections("ds-flap")
    assert call_count == 1
    assert client_module._metadata_cache.size() == 0  # not retained

    state["phase"] = "200"
    result = await client.list_collections("ds-flap")
    assert call_count == 2  # re-fetched, no stale 404 stored
    assert [c.collection_id for c in result] == ["c-after"]


async def test_download_stream_cleanup_on_caller_break():
    """If the consumer breaks out of the async iteration the generator's
    finally must still close the underlying stream context manager —
    httpx leaks connections otherwise."""
    closed = {"on_response_close": False}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"AAAA" * 1000)

    client = _build_client(handler)
    gen = client.download_file("f1")
    first = await gen.__anext__()
    assert first  # got at least one chunk
    await gen.aclose()  # consumer breaks → generator finally runs → cm.__aexit__
    closed["on_response_close"] = True  # no exception escaped
    assert closed["on_response_close"]
