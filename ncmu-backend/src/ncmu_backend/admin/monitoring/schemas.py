"""Pydantic schemas for the admin monitoring snapshot (TASK-MON-1).

One endpoint — ``GET /api/v1/ncmu/admin/monitoring`` — returns a single
structured snapshot the SPA renders on one page (spec §3). Two top-level
groups mirror the spec's section split:

  * ``business``  — §2.1 业务运营 (users / KB applications / apps / tags)
  * ``dingtalk``  — §2.2 钉钉集成状态 (config readiness + sync proxies)

plus a ``generated_at`` timestamp so the SPA can show snapshot freshness.

Field-level grounding notes (deviate from the spec's literal field names
where the real schema differs — see TASK-MON-1 CHECKPOINT):
  * ``avg_kb_processing_seconds`` derives from ``admin_processed_at -
    created_at`` (the ``done`` transition stamp), NOT ``updated_at`` —
    ``personal_kb_applications`` has no ``updated_at`` column.
  * ``DingtalkConfigReadiness`` lights ``corp_id`` (a real required
    credential) in place of the spec's ``agent_id`` (no such config
    field exists). Each light is non-empty AND not a ``CHANGE_ME``
    placeholder (mirrors TASK-PRODVAL-1 substring detection) — bare
    non-empty is meaningless since every field defaults to ``CHANGE_ME``.
    Booleans only; raw secret values are never serialized.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class KbApplicationStatusCounts(BaseModel):
    """个人 KB 申请 5 态分布 (§2.1). Every status keyed so a 0-count
    status still renders a card instead of silently vanishing."""

    pending: int = 0
    in_progress: int = 0
    done: int = 0
    rejected: int = 0
    cancelled: int = 0


class TagBindingCount(BaseModel):
    """单个标签的 user/app 绑定数 (§2.1 末行). Reuses the same count
    shape as ``admin/tags/services.list_tags_with_counts``."""

    tag_id: uuid.UUID
    name: str
    app_count: int = 0
    user_count: int = 0


class BusinessMetrics(BaseModel):
    """§2.1 业务运营快照."""

    user_total: int = Field(description="users 总数")
    user_active: int = Field(description="is_active=true 的用户数")
    user_with_dingtalk: int = Field(
        description="dingtalk_userid 非空的用户数（已建钉钉账号代理）"
    )
    new_users_7d: int = Field(description="近 7 天新增用户 (created_at >= now()-7d)")

    kb_application_status: KbApplicationStatusCounts = Field(
        description="个人 KB 申请按 status 分布"
    )
    kb_pending_backlog: int = Field(
        description="status='pending' 的待处理积压（= kb_application_status.pending）"
    )
    new_kb_applications_7d: int = Field(
        description="近 7 天新增个人 KB 申请"
    )
    avg_kb_processing_seconds: Optional[float] = Field(
        default=None,
        description=(
            "done 申请的平均处理时长（秒）= avg(admin_processed_at - "
            "created_at)；无 done 行时为 null"
        ),
    )

    app_total: int = Field(description="dify_apps 总数")
    app_active: int = Field(description="is_active=true 的 dify_apps 数")
    last_dify_app_sync_at: Optional[datetime] = Field(
        default=None,
        description="max(dify_apps.last_synced_at)（Dify App 同步，非钉钉同步）",
    )

    tag_bindings: list[TagBindingCount] = Field(
        default_factory=list,
        description="各标签的 user/app 绑定数（复用 tags/services 查询）",
    )


class DingtalkConfigReadiness(BaseModel):
    """钉钉应用配置就绪度健康灯 (§2.2). 布尔，绝不回显密钥值."""

    app_key: bool = Field(description="DINGTALK_APP_KEY 已配置（非占位）")
    app_secret: bool = Field(description="DINGTALK_APP_SECRET 已配置（非占位）")
    corp_id: bool = Field(description="DINGTALK_CORP_ID 已配置（非占位）")
    login_redirect_uri: bool = Field(
        description="DINGTALK_LOGIN_REDIRECT_URI 已配置（非占位嵌套）"
    )


class DingtalkMetrics(BaseModel):
    """§2.2 钉钉集成状态快照（代理指标）."""

    config_readiness: DingtalkConfigReadiness
    department_tag_mapping_count: int = Field(
        description="department_tag_mappings 行数（同步打标签配置规模）"
    )
    synced_account_count: int = Field(
        description="dingtalk_userid 非空的用户数（已同步建账号代理）"
    )
    tags_routing_enabled: bool = Field(
        description="TAGS_ROUTING_ENABLED flag（ON/OFF）"
    )
    routing_affected_user_count: int = Field(
        description=(
            "user_tags 中 distinct tagged user 数（routing-impact proxy；"
            "非 flag-ON 真实过滤集）"
        )
    )
    routing_affected_app_count: int = Field(
        description=(
            "app_tags 中 distinct tagged app 数（routing-impact proxy；"
            "非 flag-ON 真实过滤集）"
        )
    )


class MonitoringOut(BaseModel):
    """GET /api/v1/ncmu/admin/monitoring 的整页快照响应 (spec §3)."""

    business: BusinessMetrics
    dingtalk: DingtalkMetrics
    generated_at: datetime = Field(description="快照生成时刻（UTC）")
