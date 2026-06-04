"""TASK-MON-1 — admin monitoring snapshot endpoint.

    GET /api/v1/ncmu/admin/monitoring   require_admin

Returns one structured snapshot (spec §3) the SPA renders on a single
page: business operations (§2.1) + dingtalk integration status (§2.2) +
``generated_at``. All data comes from the live NCMU pg + settings config
readiness — no埋点 table, no cross-system (Dify/FastGPT) calls (MVP edge,
spec §1).

Gated by :func:`require_admin` so the PE-02 invariance test
(``tests/admin/test_require_admin_audit.py``) auto-detects it. Wired into
``ncmu_backend.main`` via manual ``app.include_router`` — the
auto-discovery scanner only recurses depth 1 and this lives at depth 2
(same idiom as the PE-05/-06 admin sub-packages).
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ncmu_backend.admin.monitoring.schemas import MonitoringOut
from ncmu_backend.admin.monitoring.services import (
    collect_business_metrics,
    collect_dingtalk_metrics,
)
from ncmu_backend.auth.deps import CurrentUser, require_admin
from ncmu_backend.db.session import get_db
from ncmu_backend.deps import Settings, get_settings

router = APIRouter(tags=["admin-monitoring"])


@router.get(
    "/api/v1/ncmu/admin/monitoring",
    response_model=MonitoringOut,
    summary="Admin 运营监控快照（业务运营 + 钉钉集成状态，一次拿全）",
)
async def get_monitoring_snapshot(
    _: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> MonitoringOut:
    business = await collect_business_metrics(db)
    dingtalk = await collect_dingtalk_metrics(db, settings)
    return MonitoringOut(
        business=business,
        dingtalk=dingtalk,
        generated_at=datetime.now(timezone.utc),
    )
