"""Thin service helpers for admin users CRUD (TASK-PE-06).

The route handlers in :mod:`ncmu_backend.admin.users.routes` keep the
SQL inline (so ``list_users`` reads top-to-bottom without indirection),
but the small ``UserOut`` projection — which has to consult
``settings.admin_user_id_set`` to compute the ``is_admin`` flag — is
factored out here so both the list and create/patch paths render the
same field shape.
"""
from __future__ import annotations

from ncmu_backend.admin.users.schemas import UserOut
from ncmu_backend.config import Settings
from ncmu_backend.db.models import User


def to_user_out(user: User, settings: Settings) -> UserOut:
    """Project a :class:`User` ORM row to the SPA-facing schema.

    ``is_admin`` is **computed**, not stored — admin membership comes
    from the env-driven ``NCMU_ADMIN_USER_IDS`` whitelist (mirror of
    ``auth.deps.get_current_user``'s rule). ``tags`` is empty until
    PE-09 wires the many-to-many binding.
    """
    return UserOut(
        id=user.id,
        name=user.name,
        dingtalk_userid=user.dingtalk_userid,
        dept_path=user.dept_path,
        is_active=user.is_active,
        is_admin=settings.derive_is_admin(user.id),
        tags=[],
    )
