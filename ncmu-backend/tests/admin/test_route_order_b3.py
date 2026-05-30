"""TASK-MERGE-B3 — cross-router route-order integration test (PE-07 + PE-10).

PE-10's ``admin.dsl`` router exposes the STATIC path
``GET /api/v1/ncmu/admin/apps/dsl-candidates``; PE-07's ``admin.apps`` router
exposes the PARAMETRISED ``GET /api/v1/ncmu/admin/apps/{app_id}`` that matches
*any* single segment. FastAPI matches routes across routers in registration
order, so ``admin.dsl`` MUST be ``include_router``-ed BEFORE ``admin.apps``
(see main.py route-order note) — otherwise ``{app_id}`` swallows
``dsl-candidates`` (app_id="dsl-candidates" → PE-07's 404/1014) and the picker
endpoint silently breaks.

These assertions are the regression guard for that include order. If someone
reorders main.py's includes, ``test_dsl_candidates_not_shadowed`` flips from
200-list to 404/1014 and fails loudly.
"""
from __future__ import annotations

from ncmu_backend.db.models import DifyApp


ZHANGSAN = "a0000001-0000-4000-8000-000000000001"  # admin (NCMU_ADMIN_USER_IDS default)
LISI = "a0000001-0000-4000-8000-000000000002"      # non-admin


def _admin(jwt_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {jwt_token}"}


def _non_admin(jwt_secret: str) -> dict[str, str]:
    from ncmu_backend.auth.jwt_utils import sign_jwt

    token, _exp = sign_jwt(user_id=LISI, name="李四", secret=jwt_secret)
    return {"Authorization": f"Bearer {token}"}


# ===================================================================== #
# dsl-candidates (static) must NOT be shadowed by apps/{app_id} (param)
# ===================================================================== #
async def test_dsl_candidates_not_shadowed_by_apps_param(app_client, jwt_token):
    """The literal route wins → 200 + list, NOT PE-07's 404/1014.

    A failure here (404 + code 1014 "app 'dsl-candidates' not found") is the
    exact symptom of admin.apps being registered before admin.dsl."""
    resp = await app_client.get(
        "/api/v1/ncmu/admin/apps/dsl-candidates", headers=_admin(jwt_token)
    )
    assert resp.status_code == 200, resp.text
    assert isinstance(resp.json(), list)


async def test_dsl_export_route_resolves(app_client, jwt_token):
    """POST /admin/apps/dsl-export hits the PE-10 dsl handler — proven by the
    422 from its ``app_ids`` min_length schema (apps router has no POST on
    this path → would be 405/404).

    The dsl-export route depends on ``get_dify_client``, whose real impl
    raises in the test harness (lifespan startup doesn't run under
    ASGITransport). Override it with a dummy so dependency-solving doesn't
    mask the result — the empty ``app_ids`` still 422s at validation *before*
    the handler body, so no outbound Dify call happens (mirrors the
    ``dsl_client`` override in test_dsl_export.py)."""
    import httpx as _httpx

    from ncmu_backend.main import app, get_dify_client

    dummy = _httpx.AsyncClient()
    app.dependency_overrides[get_dify_client] = lambda: dummy
    try:
        resp = await app_client.post(
            "/api/v1/ncmu/admin/apps/dsl-export",
            json={"app_ids": []},
            headers=_admin(jwt_token),
        )
    finally:
        app.dependency_overrides.pop(get_dify_client, None)
        await dummy.aclose()
    assert resp.status_code == 422, resp.text


# ===================================================================== #
# apps/{app_id} (param) still resolves — dsl single route is 2-segment
# (``/apps/{app_id}/dsl``) so it does not steal the 1-segment apps detail
# ===================================================================== #
async def test_apps_detail_still_resolves(app_client, jwt_token, async_db):
    async_db.add(
        DifyApp(dify_app_id="route-order-app", name="路由序测试", mode="chat")
    )
    await async_db.commit()
    resp = await app_client.get(
        "/api/v1/ncmu/admin/apps/route-order-app", headers=_admin(jwt_token)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dify_app_id"] == "route-order-app"
    assert body["name"] == "路由序测试"
    assert "is_active" in body  # PE-07 AdminAppOut field present


async def test_apps_detail_unknown_id_is_pe07_not_found(app_client, jwt_token):
    """Unknown 1-segment id → PE-07's 404/1014 (apps handler), confirming the
    apps detail route — not dsl — owns ``/apps/{app_id}``."""
    resp = await app_client.get(
        "/api/v1/ncmu/admin/apps/no-such-app", headers=_admin(jwt_token)
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"]["code"] == 1014


# ===================================================================== #
# require_admin gate holds across both routers
# ===================================================================== #
async def test_dsl_candidates_requires_admin(app_client, jwt_secret):
    resp = await app_client.get(
        "/api/v1/ncmu/admin/apps/dsl-candidates",
        headers=_non_admin(jwt_secret),
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == 1201


async def test_apps_detail_requires_admin(app_client, jwt_secret):
    resp = await app_client.get(
        "/api/v1/ncmu/admin/apps/route-order-app",
        headers=_non_admin(jwt_secret),
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == 1201
