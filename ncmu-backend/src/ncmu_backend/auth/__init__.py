"""Auth — JWT signing/verification + dev-login routes.

Public re-exports for downstream task batches:
  - sign_jwt / verify_jwt  : header-less JWT helpers
  - get_current_user       : FastAPI dependency for protected endpoints
"""
from ncmu_backend.auth.jwt_utils import sign_jwt, verify_jwt
from ncmu_backend.auth.deps import get_current_user

__all__ = ["sign_jwt", "verify_jwt", "get_current_user"]
