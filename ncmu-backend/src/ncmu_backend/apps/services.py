"""apps 业务层 — Phase 2C 双轨权限路由（shared ∪ owner）。

Paradigm shift（spec §3.3 / plan §4.4 PC2-C 字面 / C-INDEP-1 真代码 grounded）：
- shared 路径 = 既有 DifyConsoleClient real-time + 5 min lru cache
  (dify_console_client.py 字面禁区 / 5 kwargs 真签名保留)
- owner 路径 = app_owners INNER JOIN dify_apps
  (alembic 0007 PC1 + 0004/0005/0006 / ao.app_id String(64) = da.dify_app_id
  String(64) 类型对齐 / 无 CAST / spec §1.5 调和 #6)
- union + dedupe by app_id (防御 / owner App 不应被 Dify Console 返回，
  但 owner App 出现在 Dify Console 时以 shared 条目为准 / 顺序 shared 先 owner 后)

Feature flag `tags_routing_enabled` 由 PC2-D 引入；本模块不消费此 flag
（spec §1.5 调和 #4 / 永久 false 直到钉钉接入 / 当前态 shared 路径走
DifyConsoleClient real-time 不依赖 DB 表）。
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from ncmu_backend.apps.dify_console_client import (
    DifyConsoleClient,
    detect_app_type,
)
from ncmu_backend.config import Settings
from ncmu_backend.db.models import AppOwner, DifyApp


async def query_owned_apps(
    db: AsyncSession, owner_user_id: UUID,
) -> list[dict[str, Any]]:
    """Owner 路径 SQL — plan §4.4 AC#2 (3-col projection / SPA AppOut 仅消费)。

        SELECT ao.app_id, da.name, da.mode
        FROM app_owners ao
        INNER JOIN dify_apps da ON ao.app_id = da.dify_app_id
        WHERE ao.owner_user_id = :uid AND ao.visibility = 'owner_only'

    Note: plan §4.4 AC#2 字面 SELECT 含 5 列（api_token + visibility），但
    SPA AppOut 仅消费 id/name/mode/description。WHERE 已过滤 visibility ==
    'owner_only'，无需 SELECT；api_token 是 PC2-B (fastgpt_readonly) 才用。
    SELECT 3 列是性能优化 / 0 业务影响 / WHERE 字面与 plan 一致。

    Returns Dify-Console-shape dicts (id/name/mode/description) so the
    union with shared_apps stays homogeneous. INNER JOIN ensures any
    orphan app_owners row (no matching dify_apps cache entry) is dropped
    — orphans are a sync-lag artefact, not surfaceable apps.

    JOIN 类型对齐：ao.app_id 与 da.dify_app_id 均为 sa.String(length=64)
    （spec §1.5 调和 #6 + TASK-PC1 AC#4b 落地）— 无需 CAST。
    """
    stmt = (
        select(
            AppOwner.app_id.label("app_id"),
            DifyApp.name.label("name"),
            DifyApp.mode.label("mode"),
        )
        .join(DifyApp, AppOwner.app_id == DifyApp.dify_app_id)
        .where(AppOwner.owner_user_id == owner_user_id)
        .where(AppOwner.visibility == "owner_only")
    )
    result = await db.execute(stmt)
    return [
        {
            "id": row.app_id,
            "name": row.name,
            "mode": row.mode,
            "description": None,
        }
        for row in result
    ]


async def list_apps_for_user(
    *,
    db: AsyncSession,
    user_id: str,
    http: httpx.AsyncClient,
    cache: DifyConsoleClient,
    settings: Settings,
) -> list[dict[str, Any]]:
    """返回 user 可访问的 App 列表 = shared (DifyConsoleClient + [KB] filter)
    ∪ owner (DB query 已按 owner_only scope)，dedupe by app_id。

    顺序：shared 先 / owner 后（plan §4.4 AC#1 字面）。
    Dedupe rule：shared entry 优先（Dify Console 是 source of truth；
    owner App 不应出现在 Dify Console 返回但写防御代码）。

    Shape：每个条目是 Dify-Console-shape raw dict（id/name/mode/description/...），
    供 routes.py 投影到 AppOut。

    传播：DifyConsoleClient 抛 HTTPException 时不吞 — 由 caller 决定
    上游错误是否同时让 owner 路径失败（当前实现：直接传播 / shared 5xx 时
    owner 路径不返回，符合 fail-fast 语义）。
    """
    shared_raw = await cache.list_apps_for_user(
        user_id=user_id,
        http=http,
        base_url=settings.DIFY_BASE_URL,
        api_key=settings.DIFY_CONSOLE_API_KEY,
        tenant_id=settings.DIFY_TENANT_ID,
    )
    shared_kb = [
        a for a in shared_raw
        if detect_app_type(a.get("name") or "", a.get("tags"))
    ]

    owned = await query_owned_apps(db, UUID(user_id))

    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for entry in shared_kb:
        aid = entry.get("id")
        if not aid:
            continue
        if aid not in merged:
            merged[aid] = entry
            order.append(aid)
    for entry in owned:
        aid = entry["id"]
        if aid not in merged:
            merged[aid] = entry
            order.append(aid)
    return [merged[aid] for aid in order]


async def user_can_access_app(
    *,
    db: AsyncSession,
    user_id: str,
    app_id: str,
    http: httpx.AsyncClient,
    cache: DifyConsoleClient,
    settings: Settings,
) -> bool:
    """True iff user 通过 owner 路径 或 shared 路径能访问 app_id。

    Plan §4.4 AC#3 字面：
      1. 先查 owner（命中即返 / DB 单 query / 节省 Dify Console RTT）
      2. 否则查 shared（DifyConsoleClient + 5min lru cache）

    Empty/falsy app_id → False（短路 / 不调 DB / 不调上游）。
    Dify Console 5xx/4xx → HTTPException 502 传播
    （守 routes.py 现有 dify_status_to_ncmu 错误码语义）。

    供 fastgpt_readonly/routes.py 复用（Q8-C 字段级断言 / spec §3.3）；
    PC2-B 依赖本 task 主审 PASS 后才能 import 此 helper（C-INDEP-1 路径 A）。

    Note app_id 类型 `str`：plan §4.4 AC#3 字面 `async def user_can_access_app(
    user_id: UUID, app_id: str)` — app_id 是 dify_apps.dify_app_id String(64)
    不是 UUID。signature 这里取 user_id: str 以匹配 CurrentUser.sub 字符串形态；
    内部转 UUID 再查 DB（与 list_apps_for_user 对称）。
    """
    if not app_id:
        return False

    owned = await db.scalar(
        select(
            exists().where(
                (AppOwner.app_id == app_id)
                & (AppOwner.owner_user_id == UUID(user_id))
            )
        )
    )
    if owned:
        return True

    shared_raw = await cache.list_apps_for_user(
        user_id=user_id,
        http=http,
        base_url=settings.DIFY_BASE_URL,
        api_key=settings.DIFY_CONSOLE_API_KEY,
        tenant_id=settings.DIFY_TENANT_ID,
    )
    return any(entry.get("id") == app_id for entry in shared_raw)
