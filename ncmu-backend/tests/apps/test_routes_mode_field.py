"""TASK-69 AC#4 — /api/v1/ncmu/apps response includes ``mode`` field.

The ``apps/routes.py`` projection already passes ``mode`` from the Dify
Console payload through to ``AppOut`` (TASK-69). These tests pin both
the surfaced value (when Dify returns mode='advanced-chat') and the
defaulted value (when Dify omits the key — falls through to ``"chat"``).
"""
from __future__ import annotations

import httpx
import pytest_asyncio
import respx
from httpx import Response


@pytest_asyncio.fixture
async def apps_client_no_cache(app_client):
    """Per-test fresh ``DifyConsoleClient`` so the 67a ``lru_cache`` for
    ``get_dify_console_client`` does not leak across tests; mirrors the
    pattern in tests/admin/test_sync_apps_mode.py."""
    from ncmu_backend.apps.dify_console_client import DifyConsoleClient
    from ncmu_backend.apps.routes import get_dify_console_client
    from ncmu_backend.main import app, get_dify_client

    dcc = DifyConsoleClient(ttl_seconds=300)
    test_dify_http = httpx.AsyncClient(timeout=httpx.Timeout(10.0))

    app.dependency_overrides[get_dify_console_client] = lambda: dcc
    app.dependency_overrides[get_dify_client] = lambda: test_dify_http
    try:
        yield app_client
    finally:
        app.dependency_overrides.pop(get_dify_console_client, None)
        app.dependency_overrides.pop(get_dify_client, None)
        await test_dify_http.aclose()


# --------------------------------------------------------------------- #
# (a) Dify returns mode='advanced-chat' → AppOut.mode='advanced-chat'
# --------------------------------------------------------------------- #
async def test_apps_response_carries_mode_field(apps_client_no_cache, jwt_token):
    payload = {
        "data": [
            {
                "id": "app-kb-adv",
                "name": "[KB]员工手册",
                "mode": "advanced-chat",
                "description": "HR onboarding KB",
            }
        ],
    }
    with respx.mock(assert_all_called=True) as rx:
        rx.get(url__regex=r".*/console/api/apps.*").mock(
            return_value=Response(200, json=payload)
        )
        resp = await apps_client_no_cache.get(
            "/api/v1/ncmu/apps",
            headers={"Authorization": f"Bearer {jwt_token}"},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, list) and len(body) == 1
    assert "mode" in body[0]
    assert body[0]["mode"] == "advanced-chat"


# --------------------------------------------------------------------- #
# (b) Dify omits mode → AppOut defaults to 'chat'
# --------------------------------------------------------------------- #
async def test_apps_response_defaults_mode_to_chat(apps_client_no_cache, jwt_token):
    payload = {
        "data": [
            {
                "id": "app-kb-no-mode",
                "name": "[KB]财务知识库",
                "description": "Finance Q&A",
            }
        ],
    }
    with respx.mock(assert_all_called=True) as rx:
        rx.get(url__regex=r".*/console/api/apps.*").mock(
            return_value=Response(200, json=payload)
        )
        resp = await apps_client_no_cache.get(
            "/api/v1/ncmu/apps",
            headers={"Authorization": f"Bearer {jwt_token}"},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body) == 1
    assert body[0]["mode"] == "chat"
