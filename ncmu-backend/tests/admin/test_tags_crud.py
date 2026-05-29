"""TASK-PE-05 — admin tags CRUD endpoint 字段级 dict-shape 断言.

Routes under test (all gated by ``Depends(require_admin)``, auto-checked
by ``test_require_admin_audit.py`` after PE-02):

    GET    /api/v1/ncmu/admin/tags              — list + COUNT subquery JOIN
    POST   /api/v1/ncmu/admin/tags              — create one (409 + 1012 on dup name)
    PATCH  /api/v1/ncmu/admin/tags/{tag_id}     — partial update
    DELETE /api/v1/ncmu/admin/tags/{tag_id}     — hard delete + FK CASCADE

Mock strategy: real FastAPI app via ASGITransport (``app_client`` from
``tests/conftest.py``); real PostgreSQL via pytest-postgresql ephemeral
DB. No upstream Dify Console / FastGPT mocks needed — admin/tags module
is backend-DB-only.

Error codes (Boss 2026-05-29 拍板 A — adjacent to PE-06's 10xx family):
  1012  tag name UNIQUE conflict (POST + PATCH 409)
  1013  tag not found by id     (PATCH / DELETE 404)
  1201  admin permission required (raised by ``require_admin``)
"""
from __future__ import annotations

import uuid

import pytest_asyncio
from sqlalchemy import select

from ncmu_backend.db.models import AppTag, Tag, UserTag


# UUIDs from alembic/seeds/dev_users.sql (B-4 修订 / 同步 NCMU_ADMIN_USER_IDS).
ZHANGSAN = "a0000001-0000-4000-8000-000000000001"  # admin
LISI = "a0000001-0000-4000-8000-000000000002"      # non-admin
WANGWU = "a0000001-0000-4000-8000-000000000003"


# --------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------- #


@pytest_asyncio.fixture
async def seed_tag(async_db):
    """Insert one tag so duplicate-name + PATCH/DELETE-by-id paths can fire."""
    t = Tag(name="既存标签", description="seed for collision tests")
    async_db.add(t)
    await async_db.commit()
    await async_db.refresh(t)
    return t


@pytest_asyncio.fixture
async def seed_tag_with_bindings(async_db):
    """Tag + 2 app bindings + 2 user bindings — exercises COUNT JOIN +
    CASCADE delete path."""
    t = Tag(name="带绑定的标签", description="for cascade test")
    async_db.add(t)
    await async_db.commit()
    await async_db.refresh(t)

    # 2 app bindings (app_tags has no FK to dify_apps — plain str column).
    async_db.add_all([
        AppTag(dify_app_id="app-id-fixture-001", tag_id=t.id),
        AppTag(dify_app_id="app-id-fixture-002", tag_id=t.id),
    ])
    # 2 user bindings (FK to users.id — use existing dev seeds).
    async_db.add_all([
        UserTag(user_id=uuid.UUID(LISI), tag_id=t.id),
        UserTag(user_id=uuid.UUID(WANGWU), tag_id=t.id),
    ])
    await async_db.commit()
    return t


# --------------------------------------------------------------------- #
# (1) GET /admin/tags — empty list → 200 + []
# --------------------------------------------------------------------- #
async def test_list_tags_empty(app_client, jwt_token):
    """Fresh DB (no tags seeded); endpoint returns ``[]``, not 404 / null."""
    resp = await app_client.get(
        "/api/v1/ncmu/admin/tags",
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == []  # exact shape, not truthy/falsy


# --------------------------------------------------------------------- #
# (2) POST /admin/tags — minimal create (just name)
# --------------------------------------------------------------------- #
async def test_create_tag_minimal_just_name(app_client, jwt_token):
    """Plan §3 PE-05 Step 3 — minimal happy path; description defaults None,
    app_count + user_count deterministically 0 for a fresh tag."""
    resp = await app_client.post(
        "/api/v1/ncmu/admin/tags",
        json={"name": "新标签"},
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    # Field-level shape assertions (守 feedback_pre_existing_error_strict_validation)
    assert body["name"] == "新标签"
    assert body["description"] is None
    assert body["app_count"] == 0
    assert body["user_count"] == 0
    assert isinstance(body["id"], str)
    uuid.UUID(body["id"])  # raises if not a valid UUID


# --------------------------------------------------------------------- #
# (3) POST /admin/tags — full body (name + description)
# --------------------------------------------------------------------- #
async def test_create_tag_with_description_round_trip(
    app_client, jwt_token, async_db,
):
    """description column accepts ≤255 chars and round-trips via DB read."""
    resp = await app_client.post(
        "/api/v1/ncmu/admin/tags",
        json={"name": "技术", "description": "技术领域相关 App"},
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "技术"
    assert body["description"] == "技术领域相关 App"

    # Round-trip: row exists in DB with the same values
    refreshed = await async_db.get(Tag, uuid.UUID(body["id"]))
    assert refreshed is not None
    assert refreshed.name == "技术"
    assert refreshed.description == "技术领域相关 App"


# --------------------------------------------------------------------- #
# (4) POST duplicate name → 409 + code 1012
# --------------------------------------------------------------------- #
async def test_create_tag_duplicate_name_returns_409(
    app_client, jwt_token, seed_tag,
):
    """UNIQUE(tags.name) constraint triggers 409. Error envelope matches
    the dict-shape pattern used by PE-06 admin/users (守
    feedback_pre_existing_error_strict_validation)."""
    # Capture name BEFORE the POST. The shared-session test setup means
    # the route's rollback expires ``seed_tag`` mid-test; a post-rollback
    # ``seed_tag.name`` access triggers a sync lazy-load on the asyncpg
    # connection → ``MissingGreenlet``. Local str copy sidesteps it.
    seed_name = seed_tag.name
    resp = await app_client.post(
        "/api/v1/ncmu/admin/tags",
        json={"name": seed_name},
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert resp.status_code == 409, resp.text
    body = resp.json()
    # Field-level dict shape (not toMatchObject)
    assert "detail" in body
    assert body["detail"]["code"] == 1012
    # Echo the conflicting name so the SPA toast can quote it
    assert seed_name in body["detail"]["message"]


# --------------------------------------------------------------------- #
# (5) POST as non-admin → 403 + code 1201
# --------------------------------------------------------------------- #
async def test_create_tag_non_admin_returns_403(app_client, jwt_secret):
    """守 PE-02 invariance test + auth/deps.py require_admin contract.
    Non-admin token (李四) rejected before the handler body runs."""
    from ncmu_backend.auth.jwt_utils import sign_jwt
    lisi_token, _ = sign_jwt(user_id=LISI, name="李四", secret=jwt_secret)

    resp = await app_client.post(
        "/api/v1/ncmu/admin/tags",
        json={"name": "想偷创"},
        headers={"Authorization": f"Bearer {lisi_token}"},
    )
    assert resp.status_code == 403, resp.text
    body = resp.json()
    assert body["detail"]["code"] == 1201


# --------------------------------------------------------------------- #
# (6) GET /admin/tags — with bindings, COUNT JOIN returns real numbers
# --------------------------------------------------------------------- #
async def test_list_tags_returns_real_bind_counts(
    app_client, jwt_token, seed_tag_with_bindings,
):
    """services._tags_with_counts_query LEFT JOIN must surface
    ``app_count`` + ``user_count`` based on the join tables — fresh-test
    of the COUNT subquery aggregation (守 INDEP fresh-eyes 第 6 类 6 盲区
    现网字段级对比)."""
    resp = await app_client.get(
        "/api/v1/ncmu/admin/tags",
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) == 1
    row = body[0]
    assert row["name"] == "带绑定的标签"
    assert row["app_count"] == 2
    assert row["user_count"] == 2


# --------------------------------------------------------------------- #
# (7) GET /admin/tags — non-admin → 403
# --------------------------------------------------------------------- #
async def test_list_tags_non_admin_returns_403(app_client, jwt_secret):
    from ncmu_backend.auth.jwt_utils import sign_jwt
    lisi_token, _ = sign_jwt(user_id=LISI, name="李四", secret=jwt_secret)
    resp = await app_client.get(
        "/api/v1/ncmu/admin/tags",
        headers={"Authorization": f"Bearer {lisi_token}"},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["code"] == 1201


# --------------------------------------------------------------------- #
# (8) PATCH partial — only name changed; description preserved
# --------------------------------------------------------------------- #
async def test_update_tag_partial_name_preserves_description(
    app_client, jwt_token, seed_tag, async_db,
):
    """``exclude_unset=True`` means description stays as the seeded
    value when not present in the PATCH body."""
    resp = await app_client.patch(
        f"/api/v1/ncmu/admin/tags/{seed_tag.id}",
        json={"name": "重命名标签"},
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "重命名标签"
    assert body["description"] == seed_tag.description  # untouched

    refreshed = await async_db.get(Tag, seed_tag.id)
    assert refreshed.name == "重命名标签"
    assert refreshed.description == seed_tag.description


# --------------------------------------------------------------------- #
# (9) PATCH partial — only description changed; name preserved
# --------------------------------------------------------------------- #
async def test_update_tag_partial_description_preserves_name(
    app_client, jwt_token, seed_tag, async_db,
):
    new_desc = "新的描述"
    resp = await app_client.patch(
        f"/api/v1/ncmu/admin/tags/{seed_tag.id}",
        json={"description": new_desc},
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == seed_tag.name  # untouched
    assert body["description"] == new_desc

    refreshed = await async_db.get(Tag, seed_tag.id)
    assert refreshed.name == seed_tag.name
    assert refreshed.description == new_desc


# --------------------------------------------------------------------- #
# (10) PATCH — name collides with another tag → 409 + code 1012
# --------------------------------------------------------------------- #
async def test_update_tag_duplicate_name_returns_409(
    app_client, jwt_token, seed_tag, async_db,
):
    """Second tag exists; PATCH-ing it to seed_tag's name triggers the
    UNIQUE violation on commit. Envelope must match POST path
    (守 feedback_pre_existing_error_strict_validation)."""
    # Capture name + id BEFORE the PATCH — route's rollback expires
    # ``seed_tag`` mid-test; post-rollback attr access on the ORM obj
    # triggers ``MissingGreenlet`` (same pitfall as test #4).
    seed_name = seed_tag.name

    other = Tag(name="另一个标签", description="other")
    async_db.add(other)
    await async_db.commit()
    await async_db.refresh(other)
    other_id = other.id

    resp = await app_client.patch(
        f"/api/v1/ncmu/admin/tags/{other_id}",
        json={"name": seed_name},
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert "detail" in body
    assert body["detail"]["code"] == 1012
    assert seed_name in body["detail"]["message"]


# --------------------------------------------------------------------- #
# (11) PATCH non-existent tag → 404 + code 1013
# --------------------------------------------------------------------- #
async def test_update_tag_not_found_returns_404(app_client, jwt_token):
    missing = "deadbeef-0000-4000-8000-000000000000"
    resp = await app_client.patch(
        f"/api/v1/ncmu/admin/tags/{missing}",
        json={"name": "幽灵"},
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert resp.status_code == 404, resp.text
    body = resp.json()
    assert body["detail"]["code"] == 1013
    assert "not found" in body["detail"]["message"].lower()


# --------------------------------------------------------------------- #
# (12) PATCH non-admin → 403 + code 1201
# --------------------------------------------------------------------- #
async def test_update_tag_non_admin_returns_403(
    app_client, jwt_secret, seed_tag,
):
    from ncmu_backend.auth.jwt_utils import sign_jwt
    lisi_token, _ = sign_jwt(user_id=LISI, name="李四", secret=jwt_secret)
    resp = await app_client.patch(
        f"/api/v1/ncmu/admin/tags/{seed_tag.id}",
        json={"name": "想偷改"},
        headers={"Authorization": f"Bearer {lisi_token}"},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["code"] == 1201


# --------------------------------------------------------------------- #
# (13) DELETE — hard delete; row gone from DB
# --------------------------------------------------------------------- #
async def test_delete_tag_hard_removes_row(
    app_client, jwt_token, seed_tag, async_db,
):
    """Plan §3 PE-05 Step 3: DELETE is hard (row removed, not soft-flagged
    like PE-06 users). Different policy because tags have no audit trail
    to preserve — they're admin-visible labels only."""
    resp = await app_client.delete(
        f"/api/v1/ncmu/admin/tags/{seed_tag.id}",
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert resp.status_code == 204, resp.text

    # Row truly gone — not soft-flagged like users. Use raw SELECT (not
    # ``db.get``) to bypass the session identity_map — Core ``delete()``
    # in the route doesn't expire the cached ORM object.
    exists = (
        await async_db.execute(
            select(Tag.id).where(Tag.id == seed_tag.id)
        )
    ).scalar_one_or_none()
    assert exists is None


# --------------------------------------------------------------------- #
# (14) DELETE — FK CASCADE clears app_tags + user_tags rows automatically
# --------------------------------------------------------------------- #
async def test_delete_tag_cascade_clears_bindings(
    app_client, jwt_token, seed_tag_with_bindings, async_db,
):
    """守 alembic 0009 字面 FK ``ondelete=CASCADE`` on app_tags.tag_id +
    user_tags.tag_id. Deleting the parent tag must atomically sweep its
    join rows — this is the documented behaviour the SPA Popconfirm
    surfaces in ``"将解除 X 个 App 与 Y 个用户的绑定"``."""
    tag_id = seed_tag_with_bindings.id

    # Pre-condition: bindings exist
    pre_app = (
        await async_db.execute(
            select(AppTag).where(AppTag.tag_id == tag_id)
        )
    ).scalars().all()
    pre_user = (
        await async_db.execute(
            select(UserTag).where(UserTag.tag_id == tag_id)
        )
    ).scalars().all()
    assert len(pre_app) == 2
    assert len(pre_user) == 2

    # Act
    resp = await app_client.delete(
        f"/api/v1/ncmu/admin/tags/{tag_id}",
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert resp.status_code == 204, resp.text

    # Force a new transaction view so we see the CASCADE'd state.
    await async_db.commit()

    post_app = (
        await async_db.execute(
            select(AppTag).where(AppTag.tag_id == tag_id)
        )
    ).scalars().all()
    post_user = (
        await async_db.execute(
            select(UserTag).where(UserTag.tag_id == tag_id)
        )
    ).scalars().all()
    assert post_app == []
    assert post_user == []
    # Parent tag also gone (raw SELECT to bypass identity_map; Core
    # ``delete()`` in route doesn't expire the cached fixture obj).
    tag_exists = (
        await async_db.execute(
            select(Tag.id).where(Tag.id == tag_id)
        )
    ).scalar_one_or_none()
    assert tag_exists is None


# --------------------------------------------------------------------- #
# (15) DELETE non-existent → 404 + code 1013
# --------------------------------------------------------------------- #
async def test_delete_tag_not_found_returns_404(app_client, jwt_token):
    missing = "deadbeef-0000-4000-8000-000000000000"
    resp = await app_client.delete(
        f"/api/v1/ncmu/admin/tags/{missing}",
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"]["code"] == 1013


# --------------------------------------------------------------------- #
# (16) DELETE non-admin → 403 + code 1201
# --------------------------------------------------------------------- #
async def test_delete_tag_non_admin_returns_403(
    app_client, jwt_secret, seed_tag,
):
    from ncmu_backend.auth.jwt_utils import sign_jwt
    lisi_token, _ = sign_jwt(user_id=LISI, name="李四", secret=jwt_secret)
    resp = await app_client.delete(
        f"/api/v1/ncmu/admin/tags/{seed_tag.id}",
        headers={"Authorization": f"Bearer {lisi_token}"},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["code"] == 1201


# --------------------------------------------------------------------- #
# (17b) PATCH {"name": null} → 422 validation (REWORK-INDEP fix)
# --------------------------------------------------------------------- #
async def test_update_tag_explicit_null_name_returns_422(
    app_client, jwt_token, seed_tag,
):
    """REWORK-PE-05-INDEP Important #2 — explicit ``null`` for the ``name``
    field used to bypass ``min_length=1`` (Optional[str] lets None through),
    setattr name=None, then commit → IntegrityError → misleading 409 + 1012
    echo ``tag 'None' already exists``. The new ``field_validator("name")``
    rejects explicit None up-front so the user sees a 422 with a precise
    field-level error.

    Partial-update semantics (omit field → keep existing value) are NOT
    affected because Pydantic v2 skips validators on default values
    (``validate_default`` is False); test (10) ``partial_name_preserves_*``
    covers that path."""
    tag_id = seed_tag.id  # capture pre-validation to dodge MissingGreenlet
    resp = await app_client.patch(
        f"/api/v1/ncmu/admin/tags/{tag_id}",
        json={"name": None},
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert resp.status_code == 422, resp.text
    body = resp.json()
    # FastAPI surfaces Pydantic v2 422s as ``{"detail": [{loc, msg, type, ...}, ...]}``
    assert isinstance(body["detail"], list)
    # Field-level: at least one error tags ``name`` in its ``loc`` tuple
    # (loc typically looks like ["body", "name"]).
    name_errs = [
        err for err in body["detail"]
        if "name" in (err.get("loc") or [])
    ]
    assert len(name_errs) >= 1, (
        f"expected at least one error mentioning ``name`` in loc; got {body['detail']}"
    )
    # The validator's own message should surface (Pydantic v2 prefixes
    # validator-raised ValueErrors as ``Value error, <message>``).
    combined = " ".join(str(err.get("msg", "")) for err in name_errs)
    assert "null" in combined.lower() or "none" in combined.lower(), (
        f"expected validator message to mention null/None; got {combined!r}"
    )


# --------------------------------------------------------------------- #
# (18) PATCH echoes refreshed app_count + user_count from JOIN
# --------------------------------------------------------------------- #
async def test_update_tag_response_includes_real_counts(
    app_client, jwt_token, seed_tag_with_bindings,
):
    """守 routes.update_tag post-mutation refetch via get_tag_with_counts
    — PATCH response must show the same JOIN counts as a GET list would,
    not stale 0/0 like a fresh-create echo."""
    resp = await app_client.patch(
        f"/api/v1/ncmu/admin/tags/{seed_tag_with_bindings.id}",
        json={"description": "改下描述"},
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["description"] == "改下描述"
    assert body["app_count"] == 2
    assert body["user_count"] == 2
