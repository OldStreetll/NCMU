"""TASK-PE-10 — admin DSL export (single YAML + multi ZIP) tests.

Wire shape grounded against the 2026-05-30 docker-exec spike (Boss path-A):
  GET /console/api/apps/{id}/export?include_secret=<bool> → 200,
  body = JSON envelope ``{"data": "<yaml str>"}`` (NOT raw YAML).

Mock strategy: real ``DifyConsoleClient`` + respx for the upstream Dify
Console export call (守 SOP 10 ``feedback_tdd_mock_vs_real_api`` — respx
yields real httpx.Response objects). Per-test fresh dcc + test-scoped
httpx client override the lru_cache'd singleton, exactly like
``test_sync_apps.py``.

Covered:
  - export_app envelope-unwrap (returns ``.data`` str)
  - export_app malformed envelope → 502/9001
  - export_app unknown id (upstream 404) → 502/1003 (NOT a local 404)
  - GET single → 200 application/yaml + Content-Disposition + body
  - GET single include_secret=true → query forwarded + log.warning
  - POST multi → application/zip; unzip + per-App YAML + MANIFEST.json
  - POST multi CJK App name → safe ZIP entry (no path sep, CJK kept)
  - POST multi include_secret=true → MANIFEST flag + log.warning
  - POST multi empty app_ids → 422 (Field min_length=1)
  - GET dsl-candidates → cache rows (id/name/mode)
  - require_admin gate (non-admin → 403/1201) on all three
  - outbound Dify auth headers + include_secret query locked verbatim
"""
from __future__ import annotations

import io
import json
import logging
import zipfile

import httpx
import pytest
import pytest_asyncio
import respx
from httpx import Response

from ncmu_backend.apps.dify_console_client import DifyConsoleClient
from ncmu_backend.db.models import DifyApp


ZHANGSAN = "a0000001-0000-4000-8000-000000000001"  # admin (NCMU_ADMIN_USER_IDS default)
LISI = "a0000001-0000-4000-8000-000000000002"      # non-admin

EXPORT_RE = r".*/console/api/apps/[^/]+/export.*"


def _yaml_for(app_id: str) -> str:
    """Deterministic per-App DSL body so multi-export entries are distinct."""
    return f"app:\n  mode: chat\n  name: app-{app_id}\nversion: 0.1.5\n"


def _export_side_effect(request: httpx.Request) -> Response:
    """respx callable: echo the requested app_id back inside the envelope."""
    # path = /console/api/apps/{app_id}/export
    app_id = request.url.path.rstrip("/").split("/")[-2]
    return Response(200, json={"data": _yaml_for(app_id)})


@pytest_asyncio.fixture
async def dsl_client(app_client):
    """Override get_dify_console_client (fresh dcc, no lru_cache leak) and
    get_dify_client (test-scoped real httpx that respx intercepts).

    Yields ``(client, dcc, http)``.
    """
    from ncmu_backend.apps.routes import get_dify_console_client
    from ncmu_backend.main import app, get_dify_client

    dcc = DifyConsoleClient(ttl_seconds=300)
    test_http = httpx.AsyncClient(timeout=httpx.Timeout(10.0))

    app.dependency_overrides[get_dify_console_client] = lambda: dcc
    app.dependency_overrides[get_dify_client] = lambda: test_http
    try:
        yield app_client, dcc, test_http
    finally:
        app.dependency_overrides.pop(get_dify_console_client, None)
        app.dependency_overrides.pop(get_dify_client, None)
        await test_http.aclose()


def _admin_headers(jwt_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {jwt_token}"}


def _non_admin_token(jwt_secret: str) -> str:
    from ncmu_backend.auth.jwt_utils import sign_jwt

    token, _exp = sign_jwt(user_id=LISI, name="李四", secret=jwt_secret)
    return token


# ===================================================================== #
# export_app — unit (envelope unwrap + error mapping)
# ===================================================================== #
async def test_export_app_unwraps_data_envelope(dsl_client):
    _client, dcc, http = dsl_client
    with respx.mock(assert_all_called=True) as rx:
        route = rx.get(url__regex=EXPORT_RE).mock(
            return_value=Response(200, json={"data": "app:\n  name: foo\n"})
        )
        out = await dcc.export_app(
            app_id="app-x",
            http=http,
            base_url="http://dify-api:5001",
            api_key="k",
            tenant_id="t",
            include_secret=False,
        )
    assert out == "app:\n  name: foo\n"
    # include_secret=false forwarded as a query flag
    assert route.calls[0].request.url.params.get("include_secret") == "false"
    # auth headers locked verbatim
    sent = route.calls[0].request
    assert sent.headers.get("Authorization") == "Bearer k"
    assert sent.headers.get("X-WORKSPACE-ID") == "t"


async def test_export_app_include_secret_true_query(dsl_client):
    _client, dcc, http = dsl_client
    with respx.mock(assert_all_called=True) as rx:
        route = rx.get(url__regex=EXPORT_RE).mock(
            return_value=Response(200, json={"data": "x"})
        )
        await dcc.export_app(
            app_id="a", http=http, base_url="http://d:5001",
            api_key="k", tenant_id="t", include_secret=True,
        )
    assert route.calls[0].request.url.params.get("include_secret") == "true"


async def test_export_app_malformed_envelope_502(dsl_client):
    """A fork returning ``{...}`` without a str ``data`` → 502/9001, never
    a silent ``"None"`` body."""
    from fastapi import HTTPException

    _client, dcc, http = dsl_client
    with respx.mock(assert_all_called=True) as rx:
        rx.get(url__regex=EXPORT_RE).mock(
            return_value=Response(200, json={"not_data": 1})
        )
        with pytest.raises(HTTPException) as ei:
            await dcc.export_app(
                app_id="a", http=http, base_url="http://d:5001",
                api_key="k", tenant_id="t",
            )
    assert ei.value.status_code == 502
    assert ei.value.detail["code"] == 9001


async def test_export_app_unknown_id_upstream_404_maps_1003(dsl_client):
    from fastapi import HTTPException

    _client, dcc, http = dsl_client
    with respx.mock(assert_all_called=True) as rx:
        rx.get(url__regex=EXPORT_RE).mock(
            return_value=Response(404, json={"code": "app_not_found",
                                             "message": "App not found.",
                                             "status": 404})
        )
        with pytest.raises(HTTPException) as ei:
            await dcc.export_app(
                app_id="missing", http=http, base_url="http://d:5001",
                api_key="k", tenant_id="t",
            )
    assert ei.value.status_code == 502
    assert ei.value.detail["code"] == 1003  # 4xx upstream → 1003


# ===================================================================== #
# REWORK-PE-10-INDEP #2 — app_id injection / audit-bypass chokepoint
# ===================================================================== #
async def test_export_app_rejects_injection_app_id_before_wire(dsl_client):
    """A malicious app_id (``?`` query break-out, ``../`` traversal, path
    separators) is rejected at export_app's entry — 400, and NO outbound
    Dify call is ever made (so the admin-key request never leaves)."""
    from fastapi import HTTPException

    _client, dcc, http = dsl_client
    bad_ids = [
        "real?include_secret=true",  # query first-wins → audit bypass
        "../../other",                # path traversal
        "a/b",                        # path separator
        "a&b=1",                      # extra query param
        "a b",                        # whitespace
        "",                           # empty
    ]
    with respx.mock(assert_all_called=False) as rx:
        route = rx.get(url__regex=EXPORT_RE).mock(
            return_value=Response(200, json={"data": "x"})
        )
        for bad in bad_ids:
            with pytest.raises(HTTPException) as ei:
                await dcc.export_app(
                    app_id=bad, http=http, base_url="http://d:5001",
                    api_key="k", tenant_id="t",
                )
            assert ei.value.status_code == 400, bad
            assert ei.value.detail["code"] == 1003
    assert route.call_count == 0  # never touched the wire


async def test_export_app_include_secret_via_params_not_concat(dsl_client):
    """include_secret is a real, httpx-encoded query param (params=), not a
    string-concatenated suffix — so an app_id cannot shadow it."""
    _client, dcc, http = dsl_client
    with respx.mock(assert_all_called=True) as rx:
        route = rx.get(url__regex=EXPORT_RE).mock(
            return_value=Response(200, json={"data": "x"})
        )
        await dcc.export_app(
            app_id="valid-id", http=http, base_url="http://d:5001",
            api_key="k", tenant_id="t", include_secret=True,
        )
    sent = route.calls[0].request
    # path carries only the (validated) app_id; the flag lives in the query
    assert sent.url.path == "/console/api/apps/valid-id/export"
    assert sent.url.params.get("include_secret") == "true"


# ===================================================================== #
# GET /admin/apps/{id}/dsl — single
# ===================================================================== #
async def test_single_dsl_returns_yaml(dsl_client, jwt_token):
    client, _dcc, _http = dsl_client
    with respx.mock(assert_all_called=True) as rx:
        rx.get(url__regex=EXPORT_RE).mock(side_effect=_export_side_effect)
        resp = await client.get(
            "/api/v1/ncmu/admin/apps/app-001/dsl",
            headers=_admin_headers(jwt_token),
        )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/yaml")
    assert resp.text == _yaml_for("app-001")
    assert "app-001.yaml" in resp.headers["content-disposition"]


async def test_single_dsl_include_secret_logs_warning(
    dsl_client, jwt_token, caplog
):
    client, _dcc, _http = dsl_client
    with respx.mock(assert_all_called=True) as rx:
        route = rx.get(url__regex=EXPORT_RE).mock(side_effect=_export_side_effect)
        with caplog.at_level(logging.WARNING, logger="ncmu_backend.admin.dsl.routes"):
            resp = await client.get(
                "/api/v1/ncmu/admin/apps/app-001/dsl?include_secret=true",
                headers=_admin_headers(jwt_token),
            )
    assert resp.status_code == 200, resp.text
    assert route.calls[0].request.url.params.get("include_secret") == "true"
    assert any("WITH SECRETS" in r.message for r in caplog.records)


async def test_single_dsl_uses_cached_name_for_filename(
    dsl_client, jwt_token, async_db
):
    client, _dcc, _http = dsl_client
    async_db.add(DifyApp(dify_app_id="app-cjk", name="[KB]员工手册", mode="chat"))
    await async_db.commit()
    with respx.mock(assert_all_called=True) as rx:
        rx.get(url__regex=EXPORT_RE).mock(side_effect=_export_side_effect)
        resp = await client.get(
            "/api/v1/ncmu/admin/apps/app-cjk/dsl",
            headers=_admin_headers(jwt_token),
        )
    assert resp.status_code == 200, resp.text
    cd = resp.headers["content-disposition"]
    # ASCII fallback + RFC5987 filename* with the CJK-safe stem
    assert 'filename="app-cjk.yaml"' in cd
    assert "filename*=UTF-8''" in cd
    # path separators must never survive into the filename
    assert "/" not in cd.split("filename*=UTF-8''")[1]


async def test_single_dsl_non_admin_403(dsl_client, jwt_secret):
    client, _dcc, _http = dsl_client
    resp = await client.get(
        "/api/v1/ncmu/admin/apps/app-001/dsl",
        headers=_admin_headers(_non_admin_token(jwt_secret)),
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == 1201


# ===================================================================== #
# POST /admin/apps/dsl-export — multi ZIP
# ===================================================================== #
async def test_multi_dsl_zip_contents(dsl_client, jwt_token, async_db):
    client, _dcc, _http = dsl_client
    async_db.add(DifyApp(dify_app_id="app-a", name="销售助手", mode="chat"))
    async_db.add(DifyApp(dify_app_id="app-b", name="HR Bot", mode="agent-chat"))
    await async_db.commit()

    with respx.mock(assert_all_called=True) as rx:
        rx.get(url__regex=EXPORT_RE).mock(side_effect=_export_side_effect)
        resp = await client.post(
            "/api/v1/ncmu/admin/apps/dsl-export",
            json={"app_ids": ["app-a", "app-b"], "include_secret": False},
            headers=_admin_headers(jwt_token),
        )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/zip"
    assert "ncmu-dify-apps-export-" in resp.headers["content-disposition"]

    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = set(zf.namelist())
    assert "MANIFEST.json" in names
    # per-App entries: {safe_name}_{id}.yaml  (CJK name 兼容)
    assert "销售助手_app-a.yaml" in names
    assert "HR_Bot_app-b.yaml" in names  # space → _
    assert zf.read("销售助手_app-a.yaml").decode("utf-8") == _yaml_for("app-a")
    assert zf.read("HR_Bot_app-b.yaml").decode("utf-8") == _yaml_for("app-b")

    manifest = json.loads(zf.read("MANIFEST.json").decode("utf-8"))
    assert manifest["app_ids"] == ["app-a", "app-b"]
    assert manifest["include_secret"] is False
    assert "exported_at" in manifest and manifest["exported_at"]


async def test_multi_dsl_uncached_app_falls_back_to_id(dsl_client, jwt_token):
    """App not in cache table still exports — Dify keys on the id."""
    client, _dcc, _http = dsl_client
    with respx.mock(assert_all_called=True) as rx:
        rx.get(url__regex=EXPORT_RE).mock(side_effect=_export_side_effect)
        resp = await client.post(
            "/api/v1/ncmu/admin/apps/dsl-export",
            json={"app_ids": ["ghost-1"]},
            headers=_admin_headers(jwt_token),
        )
    assert resp.status_code == 200, resp.text
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    assert "ghost-1_ghost-1.yaml" in zf.namelist()


async def test_multi_dsl_include_secret_manifest_and_warning(
    dsl_client, jwt_token, caplog
):
    client, _dcc, _http = dsl_client
    with respx.mock(assert_all_called=True) as rx:
        rx.get(url__regex=EXPORT_RE).mock(side_effect=_export_side_effect)
        with caplog.at_level(logging.WARNING, logger="ncmu_backend.admin.dsl.routes"):
            resp = await client.post(
                "/api/v1/ncmu/admin/apps/dsl-export",
                json={"app_ids": ["app-a"], "include_secret": True},
                headers=_admin_headers(jwt_token),
            )
    assert resp.status_code == 200, resp.text
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    manifest = json.loads(zf.read("MANIFEST.json").decode("utf-8"))
    assert manifest["include_secret"] is True
    assert any("WITH SECRETS" in r.message for r in caplog.records)


async def test_multi_dsl_empty_app_ids_422(dsl_client, jwt_token):
    client, _dcc, _http = dsl_client
    resp = await client.post(
        "/api/v1/ncmu/admin/apps/dsl-export",
        json={"app_ids": []},
        headers=_admin_headers(jwt_token),
    )
    assert resp.status_code == 422


async def test_multi_dsl_non_admin_403(dsl_client, jwt_secret):
    client, _dcc, _http = dsl_client
    resp = await client.post(
        "/api/v1/ncmu/admin/apps/dsl-export",
        json={"app_ids": ["app-a"]},
        headers=_admin_headers(_non_admin_token(jwt_secret)),
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == 1201


async def test_multi_dsl_rejects_query_injection_no_audit_bypass(
    dsl_client, jwt_token, caplog
):
    """A malicious app_ids element is 422'd at the schema boundary — zero
    outbound calls AND the include_secret ``log.warning`` audit never
    fires (the injection-via-query bypass the panel flagged is closed)."""
    client, _dcc, _http = dsl_client
    with respx.mock(assert_all_called=False) as rx:
        route = rx.get(url__regex=EXPORT_RE).mock(side_effect=_export_side_effect)
        with caplog.at_level(
            logging.WARNING, logger="ncmu_backend.admin.dsl.routes"
        ):
            resp = await client.post(
                "/api/v1/ncmu/admin/apps/dsl-export",
                json={
                    "app_ids": ["good-1", "evil?include_secret=true"],
                    "include_secret": False,
                },
                headers=_admin_headers(jwt_token),
            )
    assert resp.status_code == 422, resp.text
    assert route.call_count == 0
    assert not any("WITH SECRETS" in r.message for r in caplog.records)


async def test_multi_dsl_rejects_path_traversal(dsl_client, jwt_token):
    client, _dcc, _http = dsl_client
    with respx.mock(assert_all_called=False) as rx:
        route = rx.get(url__regex=EXPORT_RE).mock(side_effect=_export_side_effect)
        resp = await client.post(
            "/api/v1/ncmu/admin/apps/dsl-export",
            json={"app_ids": ["../../etc/passwd"]},
            headers=_admin_headers(jwt_token),
        )
    assert resp.status_code == 422, resp.text
    assert route.call_count == 0


async def test_single_dsl_rejects_injection_app_id(dsl_client, jwt_token):
    """Path-param pattern 422s an encoded ``?`` break-out before any wire."""
    client, _dcc, _http = dsl_client
    with respx.mock(assert_all_called=False) as rx:
        route = rx.get(url__regex=EXPORT_RE).mock(side_effect=_export_side_effect)
        # %3F decodes to '?' inside the {app_id} path segment
        resp = await client.get(
            "/api/v1/ncmu/admin/apps/evil%3Finclude_secret=true/dsl",
            headers=_admin_headers(jwt_token),
        )
    assert resp.status_code == 422, resp.text
    assert route.call_count == 0


# ===================================================================== #
# GET /admin/apps/dsl-candidates
# ===================================================================== #
async def test_dsl_candidates_lists_cache_rows(dsl_client, jwt_token, async_db):
    client, _dcc, _http = dsl_client
    async_db.add(DifyApp(dify_app_id="cand-1", name="候选一", mode="workflow"))
    await async_db.commit()
    resp = await client.get(
        "/api/v1/ncmu/admin/apps/dsl-candidates",
        headers=_admin_headers(jwt_token),
    )
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    match = [r for r in rows if r["dify_app_id"] == "cand-1"]
    assert len(match) == 1
    assert match[0] == {"dify_app_id": "cand-1", "name": "候选一", "mode": "workflow"}


async def test_dsl_candidates_non_admin_403(dsl_client, jwt_secret):
    client, _dcc, _http = dsl_client
    resp = await client.get(
        "/api/v1/ncmu/admin/apps/dsl-candidates",
        headers=_admin_headers(_non_admin_token(jwt_secret)),
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == 1201
