"""TASK-PE-08 — tag ↔ app binding (replace-all) endpoint tests.

Routes under test (all gated by ``Depends(require_admin)``, auto-checked
by ``test_require_admin_audit.py``):

    GET  /api/v1/ncmu/admin/tags/{tag_id}/apps   — bound app ids
    PUT  /api/v1/ncmu/admin/tags/{tag_id}/apps   — replace-all (app_ids)
    GET  /api/v1/ncmu/admin/apps/{app_id}/tags   — bound tag ids
    PUT  /api/v1/ncmu/admin/apps/{app_id}/tags   — replace-all (tag_ids)

Plus: AdminAppOut.tag_count now reflects the real ``app_tags`` COUNT
(replaces PE-07's literal 0 placeholder).

AC#4 (Boss 2026-05-29 拍板 Option A — 等效证据非降阶): the "binding
makes the app reachable to the employee" contract is verified at the
**data layer**, NOT through GET /apps. Rationale (evidence-first):
``apps/services.py:list_apps_for_user`` does owner ∪ shared routing only
— it does NOT consult tags; ``tags_routing_enabled`` is config-default
False with the comment "永久 false 直到钉钉接入" and is consumed nowhere
in the repo (the shared-App tag route is a future DingTalk-phase concern,
and ``apps/`` is outside PE-08's file range). So the contract test seeds
``user_tags`` (PE-09's table, seeded directly here) + ``app_tags`` (via
this task's PUT endpoint) and asserts the JOIN a future tag-router WILL
consume correctly identifies the app as shared-tag-reachable. When
DingTalk enables the flag + wires the route, the data is already correct.

Error codes: 1012/1013 (tags) · 1014 (apps) · 1015 (binding target not
found, PE-08) · 1201 (require_admin).
"""
from __future__ import annotations

import uuid

import pytest_asyncio
from sqlalchemy import distinct, func, select

from ncmu_backend.db.models import AppTag, DifyApp, Tag, UserTag


ZHANGSAN = "a0000001-0000-4000-8000-000000000001"  # admin (jwt_token)
LISI = "a0000001-0000-4000-8000-000000000002"      # non-admin / dev seed user


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------- #
@pytest_asyncio.fixture
async def seed_apps_5(async_db):
    """5 cached apps (PK = dify_app_id String64)."""
    apps = [
        DifyApp(dify_app_id=f"pe08-app-{i}", name=f"App {i}", mode="chat")
        for i in range(1, 6)
    ]
    async_db.add_all(apps)
    await async_db.commit()
    return [a.dify_app_id for a in apps]


@pytest_asyncio.fixture
async def seed_tags_3(async_db):
    """3 tags."""
    tags = [Tag(name=f"标签{i}") for i in range(1, 4)]
    async_db.add_all(tags)
    await async_db.commit()
    for t in tags:
        await async_db.refresh(t)
    return [t.id for t in tags]


async def _app_tag_count(async_db, tag_id) -> int:
    return await async_db.scalar(
        select(func.count()).select_from(AppTag).where(AppTag.tag_id == tag_id)
    )


# ===================================================================== #
# tag → apps direction
# ===================================================================== #
async def test_replace_tag_apps_binds_and_get_reflects(
    app_client, jwt_token, seed_apps_5, seed_tags_3
):
    tag_id = str(seed_tags_3[0])
    chosen = seed_apps_5[:3]
    resp = await app_client.put(
        f"/api/v1/ncmu/admin/tags/{tag_id}/apps",
        headers=_auth(jwt_token),
        json={"app_ids": chosen},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"tag_id": tag_id, "app_count": 3}

    got = await app_client.get(
        f"/api/v1/ncmu/admin/tags/{tag_id}/apps", headers=_auth(jwt_token)
    )
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["tag_id"] == tag_id
    assert sorted(body["app_ids"]) == sorted(chosen)


async def test_replace_tag_apps_idempotent(
    app_client, jwt_token, async_db, seed_apps_5, seed_tags_3
):
    tag_id = str(seed_tags_3[0])
    payload = {"app_ids": seed_apps_5[:3]}
    r1 = await app_client.put(
        f"/api/v1/ncmu/admin/tags/{tag_id}/apps", headers=_auth(jwt_token), json=payload
    )
    r2 = await app_client.put(
        f"/api/v1/ncmu/admin/tags/{tag_id}/apps", headers=_auth(jwt_token), json=payload
    )
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json() == r2.json() == {"tag_id": tag_id, "app_count": 3}
    # No duplicate rows accumulated across the two PUTs.
    assert await _app_tag_count(async_db, seed_tags_3[0]) == 3


async def test_replace_tag_apps_empty_clears_all(
    app_client, jwt_token, async_db, seed_apps_5, seed_tags_3
):
    tag_id = str(seed_tags_3[0])
    await app_client.put(
        f"/api/v1/ncmu/admin/tags/{tag_id}/apps",
        headers=_auth(jwt_token),
        json={"app_ids": seed_apps_5[:2]},
    )
    resp = await app_client.put(
        f"/api/v1/ncmu/admin/tags/{tag_id}/apps",
        headers=_auth(jwt_token),
        json={"app_ids": []},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"tag_id": tag_id, "app_count": 0}
    assert await _app_tag_count(async_db, seed_tags_3[0]) == 0


async def test_replace_tag_apps_dedupes_duplicates(
    app_client, jwt_token, async_db, seed_apps_5, seed_tags_3
):
    tag_id = str(seed_tags_3[0])
    a = seed_apps_5[0]
    b = seed_apps_5[1]
    resp = await app_client.put(
        f"/api/v1/ncmu/admin/tags/{tag_id}/apps",
        headers=_auth(jwt_token),
        json={"app_ids": [a, a, b]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["app_count"] == 2  # distinct
    assert await _app_tag_count(async_db, seed_tags_3[0]) == 2


async def test_replace_tag_apps_nonexistent_app_404_1015(
    app_client, jwt_token, seed_tags_3
):
    tag_id = str(seed_tags_3[0])
    resp = await app_client.put(
        f"/api/v1/ncmu/admin/tags/{tag_id}/apps",
        headers=_auth(jwt_token),
        json={"app_ids": ["ghost-app"]},
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"]["code"] == 1015


async def test_replace_tag_apps_tag_not_found_404_1013(
    app_client, jwt_token, seed_apps_5
):
    resp = await app_client.put(
        f"/api/v1/ncmu/admin/tags/{uuid.uuid4()}/apps",
        headers=_auth(jwt_token),
        json={"app_ids": seed_apps_5[:1]},
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"]["code"] == 1013


async def test_tag_apps_endpoints_non_admin_403(app_client, jwt_secret, seed_tags_3):
    from ncmu_backend.auth.jwt_utils import sign_jwt

    lisi, _ = sign_jwt(user_id=LISI, name="李四", secret=jwt_secret)
    tag_id = str(seed_tags_3[0])
    get = await app_client.get(
        f"/api/v1/ncmu/admin/tags/{tag_id}/apps", headers=_auth(lisi)
    )
    put = await app_client.put(
        f"/api/v1/ncmu/admin/tags/{tag_id}/apps", headers=_auth(lisi), json={"app_ids": []}
    )
    assert get.status_code == 403 and put.status_code == 403
    assert get.json()["detail"]["code"] == 1201
    assert put.json()["detail"]["code"] == 1201


# ===================================================================== #
# app → tags direction (reverse)
# ===================================================================== #
async def test_replace_app_tags_binds_and_get_reflects(
    app_client, jwt_token, seed_apps_5, seed_tags_3
):
    app_id = seed_apps_5[0]
    chosen = [str(t) for t in seed_tags_3[:2]]
    resp = await app_client.put(
        f"/api/v1/ncmu/admin/apps/{app_id}/tags",
        headers=_auth(jwt_token),
        json={"tag_ids": chosen},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"dify_app_id": app_id, "tag_count": 2}

    got = await app_client.get(
        f"/api/v1/ncmu/admin/apps/{app_id}/tags", headers=_auth(jwt_token)
    )
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["dify_app_id"] == app_id
    assert sorted(body["tag_ids"]) == sorted(chosen)


async def test_replace_app_tags_empty_clears_all(
    app_client, jwt_token, seed_apps_5, seed_tags_3
):
    app_id = seed_apps_5[0]
    await app_client.put(
        f"/api/v1/ncmu/admin/apps/{app_id}/tags",
        headers=_auth(jwt_token),
        json={"tag_ids": [str(seed_tags_3[0])]},
    )
    resp = await app_client.put(
        f"/api/v1/ncmu/admin/apps/{app_id}/tags",
        headers=_auth(jwt_token),
        json={"tag_ids": []},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"dify_app_id": app_id, "tag_count": 0}
    got = await app_client.get(
        f"/api/v1/ncmu/admin/apps/{app_id}/tags", headers=_auth(jwt_token)
    )
    assert got.json()["tag_ids"] == []


async def test_replace_app_tags_nonexistent_tag_404_1015(
    app_client, jwt_token, seed_apps_5
):
    app_id = seed_apps_5[0]
    resp = await app_client.put(
        f"/api/v1/ncmu/admin/apps/{app_id}/tags",
        headers=_auth(jwt_token),
        json={"tag_ids": [str(uuid.uuid4())]},
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"]["code"] == 1015


async def test_replace_app_tags_app_not_found_404_1014(
    app_client, jwt_token, seed_tags_3
):
    resp = await app_client.put(
        "/api/v1/ncmu/admin/apps/ghost-app/tags",
        headers=_auth(jwt_token),
        json={"tag_ids": [str(seed_tags_3[0])]},
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"]["code"] == 1014


async def test_app_tags_endpoints_non_admin_403(app_client, jwt_secret, seed_apps_5):
    from ncmu_backend.auth.jwt_utils import sign_jwt

    lisi, _ = sign_jwt(user_id=LISI, name="李四", secret=jwt_secret)
    app_id = seed_apps_5[0]
    get = await app_client.get(
        f"/api/v1/ncmu/admin/apps/{app_id}/tags", headers=_auth(lisi)
    )
    put = await app_client.put(
        f"/api/v1/ncmu/admin/apps/{app_id}/tags", headers=_auth(lisi), json={"tag_ids": []}
    )
    assert get.status_code == 403 and put.status_code == 403


# ===================================================================== #
# tag_count real COUNT (replaces PE-07 placeholder 0) + bidirectional
# ===================================================================== #
async def test_tag_count_reflects_real_binding(
    app_client, jwt_token, seed_apps_5, seed_tags_3
):
    app_id = seed_apps_5[0]
    # Bind 2 tags to the app via the reverse endpoint.
    await app_client.put(
        f"/api/v1/ncmu/admin/apps/{app_id}/tags",
        headers=_auth(jwt_token),
        json={"tag_ids": [str(t) for t in seed_tags_3[:2]]},
    )
    # GET /admin/apps must now report tag_count=2 for that app (NOT 0).
    listed = await app_client.get(
        "/api/v1/ncmu/admin/apps", headers=_auth(jwt_token)
    )
    assert listed.status_code == 200, listed.text
    row = next(a for a in listed.json() if a["dify_app_id"] == app_id)
    assert row["tag_count"] == 2
    # An unbound app still reports 0.
    other = next(a for a in listed.json() if a["dify_app_id"] == seed_apps_5[1])
    assert other["tag_count"] == 0


async def test_binding_is_bidirectional_same_join_table(
    app_client, jwt_token, seed_apps_5, seed_tags_3
):
    # Bind via tag→apps, then read back via app→tags (same app_tags table).
    tag_id = str(seed_tags_3[0])
    app_id = seed_apps_5[0]
    await app_client.put(
        f"/api/v1/ncmu/admin/tags/{tag_id}/apps",
        headers=_auth(jwt_token),
        json={"app_ids": [app_id]},
    )
    got = await app_client.get(
        f"/api/v1/ncmu/admin/apps/{app_id}/tags", headers=_auth(jwt_token)
    )
    assert tag_id in got.json()["tag_ids"]


# ===================================================================== #
# AC#4 — data-layer join contract (Option A): binding makes the app
# shared-tag-reachable to the employee. NOT through GET /apps (see module
# docstring); the JOIN below is exactly what a future tags_routing_enabled
# router will consume.
# ===================================================================== #
async def _apps_reachable_via_shared_tag(async_db, user_id: str) -> list[str]:
    """Future tag-router's query: apps the user reaches via a shared tag =
    DISTINCT app_tags.dify_app_id JOIN user_tags ON same tag_id WHERE user."""
    q = (
        select(distinct(AppTag.dify_app_id))
        .join(UserTag, UserTag.tag_id == AppTag.tag_id)
        .where(UserTag.user_id == uuid.UUID(user_id))
    )
    return list((await async_db.execute(q)).scalars().all())


async def test_ac4_binding_makes_app_reachable_via_shared_tag(
    app_client, jwt_token, async_db, seed_apps_5, seed_tags_3
):
    tag_x = seed_tags_3[0]
    app_y = seed_apps_5[0]

    # Precondition: employee LISI reaches nothing via tags yet.
    assert await _apps_reachable_via_shared_tag(async_db, LISI) == []

    # Seed the employee↔tag side directly (user_tags = PE-09's table).
    async_db.add(UserTag(user_id=uuid.UUID(LISI), tag_id=tag_x))
    await async_db.commit()

    # Bind the app↔tag side via THIS task's PUT endpoint (app_tags).
    bind = await app_client.put(
        f"/api/v1/ncmu/admin/apps/{app_y}/tags",
        headers=_auth(jwt_token),
        json={"tag_ids": [str(tag_x)]},
    )
    assert bind.status_code == 200, bind.text

    # Contract: the join now identifies app_y as reachable for LISI.
    reachable = await _apps_reachable_via_shared_tag(async_db, LISI)
    assert app_y in reachable

    # And a different employee (王五) with no shared tag reaches nothing.
    assert await _apps_reachable_via_shared_tag(
        async_db, "a0000001-0000-4000-8000-000000000003"
    ) == []


async def test_ac4_unbinding_app_removes_reachability(
    app_client, jwt_token, async_db, seed_apps_5, seed_tags_3
):
    tag_x = seed_tags_3[0]
    app_y = seed_apps_5[0]
    async_db.add(UserTag(user_id=uuid.UUID(LISI), tag_id=tag_x))
    await async_db.commit()
    await app_client.put(
        f"/api/v1/ncmu/admin/apps/{app_y}/tags",
        headers=_auth(jwt_token),
        json={"tag_ids": [str(tag_x)]},
    )
    assert app_y in await _apps_reachable_via_shared_tag(async_db, LISI)

    # Unbind the app from the tag (replace-all with []) → no longer reachable.
    await app_client.put(
        f"/api/v1/ncmu/admin/apps/{app_y}/tags",
        headers=_auth(jwt_token),
        json={"tag_ids": []},
    )
    assert app_y not in await _apps_reachable_via_shared_tag(async_db, LISI)
