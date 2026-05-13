"""按 dify_apps.mode 路由 to orchestrator + per-App Dify client cache.

TASK-79-BACKEND-ARCH-FIX (2026-05-12): the dispatcher now looks up
`dify_apps.api_token` for the requested app_id and hands the matching
cached DifyStreamClient to the orchestrator. Reason: Dify v1.13.3 binds
each API token to one App (`api_tokens.app_id` FK), so a single shared
token can only ever address one App. Without this lookup, every
non-chat mode silently hit the chat `[KB]员工手册` App over
/v1/chat-messages (advanced-chat / agent-chat) or 400-ed on the wrong
endpoint (completion / workflow). NULL `api_token` falls back to
`DIFY_APP_DEFAULT_TOKEN` so the Phase 1 chat App path stays untouched.

Plan §B1 TASK-69 dispatcher字面落地 + PLAN-FIX-3 M-NEW-4 (dispatch 不
再持 db session — superseded by TASK-79: dispatch now needs db to look
up the per-App token; the token lookup is a single SELECT before the
SSE stream begins, so it does NOT extend db-session ownership across
the long-lived event_stream loop) + L-FIX-1 (resolve_mode fallback
已删).
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, AsyncIterator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ncmu_backend.schemas.sse_events import NcmuSseEvent
from ncmu_backend.workflow._base import BaseOrchestrator
from ncmu_backend.workflow.dify_client import DifyStreamClient

log = logging.getLogger("ncmu_backend.workflow.mode_dispatcher")


class ModeDispatcher:
    """单例：注册 4 新 mode orchestrator + 路由 dispatch + per-App client cache.

    chat mode 不在此 dispatcher（仍走 Phase 1 chat orchestrator 路径）。
    本 dispatcher 仅 4 新 mode：advanced-chat / completion / workflow /
    agent-chat。B2 task TASK-71/72/73/74 各 register 一个 orchestrator.
    """

    def __init__(self, default_client: DifyStreamClient):
        """``default_client`` supplies (base_url, default_token, timeout)
        for the per-App client cache. The default token is also the
        fallback when ``dify_apps.api_token IS NULL`` — preserves Phase 1
        chat App backward compatibility for any row that has not been
        backfilled with a per-App token.
        """
        self._default_client = default_client
        # Cache keyed by token. Seeded with the default so the chat App's
        # token resolves to the same client instance the lifespan startup
        # constructed (one less httpx.AsyncClient lifecycle to manage).
        self._client_cache: dict[str, DifyStreamClient] = {
            default_client.api_key: default_client
        }
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

    async def resolve_token(self, db: AsyncSession, app_id: str) -> str:
        """Look up ``dify_apps.api_token`` for ``app_id``.

        Returns the per-App token if set, else falls back to the default
        client's token (Phase 1 chat App back-compat). Unknown ``app_id``
        raises ``ValueError`` to match ``resolve_mode``'s error contract;
        callers in ``routes.py`` already catch this and surface 404.
        """
        from ncmu_backend.db.models import DifyApp

        result = await db.execute(
            select(DifyApp.api_token).where(DifyApp.dify_app_id == app_id)
        )
        row = result.first()
        if row is None:
            raise ValueError(f"app {app_id!r} not found")
        token = row[0]
        if token is None or token == "":
            # IMP-INDEP-1: silent fallback to the default chat App token works
            # for Phase 1 backward compat but masks an admin-sync gap if the
            # row is a new 4-mode App that should have been backfilled with a
            # per-App token. Warn so operations can spot the gap; the
            # behaviour still degrades gracefully (chat App's token is
            # harmless for chat itself, will 401 against a different App's
            # endpoint — which is the visible signal that a backfill is due).
            log.warning(
                "resolve_token: app_id=%s has NULL/empty api_token; falling "
                "back to default chat App token. If this is NOT the Phase 1 "
                "chat App, Boss must backfill: UPDATE dify_apps SET "
                "api_token='<per-App token>' WHERE dify_app_id='%s'",
                app_id, app_id,
            )
            return self._default_client.api_key
        return token

    def get_client(self, token: str) -> DifyStreamClient:
        """Return the cached DifyStreamClient for ``token`` (create + cache
        on miss). Each constructed client reuses the default's
        ``base_url`` + ``timeout`` — only the bearer token varies per App.
        """
        cached = self._client_cache.get(token)
        if cached is not None:
            return cached
        client = DifyStreamClient(
            base_url=self._default_client.base_url,
            api_key=token,
            timeout=self._default_client.timeout,
        )
        self._client_cache[token] = client
        return client

    async def dispatch(
        self,
        db: AsyncSession,
        mode: str,
        run_id: uuid.UUID,
        app_id: str,
        user_id: uuid.UUID,
        inputs: dict[str, Any],
    ) -> AsyncIterator[NcmuSseEvent]:
        """按 mode 路由到对应 orchestrator + 注入 per-App client。

        TASK-79-ARCH-FIX: ``db`` is needed once at the top to resolve the
        per-App token; after the token is resolved + client cached, the
        orchestrator iterates the upstream SSE stream without touching
        ``db`` again — so the long-lived event loop does not extend
        request-scoped session ownership.
        """
        if mode == "chat":
            raise ValueError(
                "mode=chat must use chat orchestrator path, not workflow dispatcher"
            )
        if mode not in self._registry:
            raise KeyError(f"mode {mode!r} not registered (B2 task pending)")
        token = await self.resolve_token(db, app_id)
        dify_client = self.get_client(token)
        orchestrator = self._registry[mode]
        async for event in orchestrator.run(
            dify_client, run_id, app_id, user_id, inputs
        ):
            yield event
