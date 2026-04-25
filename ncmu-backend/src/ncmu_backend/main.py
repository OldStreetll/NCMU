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
from fastapi import FastAPI, Request

import ncmu_backend
from ncmu_backend.config import get_settings

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
    log.info(
        "ncmu-backend ready — DEPLOY_PROFILE=%s ENABLE_DEV_LOGIN=%s",
        settings.DEPLOY_PROFILE,
        settings.NCMU_ENABLE_DEV_LOGIN,
    )

    yield

    client = getattr(app.state, "dify_client", None)
    if client is not None:
        await client.aclose()
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


@app.get("/healthz", tags=["meta"])
def healthz() -> dict:
    """Liveness probe used by docker-compose healthcheck + nginx upstream."""
    return {"status": "ok", "modules": _INCLUDED_MODULES}
