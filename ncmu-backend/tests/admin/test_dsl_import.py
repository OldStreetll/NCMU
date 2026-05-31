"""TASK-PE-11 — admin DSL import (single YAML / ZIP / per-App + cache sync).

Wire shape grounded against the 2026-05-30 docker-exec spike (Boss path-A):
  POST /console/api/apps/imports {"mode":"yaml-content","yaml_content":Y}
    → 200 {"id","status":"completed","app_id",...}   (app created)
    → 400 {"status":"failed","error":...,"app_id":null}
    → "pending" ⇒ POST imports/{import_id}/confirm
  GET  /console/api/apps/imports/{APP_ID}/check-dependencies (key=app_id!)
    → {"leaked_dependencies":[...]}  ([] = no missing plugins)

Mock strategy: real DifyConsoleClient + respx for the upstream Dify calls
(守 SOP 10 — respx yields real httpx.Response). Per-test fresh dcc +
test-scoped httpx override the lru_cache'd singleton (mirrors
test_dsl_export.py's dsl_client).
"""
from __future__ import annotations

import io
import json
import zipfile

import httpx
import pytest
import pytest_asyncio
import respx
import yaml
from httpx import Response
from sqlalchemy import select

from ncmu_backend.apps.dify_console_client import DifyConsoleClient
from ncmu_backend.db.models import DifyApp


ZHANGSAN = "a0000001-0000-4000-8000-000000000001"  # admin
LISI = "a0000001-0000-4000-8000-000000000002"      # non-admin

IMPORTS_RE = r".*/console/api/apps/imports$"
CONFIRM_RE = r".*/console/api/apps/imports/[^/]+/confirm$"
CHECKDEPS_RE = r".*/console/api/apps/imports/[^/]+/check-dependencies$"

_LEAKED = [{"type": "marketplace", "value": {"plugin_unique_identifier": "langgenius/openai:0.0.1"}}]


def _yaml(name: str, mode: str = "chat") -> str:
    return f"app:\n  name: {name}\n  mode: {mode}\n  description: t\nversion: 0.1.5\n"


def _imports_side_effect(request: httpx.Request) -> Response:
    """Route the import by the YAML's app.name (so ZIP members differ)."""
    body = json.loads(request.content)
    parsed = yaml.safe_load(body["yaml_content"])
    name = parsed["app"]["name"]
    if name == "FAILAPP":
        return Response(400, json={
            "id": "imp-fail", "status": "failed", "app_id": None,
            "app_mode": None, "error": "Missing app data in YAML content",
        })
    if name == "PENDAPP":
        return Response(200, json={
            "id": "imp-pend", "status": "pending", "app_id": None,
            "app_mode": None, "error": "",
        })
    app_id = f"app-{name.lower()}"
    return Response(200, json={
        "id": f"imp-{name.lower()}", "status": "completed", "app_id": app_id,
        "app_mode": parsed["app"].get("mode", "chat"), "error": "",
    })


def _checkdeps_side_effect(request: httpx.Request) -> Response:
    app_id = request.url.path.split("/imports/")[1].split("/")[0]
    leaked = _LEAKED if "plugin" in app_id else []
    return Response(200, json={"leaked_dependencies": leaked})


def _confirm_side_effect(request: httpx.Request) -> Response:
    return Response(200, json={
        "id": "imp-pend", "status": "completed", "app_id": "app-pendapp",
        "app_mode": "chat", "error": "",
    })


def _mock_all(rx: respx.MockRouter) -> None:
    rx.post(url__regex=CONFIRM_RE).mock(side_effect=_confirm_side_effect)
    rx.get(url__regex=CHECKDEPS_RE).mock(side_effect=_checkdeps_side_effect)
    rx.post(url__regex=IMPORTS_RE).mock(side_effect=_imports_side_effect)


@pytest_asyncio.fixture
async def import_client(app_client):
    from ncmu_backend.apps.routes import get_dify_console_client
    from ncmu_backend.main import app, get_dify_client

    dcc = DifyConsoleClient(ttl_seconds=300)
    test_http = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
    app.dependency_overrides[get_dify_console_client] = lambda: dcc
    app.dependency_overrides[get_dify_client] = lambda: test_http
    try:
        yield app_client
    finally:
        app.dependency_overrides.pop(get_dify_console_client, None)
        app.dependency_overrides.pop(get_dify_client, None)
        await test_http.aclose()


def _admin(jwt_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {jwt_token}"}


def _yaml_file(name: str = "app.yaml", body: str | None = None):
    data = (body if body is not None else _yaml("GOODA")).encode("utf-8")
    return {"file": (name, data, "application/x-yaml")}


def _zip_file(members: list[tuple[str, str]], name: str = "bundle.zip"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for fname, content in members:
            zf.writestr(fname, content)
    buf.seek(0)
    return {"file": (name, buf.read(), "application/zip")}


def _non_admin(jwt_secret: str) -> dict[str, str]:
    from ncmu_backend.auth.jwt_utils import sign_jwt

    token, _exp = sign_jwt(user_id=LISI, name="李四", secret=jwt_secret)
    return {"Authorization": f"Bearer {token}"}


URL = "/api/v1/ncmu/admin/apps/dsl-import"


# ===================================================================== #
# Single YAML — success + cache upsert
# ===================================================================== #
async def test_single_yaml_success_and_cache_upsert(import_client, jwt_token, async_db):
    with respx.mock(assert_all_called=False) as rx:
        _mock_all(rx)
        resp = await import_client.post(
            URL, files=_yaml_file(body=_yaml("GOODA", "completion")),
            headers=_admin(jwt_token),
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    assert body["failed"] == []
    assert len(body["succeeded"]) == 1
    assert body["succeeded"][0]["app_id"] == "app-gooda"
    assert body["succeeded"][0]["name"] == "GOODA"
    # auto-sync: the imported app is now in the dify_apps cache
    row = (
        await async_db.execute(
            select(DifyApp).where(DifyApp.dify_app_id == "app-gooda")
        )
    ).scalar_one_or_none()
    assert row is not None
    assert row.name == "GOODA"
    assert row.mode == "completion"
    assert row.last_synced_at is not None


# ===================================================================== #
# Per-App failure rows (field-level)
# ===================================================================== #
async def test_yaml_parse_error_failed_row(import_client, jwt_token):
    with respx.mock(assert_all_called=False) as rx:
        _mock_all(rx)
        resp = await import_client.post(
            URL, files=_yaml_file(body="::: not [ valid yaml"),
            headers=_admin(jwt_token),
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["succeeded"] == []
    assert body["failed"][0]["error_code"] == "YAML_PARSE"


async def test_yaml_missing_app_section_failed_row(import_client, jwt_token):
    with respx.mock(assert_all_called=False) as rx:
        _mock_all(rx)
        resp = await import_client.post(
            URL, files=_yaml_file(body="version: 0.1.5\nnotanapp: 1\n"),
            headers=_admin(jwt_token),
        )
    body = resp.json()
    assert body["failed"][0]["error_code"] == "YAML_PARSE"
    assert "app" in body["failed"][0]["error_message"]


async def test_dify_import_failed_row(import_client, jwt_token):
    with respx.mock(assert_all_called=False) as rx:
        _mock_all(rx)
        resp = await import_client.post(
            URL, files=_yaml_file(body=_yaml("FAILAPP")),
            headers=_admin(jwt_token),
        )
    body = resp.json()
    assert body["succeeded"] == []
    row = body["failed"][0]
    assert row["error_code"] == "DIFY_IMPORT"
    assert "Missing app data" in row["error_message"]


async def test_plugin_missing_failed_row_but_app_cached(
    import_client, jwt_token, async_db
):
    """PLUGIN_MISSING → failed row WITH plugin_missing + app_id; the app was
    created in Dify (never auto-deleted) so it is still cached."""
    with respx.mock(assert_all_called=False) as rx:
        _mock_all(rx)
        resp = await import_client.post(
            URL, files=_yaml_file(body=_yaml("PLUGINAPP")),
            headers=_admin(jwt_token),
        )
    body = resp.json()
    assert body["succeeded"] == []
    row = body["failed"][0]
    assert row["error_code"] == "PLUGIN_MISSING"
    assert row["app_id"] == "app-pluginapp"
    assert row["plugin_missing"] == _LEAKED
    # app exists in Dify → cached despite the missing-plugin failure row
    cached = (
        await async_db.execute(
            select(DifyApp).where(DifyApp.dify_app_id == "app-pluginapp")
        )
    ).scalar_one_or_none()
    assert cached is not None


# ===================================================================== #
# pending → confirm
# ===================================================================== #
async def test_pending_status_triggers_confirm(import_client, jwt_token):
    with respx.mock(assert_all_called=False) as rx:
        _mock_all(rx)
        resp = await import_client.post(
            URL, files=_yaml_file(body=_yaml("PENDAPP")),
            headers=_admin(jwt_token),
        )
    body = resp.json()
    assert len(body["succeeded"]) == 1
    assert body["succeeded"][0]["app_id"] == "app-pendapp"


# ===================================================================== #
# ZIP — multi + partial success
# ===================================================================== #
async def test_zip_multi_all_succeed(import_client, jwt_token):
    members = [("a.yaml", _yaml("GOODA")), ("b.yml", _yaml("GOODB"))]
    with respx.mock(assert_all_called=False) as rx:
        _mock_all(rx)
        resp = await import_client.post(
            URL, files=_zip_file(members), headers=_admin(jwt_token)
        )
    body = resp.json()
    assert body["total"] == 2
    ids = {s["app_id"] for s in body["succeeded"]}
    assert ids == {"app-gooda", "app-goodb"}
    assert body["failed"] == []


async def test_zip_partial_success(import_client, jwt_token):
    members = [
        ("ok.yaml", _yaml("GOODA")),
        ("bad.yaml", "::: broken"),
        ("fail.yaml", _yaml("FAILAPP")),
    ]
    with respx.mock(assert_all_called=False) as rx:
        _mock_all(rx)
        resp = await import_client.post(
            URL, files=_zip_file(members), headers=_admin(jwt_token)
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 3
    assert len(body["succeeded"]) == 1
    assert {f["error_code"] for f in body["failed"]} == {"YAML_PARSE", "DIFY_IMPORT"}


async def test_zip_non_yaml_members_ignored(import_client, jwt_token):
    members = [("app.yaml", _yaml("GOODA")), ("readme.txt", "hello"), ("MANIFEST.json", "{}")]
    with respx.mock(assert_all_called=False) as rx:
        _mock_all(rx)
        resp = await import_client.post(
            URL, files=_zip_file(members), headers=_admin(jwt_token)
        )
    body = resp.json()
    assert body["total"] == 1  # only the .yaml member counted


# ===================================================================== #
# Request-level faults
# ===================================================================== #
async def test_oversize_yaml_413(import_client, jwt_token):
    big = "app:\n  name: BIG\n#" + ("x" * (5 * 1024 * 1024 + 16))
    with respx.mock(assert_all_called=False) as rx:
        _mock_all(rx)
        resp = await import_client.post(
            URL, files=_yaml_file(name="big.yaml", body=big),
            headers=_admin(jwt_token),
        )
    assert resp.status_code == 413, resp.text


async def test_corrupt_zip_400(import_client, jwt_token):
    with respx.mock(assert_all_called=False) as rx:
        _mock_all(rx)
        resp = await import_client.post(
            URL, files={"file": ("bad.zip", b"not a zip at all", "application/zip")},
            headers=_admin(jwt_token),
        )
    assert resp.status_code == 400, resp.text


async def test_empty_zip_no_yaml_400(import_client, jwt_token):
    with respx.mock(assert_all_called=False) as rx:
        _mock_all(rx)
        resp = await import_client.post(
            URL, files=_zip_file([("readme.txt", "hi")]),
            headers=_admin(jwt_token),
        )
    assert resp.status_code == 400, resp.text


# ===================================================================== #
# REWORK-PE-11-INDEP — unbounded-decompression (zip-bomb / fan-out) guards
# ===================================================================== #
async def test_zip_oversize_member_rejected(import_client, jwt_token):
    """A member whose UNCOMPRESSED size exceeds the per-entry cap → that
    member ENTRY_TOO_LARGE (failed row), batch continues (sibling succeeds).

    反证: before the guard the >5MB member would be decoded + imported
    (succeeded, no ENTRY_TOO_LARGE) — this assertion only holds post-fix."""
    big_yaml = "app:\n  name: BIGAPP\n  mode: chat\n#" + ("x" * (5 * 1024 * 1024 + 16))
    members = [("ok.yaml", _yaml("GOODA")), ("big.yaml", big_yaml)]
    with respx.mock(assert_all_called=False) as rx:
        _mock_all(rx)
        resp = await import_client.post(
            URL, files=_zip_file(members), headers=_admin(jwt_token)
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 2
    assert {s["app_id"] for s in body["succeeded"]} == {"app-gooda"}
    big = [f for f in body["failed"] if f["filename"] == "big.yaml"]
    assert len(big) == 1
    assert big[0]["error_code"] == "ENTRY_TOO_LARGE"


async def test_zip_too_many_members_rejected(import_client, jwt_token):
    """>_MAX_ZIP_MEMBERS YAML members → whole-request 413 (fan-out guard).

    反证: before the guard all 51 members would be processed (51 outbound
    imports), returning 200 — this 413 only holds post-fix."""
    members = [(f"a{i}.yaml", _yaml(f"APP{i}")) for i in range(51)]
    with respx.mock(assert_all_called=False) as rx:
        _mock_all(rx)
        resp = await import_client.post(
            URL, files=_zip_file(members), headers=_admin(jwt_token)
        )
    assert resp.status_code == 413, resp.text


async def test_zip_total_budget_exceeded(import_client, jwt_token, monkeypatch):
    """Cumulative decompressed bytes over the total budget → 413 backstop.

    The 100MB real budget is impractical to hit in a unit test, so the
    threshold is monkeypatched low; the guard logic (per-read accumulation +
    abort) is what's under test. 反证: without the accumulation the members
    would all import → 200."""
    from ncmu_backend.admin.dsl import routes as dsl_routes

    monkeypatch.setattr(dsl_routes, "_MAX_ZIP_TOTAL_BYTES", 2 * 1024 * 1024)
    # two ~1.5MB members: each under the 5MB per-entry cap, but cumulative
    # > 2MB → 413 fires mid-loop.
    pad = "x" * (1_500_000)
    members = [
        ("a.yaml", f"app:\n  name: A\n  mode: chat\n#{pad}"),
        ("b.yaml", f"app:\n  name: B\n  mode: chat\n#{pad}"),
    ]
    with respx.mock(assert_all_called=False) as rx:
        _mock_all(rx)
        resp = await import_client.post(
            URL, files=_zip_file(members), headers=_admin(jwt_token)
        )
    assert resp.status_code == 413, resp.text


async def test_import_requires_admin(import_client, jwt_secret):
    with respx.mock(assert_all_called=False) as rx:
        _mock_all(rx)
        resp = await import_client.post(
            URL, files=_yaml_file(), headers=_non_admin(jwt_secret)
        )
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == 1201


# ===================================================================== #
# client-method injection guards (defense-in-depth on Dify-returned ids)
# ===================================================================== #
async def test_client_methods_reject_injection_ids():
    from fastapi import HTTPException

    dcc = DifyConsoleClient(ttl_seconds=300)
    http = httpx.AsyncClient()
    try:
        with respx.mock(assert_all_called=False) as rx:
            route = rx.route().mock(return_value=Response(200, json={}))
            for bad in ["a/b", "../x", "a?b=1"]:
                with pytest.raises(HTTPException) as ei:
                    await dcc.check_app_dependencies(
                        app_id=bad, http=http, base_url="http://d:5001",
                        api_key="k", tenant_id="t",
                    )
                assert ei.value.status_code == 400
                with pytest.raises(HTTPException):
                    await dcc.confirm_import(
                        import_id=bad, http=http, base_url="http://d:5001",
                        api_key="k", tenant_id="t",
                    )
            assert route.call_count == 0
    finally:
        await http.aclose()
