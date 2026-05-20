"""TASK-26 — pytest covering AC#1-6 for GET /api/v1/ncmu/apps.

Mock strategy: `respx_mock` fixture patches httpx globally — every call
the lifespan-owned `dify_client` makes during the test is intercepted.
Cache TTL behaviour is exercised via a fake monotonic clock injected
through a dependency override on `get_dify_console_client`.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
import respx
from httpx import Response

from ncmu_backend.apps.dify_console_client import (
    DifyConsoleClient,
    detect_app_type,
    dify_status_to_ncmu,
)


# --------------------------------------------------------------------- #
# Mock payload — 5 Dify Apps; 2 are [KB], 3 are normal
# --------------------------------------------------------------------- #
DIFY_FIVE_APPS = {
    "data": [
        {
            "id": "app-kb-1",
            "name": "[KB]员工手册",
            "mode": "advanced-chat",
            "description": "HR onboarding KB",
            "tags": [],
        },
        {
            "id": "app-kb-2",
            "name": "财务知识库",
            "mode": "chat",
            "description": "Finance Q&A",
            "tags": [{"id": "t1", "name": "kb-qa", "type": "app"}],
        },
        {
            "id": "app-normal-1",
            "name": "营销助手",
            "mode": "agent-chat",
            "description": "marketing copy generator",
            "tags": [{"id": "t2", "name": "marketing", "type": "app"}],
        },
        {
            "id": "app-normal-2",
            "name": "代码 review bot",
            "mode": "advanced-chat",
            "description": "internal devtool",
            "tags": [],
        },
        {
            "id": "app-normal-3",
            "name": "客服分类器",
            "mode": "workflow",
            "description": "intent classifier",
            "tags": [{"id": "t3", "name": "support", "type": "app"}],
        },
    ],
    "has_more": False,
    "limit": 100,
    "page": 1,
    "total": 5,
}


# --------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------- #
@pytest_asyncio.fixture
async def app_client_with_clock(app_client):
    """Wrap `app_client` so `get_dify_console_client` returns a fresh
    `DifyConsoleClient` with a fakeable monotonic clock, AND so a real
    httpx.AsyncClient is wired in as the Dify upstream (the conftest's
    `app_client` doesn't run FastAPI lifespan, so `app.state.dify_client`
    would otherwise be missing).

    Yields `(client, advance)` — call `advance(seconds)` to fast-forward
    the cache TTL without an actual sleep.
    """
    import httpx as _httpx
    from ncmu_backend.apps.routes import get_dify_console_client
    from ncmu_backend.main import app, get_dify_client

    fake_now = [0.0]

    def clock() -> float:
        return fake_now[0]

    def advance(seconds: float) -> None:
        fake_now[0] += seconds

    dcc = DifyConsoleClient(ttl_seconds=300, clock=clock)
    test_dify_http = _httpx.AsyncClient(timeout=_httpx.Timeout(10.0))

    app.dependency_overrides[get_dify_console_client] = lambda: dcc
    app.dependency_overrides[get_dify_client] = lambda: test_dify_http

    try:
        yield app_client, advance
    finally:
        app.dependency_overrides.pop(get_dify_console_client, None)
        app.dependency_overrides.pop(get_dify_client, None)
        await test_dify_http.aclose()


# --------------------------------------------------------------------- #
# AC#3 — detect_app_type truth table (no app, no DB needed)
# --------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "name,tags,expected",
    [
        ("[KB]员工手册", [], True),
        ("员工手册", ["kb-qa"], True),
        ("[KB]员工手册", ["kb-qa"], True),
        ("员工手册", ["normal"], False),
        # Dify-shape tag dicts also accepted
        ("员工手册", [{"name": "kb-qa", "type": "app"}], True),
        ("员工手册", [{"name": "marketing"}], False),
        # Edge cases that must not detect as KB
        ("", [], False),
        ("员工手册", None, False),
    ],
)
def test_detect_app_type_truth_table(name, tags, expected):
    assert detect_app_type(name, tags) is expected


def test_dify_status_mapping():
    """1003 = upstream 4xx; 9001 = upstream 5xx."""
    assert dify_status_to_ncmu(401) == 1003
    assert dify_status_to_ncmu(404) == 1003
    assert dify_status_to_ncmu(500) == 9001
    assert dify_status_to_ncmu(502) == 9001


# --------------------------------------------------------------------- #
# AC#1 — no JWT → 401
# --------------------------------------------------------------------- #
async def test_list_apps_without_jwt_returns_401(app_client):
    resp = await app_client.get("/api/v1/ncmu/apps")
    assert resp.status_code == 401
    body = resp.json()
    assert body["detail"]["code"] == 1102  # missing Authorization header


async def test_list_apps_with_garbage_jwt_returns_401(app_client):
    resp = await app_client.get(
        "/api/v1/ncmu/apps",
        headers={"Authorization": "Bearer not-a-real-jwt"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == 1105


# --------------------------------------------------------------------- #
# AC#2 — mock Dify returns 5 apps, endpoint returns the 2 [KB] ones
# --------------------------------------------------------------------- #
async def test_list_apps_filters_to_kb_only(app_client_with_clock, jwt_token):
    client, _advance = app_client_with_clock
    with respx.mock(assert_all_called=True) as rx:
        rx.get(url__regex=r".*/console/api/apps.*").mock(
            return_value=Response(200, json=DIFY_FIVE_APPS)
        )
        resp = await client.get(
            "/api/v1/ncmu/apps",
            headers={"Authorization": f"Bearer {jwt_token}"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    ids = {a["id"] for a in body}
    assert ids == {"app-kb-1", "app-kb-2"}  # only the [KB] ones
    # AppOut shape sanity — type pinned to "kb_qa"
    assert all(a["type"] == "kb_qa" for a in body)
    # name + description preserved through the projection
    by_id = {a["id"]: a for a in body}
    assert by_id["app-kb-1"]["name"] == "[KB]员工手册"
    assert by_id["app-kb-2"]["description"] == "Finance Q&A"


# --------------------------------------------------------------------- #
# AC#4 — second call within TTL is a cache HIT (no upstream re-hit)
# --------------------------------------------------------------------- #
async def test_cache_hit_within_ttl(app_client_with_clock, jwt_token):
    client, advance = app_client_with_clock
    with respx.mock(assert_all_called=True) as rx:
        route = rx.get(url__regex=r".*/console/api/apps.*").mock(
            return_value=Response(200, json=DIFY_FIVE_APPS)
        )

        r1 = await client.get(
            "/api/v1/ncmu/apps",
            headers={"Authorization": f"Bearer {jwt_token}"},
        )
        assert r1.status_code == 200
        assert route.call_count == 1

        # 5 seconds later — well within the 300 s TTL → cache HIT
        advance(5.0)
        r2 = await client.get(
            "/api/v1/ncmu/apps",
            headers={"Authorization": f"Bearer {jwt_token}"},
        )
        assert r2.status_code == 200
        assert r2.json() == r1.json()
        assert route.call_count == 1, (
            "Expected cache HIT; upstream was hit a second time"
        )


# --------------------------------------------------------------------- #
# AC#5 — second call past TTL re-hits upstream (cache expired)
# --------------------------------------------------------------------- #
async def test_cache_miss_after_ttl_expiry(app_client_with_clock, jwt_token):
    client, advance = app_client_with_clock
    with respx.mock(assert_all_called=True) as rx:
        route = rx.get(url__regex=r".*/console/api/apps.*").mock(
            return_value=Response(200, json=DIFY_FIVE_APPS)
        )

        r1 = await client.get(
            "/api/v1/ncmu/apps",
            headers={"Authorization": f"Bearer {jwt_token}"},
        )
        assert r1.status_code == 200
        assert route.call_count == 1

        # 6 minutes later — past 300 s TTL → cache MISS, upstream hit again
        advance(360.0)
        r2 = await client.get(
            "/api/v1/ncmu/apps",
            headers={"Authorization": f"Bearer {jwt_token}"},
        )
        assert r2.status_code == 200
        assert route.call_count == 2, (
            "Expected cache MISS after TTL; upstream should have been re-hit"
        )


# --------------------------------------------------------------------- #
# Error code mapping — Dify upstream 5xx → ncmu 9001 / 4xx → 1003
# --------------------------------------------------------------------- #
async def test_dify_upstream_5xx_maps_to_9001(app_client_with_clock, jwt_token):
    client, _advance = app_client_with_clock
    with respx.mock(assert_all_called=True) as rx:
        rx.get(url__regex=r".*/console/api/apps.*").mock(
            return_value=Response(503, text="upstream down")
        )
        resp = await client.get(
            "/api/v1/ncmu/apps",
            headers={"Authorization": f"Bearer {jwt_token}"},
        )
    assert resp.status_code == 502
    assert resp.json()["detail"]["code"] == 9001


async def test_dify_upstream_4xx_maps_to_1003(app_client_with_clock, jwt_token):
    client, _advance = app_client_with_clock
    with respx.mock(assert_all_called=True) as rx:
        rx.get(url__regex=r".*/console/api/apps.*").mock(
            return_value=Response(401, json={"message": "bad console key"})
        )
        resp = await client.get(
            "/api/v1/ncmu/apps",
            headers={"Authorization": f"Bearer {jwt_token}"},
        )
    assert resp.status_code == 502
    assert resp.json()["detail"]["code"] == 1003


# --------------------------------------------------------------------- #
# Cache key is per-user — two different users do not share the cache
# --------------------------------------------------------------------- #
async def test_cache_is_keyed_per_user(app_client_with_clock, jwt_secret):
    """Cache is `防越权` — flushing for one user must not leak to another."""
    from ncmu_backend.auth.jwt_utils import sign_jwt

    client, _advance = app_client_with_clock
    token_user_a, _ = sign_jwt(
        user_id="a0000001-0000-4000-8000-000000000001", name="张三", secret=jwt_secret,
    )
    token_user_b, _ = sign_jwt(
        user_id="a0000001-0000-4000-8000-000000000002", name="李四", secret=jwt_secret,
    )

    with respx.mock(assert_all_called=True) as rx:
        route = rx.get(url__regex=r".*/console/api/apps.*").mock(
            return_value=Response(200, json=DIFY_FIVE_APPS)
        )

        r_a = await client.get(
            "/api/v1/ncmu/apps",
            headers={"Authorization": f"Bearer {token_user_a}"},
        )
        r_b = await client.get(
            "/api/v1/ncmu/apps",
            headers={"Authorization": f"Bearer {token_user_b}"},
        )
        assert r_a.status_code == r_b.status_code == 200
        assert route.call_count == 2, "Different users must not share cache slot"

        # And user A re-hit should still cache-hit
        r_a2 = await client.get(
            "/api/v1/ncmu/apps",
            headers={"Authorization": f"Bearer {token_user_a}"},
        )
        assert r_a2.status_code == 200
        assert route.call_count == 2  # still 2; user A's second call hits cache


# --------------------------------------------------------------------- #
# TASK-A B-NEW-39 — admin key header 字面对账（REWORK-DEPLOY-2: path B')
#
# Path B' (Dify ADMIN_API_KEY 旁路 + X-WORKSPACE-ID) reuses the existing
# `DIFY_CONSOLE_API_KEY` env var; only its *value* swaps from a 30-min
# manual JWT to a never-expiring magic key. Dify ``ext_login.py:56-72``
# additionally gates the bypass on a non-empty ``X-WORKSPACE-ID``
# header containing the owner tenant UUID — without it the request
# falls through to console JWT verify and the admin key is rejected as
# "Invalid token". The two outbound headers NCMU sends to
# `/console/api/apps` must therefore both match the env config; this
# test regression-locks both at the same time so a future refactor
# can't silently drop either half.
# --------------------------------------------------------------------- #
async def test_list_apps_calls_dify_console_with_admin_key(
    app_client_with_clock, jwt_token, monkeypatch
):
    """Outbound `GET /console/api/apps` carries Bearer <DIFY_CONSOLE_API_KEY>
    AND X-WORKSPACE-ID <DIFY_TENANT_ID>.

    The fake values are injected via ``monkeypatch.setenv`` and the
    settings cache is cleared so the next request rebuilds ``Settings``
    from the new env (mirrors the ``app_client`` fixture's own pattern).
    """
    from ncmu_backend.config import reset_settings_cache

    client, _advance = app_client_with_clock

    # 模拟生产 NCMU/.env: DIFY_CONSOLE_API_KEY = ${DIFY_ADMIN_API_KEY} 同值
    # + DIFY_TENANT_ID (path B' — INDEP-FIX-DEPLOY-2).
    fake_admin_key = "test-admin-api-key-256bit-entropy-value"
    fake_tenant_id = "fake-tenant-uuid-00000000-0000-4000-8000-000000000000"
    monkeypatch.setenv("DIFY_CONSOLE_API_KEY", fake_admin_key)
    monkeypatch.setenv("DIFY_TENANT_ID", fake_tenant_id)
    # Settings is lru_cached; reset so the request-time Depends(get_settings)
    # rebuilds against the patched env (same trick `app_client` uses at
    # fixture setup time).
    reset_settings_cache()

    with respx.mock(assert_all_called=True) as rx:
        route = rx.get(url__regex=r".*/console/api/apps.*").mock(
            return_value=Response(
                200,
                json={
                    "data": [],
                    "page": 1,
                    "limit": 100,
                    "total": 0,
                    "has_more": False,
                },
            )
        )
        resp = await client.get(
            "/api/v1/ncmu/apps",
            headers={"Authorization": f"Bearer {jwt_token}"},
        )

    assert resp.status_code == 200
    assert route.called, "expected outbound call to Dify Console /apps"

    req = route.calls.last.request

    # 字面对账 #1: outbound Authorization header must equal "Bearer <fake>".
    auth_header = req.headers.get("authorization") or req.headers.get(
        "Authorization"
    )
    assert auth_header == f"Bearer {fake_admin_key}", (
        f"outbound Authorization 不匹配 admin key: got {auth_header!r}, "
        f"expected 'Bearer {fake_admin_key}'"
    )

    # 字面对账 #2: outbound X-WORKSPACE-ID must equal DIFY_TENANT_ID (path B').
    # httpx header names are case-insensitive on the wire; try both
    # spellings so we don't depend on httpx internal normalization choices.
    workspace_header = req.headers.get("x-workspace-id") or req.headers.get(
        "X-WORKSPACE-ID"
    )
    assert workspace_header == fake_tenant_id, (
        f"outbound X-WORKSPACE-ID 不匹配 tenant_id: got {workspace_header!r}, "
        f"expected {fake_tenant_id!r} — path B' (INDEP-FIX-DEPLOY-2) "
        f"requires this header for Dify ADMIN_API_KEY bypass to succeed"
    )
