"""Phase 2B workflow routes (TASK-69) — 仅 4 新 mode (chat 仍用 Phase 1 path).

Plan §B1 TASK-69 routes.py 字面落地 + PLAN-FIX-4 H-FRESH-2 (asyncio.
CancelledError 显式分支 → finalize 'cancelled' + raise) + PLAN-FIX-2 M5
(event_stream 每事件独立 session) + L1 (yield 一次序列化).

字面偏差 vs plan (与 mode_dispatcher.py 同型):
- ``AsyncSessionLocal`` → ``SessionLocal`` (db/session.py:48 实名)
- ``get_current_user_id`` → ``get_current_user`` returning ``CurrentUser``;
  derive ``uuid.UUID(user.sub)`` for crud / DB queries (auth/deps.py:65).
- ``sse_starlette.EventSourceResponse`` → ``fastapi.StreamingResponse`` +
  ``chat/sse.py:encode_sse_frame`` baseline pattern. Reasons:
    1. ``sse-starlette`` is not declared in ``pyproject.toml`` and not
       installed in the container — adding a new runtime dep is out of
       scope for TASK-69 (file range covers no pyproject change).
    2. Phase 1 chat module already established ``StreamingResponse`` +
       ``encode_sse_frame`` as the project SSE pattern (chat/routes.py:35,
       chat/sse.py:24); reusing it keeps the codebase coherent and avoids
       a one-task dep import.
    3. The H-FRESH-2 ``asyncio.CancelledError`` semantics come from
       FastAPI/Starlette cancelling the underlying ASGI coroutine when
       the client disconnects — identical behaviour to ``sse_starlette``
       internals (see chat/orchestrator.py:226+258 baseline confirmation).
"""
from __future__ import annotations

import asyncio
import json
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ncmu_backend.apps.dify_console_client import DifyConsoleClient
from ncmu_backend.apps.routes import get_dify_console_client
from ncmu_backend.auth.deps import CurrentUser, get_current_user
from ncmu_backend.auth.permissions import user_can_access_app
from ncmu_backend.chat.sse import encode_sse_frame
from ncmu_backend.config import Settings
from ncmu_backend.db.models import WorkflowRun
from ncmu_backend.db.session import SessionLocal, get_db
from ncmu_backend.deps import get_settings
from ncmu_backend.main import get_dify_client
from ncmu_backend.schemas.workflow import (
    WorkflowRunDetail,
    WorkflowRunRequest,
    WorkflowRunSummary,
)
from ncmu_backend.workflow import crud
from ncmu_backend.workflow.mode_dispatcher import ModeDispatcher

router = APIRouter(prefix="/api/v1/ncmu/workflow", tags=["workflow"])


def get_dispatcher(request: Request) -> ModeDispatcher:
    """从 app.state 取 startup hook 注册的全局 dispatcher 单例。"""
    dispatcher = getattr(request.app.state, "workflow_dispatcher", None)
    if dispatcher is None:
        raise RuntimeError(
            "workflow_dispatcher not initialised — lifespan startup did not run"
        )
    return dispatcher


@router.post("/apps/{app_id}/run")
async def run_workflow(
    app_id: str,
    body: WorkflowRunRequest,
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    http: httpx.AsyncClient = Depends(get_dify_client),
    cache: DifyConsoleClient = Depends(get_dify_console_client),
    db: AsyncSession = Depends(get_db),
    dispatcher: ModeDispatcher = Depends(get_dispatcher),
):
    """创建 run + 调 dispatcher.dispatch + 推 NCMU 统一 SSE 事件流。

    B2 注册前若 mode='advanced-chat' 等会抛 KeyError → 下方 except 捕获 →
    SSE error event code=503。

    TASK-BUG-5 — gated by ``auth.permissions.user_can_access_app`` (owner
    OR shared via DifyConsoleClient cache) so a logged-in user cannot
    trigger workflow runs against apps they aren't entitled to. Same shape
    as apps/routes.py:78 and fastgpt_readonly/routes.py:111; 403 + code 1002.
    """
    allowed = await user_can_access_app(
        db=db,
        user_id=user.sub,
        app_id=app_id,
        http=http,
        cache=cache,
        settings=settings,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": 1002, "message": "App not accessible by current user"},
        )

    run_id = uuid.uuid4()
    user_id = uuid.UUID(user.sub)

    try:
        mode = await dispatcher.resolve_mode(db, app_id)
    except ValueError as e:
        raise HTTPException(404, str(e))

    if mode == "chat":
        raise HTTPException(
            400, "mode=chat must use /chat endpoint, not workflow"
        )

    inputs = body.inputs or {}
    await crud.create_run(db, run_id, app_id, user_id, mode, inputs)

    # M5: event_stream 内每事件独立 session（避免 60+ commit 长占连接池）。
    async def event_stream():
        try:
            async for evt in dispatcher.dispatch(
                db, mode, run_id, app_id, user_id, inputs
            ):
                evt_dict = evt.model_dump(mode="json")
                # 先 append DB（spec §4.4 时序保证），失败标 db_error 不阻塞推送
                try:
                    async with SessionLocal() as ev_db:
                        await crud.append_node_event(ev_db, run_id, evt_dict)
                except Exception:
                    async with SessionLocal() as fail_db:
                        await crud.finalize_run(
                            fail_db, run_id, "db_error",
                            error_msg="append failed",
                        )
                # 推 frontend (chat baseline encode_sse_frame; bytes for
                # StreamingResponse direct yield).
                yield encode_sse_frame(evt.event_type, evt_dict)
                # workflow_finished 触发 finalize（独立 session）
                if evt.event_type == "workflow_finished":
                    fin_data = evt_dict.get("data", {})
                    if isinstance(fin_data, dict):
                        async with SessionLocal() as fin_db:
                            await crud.finalize_run(
                                fin_db, run_id,
                                fin_data.get("status", "succeeded"),
                                outputs=fin_data.get("outputs"),
                                error_msg=fin_data.get("error"),
                            )
        except KeyError as e:
            async with SessionLocal() as err_db:
                await crud.finalize_run(
                    err_db, run_id, "failed",
                    error_msg=f"mode not registered: {e}",
                )
            yield encode_sse_frame(
                "error", {"code": 503, "message": str(e)}
            )
        except asyncio.CancelledError:
            # H-FRESH-2 (PLAN-FIX-4): client 断连。Starlette athrow(
            # CancelledError) 中断 generator；CancelledError inherits
            # BaseException (非 Exception)，既有 except Exception 漏接 →
            # finalize_run 不被调 → 行 status='running' 滞留。显式 finalize
            # 'cancelled' + 重 raise（与 chat/orchestrator.py:226+258 一致）。
            async with SessionLocal() as cancel_db:
                await crud.finalize_run(
                    cancel_db, run_id, "cancelled",
                    error_msg="client disconnect",
                )
            raise
        except Exception as e:
            async with SessionLocal() as err_db:
                await crud.finalize_run(
                    err_db, run_id, "failed", error_msg=str(e),
                )
            yield encode_sse_frame(
                "error", {"code": 500, "message": str(e)}
            )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/apps/{app_id}/runs", response_model=list[WorkflowRunSummary])
async def list_runs(
    app_id: str,
    limit: int = 50,
    offset: int = 0,
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    http: httpx.AsyncClient = Depends(get_dify_client),
    cache: DifyConsoleClient = Depends(get_dify_console_client),
    db: AsyncSession = Depends(get_db),
):
    """List runs for ``app_id``, gated by ``auth.permissions.user_can_access_app``.

    B-NEW-NEW-A — symmetric with POST /run (line 89-101); 403 + code 1002
    when the caller is neither owner nor shared. Without this gate, the
    existing ``user_id`` filter inside ``crud.list_runs`` would leak
    ``app_id`` existence (200 + empty array indistinguishable from
    "exists but no runs of mine") — the gate enforces the same
    contract as the other 3 entitled endpoints (apps/routes.py:78,
    fastgpt_readonly/routes.py:111, workflow/routes.py:89).
    """
    allowed = await user_can_access_app(
        db=db,
        user_id=user.sub,
        app_id=app_id,
        http=http,
        cache=cache,
        settings=settings,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": 1002, "message": "App not accessible by current user"},
        )

    user_id = uuid.UUID(user.sub)
    runs = await crud.list_runs(db, app_id, user_id, limit=limit, offset=offset)
    return [
        WorkflowRunSummary(
            run_id=r.run_id, app_id=r.app_id, mode=r.mode, status=r.status,
            started_at=r.started_at, finished_at=r.finished_at,
        )
        for r in runs
    ]


@router.get("/runs/{run_id}", response_model=WorkflowRunDetail)
async def get_run(
    run_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user_id = uuid.UUID(user.sub)
    result = await db.execute(
        select(WorkflowRun).where(WorkflowRun.run_id == run_id)
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(404, "run not found")
    if run.user_id != user_id:
        raise HTTPException(403, "not your run")
    return WorkflowRunDetail(
        run_id=run.run_id, app_id=run.app_id, mode=run.mode, status=run.status,
        inputs=run.inputs, outputs=run.outputs, node_trace=run.node_trace,
        started_at=run.started_at, finished_at=run.finished_at,
        error_msg=run.error_msg,
    )


@router.delete("/runs/{run_id}", status_code=204)
async def cancel_run(
    run_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """软关流 — 仅 finalize status='cancelled'。Dify 侧任务可能仍跑（v1.13.3
    无 abort API）。H5 guard 保证仅当 run 仍 'running' 时才写 'cancelled'。"""
    user_id = uuid.UUID(user.sub)
    result = await db.execute(
        select(WorkflowRun).where(WorkflowRun.run_id == run_id)
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(404, "run not found")
    if run.user_id != user_id:
        raise HTTPException(403, "not your run")
    await crud.finalize_run(db, run_id, "cancelled")
