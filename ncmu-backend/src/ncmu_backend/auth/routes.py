"""Dev-login + dev-users routes.

Both endpoints raise HTTP 404 when NCMU_ENABLE_DEV_LOGIN is false (per
AC#3 C-3 revision: keep the routes registered so the OpenAPI doc shows
them, but reject at runtime). This makes the prod-mode behaviour visible
to operators (404, not "endpoint not found in router") and keeps the
test surface stable across profiles.
"""
from __future__ import annotations

import logging

import httpx
from fastapi import (
    APIRouter,
    Cookie,
    Depends,
    HTTPException,
    Response,
    status,
)
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ncmu_backend.auth.dingtalk_login import (
    LOGIN_STATE_COOKIE,
    LOGIN_STATE_TTL_S,
    DingTalkLoginDenied,
    build_authorize_url,
    generate_login_state,
    login_via_dingtalk,
    verify_login_state,
)
from ncmu_backend.auth.jwt_utils import sign_jwt
from ncmu_backend.config import Settings
from ncmu_backend.db.models import User
from ncmu_backend.dingtalk.client import DingTalkApiError
from ncmu_backend.deps import get_db, get_settings
from ncmu_backend.main import get_redis  # lifespan-owned Redis (TASK-STATESTORE-1)
from ncmu_backend.schemas.auth import (
    DevLoginRequest,
    DevLoginResponse,
    UserOut,
)

router = APIRouter(tags=["auth"])
log = logging.getLogger(__name__)

# 钉钉登录链路是低频交互式动作；每请求开独立 httpx.AsyncClient（企业 token
# 缓存是 client.py 进程级 global，跨实例复用，不受此影响 / 同 dingtalk/routes.py）。
_DINGTALK_TIMEOUT = httpx.Timeout(30.0, connect=5.0)


class DingTalkLoginInitResponse(BaseModel):
    """GET /auth/dingtalk/login 响应：前端拿 ``authorize_url`` 跳转钉钉授权页。

    ``state`` 同时写入 ``ncmu_ding_login_state`` cookie（double-submit CSRF）；
    前端无需读 state（已嵌在 authorize_url + cookie），此字段仅供透明/调试。
    """

    authorize_url: str
    state: str


@router.post(
    "/api/v1/ncmu/auth/dev-login",
    response_model=DevLoginResponse,
    summary="Dev-only login: trade a dev user UUID for a 24h JWT",
)
async def dev_login(
    req: DevLoginRequest,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> DevLoginResponse:
    if not settings.NCMU_ENABLE_DEV_LOGIN:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": 1100, "message": "dev-login disabled"},
        )

    result = await db.execute(
        select(User).where(User.id == req.user_id, User.is_active.is_(True))
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": 1101, "message": "dev user not found"},
        )

    # TASK-PC2-E: derive is_admin from settings.admin_user_id_set (source-of-
    # truth, per r3 I-INDEP2-1) and surface it in both the JWT claim (for
    # SPA display sync) and the UserOut payload (frontend AuthUser.is_admin
    # is hydrated from this, NOT by decoding the JWT — r3 I-INDEP2-2).
    is_admin = settings.derive_is_admin(user.id)
    jwt_str, exp = sign_jwt(
        str(user.id),
        user.name,
        settings.NCMU_JWT_SECRET,
        ttl_hours=settings.NCMU_JWT_TTL_HOURS,
        is_admin=is_admin,
    )
    user_out = UserOut.model_validate(user).model_copy(update={"is_admin": is_admin})
    return DevLoginResponse(
        jwt=jwt_str,
        user=user_out,
        expires_at=exp,
    )


@router.get(
    "/api/v1/ncmu/dev/users",
    response_model=list[UserOut],
    summary="List the dev seed users (for the dev-only login dropdown)",
)
async def list_dev_users(
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> list[UserOut]:
    if not settings.NCMU_ENABLE_DEV_LOGIN:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": 1100, "message": "dev endpoints disabled"},
        )

    result = await db.execute(
        select(User).where(User.is_active.is_(True)).order_by(User.name)
    )
    users = result.scalars().all()
    return [UserOut.model_validate(u) for u in users]


# ===================================================================== #
# DingTalk 扫码登录 — TASK-LOGIN-1
# --------------------------------------------------------------------- #
# GET  /api/v1/ncmu/auth/dingtalk/login     生成授权 URL + state（cookie）
# GET  /api/v1/ncmu/auth/dingtalk/callback  钉钉带 code/state 回调 → JWT
#
# 错误码（13xx 段；1320-1323 在本批前未被占用 — 见 dingtalk/routes.py docstring
# 13xx 占用全景：1300/1301=sessions，1302/1311=admin-dingtalk）：
#   1320 state 校验失败（缺失 / 不匹配 / 签名无效）→ 400
#   1321 回调缺 code 参数                        → 400
#   1322 dingtalk_userid 未匹配 NCMU 账号「未开通」 → 403
#   1323 钉钉上游链路失败（DingTalkApiError）       → 502
# ===================================================================== #
# state cookie scope：限定 /auth/dingtalk 子树（callback 在其下），HttpOnly
# 防 JS 读，SameSite=lax 允许钉钉 top-level GET 回跳携带，Secure 仅 prod（dev/
# 测试走 http 须可回送）。
_STATE_COOKIE_PATH = "/api/v1/ncmu/auth/dingtalk"

# 502 对外通用文案：不回显上游钉钉响应体（errcode/errmsg 进 server log），
# 避免向浏览器泄漏上游内部信息（REWORK-INDEP #2）。
_LOGIN_UPSTREAM_ERROR_MSG = "钉钉登录暂时不可用，请稍后重试"


def _login_error_response(status_code: int, code: int, message: str) -> JSONResponse:
    """构造回调错误响应，并**真正**清除 state cookie。

    REWORK-INDEP #1：``raise HTTPException`` 会另建 JSONResponse，注入的
    ``response`` 上的 ``delete_cookie`` 头会丢失 → 错误分支后 state cookie 存活
    到 max-age 可重放。改为显式 return 带 Set-Cookie 删除头的 JSONResponse，
    使所有 state 已读路径（成功/失败）都单次消费 cookie。错误信封 shape 与
    ``HTTPException`` 一致（``{"detail": {"code", "message"}}``）。
    """
    resp = JSONResponse(
        status_code=status_code,
        content={"detail": {"code": code, "message": message}},
    )
    resp.delete_cookie(key=LOGIN_STATE_COOKIE, path=_STATE_COOKIE_PATH)
    return resp


@router.get(
    "/api/v1/ncmu/auth/dingtalk/login",
    response_model=DingTalkLoginInitResponse,
    summary="发起钉钉扫码登录：返回授权 URL 并下发 state cookie（CSRF）",
)
async def dingtalk_login_init(
    response: Response,
    settings: Settings = Depends(get_settings),
    redis=Depends(get_redis),
) -> DingTalkLoginInitResponse:
    state = await generate_login_state(settings.NCMU_JWT_SECRET, redis)
    response.set_cookie(
        key=LOGIN_STATE_COOKIE,
        value=state,
        max_age=LOGIN_STATE_TTL_S,
        path=_STATE_COOKIE_PATH,
        httponly=True,
        samesite="lax",
        secure=settings.is_prod,
    )
    return DingTalkLoginInitResponse(
        authorize_url=build_authorize_url(settings, state),
        state=state,
    )


@router.get(
    "/api/v1/ncmu/auth/dingtalk/callback",
    response_model=DevLoginResponse,
    summary="钉钉登录回调：校验 state → code 换企业 userid → 匹配 → 签发 JWT",
)
async def dingtalk_login_callback(
    response: Response,
    code: str | None = None,
    state: str | None = None,
    state_cookie: str | None = Cookie(default=None, alias=LOGIN_STATE_COOKIE),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    redis=Depends(get_redis),
) -> DevLoginResponse | JSONResponse:
    # 所有「state 已读」分支（成功 / 403 / 502 / 缺 code）都经
    # _login_error_response 或 happy 路径的 response.delete_cookie 清除 state
    # cookie → 真正单次消费（REWORK-INDEP #1）。

    # ① CSRF：state（query）必须与下发时写入的 cookie 一致且签名有效。
    #    校验失败也清 cookie（避免遗留半残 state；缺 cookie 时 delete 无害）。
    if not await verify_login_state(state, state_cookie, settings.NCMU_JWT_SECRET, redis):
        return _login_error_response(
            status.HTTP_400_BAD_REQUEST, 1320, "invalid or missing login state"
        )
    # ② 缺 code（用户取消授权 / 钉钉异常回跳）→ 拒绝（state 已校验，清 cookie）。
    if not code:
        return _login_error_response(
            status.HTTP_400_BAD_REQUEST, 1321, "missing authorization code"
        )

    # happy 路径：注入的 response 头会并入返回模型的最终响应。
    response.delete_cookie(key=LOGIN_STATE_COOKIE, path=_STATE_COOKIE_PATH)

    # ③ 编排：code → 企业 userid（服务端全程解析）→ 匹配 → JWT。
    async with httpx.AsyncClient(timeout=_DINGTALK_TIMEOUT) as http:
        try:
            user, jwt_str, exp = await login_via_dingtalk(db, http, settings, code)
        except DingTalkLoginDenied:
            # 业务拒绝（未建账号）：清 cookie + 403。
            return _login_error_response(status.HTTP_403_FORBIDDEN, 1322, "未开通")
        except DingTalkApiError as exc:
            # REWORK-INDEP #2：上游 errcode/errmsg 仅进 server log，不回显浏览器。
            log.warning(
                "dingtalk login upstream error errcode=%s errmsg=%s",
                exc.errcode, exc.errmsg,
            )
            return _login_error_response(
                status.HTTP_502_BAD_GATEWAY, 1323, _LOGIN_UPSTREAM_ERROR_MSG
            )

    # is_admin 与 dev-login 同源（settings.admin_user_id_set），surface 给 SPA
    # （display state；authorization source-of-truth 仍在 auth/deps.py）。
    is_admin = settings.derive_is_admin(user.id)
    user_out = UserOut.model_validate(user).model_copy(update={"is_admin": is_admin})
    return DevLoginResponse(jwt=jwt_str, user=user_out, expires_at=exp)
