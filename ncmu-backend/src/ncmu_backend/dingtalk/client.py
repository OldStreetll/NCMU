"""DingTalk oapi client — TASK-2DA2S-01.

封装钉钉开放平台三能力（计划 2026-06-01-phase2d-a2-sync-plan.md 顶部「钉钉 API」）：

- ``get_access_token``：``GET {oapi_base}/gettoken?appkey=&appsecret=`` →
  ``{errcode, access_token, expires_in}``。token 有效期 7200s，**进程内缓存
  复用**（提前 300s 刷新），并发用 ``asyncio.Lock`` 串行化首次/过期换取，避免
  惊群多次换 token。
- ``list_departments``：递归 ``POST {oapi_base}/topapi/v2/department/listsub``
  （body ``{"dept_id": N}`` / 根 dept_id=1）→ ``{result: [{dept_id,name,parent_id}]}``，
  扁平返回全子树。``visited`` set 防环。
- ``list_department_users``：分页 ``POST {oapi_base}/topapi/v2/user/list``
  （body ``{"dept_id","cursor","size":100}``）→
  ``{result: {list:[{userid,name,...}], has_more, next_cursor}}``，按 has_more
  聚合，只取同步需要的 ``{userid, name}``。

钉钉 oapi 约定：HTTP 200 + body ``errcode``（0=成功）。非 0 由
``_check_errcode`` 统一抛 :class:`DingTalkApiError`。

测试纯 respx mock（权限生效前不依赖真钉钉）；``reset_token_cache`` 供测试
清进程级缓存，勿在生产路径调用。
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from ncmu_backend.config import Settings


# 提前刷新窗口：token 实际有效期减去这么多秒后即视为过期（防边界 race）。
_TOKEN_REFRESH_SKEW_S = 300
# expires_in 缺省兜底（钉钉文档 7200s）。
_DEFAULT_EXPIRES_IN_S = 7200
# user/list 单页大小（钉钉上限 100）。
_USER_LIST_PAGE_SIZE = 100


class DingTalkApiError(Exception):
    """钉钉 oapi 返回 errcode≠0。携带 errcode/errmsg 供 caller 分类处置。"""

    def __init__(self, errcode: int, errmsg: str) -> None:
        self.errcode = errcode
        self.errmsg = errmsg
        super().__init__(f"DingTalk oapi error errcode={errcode}: {errmsg}")


def _check_errcode(payload: dict[str, Any]) -> dict[str, Any]:
    """统一 errcode 检查：非 0 抛 DingTalkApiError，否则原样返回 payload。"""
    errcode = payload.get("errcode", 0)
    if errcode != 0:
        raise DingTalkApiError(errcode, payload.get("errmsg", ""))
    return payload


# --------------------------------------------------------------------- #
# access_token —— 进程内缓存 + asyncio.Lock 并发安全
# --------------------------------------------------------------------- #
# 进程级单 token 缓存（同一企业内部应用全局共享一个 access_token，钉钉侧
# 也是单 token 语义）。expires_at 用 time.monotonic()（不受系统时钟回拨影响）。
_token_cache: dict[str, Any] = {"token": None, "expires_at": 0.0}
_token_lock = asyncio.Lock()


def reset_token_cache() -> None:
    """清空进程级 token 缓存。仅测试用（隔离 mock HTTP 计数），勿入生产路径。"""
    _token_cache["token"] = None
    _token_cache["expires_at"] = 0.0


def _cached_token_valid() -> bool:
    return bool(_token_cache["token"]) and time.monotonic() < _token_cache["expires_at"]


async def get_access_token(
    http: httpx.AsyncClient, settings: Settings,
) -> str:
    """换取（或复用缓存的）钉钉 access_token。

    缓存命中（未到提前刷新窗口）直接返回；否则在 ``_token_lock`` 内换取，
    并发首次/过期刷新只触发 1 次底层 HTTP（double-check locking）。
    """
    if _cached_token_valid():
        return _token_cache["token"]
    async with _token_lock:
        # 锁内二次检查：等锁期间可能已被别的协程刷新。
        if _cached_token_valid():
            return _token_cache["token"]
        resp = await http.get(
            f"{settings.dingtalk_oapi_base}/gettoken",
            params={
                "appkey": settings.dingtalk_app_key,
                "appsecret": settings.dingtalk_app_secret,
            },
        )
        payload = _check_errcode(resp.json())
        token = payload["access_token"]
        expires_in = payload.get("expires_in", _DEFAULT_EXPIRES_IN_S)
        _token_cache["token"] = token
        _token_cache["expires_at"] = (
            time.monotonic() + max(0, expires_in - _TOKEN_REFRESH_SKEW_S)
        )
        return token


# --------------------------------------------------------------------- #
# 部门 —— 递归拉全子树
# --------------------------------------------------------------------- #
async def list_departments(
    http: httpx.AsyncClient,
    settings: Settings,
    token: str,
    root_dept_id: int = 1,
) -> list[dict[str, Any]]:
    """递归 listsub 拉 ``root_dept_id`` 子树，扁平返回 ``[{dept_id,name,parent_id}]``。

    listsub 返回的是「直接子部门」，故从 root 逐层下钻；root 本身不在结果中
    （与 listsub 语义一致 / 含根可选——本实现不含）。``visited`` set 防成环
    （钉钉部门树理应无环，防御性）。
    """
    flat: list[dict[str, Any]] = []
    visited: set[int] = set()

    async def _recurse(dept_id: int) -> None:
        if dept_id in visited:
            return
        visited.add(dept_id)
        resp = await http.post(
            f"{settings.dingtalk_oapi_base}/topapi/v2/department/listsub",
            params={"access_token": token},
            json={"dept_id": dept_id},
        )
        payload = _check_errcode(resp.json())
        for d in payload.get("result", []) or []:
            child_id = d.get("dept_id")
            flat.append({
                "dept_id": child_id,
                "name": d.get("name"),
                "parent_id": d.get("parent_id"),
            })
            if child_id is not None:
                await _recurse(child_id)

    await _recurse(root_dept_id)
    return flat


# --------------------------------------------------------------------- #
# 成员 —— 分页聚合
# --------------------------------------------------------------------- #
async def list_department_users(
    http: httpx.AsyncClient,
    settings: Settings,
    token: str,
    dept_id: int,
) -> list[dict[str, Any]]:
    """分页 user/list 聚合 ``dept_id`` 直属成员，返回 ``[{userid, name}]``。

    按 ``has_more``/``next_cursor`` 翻页（cursor 从 0 起），顺序保持钉钉返回
    顺序。只取同步需要的 ``userid``/``name`` 两字段。
    """
    users: list[dict[str, Any]] = []
    cursor = 0
    while True:
        resp = await http.post(
            f"{settings.dingtalk_oapi_base}/topapi/v2/user/list",
            params={"access_token": token},
            json={"dept_id": dept_id, "cursor": cursor, "size": _USER_LIST_PAGE_SIZE},
        )
        result = _check_errcode(resp.json()).get("result", {}) or {}
        for u in result.get("list", []) or []:
            users.append({"userid": u.get("userid"), "name": u.get("name")})
        if result.get("has_more"):
            cursor = result.get("next_cursor", cursor)
        else:
            break
    return users


# --------------------------------------------------------------------- #
# OAuth2 扫码登录 —— TASK-LOGIN-1
# --------------------------------------------------------------------- #
# 登录链路用钉钉**新版 v1.0 API**（api.dingtalk.com / oauth_api_base），与同步
# 半的 oapi（oapi.dingtalk.com，errcode≠0 风格）不同：v1.0 成功返 2xx + JSON，
# 失败返 **HTTP 非 2xx** + body ``{"code": "<str>", "message": "..."}``（无
# errcode 字段）。故这两步用 :func:`_check_v1_response` 按 HTTP status 判定，失败
# 统一抛 :class:`DingTalkApiError`（spec §3「任何链路失败 → 拒绝，复用
# DingTalkApiError」），errcode 字段填 HTTP status 便于 caller 归因。
# 第三步 getbyunionid 仍是老 oapi（errcode 风格），复用 :func:`_check_errcode`。


def _check_v1_response(resp: httpx.Response) -> dict[str, Any]:
    """v1.0 API（api.dingtalk.com）统一错误检查。

    HTTP 非 2xx → 抛 DingTalkApiError(errcode=status, errmsg=body.code/message)；
    2xx → 返回解析后的 JSON dict。绝不静默放行非 2xx。
    """
    if resp.is_success:
        return resp.json()
    # 失败体可能不是 JSON（网关层错误等），防御性解析。
    try:
        body = resp.json()
        detail = body.get("message") or body.get("code") or resp.text
    except Exception:
        detail = resp.text
    raise DingTalkApiError(resp.status_code, f"dingtalk v1.0 api: {detail}")


async def exchange_user_token(
    http: httpx.AsyncClient, settings: Settings, code: str,
) -> str:
    """① code → userAccessToken（用户身份 token，非企业 token）。

    ``POST {oauth_api_base}/v1.0/oauth2/userAccessToken``
    body ``{clientId, clientSecret, code, grantType:"authorization_code"}``
    → ``{accessToken, expireIn, ...}``。失败抛 DingTalkApiError。
    """
    resp = await http.post(
        f"{settings.dingtalk_oauth_api_base}/v1.0/oauth2/userAccessToken",
        json={
            "clientId": settings.dingtalk_app_key,
            "clientSecret": settings.dingtalk_app_secret,
            "code": code,
            "grantType": "authorization_code",
        },
    )
    payload = _check_v1_response(resp)
    token = payload.get("accessToken")
    if not token:
        raise DingTalkApiError(0, "userAccessToken response missing accessToken")
    return token


async def get_login_userinfo(
    http: httpx.AsyncClient, settings: Settings, user_token: str,
) -> str:
    """② userAccessToken → 当前登录用户 unionId。

    ``GET {oauth_api_base}/v1.0/contact/users/me`` header
    ``x-acs-dingtalk-access-token: {user_token}`` → ``{unionId, openId, nick}``。
    返回 ``unionId``。失败 / 缺 unionId 抛 DingTalkApiError（绝不返回空冒充）。
    """
    resp = await http.get(
        f"{settings.dingtalk_oauth_api_base}/v1.0/contact/users/me",
        headers={"x-acs-dingtalk-access-token": user_token},
    )
    payload = _check_v1_response(resp)
    union_id = payload.get("unionId")
    if not union_id:
        raise DingTalkApiError(0, "contact/users/me response missing unionId")
    return union_id


async def get_userid_by_unionid(
    http: httpx.AsyncClient, settings: Settings, corp_token: str, unionid: str,
) -> str:
    """③ unionId → 企业 userid（用企业 access_token，老 oapi errcode 风格）。

    ``POST {oapi_base}/topapi/user/getbyunionid?access_token={corp_token}``
    body ``{"unionid": unionid}`` → ``{errcode, result:{userid}}``。
    errcode≠0 由 _check_errcode 抛 DingTalkApiError；缺 userid 亦抛（绝不放行）。
    """
    resp = await http.post(
        f"{settings.dingtalk_oapi_base}/topapi/user/getbyunionid",
        params={"access_token": corp_token},
        json={"unionid": unionid},
    )
    payload = _check_errcode(resp.json())
    userid = (payload.get("result") or {}).get("userid")
    if not userid:
        raise DingTalkApiError(0, "getbyunionid response missing result.userid")
    return userid
