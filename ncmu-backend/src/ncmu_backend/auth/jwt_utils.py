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
) -> tuple[str, datetime]:
    """Return `(jwt_string, expires_at_utc)`."""
    now = datetime.now(timezone.utc)
    exp = now + timedelta(hours=ttl_hours)
    payload = {
        "sub": user_id,
        "name": name,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
        "iss": ISSUER,
        "aud": AUDIENCE,
    }
    return jwt.encode(payload, secret, algorithm=ALGORITHM), exp


def verify_jwt(token: str, secret: str) -> dict:
    """Verify signature + standard claims; raises jwt.PyJWTError on failure."""
    return jwt.decode(
        token,
        secret,
        algorithms=[ALGORITHM],
        audience=AUDIENCE,
        issuer=ISSUER,
    )
