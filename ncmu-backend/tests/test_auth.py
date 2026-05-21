"""TASK-25 AC#1-7 — auth endpoint behaviour + JWT contract.

The 6 tests in this module map 1:1 to AC#1 through AC#6 of the TASK-25
spec; AC#7 is the meta-requirement that *these tests pass*.

Each test takes the `app_client` fixture (httpx.AsyncClient against
the FastAPI ASGI app, with NCMU_ENABLE_DEV_LOGIN=true and the
ephemeral test DB seeded with dev_users.sql).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
import pytest

# All dev seed users — see alembic/seeds/dev_users.sql.
ZHANGSAN_UUID = "a0000001-0000-4000-8000-000000000001"  # default admin
LISI_UUID = "a0000001-0000-4000-8000-000000000002"      # non-admin
NONEXISTENT_UUID = "ffffffff-ffff-4fff-8fff-ffffffffffff"


# --------------------------------------------------------------------- AC#1
async def test_dev_login_returns_jwt_and_user(app_client):
    r = await app_client.post(
        "/api/v1/ncmu/auth/dev-login", json={"user_id": ZHANGSAN_UUID}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "jwt" in body and isinstance(body["jwt"], str) and body["jwt"].count(".") == 2
    assert body["user"]["id"] == ZHANGSAN_UUID
    assert body["user"]["name"] == "张三"
    assert body["user"]["is_active"] is True
    # TASK-PC2-E AC#1: dev-login response.body.user surfaces is_admin so
    # the SPA can sync AuthUser.is_admin without JWT decoding. 张三 is the
    # default admin (NCMU_ADMIN_USER_IDS default in config.py:62), so the
    # field must be literal True here.
    assert "is_admin" in body["user"]
    assert body["user"]["is_admin"] is True
    # expires_at is a serialised ISO datetime ~24h from now.
    exp = datetime.fromisoformat(body["expires_at"].replace("Z", "+00:00"))
    delta = exp - datetime.now(timezone.utc)
    assert timedelta(hours=23, minutes=55) < delta < timedelta(hours=24, minutes=5)


# --------------------------------------------------------------------- AC#1 (non-admin variant)
async def test_dev_login_non_admin_user_returns_is_admin_false(app_client):
    """TASK-PC2-E AC#1: non-admin dev user (李四) must round-trip
    is_admin=False so the SPA hides admin-only UI. Validates that
    admin_user_id_set membership is the literal predicate (not "any
    seeded user is admin")."""
    r = await app_client.post(
        "/api/v1/ncmu/auth/dev-login", json={"user_id": LISI_UUID}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user"]["id"] == LISI_UUID
    assert body["user"]["name"] == "李四"
    assert body["user"]["is_admin"] is False


# --------------------------------------------------------------------- AC#2
async def test_dev_login_unknown_user_returns_404_with_code_1101(app_client):
    r = await app_client.post(
        "/api/v1/ncmu/auth/dev-login", json={"user_id": NONEXISTENT_UUID}
    )
    assert r.status_code == 404
    body = r.json()
    # FastAPI envelopes our HTTPException(detail=...) as {"detail": {...}}.
    assert body["detail"]["code"] == 1101
    assert "not found" in body["detail"]["message"].lower()


# --------------------------------------------------------------------- AC#3
async def test_dev_login_returns_404_when_disabled(app_client, monkeypatch):
    """AC#3 (C-3 修订): the route stays registered (visible in OpenAPI)
    but the handler raises 404 when NCMU_ENABLE_DEV_LOGIN=false."""
    monkeypatch.setenv("NCMU_ENABLE_DEV_LOGIN", "false")
    from ncmu_backend.config import reset_settings_cache
    reset_settings_cache()

    r = await app_client.post(
        "/api/v1/ncmu/auth/dev-login", json={"user_id": ZHANGSAN_UUID}
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == 1100   # disabled, not "user not found"


# --------------------------------------------------------------------- AC#4
async def test_dev_users_returns_5_seed_users(app_client):
    r = await app_client.get("/api/v1/ncmu/dev/users")
    assert r.status_code == 200, r.text
    users = r.json()
    assert len(users) == 5
    names = {u["name"] for u in users}
    assert names == {"张三", "李四", "王五", "赵六", "钱七"}
    # Sorted by name (default Postgres collation, stable across runs).
    assert [u["name"] for u in users] == sorted(names)


# --------------------------------------------------------------------- AC#5
async def test_jwt_payload_has_seven_required_claims(app_client, jwt_secret):
    """TASK-PC2-E extends the AC#5 claim set with `is_admin` (display-only
    state, NOT consulted by auth/deps.py — see r3 I-INDEP2-1)."""
    r = await app_client.post(
        "/api/v1/ncmu/auth/dev-login", json={"user_id": ZHANGSAN_UUID}
    )
    token = r.json()["jwt"]
    payload = jwt.decode(
        token, jwt_secret, algorithms=["HS256"],
        audience="ncmu-spa", issuer="ncmu-backend",
    )
    assert set(payload.keys()) == {"sub", "name", "iat", "exp", "iss", "aud", "is_admin"}
    assert payload["sub"] == ZHANGSAN_UUID
    assert payload["name"] == "张三"
    assert payload["iss"] == "ncmu-backend"
    assert payload["aud"] == "ncmu-spa"
    # 张三 is the default admin → claim must be literal True.
    assert payload["is_admin"] is True
    # iat / exp are int (POSIX seconds), exp ~ iat + 24h * 3600.
    assert isinstance(payload["iat"], int) and isinstance(payload["exp"], int)
    assert 24 * 3600 - 30 < payload["exp"] - payload["iat"] < 24 * 3600 + 30


# --------------------------------------------------------------------- AC#5 (non-admin JWT claim)
async def test_jwt_is_admin_claim_false_for_non_admin_user(app_client, jwt_secret):
    """TASK-PC2-E AC#2: non-admin dev-login → JWT `is_admin` claim is
    literal False (not absent, not truthy-ish)."""
    r = await app_client.post(
        "/api/v1/ncmu/auth/dev-login", json={"user_id": LISI_UUID}
    )
    token = r.json()["jwt"]
    payload = jwt.decode(
        token, jwt_secret, algorithms=["HS256"],
        audience="ncmu-spa", issuer="ncmu-backend",
    )
    assert "is_admin" in payload
    assert payload["is_admin"] is False


# --------------------------------------------------------------------- AC#5 (sign_jwt round-trip)
def test_sign_jwt_round_trips_is_admin_byte_exact(jwt_secret):
    """TASK-PC2-E AC#2: sign_jwt(is_admin=True) → verify_jwt → payload
    contains the literal True. Direct util-level test, no FastAPI app."""
    from ncmu_backend.auth.jwt_utils import sign_jwt, verify_jwt

    token_admin, _ = sign_jwt("u-admin", "Admin", jwt_secret, is_admin=True)
    decoded_admin = verify_jwt(token_admin, jwt_secret)
    assert decoded_admin["is_admin"] is True

    token_plain, _ = sign_jwt("u-plain", "Plain", jwt_secret, is_admin=False)
    decoded_plain = verify_jwt(token_plain, jwt_secret)
    assert decoded_plain["is_admin"] is False

    # sign_jwt's `is_admin` parameter defaults to False so legacy call sites
    # (none in-tree post-TASK-PC2-E, but library callers may exist) still
    # produce valid tokens.
    token_default, _ = sign_jwt("u-default", "Default", jwt_secret)
    decoded_default = verify_jwt(token_default, jwt_secret)
    assert decoded_default["is_admin"] is False


# --------------------------------------------------------------------- AC#5 (old token backward compat)
async def test_legacy_jwt_without_is_admin_claim_still_authorized(app_client, jwt_secret):
    """TASK-PC2-E AC#2 "0 token 兼容性破坏": a JWT issued before this task
    has no `is_admin` claim. get_current_user must accept it and default
    is_admin=False — silently, without raising or forcing re-login.

    Depends on `app_client` (not used directly) so the deterministic
    NCMU_JWT_SECRET monkeypatch is in place and `get_settings()` returns
    the same secret used to sign the legacy token."""
    from ncmu_backend.auth.deps import get_current_user
    from ncmu_backend.config import get_settings

    legacy_payload = {
        "sub": LISI_UUID,
        "name": "李四",
        "iat": int(datetime.now(timezone.utc).timestamp()),
        "exp": int((datetime.now(timezone.utc) + timedelta(hours=24)).timestamp()),
        "iss": "ncmu-backend",
        "aud": "ncmu-spa",
        # Note: no `is_admin` claim — this is the pre-TASK-PC2-E shape.
    }
    legacy_token = jwt.encode(legacy_payload, jwt_secret, algorithm="HS256")

    settings = get_settings()
    user = await get_current_user(
        authorization=f"Bearer {legacy_token}", settings=settings
    )
    # Backend authorization derives is_admin from settings.admin_user_id_set
    # (r3 I-INDEP2-1 source-of-truth), so 李四 → False regardless of the
    # absent claim. Critically, no exception was raised.
    assert user.sub == LISI_UUID
    assert user.is_admin is False


# --------------------------------------------------------------------- AC#6
async def test_expired_jwt_is_rejected_by_get_current_user(app_client, jwt_secret):
    """Expired tokens raise HTTPException(401) via get_current_user.

    We exercise this through /healthz? — no, /healthz isn't protected.
    Instead, use the get_current_user dep directly: import and call it
    against a manually-crafted expired token.
    """
    from fastapi import HTTPException
    from ncmu_backend.auth.deps import get_current_user
    from ncmu_backend.config import get_settings

    expired_payload = {
        "sub": ZHANGSAN_UUID,
        "name": "张三",
        "iat": int((datetime.now(timezone.utc) - timedelta(hours=48)).timestamp()),
        "exp": int((datetime.now(timezone.utc) - timedelta(hours=24)).timestamp()),
        "iss": "ncmu-backend",
        "aud": "ncmu-spa",
    }
    expired_token = jwt.encode(expired_payload, jwt_secret, algorithm="HS256")

    settings = get_settings()
    with pytest.raises(HTTPException) as ei:
        await get_current_user(
            authorization=f"Bearer {expired_token}", settings=settings
        )
    assert ei.value.status_code == 401
    assert ei.value.detail["code"] == 1104  # token expired
