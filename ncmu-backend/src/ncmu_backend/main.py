"""FastAPI app entry — lifespan + router auto-discovery + /healthz.

M-2 修订：sub-packages with `routes.py` are auto-included; downstream
TASK-26/27/28 only need to drop a new `ncmu_backend/<mod>/routes.py`
exporting `router = APIRouter()` — no main.py edit required.

C-1 + A-3 修订：the Dify-bound httpx.AsyncClient is initialised in the
lifespan startup handler and stored on `app.state.dify_client`. TASK-27
takes it via `Depends(get_dify_client)`. Module-level singletons would
have surprising lifetimes under uvicorn --reload; binding to app.state
makes ownership explicit.

B-5 + A-2 修订：when DEPLOY_PROFILE=prod and NCMU_ENABLE_DEV_LOGIN=true
the lifespan startup raises RuntimeError so uvicorn marks the worker
fatally broken and exits non-zero. (sys.exit gets swallowed by uvicorn
≤0.23, so we deliberately use raise.)
"""
from __future__ import annotations

import importlib
import logging
import os
import pkgutil
from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
import redis.asyncio as aioredis
from fastapi import FastAPI, Request

import ncmu_backend
from ncmu_backend.config import get_settings
from ncmu_backend.workflow.advanced_chat import AdvancedChatOrchestrator
from ncmu_backend.workflow.agent_chat import AgentChatOrchestrator
from ncmu_backend.workflow.completion import CompletionOrchestrator
from ncmu_backend.workflow.workflow import WorkflowOrchestrator

log = logging.getLogger("ncmu_backend.main")


def get_dify_client(request: Request) -> httpx.AsyncClient:
    """FastAPI dependency: return the lifespan-owned httpx singleton.

    TASK-27's orchestrator does:
        async def stream(... dify: httpx.AsyncClient = Depends(get_dify_client)):
            async with dify.stream("POST", ...) as r: ...
    """
    client = getattr(request.app.state, "dify_client", None)
    if client is None:
        raise RuntimeError(
            "dify_client not initialised — lifespan startup did not run "
            "(or `app` was constructed without lifespan=lifespan)."
        )
    return client


def get_redis(request: Request) -> aioredis.Redis:
    """FastAPI dependency: return the lifespan-owned Redis client.

    TASK-STATESTORE-1 — DingTalk login uses this for single-use CSRF state
    (SETEX nonce on init, GETDEL on callback). Mirrors get_dify_client:
    the client is built once in lifespan startup and stored on
    `app.state.redis`; tests override this dependency (or set app.state.redis)
    with a fakeredis client.
    """
    client = getattr(request.app.state, "redis", None)
    if client is None:
        raise RuntimeError(
            "redis not initialised — lifespan startup did not run "
            "(or `app` was constructed without lifespan=lifespan)."
        )
    return client


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()

    # B-5 + A-2 修订：fail-fast in prod profile if dev-login is on.
    if settings.NCMU_ENABLE_DEV_LOGIN and os.environ.get("DEPLOY_PROFILE") == "prod":
        msg = (
            "REFUSE: NCMU_ENABLE_DEV_LOGIN=true with DEPLOY_PROFILE=prod — "
            "dev login MUST be disabled in production."
        )
        log.critical(msg)
        raise RuntimeError(msg)

    # C-1 + A-3 修订: Dify-bound httpx singleton.
    app.state.dify_client = httpx.AsyncClient(
        timeout=httpx.Timeout(60.0, connect=5.0)
    )

    # TASK-STATESTORE-1: process-singleton Redis client (DB 0) for DingTalk
    # login single-use CSRF state. from_url is lazy (no connect until first
    # command), mirroring the dify_client singleton lifetime via app.state.
    app.state.redis = aioredis.from_url(settings.REDIS_URL)

    # TASK-69 (Phase 2B B1) + TASK-79-BACKEND-ARCH-FIX (2026-05-12):
    # workflow dispatcher 单例。Dispatcher's constructor takes a default
    # DifyStreamClient — its base_url + api_key + timeout become the
    # defaults for the per-App client cache (workflow/mode_dispatcher.py).
    # The default token (chat App's [KB]员工手册 token) is the fallback
    # when dify_apps.api_token IS NULL, so any non-backfilled row still
    # works against the chat App (same behaviour as pre-79).
    from ncmu_backend.workflow.dify_client import DifyStreamClient
    from ncmu_backend.workflow.mode_dispatcher import ModeDispatcher
    app.state.workflow_dispatcher = ModeDispatcher(
        DifyStreamClient(
            base_url=settings.DIFY_BASE_URL,
            api_key=settings.DIFY_APP_DEFAULT_TOKEN,
        )
    )
    # Orchestrators are now stateless event-mapping pipelines — the
    # dispatcher injects the per-App DifyStreamClient via .run() at
    # dispatch time (BaseOrchestrator.run's first arg). No client needed
    # at construction.
    app.state.workflow_dispatcher.register("advanced-chat", AdvancedChatOrchestrator())  # TASK-71
    app.state.workflow_dispatcher.register("completion", CompletionOrchestrator())  # TASK-72
    app.state.workflow_dispatcher.register("workflow", WorkflowOrchestrator())  # TASK-73
    app.state.workflow_dispatcher.register("agent-chat", AgentChatOrchestrator())  # TASK-74

    # M7: boot-sweeper — 把上次 process crash / SSE 断连留下的 status='running'
    # 行兜底为 'timeout'。L-NEW-6 (PLAN-FIX-3): 阈值 15 min 与 spec §5.1 SSE
    # 心跳 timeout (10 min) 不同概念 — 给 SSE timeout 路径 +5 min cushion 避免
    # 误清正常即将 finalize 的 run。lifespan startup 期间执行 1 次；不开 cron。
    from ncmu_backend.db.session import SessionLocal
    from sqlalchemy import text as sql_text
    try:
        async with SessionLocal() as sweep_db:
            result = await sweep_db.execute(sql_text(
                "UPDATE workflow_runs "
                "SET status='timeout', finished_at=NOW(), "
                "    error_msg='boot-sweeper: process restart' "
                "WHERE status='running' AND started_at < NOW() - INTERVAL '15 min'"
            ))
            await sweep_db.commit()
            log.info(
                "workflow_runs boot-sweeper: %d row(s) marked timeout",
                result.rowcount or 0,
            )
    except Exception as exc:
        # boot-sweeper 失败（DB 不可达 / 表不存在）不阻塞 startup —
        # 主路径 chat 不依赖 workflow_runs 表，记 warning 即可。
        log.warning("boot-sweeper skipped: %s", exc)

    log.info(
        "ncmu-backend ready — DEPLOY_PROFILE=%s ENABLE_DEV_LOGIN=%s",
        settings.DEPLOY_PROFILE,
        settings.NCMU_ENABLE_DEV_LOGIN,
    )

    yield

    client = getattr(app.state, "dify_client", None)
    if client is not None:
        await client.aclose()

    # TASK-STATESTORE-1: close the Redis client's connection pool on shutdown.
    redis_client = getattr(app.state, "redis", None)
    if redis_client is not None:
        await redis_client.aclose()

    # REWORK-INDEP-I2: process-singleton FastGPTReadOnlyClient owns an
    # httpx.AsyncClient. Close it once on shutdown so the connection
    # pool drains cleanly (the routes never call aclose per-request —
    # that's the whole point of singletonising).
    try:
        from ncmu_backend.fastgpt_readonly.routes import aclose_fastgpt_client
        await aclose_fastgpt_client()
    except Exception as exc:
        log.warning("FastGPT client shutdown skipped: %s", exc)

    log.info("ncmu-backend shutdown complete")


def _discover_and_include_routers(app: FastAPI) -> list[str]:
    """Scan ncmu_backend.<sub>.routes — include any `router` they export.

    Only first-level subpackages are scanned (depth 1). Module import
    failures are surfaced (not swallowed) so a typo in a routes.py
    doesn't silently drop endpoints.
    """
    included: list[str] = []
    for _finder, modname, ispkg in pkgutil.iter_modules(ncmu_backend.__path__):
        if not ispkg:
            continue
        try:
            mod = importlib.import_module(f"ncmu_backend.{modname}.routes")
        except ModuleNotFoundError as exc:
            # Sub-package without a routes.py — that's normal (e.g. db, schemas).
            if exc.name == f"ncmu_backend.{modname}.routes":
                continue
            raise
        router = getattr(mod, "router", None)
        if router is not None:
            app.include_router(router)
            included.append(modname)
    log.info("router auto-discovery: included %s", included)
    return included


app = FastAPI(
    title="NCMU Backend",
    version="0.1.0",
    description="Phase 1 NCMU backend — Dify orchestration + chat sessions + admin",
    lifespan=lifespan,
)

# Eagerly run discovery at import time so OpenAPI schema is complete
# the moment the worker accepts connections (export-openapi.sh relies
# on this — no warm-up request needed).
_INCLUDED_MODULES = _discover_and_include_routers(app)

# Phase 2C TASK-PC2-A: ``personal_kb.admin_routes`` lives next to
# ``personal_kb.routes`` (which the auto-discovery already picked up).
# The discovery scanner only honours ``routes.py``, so the admin
# router gets a manual include here — single edit, two routers.
from ncmu_backend.personal_kb import admin_routes as _personal_kb_admin_routes
app.include_router(_personal_kb_admin_routes.router)

# Phase 2E TASK-PE-06: ``admin.users.routes`` lives at depth 2
# (``ncmu_backend.admin.users.routes``) — the auto-discovery scanner only
# recurses to depth 1 (``ncmu_backend.<sub>.routes``), so the admin-users
# router is included manually here. Same pattern as personal_kb above.
from ncmu_backend.admin.users import routes as _admin_users_routes
app.include_router(_admin_users_routes.router)
# Phase 2E TASK-PE-05: ``admin.tags.routes`` lives at depth 2
# (``ncmu_backend.admin.tags.routes``) — the auto-discovery scanner only
# recurses ``ncmu_backend.<sub>.routes`` (depth 1) so admin sub-package
# routers ship via the same manual include idiom as PE-06's
# ``admin.users.routes`` (4-way merge handled by Pane 0 at ff-merge time
# when PE-06 lands on main).
from ncmu_backend.admin.tags import routes as _admin_tags_routes
app.include_router(_admin_tags_routes.router)
# TASK-MERGE-B3 route-order (CRITICAL): ``admin.dsl`` MUST be included
# BEFORE ``admin.apps``. PE-10's ``GET /api/v1/ncmu/admin/apps/dsl-candidates``
# is a static path; PE-07's ``GET /api/v1/ncmu/admin/apps/{app_id}`` is a
# parametrised path that matches *any* single segment. FastAPI matches across
# routers in registration order, so if ``admin.apps`` registered first,
# ``{app_id}`` would swallow ``dsl-candidates`` (app_id="dsl-candidates" →
# 1014 not-found). Registering ``admin.dsl`` first lets the literal win.
# (``dsl-export`` is POST and ``{app_id}/dsl`` is 2-segment, so only
# ``dsl-candidates`` is actually at risk — but keep dsl-first for clarity.)
# Covered by tests/admin/test_route_order_b3.py.
#
# Phase 2E TASK-PE-10: ``admin.dsl.routes`` lives at depth 2
# (``ncmu_backend.admin.dsl.routes``) — same manual-include idiom as the
# PE-05/PE-06 admin sub-packages (auto-discovery only recurses depth 1).
from ncmu_backend.admin.dsl import routes as _admin_dsl_routes
app.include_router(_admin_dsl_routes.router)
# Phase 2E TASK-PE-07: ``admin.apps.routes`` lives at depth 2
# (``ncmu_backend.admin.apps.routes``) — same manual-include idiom as the
# PE-05/-06 admin sub-packages above (the auto-discovery scanner only
# recurses depth 1). Included AFTER admin.dsl per the route-order note above.
from ncmu_backend.admin.apps import routes as _admin_apps_routes
app.include_router(_admin_apps_routes.router)
# TASK-MON-1: ``admin.monitoring.routes`` lives at depth 2
# (``ncmu_backend.admin.monitoring.routes``) — same manual-include idiom as
# the PE-05/-06/-07/-10 admin sub-packages above (auto-discovery only
# recurses depth 1). Static path ``/admin/monitoring`` — no param-route
# collision, so registration order is unconstrained.
from ncmu_backend.admin.monitoring import routes as _admin_monitoring_routes
app.include_router(_admin_monitoring_routes.router)


@app.get("/healthz", tags=["meta"])
def healthz() -> dict:
    """Liveness probe used by docker-compose healthcheck + nginx upstream."""
    return {"status": "ok", "modules": _INCLUDED_MODULES}
