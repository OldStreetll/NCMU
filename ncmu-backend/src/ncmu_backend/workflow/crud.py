"""workflow_runs CRUD (TASK-69 / Phase 2B B1). 所有写操作即时 commit.

Plan §B1 TASK-69 crud.py 字面落地 + PLAN-FIX-2 H5/M6 + L9 RunStatus
Literal:

- M6: bindparam(rid, type_=PG_UUID) + bindparam(evt, type_=String) 防
  asyncpg 隐式 cast 失败 (InvalidTextRepresentation).
- H5: finalize_run 仅当 status='running' 才写终态行，保护两条竞态：
    (a) 用户 DELETE /runs/{id} 写 'cancelled' 后 workflow_finished 不再
        把 'cancelled' 抹回 'succeeded'。
    (b) crud.append_node_event 异常先 'db_error' 后，event_stream finally
        块的 'failed' 不会再覆盖 'db_error'。
- L9 RunStatus Literal: finalize_run 入参类型仅 7 终态（不含 'running'）；
  DB workflow_runs CHECK 约束 8 值（含 'running'），design 正确不冲突。
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Literal

from sqlalchemy import String, bindparam, desc, func, select, text, update
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession

from ncmu_backend.db.models import WorkflowRun


RunStatus = Literal[
    "succeeded", "failed", "stopped", "exception",
    "timeout", "cancelled", "db_error",
]


async def create_run(
    db: AsyncSession,
    run_id: uuid.UUID,
    app_id: str,
    user_id: uuid.UUID,
    mode: str,
    inputs: dict[str, Any],
) -> None:
    run = WorkflowRun(
        run_id=run_id,
        app_id=app_id,
        user_id=user_id,
        mode=mode,
        inputs=inputs,
        outputs={},
        status="running",
        node_trace=[],
    )
    db.add(run)
    await db.commit()


async def append_node_event(
    db: AsyncSession,
    run_id: uuid.UUID,
    event_dict: dict[str, Any],
) -> None:
    """append 单 NCMU SSE event 到 node_trace JSONB 数组末尾。

    使用 PostgreSQL ``jsonb || jsonb`` operator 原子追加（单 SQL UPDATE，
    避免 read-modify-write 竞态）。event_dict 通常是
    ``NcmuSseEvent.model_dump(mode='json')``。

    M6: bindparam 显式声明 ``rid`` 类型为 PG_UUID + ``evt`` 类型为 String，
    避免 asyncpg 隐式 cast 失败。
    """
    stmt = text(
        "UPDATE workflow_runs "
        "SET node_trace = node_trace || CAST(:evt AS jsonb) "
        "WHERE run_id = :rid"
    ).bindparams(
        bindparam("rid", type_=PG_UUID(as_uuid=True)),
        bindparam("evt", type_=String),
    )
    await db.execute(stmt, {"evt": json.dumps([event_dict]), "rid": run_id})
    await db.commit()


async def finalize_run(
    db: AsyncSession,
    run_id: uuid.UUID,
    status: RunStatus,
    outputs: dict[str, Any] | None = None,
    error_msg: str | None = None,
) -> None:
    """run 终态写入：status / outputs / finished_at / error_msg。

    H5: 仅当当前行 ``status='running'`` 才更新；终态行不再被覆盖。返回值
    None；调用方不感知是否真正命中（fire-and-forget）。若需感知，先 SELECT。
    """
    values: dict[str, Any] = {
        "status": status,
        "finished_at": func.now(),
    }
    if outputs is not None:
        values["outputs"] = outputs
    if error_msg is not None:
        values["error_msg"] = error_msg

    await db.execute(
        update(WorkflowRun)
        .where(WorkflowRun.run_id == run_id)
        .where(WorkflowRun.status == "running")
        .values(**values)
    )
    await db.commit()


async def list_runs(
    db: AsyncSession,
    app_id: str,
    user_id: uuid.UUID,
    limit: int = 50,
    offset: int = 0,
) -> list[WorkflowRun]:
    """返回 run 元数据（不含 node_trace JSONB，性能考虑）。

    按 ix_workflow_runs_app_user_started index 排序：started_at DESC。
    详情接口（GET /runs/{id}）才返回完整 node_trace。
    """
    result = await db.execute(
        select(WorkflowRun)
        .where(
            WorkflowRun.app_id == app_id,
            WorkflowRun.user_id == user_id,
        )
        .order_by(desc(WorkflowRun.started_at))
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())
