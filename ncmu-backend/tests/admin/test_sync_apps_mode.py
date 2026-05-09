"""TASK-67b AC#13 — POST /api/v1/ncmu/admin/sync_apps writes `mode` field (≥3).

Cases:
  (a) mock list_apps_for_user returns 1 row with `mode='advanced-chat'` →
      after sync, dify_apps.mode='advanced-chat' for that row
  (b) mock returns 1 row WITHOUT a `mode` field → server-side default
      'chat' surfaces (the endpoint passes `row.get("mode", "chat")`,
      so the column is set to 'chat' on INSERT)
  (c) UPDATE path: first sync writes mode='chat' → second sync mocks
      mode='workflow' → after the second sync, dify_apps.mode='workflow'

Mock strategy mirrors test_sync_apps.py — real DifyConsoleClient + respx
+ a fresh client override so 67a's lru_cache doesn't leak.
"""
from __future__ import annotations

import httpx
import pytest_asyncio
import respx
from httpx import Response
from sqlalchemy import text


@pytest_asyncio.fixture
async def sync_apps_client(app_client):
    """Mirrors the fixture in test_sync_apps.py; per-test fresh dcc +
    real httpx for the upstream call."""
    from ncmu_backend.apps.dify_console_client import DifyConsoleClient
    from ncmu_backend.apps.routes import get_dify_console_client
    from ncmu_backend.main import app, get_dify_client

    dcc = DifyConsoleClient(ttl_seconds=300)
    test_dify_http = httpx.AsyncClient(timeout=httpx.Timeout(10.0))

    app.dependency_overrides[get_dify_console_client] = lambda: dcc
    app.dependency_overrides[get_dify_client] = lambda: test_dify_http
    try:
        yield app_client, dcc
    finally:
        app.dependency_overrides.pop(get_dify_console_client, None)
        app.dependency_overrides.pop(get_dify_client, None)
        await test_dify_http.aclose()


# --------------------------------------------------------------------- #
# (a) mock returns mode='advanced-chat' → row.mode is 'advanced-chat'
# --------------------------------------------------------------------- #
async def test_sync_apps_writes_mode_from_console(
    sync_apps_client, async_db, jwt_token,
):
    client, _dcc = sync_apps_client
    payload = {"data": [{"id": "app-adv", "name": "Adv", "mode": "advanced-chat"}]}
    with respx.mock(assert_all_called=True) as rx:
        rx.get(url__regex=r".*/console/api/apps.*").mock(
            return_value=Response(200, json=payload)
        )
        resp = await client.post(
            "/api/v1/ncmu/admin/sync_apps",
            headers={"Authorization": f"Bearer {jwt_token}"},
        )

    assert resp.status_code == 200, resp.text
    mode = (
        await async_db.execute(
            text("SELECT mode FROM dify_apps WHERE dify_app_id = :id"),
            {"id": "app-adv"},
        )
    ).scalar_one()
    assert mode == "advanced-chat"


# --------------------------------------------------------------------- #
# (b) mock missing `mode` → endpoint defaults to 'chat'
# --------------------------------------------------------------------- #
async def test_sync_apps_defaults_mode_to_chat(
    sync_apps_client, async_db, jwt_token,
):
    client, _dcc = sync_apps_client
    payload = {"data": [{"id": "app-no-mode", "name": "No Mode"}]}
    with respx.mock(assert_all_called=True) as rx:
        rx.get(url__regex=r".*/console/api/apps.*").mock(
            return_value=Response(200, json=payload)
        )
        resp = await client.post(
            "/api/v1/ncmu/admin/sync_apps",
            headers={"Authorization": f"Bearer {jwt_token}"},
        )

    assert resp.status_code == 200, resp.text
    mode = (
        await async_db.execute(
            text("SELECT mode FROM dify_apps WHERE dify_app_id = :id"),
            {"id": "app-no-mode"},
        )
    ).scalar_one()
    assert mode == "chat"


# --------------------------------------------------------------------- #
# (c) UPDATE path — second sync overwrites mode
# --------------------------------------------------------------------- #
async def test_sync_apps_updates_mode_on_conflict(
    sync_apps_client, async_db, jwt_token,
):
    client, _dcc = sync_apps_client
    v1 = {"data": [{"id": "app-evolve", "name": "Evolve", "mode": "chat"}]}
    v2 = {"data": [{"id": "app-evolve", "name": "Evolve", "mode": "workflow"}]}

    with respx.mock(assert_all_called=True) as rx:
        rx.get(url__regex=r".*/console/api/apps.*").mock(
            side_effect=[
                Response(200, json=v1),
                Response(200, json=v2),
            ]
        )
        r1 = await client.post(
            "/api/v1/ncmu/admin/sync_apps",
            headers={"Authorization": f"Bearer {jwt_token}"},
        )
        r2 = await client.post(
            "/api/v1/ncmu/admin/sync_apps",
            headers={"Authorization": f"Bearer {jwt_token}"},
        )

    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text
    mode = (
        await async_db.execute(
            text("SELECT mode FROM dify_apps WHERE dify_app_id = :id"),
            {"id": "app-evolve"},
        )
    ).scalar_one()
    assert mode == "workflow"
