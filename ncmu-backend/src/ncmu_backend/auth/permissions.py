"""Cross-cutting authorization helpers — App-scoped permission gates.

Previously lived in ``apps/services.py`` next to the domain business
logic (Phase 2C dual-track routing). TASK-BNN-B moved the helper to
``auth/`` because it's a cross-cutting concern reused by ``apps/``,
``workflow/``, and ``fastgpt_readonly/`` — it belongs with the other
auth primitives (``deps.py`` / ``jwt_utils.py`` / ``routes.py``), not
inside a single domain module. 0 behavior change; pure relocation.
"""
from __future__ import annotations

from uuid import UUID

import httpx
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from ncmu_backend.apps import services
from ncmu_backend.apps.dify_console_client import DifyConsoleClient
from ncmu_backend.config import Settings
from ncmu_backend.db.models import AppOwner


async def user_can_access_app(
    *,
    db: AsyncSession,
    user_id: str,
    app_id: str,
    http: httpx.AsyncClient,
    cache: DifyConsoleClient,
    settings: Settings,
) -> bool:
    """True iff user 通过 owner 路径 或 *可见* shared 路径能访问 app_id。

    判定顺序：
      1. Empty/falsy app_id → False（短路 / 不调 DB / 不调上游）。
      2. owner 命中 → True（DB 单 EXISTS query / 短路 / **Dify-independent**，
         Dify Console 挂了 owner 仍能访问自己的 App / 节省 RTT）。
      3. 否则：门禁可见性 == 列表可见性 —— 复用
         ``services.list_apps_for_user``（owned ∪ **过滤后**的 shared），
         判 app_id 是否在该 user 的可见集内。

    TASK-2DA1-GATE（设计 spec §7.1 独审 Important / Boss 2026-06-03 决策 B）：
    旧实现第 3 步取 **raw Dify 共享列表**（``cache.list_apps_for_user``），
    既不过 [KB] 名字过滤（flag OFF）也不过 user_tags ∩ app_tags（flag ON），
    与 GET /apps 列表（``services.list_apps_for_user``）可见性裂开 →
    列表里看不到的 App 仍能过门禁 = **over-share（横向越权）**。本修把第 3 步
    换成 ``services.list_apps_for_user``，使「能访问 ⟺ 在可见列表里」。
      - flag OFF（prod 现状）：shared 收紧为 [KB] 过滤后的 ∪ owned
        （修 pre-existing over-share；现有 [KB] 应用照常可访问）。
      - flag ON：shared = user_tags ∩ app_tags ∪ owned（含 [KB] 名也走标签）。
    **owner 短路（第 2 步）保留**：owner 路径不受 flag 影响、不耦合 Dify 可用性。

    Dify Console 5xx/4xx → HTTPException 502 传播（守 routes.py 现有
    dify_status_to_ncmu 错误码语义；仅在第 3 步非 owner 路径触发）。

    供 fastgpt_readonly/routes.py 复用（Q8-C 字段级断言 / spec §3.3）。

    Note app_id 类型 `str`：app_id 是 dify_apps.dify_app_id String(64) 不是
    UUID。signature 取 user_id: str 以匹配 CurrentUser.sub 字符串形态；内部转
    UUID 查 DB（与 list_apps_for_user 对称）。
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

    # 非 owner：门禁可见性必须 == 列表可见性（修 over-share）。复用
    # services.list_apps_for_user（owned ∪ 过滤后 shared / 消费 flag），不在
    # 此重复写过滤逻辑。services 内部会再算一次 owned，但此处已确定非 owner，
    # 那份 owned 对 app_id 判定无影响（纯一次冗余 DB query，换 DRY 简洁）。
    visible = await services.list_apps_for_user(
        db=db,
        user_id=user_id,
        http=http,
        cache=cache,
        settings=settings,
    )
    return any(entry.get("id") == app_id for entry in visible)
