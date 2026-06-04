"""Aggregation helpers for the admin monitoring snapshot (TASK-MON-1).

The route handler (``routes.py``) calls :func:`collect_business_metrics`
and :func:`collect_dingtalk_metrics`; this module owns the SQL so the
route stays a thin assembler. Counts run as small scalar queries against
the live NCMU pg (no埋点 table — spec §1 MVP). Tag binding counts reuse
``admin.tags.services.list_tags_with_counts`` so the join-count query
shape lives in exactly one place.

Time window for the "近 7 天" trends is computed once per request as a
timezone-aware ``datetime`` cutoff and pushed into the WHERE clause; the
rows' ``created_at`` columns are ``DateTime(timezone=True)`` so asyncpg
compares them correctly.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ncmu_backend.admin.monitoring.schemas import (
    BusinessMetrics,
    DingtalkConfigReadiness,
    DingtalkMetrics,
    KbApplicationStatusCounts,
    TagBindingCount,
)
from ncmu_backend.admin.tags.services import list_tags_with_counts
from ncmu_backend.config import Settings
from ncmu_backend.db.models import (
    AppTag,
    DepartmentTagMapping,
    DifyApp,
    PersonalKbApplication,
    User,
    UserTag,
)

_SEVEN_DAYS = timedelta(days=7)
_KB_STATUSES = ("pending", "in_progress", "done", "rejected", "cancelled")


def _is_configured(value: str | None) -> bool:
    """A config field counts as 'ready' only if it's non-empty AND not a
    ``CHANGE_ME`` placeholder. Every dingtalk credential defaults to
    ``CHANGE_ME`` (login_redirect_uri to ``https://CHANGE_ME/...``), so a
    bare truthiness check would light green on an unconfigured deploy.
    Mirrors TASK-PRODVAL-1's substring detection (config.py header)."""
    return bool(value) and "CHANGE_ME" not in value


def dingtalk_config_readiness(settings: Settings) -> DingtalkConfigReadiness:
    """Pure settings→health-lights map (no DB; unit-testable standalone).

    Never returns the secret values — only booleans (spec §2.2 '绝不回显
    密钥值')."""
    return DingtalkConfigReadiness(
        app_key=_is_configured(settings.dingtalk_app_key),
        app_secret=_is_configured(settings.dingtalk_app_secret),
        corp_id=_is_configured(settings.dingtalk_corp_id),
        login_redirect_uri=_is_configured(settings.dingtalk_login_redirect_uri),
    )


async def collect_business_metrics(db: AsyncSession) -> BusinessMetrics:
    """§2.1 业务运营 — user / KB-application / app / tag aggregates."""
    cutoff = datetime.now(timezone.utc) - _SEVEN_DAYS

    user_total = await db.scalar(select(func.count()).select_from(User)) or 0
    user_active = (
        await db.scalar(
            select(func.count()).select_from(User).where(User.is_active.is_(True))
        )
        or 0
    )
    user_with_dingtalk = (
        await db.scalar(
            select(func.count())
            .select_from(User)
            .where(User.dingtalk_userid.is_not(None))
        )
        or 0
    )
    new_users_7d = (
        await db.scalar(
            select(func.count())
            .select_from(User)
            .where(User.created_at >= cutoff)
        )
        or 0
    )

    # KB application status distribution — one grouped pass, then fan the
    # rows into the fixed 5-key schema (a status with 0 rows stays 0).
    status_rows = (
        await db.execute(
            select(
                PersonalKbApplication.status, func.count()
            ).group_by(PersonalKbApplication.status)
        )
    ).all()
    status_map = {status: count for status, count in status_rows}
    kb_status = KbApplicationStatusCounts(
        **{name: status_map.get(name, 0) for name in _KB_STATUSES}
    )

    new_kb_applications_7d = (
        await db.scalar(
            select(func.count())
            .select_from(PersonalKbApplication)
            .where(PersonalKbApplication.created_at >= cutoff)
        )
        or 0
    )

    # Average processing time over done rows. admin_processed_at is the
    # done-transition stamp (personal_kb_applications has no updated_at).
    # avg(...) is NULL when there are no done rows → null in the response.
    avg_seconds = await db.scalar(
        select(
            func.avg(
                func.extract(
                    "epoch",
                    PersonalKbApplication.admin_processed_at
                    - PersonalKbApplication.created_at,
                )
            )
        ).where(
            PersonalKbApplication.status == "done",
            PersonalKbApplication.admin_processed_at.is_not(None),
        )
    )

    app_total = await db.scalar(select(func.count()).select_from(DifyApp)) or 0
    app_active = (
        await db.scalar(
            select(func.count())
            .select_from(DifyApp)
            .where(DifyApp.is_active.is_(True))
        )
        or 0
    )
    last_dify_app_sync_at = await db.scalar(
        select(func.max(DifyApp.last_synced_at))
    )

    tags = await list_tags_with_counts(db)
    tag_bindings = [
        TagBindingCount(
            tag_id=t.id,
            name=t.name,
            app_count=t.app_count,
            user_count=t.user_count,
        )
        for t in tags
    ]

    return BusinessMetrics(
        user_total=user_total,
        user_active=user_active,
        user_with_dingtalk=user_with_dingtalk,
        new_users_7d=new_users_7d,
        kb_application_status=kb_status,
        kb_pending_backlog=kb_status.pending,
        new_kb_applications_7d=new_kb_applications_7d,
        avg_kb_processing_seconds=(
            float(avg_seconds) if avg_seconds is not None else None
        ),
        app_total=app_total,
        app_active=app_active,
        last_dify_app_sync_at=last_dify_app_sync_at,
        tag_bindings=tag_bindings,
    )


async def collect_dingtalk_metrics(
    db: AsyncSession, settings: Settings
) -> DingtalkMetrics:
    """§2.2 钉钉集成状态 — config readiness + sync proxy counts."""
    department_tag_mapping_count = (
        await db.scalar(select(func.count()).select_from(DepartmentTagMapping)) or 0
    )
    # synced_account_count is the same query as business.user_with_dingtalk
    # (spec lists the proxy under both groups); recomputed here to keep the
    # two collectors independent rather than threading the value through.
    synced_account_count = (
        await db.scalar(
            select(func.count())
            .select_from(User)
            .where(User.dingtalk_userid.is_not(None))
        )
        or 0
    )
    routing_affected_user_count = (
        await db.scalar(
            select(func.count(func.distinct(UserTag.user_id))).select_from(UserTag)
        )
        or 0
    )
    routing_affected_app_count = (
        await db.scalar(
            select(func.count(func.distinct(AppTag.dify_app_id))).select_from(AppTag)
        )
        or 0
    )

    return DingtalkMetrics(
        config_readiness=dingtalk_config_readiness(settings),
        department_tag_mapping_count=department_tag_mapping_count,
        synced_account_count=synced_account_count,
        tags_routing_enabled=settings.tags_routing_enabled,
        routing_affected_user_count=routing_affected_user_count,
        routing_affected_app_count=routing_affected_app_count,
    )


__all__: Sequence[str] = (
    "dingtalk_config_readiness",
    "collect_business_metrics",
    "collect_dingtalk_metrics",
)
