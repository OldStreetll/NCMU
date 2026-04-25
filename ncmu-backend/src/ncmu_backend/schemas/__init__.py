"""Pydantic schemas — split into modules so TASK-26/27/28 can each own
its file (`schemas/apps.py`, `schemas/sessions.py`, `schemas/admin.py`)
without touching this package's existing files.

Re-exports are kept narrow on purpose; downstream code should import
from the leaf module (`from ncmu_backend.schemas.auth import UserOut`).
"""
from ncmu_backend.schemas.common import ErrorBody, PageMeta
from ncmu_backend.schemas.auth import (
    DevLoginRequest,
    DevLoginResponse,
    UserOut,
)

__all__ = [
    "ErrorBody",
    "PageMeta",
    "DevLoginRequest",
    "DevLoginResponse",
    "UserOut",
]
