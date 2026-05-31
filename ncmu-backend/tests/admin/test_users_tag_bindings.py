"""TASK-PE-09 — user ↔ tag binding (replace-all) endpoint tests.

Routes under test (all gated by ``Depends(require_admin)``, auto-checked by
``test_require_admin_audit.py``):

    GET  /api/v1/ncmu/admin/users/{user_id}/tags  — bound tag ids
    PUT  /api/v1/ncmu/admin/users/{user_id}/tags  — replace-all (tag_ids)
    GET  /api/v1/ncmu/admin/tags/{tag_id}/users   — bound user ids
    PUT  /api/v1/ncmu/admin/tags/{tag_id}/users   — replace-all (user_ids)

This is the reverse-roles mirror of PE-08's ``test_tags_app_bindings.py``
(app↔tag); same ``user_tags`` join table, same replace-all / idempotent /
de-dupe / phantom-id-404 contract.

AC#4 (Boss 2026-05-29 拍板 Option A — 等效证据非降阶): "binding makes the
app reachable to the employee via a shared tag" is verified at the **data
layer**, NOT through employee routing. Rationale (evidence-first, identical
to PE-08 AC#4): ``tags_routing_enabled`` is config-default False with the
comment "永久 false 直到钉钉接入" and is consumed nowhere in the repo, so the
employee-side routing E2E is unreachable. The contract test therefore binds
the user↔tag side via THIS task's PUT endpoint + seeds the app↔tag side
directly (``app_tags``, PE-08's table) and asserts the JOIN a future
tag-router WILL consume correctly identifies the app as shared-tag-reachable
for the user. (PE-08 AC#4 did the inverse: bound app↔tag via endpoint, seeded
user↔tag directly.) When DingTalk enables the flag + wires the route, the
data is already correct.

Error codes: 1011 (user not found) · 1013 (tag not found) · 1016 (binding
target not found, PE-09 — next after PE-08's 1015) · 1201 (require_admin).
"""
from __future__ import annotations

import uuid

import pytest_asyncio
from sqlalchemy import distinct, func, select

from ncmu_backend.db.models import AppTag, DifyApp, Tag, User, UserTag


LISI = "a0000001-0000-4000-8000-000000000002"  # non-admin dev id (403 cases)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------- #
@pytest_asyncio.fixture
async def seed_users_3(async_db):
    """3 users (explicit uuid id, mirroring admin.users.routes.create_user's
    ``User(id=uuid4(), ...)`` constructor)."""
    users = [
        User(
            id=uuid.uuid4(),
            name=f"用户{i}",
            dingtalk_userid=f"pe09-dt-{i}",
            dept_path=None,
            is_active=True,
        )
        for i in range(1, 4)
    ]
    async_db.add_all(users)
    await async_db.commit()
    return [u.id for u in users]


@pytest_asyncio.fixture
async def seed_tags_3(async_db):
    """3 tags."""
    tags = [Tag(name=f"标签{i}") for i in range(1, 4)]
    async_db.add_all(tags)
    await async_db.commit()
    for t in tags:
        await async_db.refresh(t)
    return [t.id for t in tags]


async def _user_tags_count(async_db, user_id) -> int:
    return await async_db.scalar(
        select(func.count()).select_from(UserTag).where(UserTag.user_id == user_id)
    )


async def _tag_users_count(async_db, tag_id) -> int:
    return await async_db.scalar(
        select(func.count()).select_from(UserTag).where(UserTag.tag_id == tag_id)
    )


# ===================================================================== #
# user → tags direction
# ===================================================================== #
async def test_replace_user_tags_binds_and_get_reflects(
    app_client, jwt_token, seed_users_3, seed_tags_3
):
    user_id = str(seed_users_3[0])
    chosen = [str(t) for t in seed_tags_3[:2]]
    resp = await app_client.put(
        f"/api/v1/ncmu/admin/users/{user_id}/tags",
        headers=_auth(jwt_token),
        json={"tag_ids": chosen},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"user_id": user_id, "tag_count": 2}

    got = await app_client.get(
        f"/api/v1/ncmu/admin/users/{user_id}/tags", headers=_auth(jwt_token)
    )
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["user_id"] == user_id
    assert sorted(body["tag_ids"]) == sorted(chosen)


async def test_replace_user_tags_idempotent(
    app_client, jwt_token, async_db, seed_users_3, seed_tags_3
):
    user_id = str(seed_users_3[0])
    payload = {"tag_ids": [str(t) for t in seed_tags_3[:2]]}
    r1 = await app_client.put(
        f"/api/v1/ncmu/admin/users/{user_id}/tags", headers=_auth(jwt_token), json=payload
    )
    r2 = await app_client.put(
        f"/api/v1/ncmu/admin/users/{user_id}/tags", headers=_auth(jwt_token), json=payload
    )
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json() == r2.json() == {"user_id": user_id, "tag_count": 2}
    # No duplicate rows accumulated across the two PUTs.
    assert await _user_tags_count(async_db, seed_users_3[0]) == 2


async def test_replace_user_tags_empty_clears_all(
    app_client, jwt_token, async_db, seed_users_3, seed_tags_3
):
    user_id = str(seed_users_3[0])
    await app_client.put(
        f"/api/v1/ncmu/admin/users/{user_id}/tags",
        headers=_auth(jwt_token),
        json={"tag_ids": [str(t) for t in seed_tags_3[:2]]},
    )
    resp = await app_client.put(
        f"/api/v1/ncmu/admin/users/{user_id}/tags",
        headers=_auth(jwt_token),
        json={"tag_ids": []},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"user_id": user_id, "tag_count": 0}
    assert await _user_tags_count(async_db, seed_users_3[0]) == 0


async def test_replace_user_tags_dedupes_duplicates(
    app_client, jwt_token, async_db, seed_users_3, seed_tags_3
):
    user_id = str(seed_users_3[0])
    a = str(seed_tags_3[0])
    b = str(seed_tags_3[1])
    resp = await app_client.put(
        f"/api/v1/ncmu/admin/users/{user_id}/tags",
        headers=_auth(jwt_token),
        json={"tag_ids": [a, a, b]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["tag_count"] == 2  # distinct
    assert await _user_tags_count(async_db, seed_users_3[0]) == 2


async def test_replace_user_tags_nonexistent_tag_404_1016(
    app_client, jwt_token, seed_users_3
):
    user_id = str(seed_users_3[0])
    resp = await app_client.put(
        f"/api/v1/ncmu/admin/users/{user_id}/tags",
        headers=_auth(jwt_token),
        json={"tag_ids": [str(uuid.uuid4())]},
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"]["code"] == 1016


async def test_replace_user_tags_user_not_found_404_1011(
    app_client, jwt_token, seed_tags_3
):
    resp = await app_client.put(
        f"/api/v1/ncmu/admin/users/{uuid.uuid4()}/tags",
        headers=_auth(jwt_token),
        json={"tag_ids": [str(seed_tags_3[0])]},
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"]["code"] == 1011


async def test_user_tags_endpoints_non_admin_403(app_client, jwt_secret, seed_users_3):
    from ncmu_backend.auth.jwt_utils import sign_jwt

    lisi, _ = sign_jwt(user_id=LISI, name="李四", secret=jwt_secret)
    user_id = str(seed_users_3[0])
    get = await app_client.get(
        f"/api/v1/ncmu/admin/users/{user_id}/tags", headers=_auth(lisi)
    )
    put = await app_client.put(
        f"/api/v1/ncmu/admin/users/{user_id}/tags",
        headers=_auth(lisi),
        json={"tag_ids": []},
    )
    assert get.status_code == 403 and put.status_code == 403
    assert get.json()["detail"]["code"] == 1201
    assert put.json()["detail"]["code"] == 1201


# ===================================================================== #
# tag → users direction (reverse)
# ===================================================================== #
async def test_replace_tag_users_binds_and_get_reflects(
    app_client, jwt_token, seed_users_3, seed_tags_3
):
    tag_id = str(seed_tags_3[0])
    chosen = [str(u) for u in seed_users_3[:2]]
    resp = await app_client.put(
        f"/api/v1/ncmu/admin/tags/{tag_id}/users",
        headers=_auth(jwt_token),
        json={"user_ids": chosen},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"tag_id": tag_id, "user_count": 2}

    got = await app_client.get(
        f"/api/v1/ncmu/admin/tags/{tag_id}/users", headers=_auth(jwt_token)
    )
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["tag_id"] == tag_id
    assert sorted(body["user_ids"]) == sorted(chosen)


async def test_replace_tag_users_empty_clears_all(
    app_client, jwt_token, seed_users_3, seed_tags_3
):
    tag_id = str(seed_tags_3[0])
    await app_client.put(
        f"/api/v1/ncmu/admin/tags/{tag_id}/users",
        headers=_auth(jwt_token),
        json={"user_ids": [str(seed_users_3[0])]},
    )
    resp = await app_client.put(
        f"/api/v1/ncmu/admin/tags/{tag_id}/users",
        headers=_auth(jwt_token),
        json={"user_ids": []},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"tag_id": tag_id, "user_count": 0}
    got = await app_client.get(
        f"/api/v1/ncmu/admin/tags/{tag_id}/users", headers=_auth(jwt_token)
    )
    assert got.json()["user_ids"] == []


async def test_replace_tag_users_dedupes_duplicates(
    app_client, jwt_token, async_db, seed_users_3, seed_tags_3
):
    tag_id = str(seed_tags_3[0])
    a = str(seed_users_3[0])
    b = str(seed_users_3[1])
    resp = await app_client.put(
        f"/api/v1/ncmu/admin/tags/{tag_id}/users",
        headers=_auth(jwt_token),
        json={"user_ids": [a, a, b]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["user_count"] == 2
    assert await _tag_users_count(async_db, seed_tags_3[0]) == 2


async def test_replace_tag_users_nonexistent_user_404_1016(
    app_client, jwt_token, seed_tags_3
):
    tag_id = str(seed_tags_3[0])
    resp = await app_client.put(
        f"/api/v1/ncmu/admin/tags/{tag_id}/users",
        headers=_auth(jwt_token),
        json={"user_ids": [str(uuid.uuid4())]},
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"]["code"] == 1016


async def test_replace_tag_users_tag_not_found_404_1013(
    app_client, jwt_token, seed_users_3
):
    resp = await app_client.put(
        f"/api/v1/ncmu/admin/tags/{uuid.uuid4()}/users",
        headers=_auth(jwt_token),
        json={"user_ids": [str(seed_users_3[0])]},
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"]["code"] == 1013


async def test_tag_users_endpoints_non_admin_403(app_client, jwt_secret, seed_tags_3):
    from ncmu_backend.auth.jwt_utils import sign_jwt

    lisi, _ = sign_jwt(user_id=LISI, name="李四", secret=jwt_secret)
    tag_id = str(seed_tags_3[0])
    get = await app_client.get(
        f"/api/v1/ncmu/admin/tags/{tag_id}/users", headers=_auth(lisi)
    )
    put = await app_client.put(
        f"/api/v1/ncmu/admin/tags/{tag_id}/users",
        headers=_auth(lisi),
        json={"user_ids": []},
    )
    assert get.status_code == 403 and put.status_code == 403


# ===================================================================== #
# bidirectional — same join table
# ===================================================================== #
async def test_binding_is_bidirectional_same_join_table(
    app_client, jwt_token, seed_users_3, seed_tags_3
):
    # Bind via user→tags, then read back via tag→users (same user_tags table).
    user_id = str(seed_users_3[0])
    tag_id = str(seed_tags_3[0])
    await app_client.put(
        f"/api/v1/ncmu/admin/users/{user_id}/tags",
        headers=_auth(jwt_token),
        json={"tag_ids": [tag_id]},
    )
    got = await app_client.get(
        f"/api/v1/ncmu/admin/tags/{tag_id}/users", headers=_auth(jwt_token)
    )
    assert user_id in got.json()["user_ids"]


# ===================================================================== #
# AC#4 — data-layer join contract (Option A): binding the user to a tag
# makes the tag's apps reachable to the user. NOT through employee routing
# (see module docstring); the JOIN below is exactly what a future
# tags_routing_enabled router will consume. Reverse of PE-08 AC#4.
# ===================================================================== #
async def _apps_reachable_via_shared_tag(async_db, user_id) -> list[str]:
    """Future tag-router's query: apps the user reaches via a shared tag =
    DISTINCT app_tags.dify_app_id JOIN user_tags ON same tag_id WHERE user."""
    q = (
        select(distinct(AppTag.dify_app_id))
        .join(UserTag, UserTag.tag_id == AppTag.tag_id)
        .where(UserTag.user_id == user_id)
    )
    return list((await async_db.execute(q)).scalars().all())


async def test_ac4_binding_user_to_tag_makes_app_reachable(
    app_client, jwt_token, async_db, seed_users_3, seed_tags_3
):
    user_id = seed_users_3[0]
    tag_x = seed_tags_3[0]

    # Seed the app↔tag side directly (app_tags = PE-08's table).
    async_db.add(DifyApp(dify_app_id="pe09-app-y", name="App Y", mode="chat"))
    await async_db.commit()
    async_db.add(AppTag(dify_app_id="pe09-app-y", tag_id=tag_x))
    await async_db.commit()

    # Precondition: the user reaches nothing via tags yet.
    assert await _apps_reachable_via_shared_tag(async_db, user_id) == []

    # Bind the user↔tag side via THIS task's PUT endpoint (user_tags).
    bind = await app_client.put(
        f"/api/v1/ncmu/admin/users/{user_id}/tags",
        headers=_auth(jwt_token),
        json={"tag_ids": [str(tag_x)]},
    )
    assert bind.status_code == 200, bind.text

    # Contract: the join now identifies pe09-app-y as reachable for the user.
    assert "pe09-app-y" in await _apps_reachable_via_shared_tag(async_db, user_id)

    # A different user with no shared tag reaches nothing.
    assert await _apps_reachable_via_shared_tag(async_db, seed_users_3[1]) == []


async def test_ac4_unbinding_user_removes_reachability(
    app_client, jwt_token, async_db, seed_users_3, seed_tags_3
):
    user_id = seed_users_3[0]
    tag_x = seed_tags_3[0]
    async_db.add(DifyApp(dify_app_id="pe09-app-z", name="App Z", mode="chat"))
    await async_db.commit()
    async_db.add(AppTag(dify_app_id="pe09-app-z", tag_id=tag_x))
    await async_db.commit()
    await app_client.put(
        f"/api/v1/ncmu/admin/users/{user_id}/tags",
        headers=_auth(jwt_token),
        json={"tag_ids": [str(tag_x)]},
    )
    assert "pe09-app-z" in await _apps_reachable_via_shared_tag(async_db, user_id)

    # Unbind (replace-all with []) → no longer reachable.
    await app_client.put(
        f"/api/v1/ncmu/admin/users/{user_id}/tags",
        headers=_auth(jwt_token),
        json={"tag_ids": []},
    )
    assert "pe09-app-z" not in await _apps_reachable_via_shared_tag(async_db, user_id)
