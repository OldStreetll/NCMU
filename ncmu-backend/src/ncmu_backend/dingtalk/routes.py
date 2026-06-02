"""DingTalk admin 路由 — TASK-2DA2S-03（无 SPA UI，本批 curl/test 触发）。

URL surface（沿用 ``/api/v1/ncmu/admin`` 既有约定）：

    GET  /api/v1/ncmu/admin/dingtalk/departments   list_departments(root=1) 发现 dept_id 用
    POST /api/v1/ncmu/admin/dingtalk/sync          触发同步 → SyncResult JSON

两条都 ``Depends(require_admin)`` 门禁（被 ``tests/admin/test_require_admin_audit.py``
自动巡检）。错误信封 ``{"detail": {"code": <int>, "message": "..."}}`` 与
``admin/users/routes.py`` 一致。本模块用错误码 **1311**（上游 errcode≠0）/ **1302**
（缺 root_dept_id）—— 13xx 段**非排他**：``sessions/routes.py`` 已占 1300/1301，故
本模块避开 1300/1301（INDEP Minor#1 / Boss 拍）。

**main.py 不需手动 include**：``dingtalk`` 是 depth-1 子包，``main.py`` 的
``_discover_and_include_routers`` 已按 ``ncmu_backend.<sub>.routes`` 自动挂载本
router（参 main.py 顶部 docstring「drop a new routes.py — no main.py edit required」）。
手动再 include 会 double-register。
"""
from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from sqlalchemy.ext.asyncio import AsyncSession

from ncmu_backend.auth.deps import CurrentUser, require_admin
from ncmu_backend.config import Settings, get_settings
from ncmu_backend.db.session import get_db
from ncmu_backend.dingtalk.client import (
    DingTalkApiError,
    get_access_token,
    list_departments,
)
from ncmu_backend.dingtalk.sync_service import SyncResult, sync_contacts

router = APIRouter(tags=["admin-dingtalk"])

# 同步是 admin 触发的低频后台动作；每请求开一个独立 httpx.AsyncClient（token 缓存
# 是 client.py 进程级 module global，跨 client 实例复用，不受此影响）。
_DINGTALK_TIMEOUT = httpx.Timeout(30.0, connect=5.0)


class SyncRequest(BaseModel):
    """POST /sync body。``root_dept_id`` 缺省读 settings.dingtalk_sync_root_dept_id。"""

    root_dept_id: int | None = None


def _api_error(exc: DingTalkApiError) -> HTTPException:
    # 1311 — 钉钉 oapi 上游 errcode≠0（502，区分本服务自身 4xx）。
    # 1301 已被 sessions/routes.py 占用（invalid cursor）→ 避开消歧（INDEP Minor#1）。
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail={
            "code": 1311,
            "message": f"dingtalk api error {exc.errcode}: {exc.errmsg}",
        },
    )


def _missing_root() -> HTTPException:
    # 1302 — body 未给 root_dept_id 且 DINGTALK_SYNC_ROOT_DEPT_ID 未配。
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={
            "code": 1302,
            "message": "root_dept_id not provided and DINGTALK_SYNC_ROOT_DEPT_ID unset",
        },
    )


@router.get(
    "/api/v1/ncmu/admin/dingtalk/departments",
    summary="发现钉钉部门树（root=1）— 一次性辅助，用于查「软件开发部」dept_id",
)
async def discover_departments(
    _: CurrentUser = Depends(require_admin),
    settings: Settings = Depends(get_settings),
) -> dict:
    """从钉钉根（dept_id=1）拉全部门树，返回扁平 ``[{dept_id,name,parent_id}]``。

    仅供 admin 发现「软件开发部」dept_id 后填入 ``DINGTALK_SYNC_ROOT_DEPT_ID``；
    不写 DB。"""
    async with httpx.AsyncClient(timeout=_DINGTALK_TIMEOUT) as http:
        try:
            token = await get_access_token(http, settings)
            depts = await list_departments(http, settings, token, root_dept_id=1)
        except DingTalkApiError as exc:
            raise _api_error(exc)
    return {"departments": depts}


@router.post(
    "/api/v1/ncmu/admin/dingtalk/sync",
    response_model=SyncResult,
    summary="触发软件开发部子树通讯录同步（建/更 NCMU user + 打 user_tags）",
)
async def trigger_sync(
    body: SyncRequest | None = None,
    _: CurrentUser = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db),
) -> SyncResult:
    """编排同步：``body.root_dept_id`` 缺省回落 ``settings.dingtalk_sync_root_dept_id``；
    两者皆空 → 400。钉钉上游 errcode≠0 → 502。"""
    requested = body.root_dept_id if body is not None else None
    root_dept_id = (
        requested if requested is not None else settings.dingtalk_sync_root_dept_id
    )
    if root_dept_id is None:
        raise _missing_root()
    async with httpx.AsyncClient(timeout=_DINGTALK_TIMEOUT) as http:
        try:
            return await sync_contacts(db, http, settings, root_dept_id)
        except DingTalkApiError as exc:
            raise _api_error(exc)
