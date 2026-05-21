"""JWT sign / verify helpers (HS256, PyJWT).

M-IND-3 修订：use PyJWT (`import jwt`), NOT python-jose (unmaintained
since 2024). Token shape per AC#5: sub / name / iat / exp / iss / aud.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt

ALGORITHM = "HS256"
ISSUER = "ncmu-backend"
AUDIENCE = "ncmu-spa"


def sign_jwt(
    user_id: str,
    name: str,
    secret: str,
    ttl_hours: int = 24,
    is_admin: bool = False,
) -> tuple[str, datetime]:
    """Return `(jwt_string, expires_at_utc)`.

    TASK-PC2-E: `is_admin` is included as a claim purely so the SPA can
    sync `AuthUser.is_admin` without round-tripping through the backend.
    Backend authorization keeps `settings.admin_user_id_set` as the
    source-of-truth (auth/deps.py:get_current_user) per r3 I-INDEP2-1 —
    the claim is **display state**, not an authorization input.
    """
    now = datetime.now(timezone.utc)
    exp = now + timedelta(hours=ttl_hours)
    payload = {
        "sub": user_id,
        "name": name,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
        "iss": ISSUER,
        "aud": AUDIENCE,
        "is_admin": bool(is_admin),
    }
    return jwt.encode(payload, secret, algorithm=ALGORITHM), exp


def verify_jwt(token: str, secret: str) -> dict:
    """Verify signature + standard claims; raises jwt.PyJWTError on failure.

    Tokens issued before TASK-PC2-E have no `is_admin` claim — callers
    that care about it must default to False (the contract is "claim
    absent ⇒ not admin"), so old tokens stay valid without forcing a
    re-login. Backend authorization does not read this claim at all.
    """
    return jwt.decode(
        token,
        secret,
        algorithms=[ALGORITHM],
        audience=AUDIENCE,
        issuer=ISSUER,
    )
