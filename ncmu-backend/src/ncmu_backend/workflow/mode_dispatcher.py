"""按 dify_apps.mode 路由 to orchestrator (TASK-69 / Phase 2B B1).

Plan §B1 TASK-69 mode_dispatcher.py 字面落地 + PLAN-FIX-3 M-NEW-4
(dispatch 不再持 db session) + L-FIX-1 (resolve_mode fallback 已删).

Note (TASK-69 字面偏差 vs plan): plan main.py lifespan example calls
``DifyStreamClient(app.state.dify_client)``; the TASK-68-committed
signature is ``DifyStreamClient(base_url, api_key)``. NIT-INDEP68-2 已
flag this for TASK-71 sweep. The dispatcher only stores the reference
for B2 orchestrators to consume; ``dispatch`` itself never calls
``self._dify``, so the typing here is informational.
"""
from __future__ import annotations

import uuid
from typing import Any, AsyncIterator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ncmu_backend.schemas.sse_events import NcmuSseEvent
from ncmu_backend.workflow._base import BaseOrchestrator
from ncmu_backend.workflow.dify_client import DifyStreamClient


class ModeDispatcher:
    """单例：注册 4 新 mode orchestrator + 路由 dispatch。

    chat mode 不在此 dispatcher（仍走 Phase 1 chat orchestrator 路径）。
    本 dispatcher 仅 4 新 mode：advanced-chat / completion / workflow /
    agent-chat。B2 task TASK-71/72/73/74 各 register 一个 orchestrator。
    """

    def __init__(self, dify_client: DifyStreamClient):
        self._dify = dify_client
        self._registry: dict[str, BaseOrchestrator] = {}

    def register(self, mode: str, orchestrator: BaseOrchestrator) -> None:
        if mode in self._registry:
            raise ValueError(f"mode {mode!r} already registered")
        self._registry[mode] = orchestrator

    async def resolve_mode(self, db: AsyncSession, app_id: str) -> str:
        """按 dify_apps.mode 解析 mode。

        L-FIX-1 (PLAN-FIX-2): H3 fallback 已删；mode 列 server_default='chat'
        兜底，正常路径下 mode 永不为 NULL/空字符串。raise 仅作 sanity check —
        admin /sync_apps 完整性应保证 mode 列被填；触发 raise 即 admin sync
        链路有 bug。
        """
        from ncmu_backend.db.models import DifyApp

        result = await db.execute(
            select(DifyApp.mode).where(DifyApp.dify_app_id == app_id)
        )
        row = result.first()
        if row is None:
            raise ValueError(f"app {app_id!r} not found")
        mode = row[0]
        if mode and mode != "":
            return mode
        raise RuntimeError(
            f"app {app_id!r} has empty mode column — admin /sync_apps may have "
            "failed; check sync flow (TASK-67b: admin sync upsert 需写 mode 字段)"
        )

    async def dispatch(
        self,
        mode: str,
        run_id: uuid.UUID,
        app_id: str,
        user_id: uuid.UUID,
        inputs: dict[str, Any],
    ) -> AsyncIterator[NcmuSseEvent]:
        """按 mode 路由到对应 orchestrator。

        M-NEW-4 修订 (PLAN-FIX-3): dispatch 不再调 resolve_mode；调用方在
        routes handler 提前用请求级 db session 解析；本方法纯路由 + 透传，
        不持 db。这避免长 SSE 流期间请求级 db 占用连接池（与 M5 完全闭环）。
        """
        if mode == "chat":
            raise ValueError(
                "mode=chat must use chat orchestrator path, not workflow dispatcher"
            )
        if mode not in self._registry:
            raise KeyError(f"mode {mode!r} not registered (B2 task pending)")
        orchestrator = self._registry[mode]
        async for event in orchestrator.run(run_id, app_id, user_id, inputs):
            yield event
