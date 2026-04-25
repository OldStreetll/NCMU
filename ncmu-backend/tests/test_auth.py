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
ZHANGSAN_UUID = "a0000001-0000-4000-8000-000000000001"
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
    # expires_at is a serialised ISO datetime ~24h from now.
    exp = datetime.fromisoformat(body["expires_at"].replace("Z", "+00:00"))
    delta = exp - datetime.now(timezone.utc)
    assert timedelta(hours=23, minutes=55) < delta < timedelta(hours=24, minutes=5)


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
async def test_jwt_payload_has_six_required_claims(app_client, jwt_secret):
    r = await app_client.post(
        "/api/v1/ncmu/auth/dev-login", json={"user_id": ZHANGSAN_UUID}
    )
    token = r.json()["jwt"]
    payload = jwt.decode(
        token, jwt_secret, algorithms=["HS256"],
        audience="ncmu-spa", issuer="ncmu-backend",
    )
    assert set(payload.keys()) == {"sub", "name", "iat", "exp", "iss", "aud"}
    assert payload["sub"] == ZHANGSAN_UUID
    assert payload["name"] == "张三"
    assert payload["iss"] == "ncmu-backend"
    assert payload["aud"] == "ncmu-spa"
    # iat / exp are int (POSIX seconds), exp ~ iat + 24h * 3600.
    assert isinstance(payload["iat"], int) and isinstance(payload["exp"], int)
    assert 24 * 3600 - 30 < payload["exp"] - payload["iat"] < 24 * 3600 + 30


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
