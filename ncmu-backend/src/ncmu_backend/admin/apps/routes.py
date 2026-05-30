"""TASK-PE-07 — admin App-management endpoints.

All routes are gated by :func:`require_admin` so the PE-02 invariance
test (``tests/admin/test_require_admin_audit.py``) auto-detects them.

URL surface (spec §5.5.3):

    GET   /api/v1/ncmu/admin/apps              list (include_inactive + search)
    GET   /api/v1/ncmu/admin/apps/{app_id}     single-app detail
    PATCH /api/v1/ncmu/admin/apps/{app_id}     toggle is_active only

POST /admin/sync_apps is NOT here — the Phase 2B endpoint in
``admin.routes`` is reused as-is (the SPA "立即同步" button calls it,
and the alembic-0010 SCOPE-CHANGE made it stamp ``last_synced_at``).

``app_id`` path params are **str**, not UUID: the cache PK is
``dify_apps.dify_app_id`` = ``String(64)`` (the Dify Console App id),
contrary to the plan §4 Step 1 sketch which assumed a UUID ``id`` column
(Boss 2026-05-29 拍板 — use the real PK type).

Error envelope — ``{"detail": {"code": <int>, "message": "..."}}`` —
matches admin/users + admin/tags so the SPA reads every admin error
uniformly. Code used here:

  1014  app not found by dify_app_id (PATCH / GET detail 404)
  1201  admin permission required (raised by ``require_admin``)

1014 is the next free slot in the admin 10xx family (1010/1011 = users,
1012/1013 = tags). PE-08's apps↔tags binding endpoints will extend from
1015 in this same module.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ncmu_backend.admin.apps.schemas import AdminAppOut, AdminAppUpdate
from ncmu_backend.admin.apps.services import get_admin_app, list_admin_apps
from ncmu_backend.auth.deps import CurrentUser, require_admin
from ncmu_backend.db.models import DifyApp
from ncmu_backend.db.session import get_db

router = APIRouter(tags=["admin-apps"])


def _not_found(app_id: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": 1014, "message": f"app '{app_id}' not found"},
    )


@router.get(
    "/api/v1/ncmu/admin/apps",
    response_model=list[AdminAppOut],
    summary="List cached Dify apps (admin view; include_inactive + search)",
)
async def list_apps(
    include_inactive: bool = False,
    search: str | None = None,
    _: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[AdminAppOut]:
    """Admin list = ALL cached apps (not employee tag/owner filtered).

    Default hides ``is_active=false`` rows; ``include_inactive=true``
    shows them so the admin can re-activate a previously-disabled app.
    ``search`` is a case-insensitive ``name`` substring match.
    """
    return await list_admin_apps(
        db, include_inactive=include_inactive, search=search
    )


@router.get(
    "/api/v1/ncmu/admin/apps/{app_id}",
    response_model=AdminAppOut,
    summary="Single cached app by dify_app_id (admin sees all fields)",
)
async def get_app(
    app_id: str,
    _: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminAppOut:
    out = await get_admin_app(db, app_id)
    if out is None:
        raise _not_found(app_id)
    return out


@router.patch(
    "/api/v1/ncmu/admin/apps/{app_id}",
    response_model=AdminAppOut,
    summary="Toggle is_active only (name/mode owned by Dify Console)",
)
async def update_app(
    app_id: str,
    body: AdminAppUpdate,
    _: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminAppOut:
    """Only ``is_active`` is mutable. An empty body (``is_active`` omitted)
    is a no-op that echoes the current row — not a 422 — so the SPA can
    PATCH idempotently.
    """
    app = await db.get(DifyApp, app_id)
    if app is None:
        raise _not_found(app_id)
    if body.is_active is not None:
        app.is_active = body.is_active
        await db.commit()
        await db.refresh(app)
    out = await get_admin_app(db, app_id)
    # ``app`` was just fetched/committed in this session; get_admin_app can
    # only return None if the row vanished mid-request — defensive guard
    # mirrors admin/tags/routes.py style.
    if out is None:
        raise _not_found(app_id)
    return out
