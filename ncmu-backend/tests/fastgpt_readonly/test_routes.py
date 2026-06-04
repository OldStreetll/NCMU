"""fastgpt_readonly/ endpoint integration tests — plan §4.3 AC#5, AC#7.

Covers the two employee-facing routes (``/api/v1/ncmu/kbs/{app_id}/files`` +
``/download``), the ``user_can_access_app`` permission gate, the
``file_id`` reverse-lookup防越权, and the upstream-error translation
contract (503 / 404 / 500 surfaced to the user with the spec's Chinese
copy).
"""
from __future__ import annotations

import json

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text


ZHANGSAN_UUID = "a0000001-0000-4000-8000-000000000001"
LISI_UUID = "a0000001-0000-4000-8000-000000000002"


# ───────────────────────────────────────────────────────────── helpers ──
async def _seed_app_with_kb(
    db,
    *,
    app_id: str,
    owner_uuid: str = ZHANGSAN_UUID,
    dataset_id: str = "ds-handbook",
    config_name: str | None = None,
) -> None:
    # external_kb_name is UNIQUE — derive from dataset_id if not given.
    if config_name is None:
        config_name = f"kb-{dataset_id}"
    """Seed dify_apps + app_owners + dify_external_kb_configs + binding."""
    await db.execute(text(
        "INSERT INTO dify_apps (dify_app_id, name, mode) "
        "VALUES (:aid, :name, 'chat') ON CONFLICT (dify_app_id) DO NOTHING"
    ), {"aid": app_id, "name": f"App-{app_id}"})
    await db.execute(text(
        "INSERT INTO app_owners (app_id, owner_user_id, visibility) "
        "VALUES (:aid, :uid, 'owner_only')"
    ), {"aid": app_id, "uid": owner_uuid})
    config_row = (await db.execute(text(
        "INSERT INTO dify_external_kb_configs "
        "(external_kb_name, fastgpt_dataset_id, api_key_label) "
        "VALUES (:n, :dsid, 'fastgpt-default') "
        "RETURNING id"
    ), {"n": config_name, "dsid": dataset_id})).scalar_one()
    await db.execute(text(
        "INSERT INTO dify_app_kb_bindings (dify_app_id, external_kb_config_id) "
        "VALUES (:aid, :cid)"
    ), {"aid": app_id, "cid": config_row})
    await db.commit()


def _make_mock_fastgpt_handler(scenarios: dict[str, httpx.Response]):
    """Return a MockTransport handler that picks responses by URL path.

    Keys: ``list:<datasetId>`` / ``detail:<collectionId>`` /
    ``download:<fileId>`` / ``healthcheck`` — caller supplies whichever
    paths the test exercises.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/api/core/dataset/collection/list" in url:
            # Real FastGPT v4.14.10.2: list is POST + JSON body — datasetId
            # travels in the body, not the query string (实测对账 2026-06-04).
            body = json.loads(request.content) if request.content else {}
            ds = body.get("datasetId", "")
            key = f"list:{ds}"
            return scenarios.get(
                key,
                scenarios.get("list:*", httpx.Response(200, json={"data": {"data": [], "total": 0}})),
            )
        if "/api/core/dataset/collection/detail" in url:
            cid = request.url.params.get("id", "")
            return scenarios.get(f"detail:{cid}", httpx.Response(200, json={"data": {}}))
        if "/api/common/file/read" in url:
            fid = request.url.params.get("id", "")
            return scenarios.get(f"download:{fid}", httpx.Response(404, text="not configured"))
        return httpx.Response(404, text="unhandled")
    return handler


@pytest_asyncio.fixture
async def fastgpt_route_client(app_client, async_db, monkeypatch):
    """Wraps ``app_client`` with the standard apps/ DI overrides + a slot
    for the test to inject its own FastGPT mock handler.

    Returns ``(client, async_db, install_mock)`` — call ``install_mock``
    inside the test with a scenarios dict to wire up the FastGPT side.
    """
    from ncmu_backend.apps.dify_console_client import DifyConsoleClient
    from ncmu_backend.apps.routes import get_dify_console_client
    from ncmu_backend.fastgpt_readonly.client import FastGPTReadOnlyClient
    from ncmu_backend.fastgpt_readonly.routes import _get_fastgpt_client
    from ncmu_backend.main import app, get_dify_client

    dcc = DifyConsoleClient(ttl_seconds=300)
    test_http = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
    app.dependency_overrides[get_dify_console_client] = lambda: dcc
    app.dependency_overrides[get_dify_client] = lambda: test_http

    holder: dict = {}

    def install_mock(scenarios: dict[str, httpx.Response]):
        handler = _make_mock_fastgpt_handler(scenarios)
        mock_client = FastGPTReadOnlyClient(
            base_url="http://fastgpt.example",
            api_key="test",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        holder["client"] = mock_client
        app.dependency_overrides[_get_fastgpt_client] = lambda: mock_client
        return mock_client

    try:
        yield app_client, async_db, install_mock
    finally:
        app.dependency_overrides.pop(get_dify_console_client, None)
        app.dependency_overrides.pop(get_dify_client, None)
        app.dependency_overrides.pop(_get_fastgpt_client, None)
        await test_http.aclose()
        mock_client = holder.get("client")
        if mock_client is not None:
            await mock_client.aclose()


# pytest-asyncio "auto" mode (pyproject.toml asyncio_mode = "auto") marks
# async tests automatically — no module-level pytestmark needed.


# ────────────────────────────────────────── happy paths (AC#5a / AC#5b) ──
async def test_list_kb_files_returns_normalised_file_meta(fastgpt_route_client, jwt_token):
    client, db, install_mock = fastgpt_route_client
    await _seed_app_with_kb(db, app_id="app-A", dataset_id="ds-A")
    install_mock({
        "list:ds-A": httpx.Response(200, json={"data": {"data": [
            {"_id": "coll-A", "name": "员工守则.pdf"},
        ], "total": 1}}),
        "detail:coll-A": httpx.Response(200, json={"data": {
            "_id": "coll-A",
            "name": "员工守则.pdf",
            "file": {
                "fileId": "f-handbook",
                "filename": "员工守则.pdf",
                "size": 2400000,
                "uploadTime": "2026-05-01T08:00:00Z",
                "contentType": "application/pdf",
            },
        }}),
    })

    resp = await client.get(
        "/api/v1/ncmu/kbs/app-A/files",
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body) == 1
    f = body[0]
    # AC#5a 5 字段全非空字段级断言
    assert f["file_id"] == "f-handbook"
    assert f["original_filename"] == "员工守则.pdf"
    assert f["size_bytes"] == 2400000
    assert f["uploaded_at"] == "2026-05-01T08:00:00Z"
    assert f["content_type"] == "application/pdf"


async def test_download_kb_file_streams_with_correct_headers(
    fastgpt_route_client, jwt_token,
):
    client, db, install_mock = fastgpt_route_client
    await _seed_app_with_kb(db, app_id="app-B", dataset_id="ds-B")
    install_mock({
        "list:ds-B": httpx.Response(200, json={"data": {"data": [
            {"_id": "coll-B", "name": "policy.pdf"},
        ], "total": 1}}),
        "detail:coll-B": httpx.Response(200, json={"data": {
            "file": {
                "fileId": "f-policy",
                "filename": "policy.pdf",
                "size": 100,
                "uploadTime": "2026-05-01",
                "contentType": "application/pdf",
            },
        }}),
        "download:f-policy": httpx.Response(200, content=b"PDF-BYTES-HERE",
                                            headers={"Content-Type": "application/pdf"}),
    })

    resp = await client.get(
        "/api/v1/ncmu/kbs/app-B/files/f-policy/download",
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert resp.status_code == 200
    assert resp.content == b"PDF-BYTES-HERE"
    cd = resp.headers.get("content-disposition", "")
    assert "attachment" in cd
    assert "policy.pdf" in cd
    assert resp.headers.get("content-type", "").startswith("application/pdf")


# ─────────────────────────────────── permission gate (Q8-C / AC#5a+b) ──
async def test_list_files_returns_403_when_user_cannot_access_app(
    fastgpt_route_client, jwt_token, app_client, async_db,
):
    """李四 has no owner row + Dify Console returns empty → forbidden."""
    client, db, install_mock = fastgpt_route_client
    await _seed_app_with_kb(db, app_id="app-private", owner_uuid=ZHANGSAN_UUID, dataset_id="ds-x")

    # JWT for 李四 (non-owner / not in Dify Console shared list)
    import jwt as pyjwt
    from datetime import datetime, timedelta, timezone
    lisi_token = pyjwt.encode(
        {
            "sub": LISI_UUID,
            "name": "李四",
            "iat": int(datetime.now(timezone.utc).timestamp()),
            "exp": int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()),
            "iss": "ncmu-backend",
            "aud": "ncmu-spa",
        },
        "test-secret-deterministic-32-bytes-long-xx",
        algorithm="HS256",
    )

    import respx
    from httpx import Response as HxResp
    with respx.mock(assert_all_called=False) as rx:
        rx.get(url__regex=r".*/console/api/apps.*").mock(
            return_value=HxResp(200, json={"data": []})
        )
        install_mock({})  # FastGPT should never be hit
        resp = await client.get(
            "/api/v1/ncmu/kbs/app-private/files",
            headers={"Authorization": f"Bearer {lisi_token}"},
        )
    assert resp.status_code == 403
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == 1002, detail
    assert "无权访问" in detail.get("message", ""), detail


async def test_download_rejects_file_id_outside_apps_bound_datasets(
    fastgpt_route_client, jwt_token,
):
    """防越权 (AC#5b): user owns app-C, but tries to download a file_id
    that doesn't appear in any of app-C's KBs → 403."""
    client, db, install_mock = fastgpt_route_client
    await _seed_app_with_kb(db, app_id="app-C", dataset_id="ds-C")
    install_mock({
        "list:ds-C": httpx.Response(200, json={"data": {"data": [
            {"_id": "coll-C", "name": "only-file.pdf"},
        ], "total": 1}}),
        "detail:coll-C": httpx.Response(200, json={"data": {
            "file": {
                "fileId": "f-legit",
                "filename": "only-file.pdf",
                "size": 1, "uploadTime": "2026-05-01",
                "contentType": "application/pdf",
            },
        }}),
        # download:f-foreign would succeed if hit — but reverse lookup must reject first
        "download:f-foreign": httpx.Response(200, content=b"SHOULD-NEVER-LEAK"),
    })

    resp = await client.get(
        "/api/v1/ncmu/kbs/app-C/files/f-foreign/download",
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert resp.status_code == 403
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == 1002, detail
    assert "无权访问" in detail.get("message", ""), detail


# ─────────────────────────────────────────────── empty-binding paths ──
async def test_list_files_returns_empty_when_app_has_no_kb_bindings(
    fastgpt_route_client, jwt_token, async_db,
):
    """User owns the app but it has no KB binding → 200 + []."""
    client, db, install_mock = fastgpt_route_client
    # Seed owner WITHOUT a binding
    await db.execute(text(
        "INSERT INTO dify_apps (dify_app_id, name, mode) VALUES "
        "(:aid, 'no-kb-app', 'chat') ON CONFLICT DO NOTHING"
    ), {"aid": "app-nokb"})
    await db.execute(text(
        "INSERT INTO app_owners (app_id, owner_user_id, visibility) "
        "VALUES ('app-nokb', :uid, 'owner_only')"
    ), {"uid": ZHANGSAN_UUID})
    await db.commit()
    install_mock({})  # FastGPT never hit

    resp = await client.get(
        "/api/v1/ncmu/kbs/app-nokb/files",
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert resp.status_code == 200
    assert resp.json() == []


# ─────────────────────────────── error translation (AC#3 surface check) ──
async def test_upstream_404_translates_to_404_with_file_removed_copy(
    fastgpt_route_client, jwt_token,
):
    client, db, install_mock = fastgpt_route_client
    await _seed_app_with_kb(db, app_id="app-D", dataset_id="ds-D")
    install_mock({
        "list:ds-D": httpx.Response(404, json={"error": "dataset removed"}),
    })

    resp = await client.get(
        "/api/v1/ncmu/kbs/app-D/files",
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert resp.status_code == 404
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == 2002, detail
    assert "已被管理员移除" in detail.get("message", ""), detail


async def test_upstream_connect_error_translates_to_503_unavailable(
    fastgpt_route_client, jwt_token,
):
    client, db, install_mock = fastgpt_route_client
    await _seed_app_with_kb(db, app_id="app-E", dataset_id="ds-E")

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("fastgpt down")
    from ncmu_backend.fastgpt_readonly.client import FastGPTReadOnlyClient
    from ncmu_backend.fastgpt_readonly.routes import _get_fastgpt_client
    from ncmu_backend.main import app

    mock_client = FastGPTReadOnlyClient(
        base_url="http://fastgpt.example", api_key="test",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(boom)),
    )
    app.dependency_overrides[_get_fastgpt_client] = lambda: mock_client
    try:
        resp = await client.get(
            "/api/v1/ncmu/kbs/app-E/files",
            headers={"Authorization": f"Bearer {jwt_token}"},
        )
    finally:
        await mock_client.aclose()

    assert resp.status_code == 503
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == 2001, detail
    assert "暂时不可用" in detail.get("message", ""), detail


async def test_upstream_500_with_auth_marker_surfaces_as_500_admin_copy(
    fastgpt_route_client, jwt_token,
):
    """500 + JSON {"code":403} (v4.14.x auth quirk) → 500 + "配置异常"."""
    client, db, install_mock = fastgpt_route_client
    await _seed_app_with_kb(db, app_id="app-F", dataset_id="ds-F")
    install_mock({
        "list:ds-F": httpx.Response(
            500,
            content=json.dumps({"code": 403, "statusText": "unAuthorization"}).encode(),
            headers={"Content-Type": "application/json"},
        ),
    })

    resp = await client.get(
        "/api/v1/ncmu/kbs/app-F/files",
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert resp.status_code == 500
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == 2003, detail
    assert "配置异常" in detail.get("message", ""), detail


# ────────────────────────────────────── unauthenticated request (AC#7) ──
async def test_missing_jwt_returns_401(fastgpt_route_client):
    client, _, _install = fastgpt_route_client
    resp = await client.get("/api/v1/ncmu/kbs/anything/files")
    assert resp.status_code == 401


