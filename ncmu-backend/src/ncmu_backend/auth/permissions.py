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
