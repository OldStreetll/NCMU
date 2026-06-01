"""TASK-PE-06 — admin 用户管理 CRUD endpoint 字段级 dict-shape 断言.

Routes under test (all gated by `Depends(require_admin)`, auto-checked
by ``test_require_admin_audit.py`` after PE-02):

    GET    /api/v1/ncmu/admin/users               — list + pagination + search
    POST   /api/v1/ncmu/admin/users               — create (single)
    PATCH  /api/v1/ncmu/admin/users/{user_id}     — partial update
    DELETE /api/v1/ncmu/admin/users/{user_id}     — soft-delete (is_active=False)

Mock strategy: real FastAPI app via ASGITransport (``app_client`` from
``tests/conftest.py``); real PostgreSQL via pytest-postgresql ephemeral DB.
No upstream Dify Console / FastGPT mocks needed — admin/users module is
backend-DB-only.

Seed convention: ``dev_users.sql`` injects 5 fixed-UUID users (张三 admin,
李四/王五/赵六/钱七 non-admin, all with ``dingtalk_userid=NULL``). Tests
that need richer state (dingtalk-set user, inactive user) insert
additional rows via ``async_db`` directly.
"""
from __future__ import annotations

import uuid

import pytest_asyncio

from ncmu_backend.db.models import User


# UUIDs from alembic/seeds/dev_users.sql (B-4 修订 / 同步 NCMU_ADMIN_USER_IDS).
ZHANGSAN = "a0000001-0000-4000-8000-000000000001"  # admin
LISI = "a0000001-0000-4000-8000-000000000002"      # non-admin
WANGWU = "a0000001-0000-4000-8000-000000000003"
ZHAOLIU = "a0000001-0000-4000-8000-000000000004"


# --------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------- #

@pytest_asyncio.fixture
async def seed_user_with_dingtalk(async_db):
    """Insert a user with a non-null dingtalk_userid to test 409 duplicate.

    Existing 5 dev seeds all have ``dingtalk_userid IS NULL`` — they
    cannot trigger the UNIQUE-violation path on their own.
    """
    u = User(
        id=uuid.uuid4(),
        name="既存钉钉用户",
        dingtalk_userid="dingtalk-existing-001",
        dept_path="/技术/测试组",
        is_active=True,
    )
    async_db.add(u)
    await async_db.commit()
    await async_db.refresh(u)
    return u


@pytest_asyncio.fixture
async def seed_inactive_user(async_db):
    """A user with is_active=False so list-default-hides-inactive can assert."""
    u = User(
        id=uuid.uuid4(),
        name="已停用",
        dingtalk_userid=None,
        dept_path="/HR/离职组",
        is_active=False,
    )
    async_db.add(u)
    await async_db.commit()
    await async_db.refresh(u)
    return u


# --------------------------------------------------------------------- #
# (1) POST /admin/users — minimal create (just name)
# --------------------------------------------------------------------- #
async def test_create_user_minimal_just_name(app_client, jwt_token):
    """Plan §3 PE-06 Step 2 — minimal happy path:
       only ``name`` required; ``dingtalk_userid`` / ``dept_path`` default
       null; ``is_admin=False`` because new UUID not in admin_user_id_set."""
    resp = await app_client.post(
        "/api/v1/ncmu/admin/users",
        json={"name": "李四二代"},
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    # Field-level shape assertions (守 feedback_pre_existing_error_strict_validation)
    assert body["name"] == "李四二代"
    assert body["is_admin"] is False
    assert body["is_active"] is True
    assert body["dingtalk_userid"] is None
    assert body["dept_path"] is None
    # ``id`` is a fresh UUID; just check shape, not value
    assert isinstance(body["id"], str)
    uuid.UUID(body["id"])  # raises if not a valid UUID
    # PE-09 tags expansion not yet wired — empty list is the contract
    assert body["tags"] == []


# --------------------------------------------------------------------- #
# (2) POST /admin/users — full body (name + dingtalk_userid + dept_path)
# --------------------------------------------------------------------- #
async def test_create_user_full_body_round_trip(app_client, jwt_token, async_db):
    """Plan §0.2 字段对账：users 真表字段 = name + dingtalk_userid + dept_path
    (no phone / no role). POST 入参全字段 round-trip 通过 GET-by-id 验证。"""
    resp = await app_client.post(
        "/api/v1/ncmu/admin/users",
        json={
            "name": "周八",
            "dingtalk_userid": "dingtalk-zhou-008",
            "dept_path": "/市场/品牌",
        },
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "周八"
    assert body["dingtalk_userid"] == "dingtalk-zhou-008"
    assert body["dept_path"] == "/市场/品牌"
    assert body["is_active"] is True

    # Round-trip: row exists in DB with the same values
    refreshed = await async_db.get(User, uuid.UUID(body["id"]))
    assert refreshed is not None
    assert refreshed.name == "周八"
    assert refreshed.dingtalk_userid == "dingtalk-zhou-008"
    assert refreshed.dept_path == "/市场/品牌"


# --------------------------------------------------------------------- #
# (3) POST duplicate dingtalk_userid → 409 + code 1010
# --------------------------------------------------------------------- #
async def test_create_user_dingtalk_duplicate_returns_409(
    app_client, jwt_token, seed_user_with_dingtalk,
):
    """UNIQUE(dingtalk_userid) constraint in users table triggers 409.
    Error envelope must match the dict-shape pattern used by other admin
    endpoints (守 feedback_pre_existing_error_strict_validation)."""
    resp = await app_client.post(
        "/api/v1/ncmu/admin/users",
        json={
            "name": "撞钉钉的新人",
            "dingtalk_userid": seed_user_with_dingtalk.dingtalk_userid,
        },
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert resp.status_code == 409, resp.text
    body = resp.json()
    # Field-level dict shape (not toMatchObject)
    assert "detail" in body
    assert body["detail"]["code"] == 1010
    assert "dingtalk_userid" in body["detail"]["message"].lower() or \
           "exist" in body["detail"]["message"].lower()


# --------------------------------------------------------------------- #
# (4) POST as non-admin → 403 + code 1201
# --------------------------------------------------------------------- #
async def test_create_user_non_admin_returns_403(app_client, jwt_secret):
    """守 PE-02 invariance test + auth/deps.py require_admin contract.
    Non-admin token (李四) is rejected before the handler body runs."""
    from ncmu_backend.auth.jwt_utils import sign_jwt
    lisi_token, _ = sign_jwt(user_id=LISI, name="李四", secret=jwt_secret)

    resp = await app_client.post(
        "/api/v1/ncmu/admin/users",
        json={"name": "想偷创的用户"},
        headers={"Authorization": f"Bearer {lisi_token}"},
    )
    assert resp.status_code == 403, resp.text
    body = resp.json()
    assert body["detail"]["code"] == 1201


# --------------------------------------------------------------------- #
# (5) GET /admin/users — default excludes inactive
# --------------------------------------------------------------------- #
async def test_list_users_default_hides_inactive(
    app_client, jwt_token, seed_inactive_user,
):
    """Default ``include_inactive=False``; the seeded inactive user must
    NOT appear. Existing 5 dev seeds (all is_active=True) DO appear."""
    resp = await app_client.get(
        "/api/v1/ncmu/admin/users",
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "total" in body
    assert "items" in body
    assert isinstance(body["items"], list)
    item_ids = [u["id"] for u in body["items"]]
    assert str(seed_inactive_user.id) not in item_ids
    # All returned items must have is_active=True
    assert all(u["is_active"] is True for u in body["items"])


# --------------------------------------------------------------------- #
# (6) GET /admin/users?include_inactive=true — returns inactive too
# --------------------------------------------------------------------- #
async def test_list_users_include_inactive(
    app_client, jwt_token, seed_inactive_user,
):
    resp = await app_client.get(
        "/api/v1/ncmu/admin/users?include_inactive=true",
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    item_ids = [u["id"] for u in body["items"]]
    assert str(seed_inactive_user.id) in item_ids


# --------------------------------------------------------------------- #
# (7) GET /admin/users?search=张 — only names containing 张 returned
# --------------------------------------------------------------------- #
async def test_list_users_search_by_name(app_client, jwt_token):
    """Dev seeds include 张三 + 李四 + 王五 + 赵六 + 钱七;
    ``search=张`` must filter to only 张三."""
    resp = await app_client.get(
        "/api/v1/ncmu/admin/users?search=张",
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    names = [u["name"] for u in body["items"]]
    assert len(names) >= 1
    assert all("张" in n for n in names)


# --------------------------------------------------------------------- #
# (8) GET /admin/users — is_admin computed from settings.admin_user_id_set
# --------------------------------------------------------------------- #
async def test_list_users_is_admin_computed_from_whitelist(app_client, jwt_token):
    """守 plan §3 PE-06 contract: ``is_admin`` is **not** stored on
    users row — it's computed by checking ``str(user.id).lower() in
    settings.admin_user_id_set``. 张三 (a0...0001) is the dev admin per
    ``.env`` NCMU_ADMIN_USER_IDS default; the other 4 seed users are not."""
    resp = await app_client.get(
        "/api/v1/ncmu/admin/users?include_inactive=true",
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert resp.status_code == 200, resp.text
    by_id = {u["id"]: u for u in resp.json()["items"]}
    assert by_id[ZHANGSAN]["is_admin"] is True
    assert by_id[LISI]["is_admin"] is False
    assert by_id[WANGWU]["is_admin"] is False


# --------------------------------------------------------------------- #
# (9) GET /admin/users — non-admin → 403
# --------------------------------------------------------------------- #
async def test_list_users_non_admin_returns_403(app_client, jwt_secret):
    from ncmu_backend.auth.jwt_utils import sign_jwt
    lisi_token, _ = sign_jwt(user_id=LISI, name="李四", secret=jwt_secret)
    resp = await app_client.get(
        "/api/v1/ncmu/admin/users",
        headers={"Authorization": f"Bearer {lisi_token}"},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["code"] == 1201


# --------------------------------------------------------------------- #
# (10) PATCH /admin/users/{id} — partial update, only name
# --------------------------------------------------------------------- #
async def test_update_user_partial_name(app_client, jwt_token, async_db):
    resp = await app_client.patch(
        f"/api/v1/ncmu/admin/users/{ZHAOLIU}",
        json={"name": "赵六改名"},
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "赵六改名"
    assert body["dept_path"] == "/财务/总账"  # unchanged
    # DB-side verification
    refreshed = await async_db.get(User, uuid.UUID(ZHAOLIU))
    assert refreshed.name == "赵六改名"


# --------------------------------------------------------------------- #
# (10a-1) TASK-FIX-NULLNAME — PATCH {"name": null} → 422 (validator rejects).
# Regression for PE-06 latent bug: Optional[str] let null bypass min_length=1
# → setattr(user, "name", None) → NOT NULL UPDATE → misleading 409. The
# _reject_explicit_none field_validator (mirror of TagUpdate) gates it at the
# request layer. 守 feedback_pydantic_v2_optional_min_length_null_bypass.
# --------------------------------------------------------------------- #
async def test_update_user_name_explicit_null_returns_422(
    app_client, jwt_token, async_db,
):
    resp = await app_client.patch(
        f"/api/v1/ncmu/admin/users/{ZHAOLIU}",
        json={"name": None},
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert resp.status_code == 422, resp.text
    # FastAPI request-validation error: detail is a list of error dicts;
    # our ValueError message must surface so the SPA can show it.
    assert "name cannot be null" in resp.text
    # DB untouched — name not nulled.
    refreshed = await async_db.get(User, uuid.UUID(ZHAOLIU))
    assert refreshed.name is not None


# --------------------------------------------------------------------- #
# (10a-2) TASK-FIX-NULLNAME — omit ``name`` key → partial update still works
# (the validator only runs on explicitly-set values; omit-to-keep unaffected).
# --------------------------------------------------------------------- #
async def test_update_user_omit_name_updates_other_field(
    app_client, jwt_token, async_db,
):
    resp = await app_client.patch(
        f"/api/v1/ncmu/admin/users/{ZHAOLIU}",
        json={"dept_path": "/财务/审计"},  # no "name" key
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dept_path"] == "/财务/审计"
    assert body["name"]  # name preserved (non-empty), not nulled
    refreshed = await async_db.get(User, uuid.UUID(ZHAOLIU))
    assert refreshed.dept_path == "/财务/审计"
    assert refreshed.name is not None


# --------------------------------------------------------------------- #
# (10a-3) TASK-FIX-NULLNAME — PATCH {"name": "新名"} → 200 (valid string path
# unaffected by the validator).
# --------------------------------------------------------------------- #
async def test_update_user_name_valid_string_succeeds(
    app_client, jwt_token, async_db,
):
    resp = await app_client.patch(
        f"/api/v1/ncmu/admin/users/{ZHAOLIU}",
        json={"name": "赵六新名"},
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "赵六新名"
    refreshed = await async_db.get(User, uuid.UUID(ZHAOLIU))
    assert refreshed.name == "赵六新名"


# --------------------------------------------------------------------- #
# (10b) PATCH /admin/users/{id} — dingtalk_userid collides → 409 + code 1010
# --------------------------------------------------------------------- #
async def test_update_user_dingtalk_duplicate_returns_409(
    app_client, jwt_token, seed_user_with_dingtalk,
):
    """REWORK-PE-06-INDEP #3 — PATCH IntegrityError path (routes.py:155-159)
    was not exercised; POST path was covered by test #3. UNIQUE constraint
    on users.dingtalk_userid fires identically on UPDATE; envelope must
    match the POST path shape (守 feedback_pre_existing_error_strict_validation)."""
    # Seed creates user A with dingtalk-existing-001. LISI is a dev-seed
    # user with dingtalk_userid=NULL — PATCH-ing LISI's dingtalk to A's
    # value triggers the UNIQUE violation.
    resp = await app_client.patch(
        f"/api/v1/ncmu/admin/users/{LISI}",
        json={"dingtalk_userid": seed_user_with_dingtalk.dingtalk_userid},
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert resp.status_code == 409, resp.text
    body = resp.json()
    # Field-level dict shape (mirrors test #3 / not toMatchObject)
    assert "detail" in body
    assert body["detail"]["code"] == 1010
    assert "dingtalk_userid" in body["detail"]["message"].lower() or \
           "exist" in body["detail"]["message"].lower()


# --------------------------------------------------------------------- #
# (11) PATCH non-existent user → 404
# --------------------------------------------------------------------- #
async def test_update_user_not_found_returns_404(app_client, jwt_token):
    missing = "deadbeef-0000-4000-8000-000000000000"
    resp = await app_client.patch(
        f"/api/v1/ncmu/admin/users/{missing}",
        json={"name": "幽灵"},
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"]["code"] == 1011


# --------------------------------------------------------------------- #
# (12) DELETE /admin/users/{id} — soft delete (is_active=False, row stays)
# --------------------------------------------------------------------- #
async def test_deactivate_user_soft_delete(app_client, jwt_token, async_db):
    """Plan §3 PE-06 Step 1: DELETE is soft — flip is_active=False; row
    stays so chat_sessions FK + audit trail are preserved."""
    resp = await app_client.delete(
        f"/api/v1/ncmu/admin/users/{LISI}",
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert resp.status_code == 204, resp.text
    refreshed = await async_db.get(User, uuid.UUID(LISI))
    assert refreshed is not None  # row preserved
    assert refreshed.is_active is False


# --------------------------------------------------------------------- #
# (13) DELETE non-existent → 404
# --------------------------------------------------------------------- #
async def test_deactivate_user_not_found_returns_404(app_client, jwt_token):
    missing = "deadbeef-0000-4000-8000-000000000000"
    resp = await app_client.delete(
        f"/api/v1/ncmu/admin/users/{missing}",
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"]["code"] == 1011


# --------------------------------------------------------------------- #
# (14) PATCH/DELETE non-admin → 403
# --------------------------------------------------------------------- #
async def test_patch_user_non_admin_returns_403(app_client, jwt_secret):
    from ncmu_backend.auth.jwt_utils import sign_jwt
    lisi_token, _ = sign_jwt(user_id=LISI, name="李四", secret=jwt_secret)
    resp = await app_client.patch(
        f"/api/v1/ncmu/admin/users/{ZHAOLIU}",
        json={"name": "想偷改名"},
        headers={"Authorization": f"Bearer {lisi_token}"},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["code"] == 1201


async def test_delete_user_non_admin_returns_403(app_client, jwt_secret):
    from ncmu_backend.auth.jwt_utils import sign_jwt
    lisi_token, _ = sign_jwt(user_id=LISI, name="李四", secret=jwt_secret)
    resp = await app_client.delete(
        f"/api/v1/ncmu/admin/users/{ZHAOLIU}",
        headers={"Authorization": f"Bearer {lisi_token}"},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["code"] == 1201
