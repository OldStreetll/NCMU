"""TASK-PC2-C — /api/v1/ncmu/apps endpoint dual-track union/dedupe coverage.

routes.py post-paradigm-shift wires through apps/services.py:list_apps_for_user
(plan §4.4 / spec §3.3). End-to-end coverage:
- HTTP layer (JWT + dependency injection)
- services layer (shared ∪ owner union)
- DB layer (app_owners INNER JOIN dify_apps)
- AppOut projection (id/name/type/mode/description)

Coexists with test_routes_mode_field.py (TASK-69 AC#4 mode-field coverage / 不动).
"""
from __future__ import annotations

import httpx
import pytest_asyncio
import respx
from httpx import Response
from sqlalchemy import text


ZHANGSAN_UUID = "a0000001-0000-4000-8000-000000000001"


@pytest_asyncio.fixture
async def apps_route_client(app_client, async_db):
    """app_client + per-test fresh DifyConsoleClient (no cache leak) +
    real test httpx so lifespan-injected get_dify_client doesn't need lifespan.

    Mirrors test_routes_mode_field.py's apps_client_no_cache fixture but
    keeps async_db visible so the test can pre-seed app_owners rows.
    """
    from ncmu_backend.apps.dify_console_client import DifyConsoleClient
    from ncmu_backend.apps.routes import get_dify_console_client
    from ncmu_backend.main import app, get_dify_client

    dcc = DifyConsoleClient(ttl_seconds=300)
    test_http = httpx.AsyncClient(timeout=httpx.Timeout(10.0))

    app.dependency_overrides[get_dify_console_client] = lambda: dcc
    app.dependency_overrides[get_dify_client] = lambda: test_http
    try:
        yield app_client, async_db
    finally:
        app.dependency_overrides.pop(get_dify_console_client, None)
        app.dependency_overrides.pop(get_dify_client, None)
        await test_http.aclose()


async def _seed_owner(db, app_id: str, name: str, mode: str = "chat") -> None:
    await db.execute(text(
        "INSERT INTO dify_apps (dify_app_id, name, mode) "
        "VALUES (:aid, :name, :mode) ON CONFLICT (dify_app_id) DO NOTHING"
    ), {"aid": app_id, "name": name, "mode": mode})
    await db.execute(text(
        "INSERT INTO app_owners (app_id, owner_user_id, visibility) "
        "VALUES (:aid, :uid, 'owner_only')"
    ), {"aid": app_id, "uid": ZHANGSAN_UUID})
    await db.commit()


# ── Union — shared ∪ owner, both populated ─────────────────────────────────


async def test_route_returns_union_shared_plus_owner(apps_route_client, jwt_token):
    client, db = apps_route_client
    await _seed_owner(db, "owner-route-1", "我的个人手册", "chat")
    payload = {
        "data": [
            {"id": "shared-route-1", "name": "[KB]员工手册",
             "mode": "advanced-chat", "description": "HR onboarding"},
        ],
    }
    with respx.mock(assert_all_called=True) as rx:
        rx.get(url__regex=r".*/console/api/apps.*").mock(
            return_value=Response(200, json=payload)
        )
        resp = await client.get(
            "/api/v1/ncmu/apps",
            headers={"Authorization": f"Bearer {jwt_token}"},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    ids = [a["id"] for a in body]
    assert set(ids) == {"shared-route-1", "owner-route-1"}, (
        f"union mismatch: got {ids}"
    )
    # Plan AC#1 字面 — shared 先 / owner 后
    assert ids == ["shared-route-1", "owner-route-1"], (
        f"ordering broken (expected shared-first): {ids}"
    )
    # AppOut projection — every entry must have the 5 shape fields
    for entry in body:
        assert "id" in entry and "name" in entry
        assert entry["type"] == "kb_qa"
        assert "mode" in entry


async def test_route_dedupe_at_endpoint(apps_route_client, jwt_token):
    """Same app_id in shared and owner → endpoint returns single entry."""
    client, db = apps_route_client
    await _seed_owner(db, "shared-via-owner", "personal", "chat")
    payload = {
        "data": [
            {"id": "shared-via-owner", "name": "[KB]shared-name",
             "mode": "chat", "description": "shared"},
        ],
    }
    with respx.mock(assert_all_called=True) as rx:
        rx.get(url__regex=r".*/console/api/apps.*").mock(
            return_value=Response(200, json=payload)
        )
        resp = await client.get(
            "/api/v1/ncmu/apps",
            headers={"Authorization": f"Bearer {jwt_token}"},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body) == 1, f"dedupe failed; got {body}"
    assert body[0]["id"] == "shared-via-owner"
    # shared-first wins on dedupe (Dify Console is source of truth)
    assert body[0]["name"] == "[KB]shared-name"


async def test_route_owner_only_path_returns_personal_apps(apps_route_client, jwt_token):
    """shared empty, owner has private apps → 200 + owner apps surface."""
    client, db = apps_route_client
    await _seed_owner(db, "personal-only-app", "我的私有 KB", "advanced-chat")
    with respx.mock(assert_all_called=True) as rx:
        rx.get(url__regex=r".*/console/api/apps.*").mock(
            return_value=Response(200, json={"data": []})
        )
        resp = await client.get(
            "/api/v1/ncmu/apps",
            headers={"Authorization": f"Bearer {jwt_token}"},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body) == 1
    assert body[0]["id"] == "personal-only-app"
    assert body[0]["mode"] == "advanced-chat"


async def test_route_no_jwt_still_returns_401(apps_route_client):
    """Sanity: paradigm shift 不破坏既有 auth gating."""
    client, _ = apps_route_client
    resp = await client.get("/api/v1/ncmu/apps")
    assert resp.status_code == 401
