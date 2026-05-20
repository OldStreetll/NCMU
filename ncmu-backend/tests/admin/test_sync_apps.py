"""TASK-67a AC#8 — POST /api/v1/ncmu/admin/sync_apps unit tests (≥5 cases).

Cases (per AC#8 a-e):
  (a) mock list_apps_for_user returns 3 → upserted=3, total_console=3
  (b) sync 2 times with different name → 2nd row UPDATE 实测 SQL
  (c) mock returns 1 dict missing `id` → upserted=0 (跳过无 id 行)
  (d) non-admin user → 403 + code 1201
  (e) ★M-NEW-5 cache invalidate 实测 — pre-populate cache via GET /apps,
      then POST sync_apps; verify cache.invalidate(user.sub) called once
      AND upstream真 fetch happened (route.call_count == 2, not cache HIT).

Mock strategy: real DifyConsoleClient + respx for upstream Dify Console.
Override `get_dify_console_client` (per-test fresh dcc to avoid global
lru_cache leakage) and `get_dify_client` (test-scoped real httpx).
"""
from __future__ import annotations

import httpx
import pytest
import pytest_asyncio
import respx
from httpx import Response
from sqlalchemy import text


ZHANGSAN = "a0000001-0000-4000-8000-000000000001"  # admin per dev_users.sql + NCMU_ADMIN_USER_IDS default
LISI = "a0000001-0000-4000-8000-000000000002"      # non-admin


@pytest_asyncio.fixture
async def sync_apps_client(app_client):
    """Wire `app_client` so `get_dify_console_client` returns a fresh
    DifyConsoleClient (so the per-process lru_cache from
    `apps.routes.get_dify_console_client` doesn't leak state across
    tests) and `get_dify_client` returns a test-scoped real httpx
    client (respx will intercept its calls).

    Yields `(client, dcc)` so the M-NEW-5 test can spy on
    `dcc.invalidate`.
    """
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
# (a) mock returns 3 → upserted=3, total_console=3
# --------------------------------------------------------------------- #
async def test_sync_apps_upserts_three_rows(sync_apps_client, jwt_token):
    from ncmu_backend.config import get_settings

    client, _dcc = sync_apps_client
    payload = {
        "data": [
            {"id": "app-001", "name": "Foo"},
            {"id": "app-002", "name": "Bar"},
            {"id": "app-003", "name": "Baz"},
        ],
    }
    with respx.mock(assert_all_called=True) as rx:
        route = rx.get(url__regex=r".*/console/api/apps.*").mock(
            return_value=Response(200, json=payload)
        )
        resp = await client.post(
            "/api/v1/ncmu/admin/sync_apps",
            headers={"Authorization": f"Bearer {jwt_token}"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"upserted": 3, "total_console": 3}
    # B-NEW-47: lock outbound Dify Console headers verbatim. Pins both
    # the header NAMES ("Authorization" / "X-WORKSPACE-ID") and the
    # VALUES (Bearer scheme + ``DIFY_CONSOLE_API_KEY`` / ``DIFY_TENANT_ID``
    # from Settings). Guards against header-name typos and
    # value-source mix-ups (same class as the dev-login
    # ``username``→``user_id`` cross-stack field error).
    settings = get_settings()
    sent = route.calls[0].request
    assert sent.headers.get("Authorization") == f"Bearer {settings.DIFY_CONSOLE_API_KEY}"
    assert sent.headers.get("X-WORKSPACE-ID") == settings.DIFY_TENANT_ID


# --------------------------------------------------------------------- #
# (b) sync twice with different name → 2nd sync UPDATEs row name
# --------------------------------------------------------------------- #
async def test_sync_apps_upsert_updates_existing_row_name(
    sync_apps_client, async_db, jwt_token,
):
    client, _dcc = sync_apps_client
    v1 = {"data": [{"id": "app-x", "name": "Original Name"}]}
    v2 = {"data": [{"id": "app-x", "name": "Updated Name"}]}

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

    # SELECT name FROM dify_apps WHERE dify_app_id='app-x' must be the v2 value
    name = (
        await async_db.execute(
            text("SELECT name FROM dify_apps WHERE dify_app_id = :id"),
            {"id": "app-x"},
        )
    ).scalar_one()
    assert name == "Updated Name"


# --------------------------------------------------------------------- #
# (c) row without `id` field is skipped → upserted=0, total_console=1
# --------------------------------------------------------------------- #
async def test_sync_apps_skips_rows_without_id(sync_apps_client, jwt_token):
    client, _dcc = sync_apps_client
    payload = {"data": [{"name": "missing-id-here"}]}
    with respx.mock(assert_all_called=True) as rx:
        rx.get(url__regex=r".*/console/api/apps.*").mock(
            return_value=Response(200, json=payload)
        )
        resp = await client.post(
            "/api/v1/ncmu/admin/sync_apps",
            headers={"Authorization": f"Bearer {jwt_token}"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["upserted"] == 0
    assert body["total_console"] == 1


# --------------------------------------------------------------------- #
# (d) non-admin (李四) → 403 + code 1201
# --------------------------------------------------------------------- #
async def test_sync_apps_non_admin_returns_403(sync_apps_client, jwt_secret):
    from ncmu_backend.auth.jwt_utils import sign_jwt

    client, _dcc = sync_apps_client
    lisi_token, _ = sign_jwt(user_id=LISI, name="李四", secret=jwt_secret)

    resp = await client.post(
        "/api/v1/ncmu/admin/sync_apps",
        headers={"Authorization": f"Bearer {lisi_token}"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == 1201


# --------------------------------------------------------------------- #
# (e) ★M-NEW-5: invalidate cache before fetch
#     - GET /apps populates cache for user 张三
#     - POST /sync_apps must call cache.invalidate(user.sub) FIRST,
#       then real fetch upstream (NOT served from cached entry)
# --------------------------------------------------------------------- #
async def test_sync_apps_invalidates_cache_before_fetch(sync_apps_client, jwt_token):
    from ncmu_backend.config import get_settings

    client, dcc = sync_apps_client

    invalidate_calls: list = []
    real_invalidate = dcc.invalidate

    def _spy_invalidate(user_id=None):
        invalidate_calls.append(user_id)
        return real_invalidate(user_id)

    dcc.invalidate = _spy_invalidate

    # Use a [KB]-prefixed name so GET /apps actually returns it (filter
    # passes detect_app_type) — otherwise the cache pre-populate step is
    # still cached server-side, but the visible response would be empty.
    # Either way the cache HIT/MISS count is what matters.
    payload = {"data": [{"id": "app-cache-test", "name": "[KB]CacheTest"}]}
    with respx.mock(assert_all_called=True) as rx:
        route = rx.get(url__regex=r".*/console/api/apps.*").mock(
            return_value=Response(200, json=payload)
        )

        # 1. Pre-populate cache for 张三 (user.sub = ZHANGSAN)
        r0 = await client.get(
            "/api/v1/ncmu/apps",
            headers={"Authorization": f"Bearer {jwt_token}"},
        )
        assert r0.status_code == 200
        assert route.call_count == 1, "GET /apps should hit upstream once"

        # 2. POST /sync_apps — must invalidate then re-fetch upstream
        r1 = await client.post(
            "/api/v1/ncmu/admin/sync_apps",
            headers={"Authorization": f"Bearer {jwt_token}"},
        )

    assert r1.status_code == 200, r1.text
    # M-NEW-5 essence: invalidate called exactly once, with user.sub
    assert invalidate_calls == [ZHANGSAN], (
        f"expected invalidate([ZHANGSAN]); got {invalidate_calls}"
    )
    # And the upstream fetch happened a second time (NOT cache HIT)
    assert route.call_count == 2, (
        "sync_apps must bypass the per-user TTL cache; expected upstream "
        f"hit twice but got {route.call_count}"
    )
    # B-NEW-47: both outbound calls (GET /apps + POST /sync_apps' refetch)
    # MUST carry the same Bearer + X-WORKSPACE-ID — locks the consistent
    # contract across the two code paths that share ``DifyConsoleClient``.
    settings = get_settings()
    for i, call in enumerate(route.calls):
        sent = call.request
        assert sent.headers.get("Authorization") == f"Bearer {settings.DIFY_CONSOLE_API_KEY}", (
            f"call #{i} missing Bearer header"
        )
        assert sent.headers.get("X-WORKSPACE-ID") == settings.DIFY_TENANT_ID, (
            f"call #{i} missing X-WORKSPACE-ID header"
        )
