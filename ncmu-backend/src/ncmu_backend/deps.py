"""FastAPI dependency aggregator — re-exports the cross-cutting deps so
endpoint modules can `from ncmu_backend.deps import get_db, get_settings,
get_current_user` from one place instead of digging into sub-packages.
"""
from ncmu_backend.auth.deps import CurrentUser, get_current_user, require_admin
from ncmu_backend.config import Settings, get_settings
from ncmu_backend.db.session import get_db

__all__ = [
    "Settings",
    "get_settings",
    "get_db",
    "CurrentUser",
    "get_current_user",
    "require_admin",
]
