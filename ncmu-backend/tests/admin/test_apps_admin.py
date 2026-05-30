"""TASK-PE-07 — admin App-management endpoint 字段级 dict-shape 断言.

Routes under test (all gated by ``Depends(require_admin)``, auto-checked
by ``test_require_admin_audit.py`` after PE-02):

    GET   /api/v1/ncmu/admin/apps              — list (include_inactive + search)
    GET   /api/v1/ncmu/admin/apps/{app_id}     — single-app detail
    PATCH /api/v1/ncmu/admin/apps/{app_id}     — toggle is_active only

Mock strategy: real FastAPI app via ASGITransport (``app_client`` from
``tests/conftest.py``); real PostgreSQL via pytest-postgresql ephemeral
DB (alembic 0010 adds ``is_active`` + ``last_synced_at`` so these tests
implicitly regression-lock the migration). No upstream Dify Console /
FastGPT mocks needed — the admin/apps read+toggle surface is DB-only
(POST /sync_apps, which DOES hit Dify Console, is covered by the
pre-existing test_sync_apps.py and is NOT re-implemented in this module).

PK is ``dify_app_id`` (String(64)), NOT a UUID ``id`` — Boss 2026-05-29
拍板 to match the real ``dify_apps`` schema (the plan §4 sketch assumed a
UUID id). So path params here are plain strings.

Error codes:
  1014  app not found by dify_app_id (PATCH / GET detail 404)
  1201  admin permission required (raised by ``require_admin``)
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest_asyncio
import respx
from httpx import Response
from sqlalchemy import text

from ncmu_backend.db.models import DifyApp


# UUIDs from alembic/seeds/dev_users.sql (同 NCMU_ADMIN_USER_IDS).
ZHANGSAN = "a0000001-0000-4000-8000-000000000001"  # admin (jwt_token fixture)
LISI = "a0000001-0000-4000-8000-000000000002"      # non-admin

EXPECTED_KEYS = {
    "dify_app_id",
    "name",
    "mode",
    "is_active",
    "last_synced_at",
    "tag_count",
}

# Fixed tz-aware sync time so the serialization assertion is deterministic.
SYNCED_AT = datetime(2026, 5, 29, 12, 0, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------- #
@pytest_asyncio.fixture
async def seed_apps(async_db):
    """3 cached apps: 2 active (one synced / one never-synced) + 1 inactive.

    Exercises name-ordering, include_inactive filtering, search ILIKE, and
    last_synced_at serialization (non-null + null).
    """
    apps = [
        DifyApp(
            dify_app_id="app-aaa",
            name="Alpha 客服",
            mode="chat",
            is_active=True,
            last_synced_at=SYNCED_AT,
        ),
        DifyApp(
            dify_app_id="app-bbb",
            name="Beta 工作流",
            mode="workflow",
            is_active=True,
            last_synced_at=None,  # never synced since 0010
        ),
        DifyApp(
            dify_app_id="app-ccc",
            name="Gamma 已停用",
            mode="agent-chat",
            is_active=False,
            last_synced_at=SYNCED_AT,
        ),
    ]
    async_db.add_all(apps)
    await async_db.commit()
    return apps


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------- #
# (1) GET /admin/apps — empty → 200 + []
# --------------------------------------------------------------------- #
async def test_list_apps_empty(app_client, jwt_token):
    resp = await app_client.get("/api/v1/ncmu/admin/apps", headers=_auth(jwt_token))
    assert resp.status_code == 200, resp.text
    assert resp.json() == []  # exact shape, not falsy


# --------------------------------------------------------------------- #
# (2) GET /admin/apps — default hides inactive + field-level shape + order
# --------------------------------------------------------------------- #
async def test_list_apps_default_active_only_shape(app_client, jwt_token, seed_apps):
    resp = await app_client.get("/api/v1/ncmu/admin/apps", headers=_auth(jwt_token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Gamma (inactive) excluded → 2 rows, name-ordered.
    assert [a["name"] for a in body] == ["Alpha 客服", "Beta 工作流"]
    for item in body:
        assert set(item.keys()) == EXPECTED_KEYS  # no icon/description leaked
    alpha = body[0]
    assert alpha["dify_app_id"] == "app-aaa"
    assert alpha["mode"] == "chat"
    assert alpha["is_active"] is True
    assert alpha["tag_count"] == 0  # PE-08 placeholder
    # last_synced_at serializes to an ISO string carrying the date.
    assert isinstance(alpha["last_synced_at"], str)
    assert alpha["last_synced_at"].startswith("2026-05-29T12:00:00")
    # Beta never synced → explicit null (not missing key).
    assert body[1]["last_synced_at"] is None


# --------------------------------------------------------------------- #
# (3) GET /admin/apps?include_inactive=true → shows the disabled row too
# --------------------------------------------------------------------- #
async def test_list_apps_include_inactive(app_client, jwt_token, seed_apps):
    resp = await app_client.get(
        "/api/v1/ncmu/admin/apps?include_inactive=true", headers=_auth(jwt_token)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [a["name"] for a in body] == ["Alpha 客服", "Beta 工作流", "Gamma 已停用"]
    gamma = body[2]
    assert gamma["is_active"] is False


# --------------------------------------------------------------------- #
# (4) GET /admin/apps?search=… → case-insensitive name ILIKE
# --------------------------------------------------------------------- #
async def test_list_apps_search_ilike(app_client, jwt_token, seed_apps):
    # lowercase term matches "Alpha 客服" (ILIKE is case-insensitive).
    resp = await app_client.get(
        "/api/v1/ncmu/admin/apps?search=alpha", headers=_auth(jwt_token)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [a["name"] for a in body] == ["Alpha 客服"]

    # CJK substring match on a different row.
    resp2 = await app_client.get(
        "/api/v1/ncmu/admin/apps?search=工作", headers=_auth(jwt_token)
    )
    assert resp2.status_code == 200, resp2.text
    assert [a["name"] for a in resp2.json()] == ["Beta 工作流"]


# --------------------------------------------------------------------- #
# (5) GET /admin/apps — non-admin → 403 + code 1201
# --------------------------------------------------------------------- #
async def test_list_apps_non_admin_returns_403(app_client, jwt_secret):
    from ncmu_backend.auth.jwt_utils import sign_jwt

    lisi_token, _ = sign_jwt(user_id=LISI, name="李四", secret=jwt_secret)
    resp = await app_client.get(
        "/api/v1/ncmu/admin/apps", headers=_auth(lisi_token)
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["code"] == 1201


# --------------------------------------------------------------------- #
# (6) GET /admin/apps/{id} — detail happy path
# --------------------------------------------------------------------- #
async def test_get_app_detail(app_client, jwt_token, seed_apps):
    resp = await app_client.get(
        "/api/v1/ncmu/admin/apps/app-ccc", headers=_auth(jwt_token)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body.keys()) == EXPECTED_KEYS
    assert body["dify_app_id"] == "app-ccc"
    assert body["name"] == "Gamma 已停用"
    assert body["mode"] == "agent-chat"
    assert body["is_active"] is False
    assert body["tag_count"] == 0


# --------------------------------------------------------------------- #
# (7) GET /admin/apps/{id} — not found → 404 + 1014
# --------------------------------------------------------------------- #
async def test_get_app_detail_not_found(app_client, jwt_token, seed_apps):
    resp = await app_client.get(
        "/api/v1/ncmu/admin/apps/does-not-exist", headers=_auth(jwt_token)
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"]["code"] == 1014


# --------------------------------------------------------------------- #
# (8) GET /admin/apps/{id} — non-admin → 403
# --------------------------------------------------------------------- #
async def test_get_app_detail_non_admin_returns_403(app_client, jwt_secret, seed_apps):
    from ncmu_backend.auth.jwt_utils import sign_jwt

    lisi_token, _ = sign_jwt(user_id=LISI, name="李四", secret=jwt_secret)
    resp = await app_client.get(
        "/api/v1/ncmu/admin/apps/app-aaa", headers=_auth(lisi_token)
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["code"] == 1201


# --------------------------------------------------------------------- #
# (9) PATCH /admin/apps/{id} — deactivate → 200 + is_active false echoed
# --------------------------------------------------------------------- #
async def test_patch_app_deactivate(app_client, jwt_token, seed_apps):
    resp = await app_client.patch(
        "/api/v1/ncmu/admin/apps/app-aaa",
        headers=_auth(jwt_token),
        json={"is_active": False},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body.keys()) == EXPECTED_KEYS
    assert body["dify_app_id"] == "app-aaa"
    assert body["is_active"] is False

    # Persisted: now hidden from the default (active-only) list.
    listed = await app_client.get("/api/v1/ncmu/admin/apps", headers=_auth(jwt_token))
    assert [a["name"] for a in listed.json()] == ["Beta 工作流"]


# --------------------------------------------------------------------- #
# (10) PATCH /admin/apps/{id} — reactivate a disabled app → 200 + true
# --------------------------------------------------------------------- #
async def test_patch_app_reactivate(app_client, jwt_token, seed_apps):
    resp = await app_client.patch(
        "/api/v1/ncmu/admin/apps/app-ccc",
        headers=_auth(jwt_token),
        json={"is_active": True},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_active"] is True


# --------------------------------------------------------------------- #
# (11) PATCH /admin/apps/{id} — empty body is a no-op echo (NOT 422)
# --------------------------------------------------------------------- #
async def test_patch_app_empty_body_is_noop(app_client, jwt_token, seed_apps):
    resp = await app_client.patch(
        "/api/v1/ncmu/admin/apps/app-aaa",
        headers=_auth(jwt_token),
        json={},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Unchanged row echoed (Alpha stays active).
    assert body["dify_app_id"] == "app-aaa"
    assert body["is_active"] is True


# --------------------------------------------------------------------- #
# (12) PATCH /admin/apps/{id} — not found → 404 + 1014
# --------------------------------------------------------------------- #
async def test_patch_app_not_found(app_client, jwt_token, seed_apps):
    resp = await app_client.patch(
        "/api/v1/ncmu/admin/apps/nope",
        headers=_auth(jwt_token),
        json={"is_active": False},
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"]["code"] == 1014


# --------------------------------------------------------------------- #
# (13) PATCH /admin/apps/{id} — non-admin → 403 + 1201
# --------------------------------------------------------------------- #
async def test_patch_app_non_admin_returns_403(app_client, jwt_secret, seed_apps):
    from ncmu_backend.auth.jwt_utils import sign_jwt

    lisi_token, _ = sign_jwt(user_id=LISI, name="李四", secret=jwt_secret)
    resp = await app_client.patch(
        "/api/v1/ncmu/admin/apps/app-aaa",
        headers=_auth(lisi_token),
        json={"is_active": False},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["code"] == 1201


# ===================================================================== #
# REWORK-PE-07-INDEP — last_synced_at 盖戳覆盖.
#
# /indep-panel 实质 Minor (silent-drop 防护): the SCOPE-CHANGE's two
# ``last_synced_at=func.now()`` lines in admin/routes.py:sync_apps —
#   (a) ``.values()``               INSERT branch (brand-new app)
#   (b) ``on_conflict_do_update``   set_ branch  (re-sync existing app)
# — are the *core purpose* of this task + alembic 0010, yet had 0 tests:
# the existing last_synced_at assertions only ORM-seed a row then read
# GET serialization, never calling sync_apps. Deleting either func.now()
# line left all tests green. These two tests close that gap by actually
# driving POST /sync_apps and asserting the column is stamped in the DB,
# one test per branch (verified via the AC#3 reverse-validation: removing
# each line makes the matching test fail).
#
# Mock strategy mirrors test_sync_apps.py: real DifyConsoleClient + respx
# for the upstream Dify Console GET /console/api/apps.
# ===================================================================== #
@pytest_asyncio.fixture
async def sync_apps_client(app_client):
    """``app_client`` wired so ``get_dify_console_client`` yields a fresh
    DifyConsoleClient (no per-process lru_cache leak across tests) and
    ``get_dify_client`` yields a test-scoped real httpx client (respx
    intercepts its calls). Mirror of test_sync_apps.py's fixture."""
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


async def _synced_at(async_db, app_id: str):
    """Read dify_apps.last_synced_at for one row (None if NULL / absent)."""
    return (
        await async_db.execute(
            text("SELECT last_synced_at FROM dify_apps WHERE dify_app_id = :id"),
            {"id": app_id},
        )
    ).scalar_one_or_none()


# --------------------------------------------------------------------- #
# (14) sync_apps INSERT branch — brand-new app gets last_synced_at on
#      first sync (covers admin/routes.py .values() func.now()).
# --------------------------------------------------------------------- #
async def test_sync_apps_stamps_last_synced_at_on_insert(
    sync_apps_client, async_db, jwt_token
):
    client = sync_apps_client
    # Brand-new id not present in dify_apps → sync takes the INSERT path.
    payload = {"data": [{"id": "app-stamp-insert", "name": "Stamp Insert"}]}
    with respx.mock(assert_all_called=True) as rx:
        rx.get(url__regex=r".*/console/api/apps.*").mock(
            return_value=Response(200, json=payload)
        )
        resp = await client.post(
            "/api/v1/ncmu/admin/sync_apps", headers=_auth(jwt_token)
        )
    assert resp.status_code == 200, resp.text
    stamped = await _synced_at(async_db, "app-stamp-insert")
    # The whole point of the SCOPE-CHANGE: first sync stamps the column.
    assert stamped is not None, (
        "INSERT branch must stamp last_synced_at (admin/routes.py .values() "
        "func.now()); got NULL → the func.now() line is missing"
    )
    assert isinstance(stamped, datetime)


# --------------------------------------------------------------------- #
# (15) sync_apps UPDATE branch — re-syncing an existing row whose
#      last_synced_at is NULL re-stamps it (covers the on_conflict set_
#      func.now(); a seeded NULL stays NULL if the set_ line is removed).
# --------------------------------------------------------------------- #
async def test_sync_apps_restamps_last_synced_at_on_conflict_update(
    sync_apps_client, async_db, jwt_token
):
    client = sync_apps_client
    # Pre-seed an existing cache row with NULL last_synced_at so the
    # re-sync exercises the ON CONFLICT DO UPDATE branch. A NULL→NULL
    # outcome (set_ line deleted) is distinguishable from NULL→stamped.
    async_db.add(
        DifyApp(
            dify_app_id="app-stamp-update",
            name="Stamp Update",
            mode="chat",
            is_active=True,
            last_synced_at=None,
        )
    )
    await async_db.commit()
    assert await _synced_at(async_db, "app-stamp-update") is None  # precondition

    payload = {"data": [{"id": "app-stamp-update", "name": "Stamp Update v2"}]}
    with respx.mock(assert_all_called=True) as rx:
        rx.get(url__regex=r".*/console/api/apps.*").mock(
            return_value=Response(200, json=payload)
        )
        resp = await client.post(
            "/api/v1/ncmu/admin/sync_apps", headers=_auth(jwt_token)
        )
    assert resp.status_code == 200, resp.text
    stamped = await _synced_at(async_db, "app-stamp-update")
    assert stamped is not None, (
        "ON CONFLICT DO UPDATE branch must re-stamp last_synced_at "
        "(admin/routes.py set_ func.now()); seeded NULL stayed NULL → the "
        "set_ func.now() line is missing"
    )
    assert isinstance(stamped, datetime)
