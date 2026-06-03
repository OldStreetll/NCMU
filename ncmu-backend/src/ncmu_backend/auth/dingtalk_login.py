"""DingTalk 扫码登录编排 — TASK-LOGIN-1.

设计 spec ``NCMU-Wiki/sources/phase2/2026-06-03-phase2d-a2-login-skeleton-design.md``。

回调链（spec §1）：
  ① code → userAccessToken             (client.exchange_user_token)
  ② userAccessToken → /me → unionId    (client.get_login_userinfo)
  ③ (企业 token,复用 T01) getbyunionid → 企业 userid  (client.get_userid_by_unionid)
  ④ 按 dingtalk_userid 匹配 NCMU users → 命中 sign_jwt(sub=user.id) / 未命中 403「未开通」

🔴 安全硬约束（spec §3）：
- 匹配用的企业 userid **必须服务端经 ①②③ 解析**，绝不接受前端/请求传入 userid
  （:func:`login_via_dingtalk` 只吃 ``code``，userid 全程在链路内部解析，无入口外泄）。
- ``state`` 防 CSRF + 防重放（TASK-STATESTORE-1）：:func:`generate_login_state`
  发 HMAC 签名 state 同时写 query + cookie（double-submit）**并把 nonce SETEX 进
  redis**（服务端单次性记录 / TTL 300s）；:func:`verify_login_state` 校验
  query==cookie 且签名有效（恒定时比较防 timing），**再 GETDEL 原子消费 redis
  nonce**（存在=首用有效即销毁 / 不存在=过期或重放→拒绝）。主 TTL 由 redis
  承载，cookie ``max-age`` 作次级 TTL；redis 单次性堵住 300s 窗口内重放。
- 钉钉任何链路失败 → :class:`~ncmu_backend.dingtalk.client.DingTalkApiError`
  上抛（不静默放行）；userid 未匹配 → :class:`DingTalkLoginDenied`（拒绝「未开通」）。

JWT shape 与 dev-login 完全一致（``sign_jwt(sub=user.id, name, is_admin)``），
经既有 ``verify_jwt`` / ``get_current_user`` 可消费。
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime
from urllib.parse import urlencode

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ncmu_backend.auth.jwt_utils import sign_jwt
from ncmu_backend.config import Settings
from ncmu_backend.db.models import User

# state 三段式（cookie + query 同值，double-submit）。cookie 名固定。
LOGIN_STATE_COOKIE = "ncmu_ding_login_state"
# state TTL（秒）—— 授权往返窗口足够。**主 TTL 由 redis key 承载**（SETEX），
# cookie max-age 作次级 TTL；超时则 redis key 消失 + cookie 失效 → 回调拒绝。
LOGIN_STATE_TTL_S = 300
# nonce 与签名以 "." 分隔（urlsafe base64 / hex 均不含 "."，无歧义）。
_STATE_SEP = "."
# redis key 前缀（NCMU 用 DB 0 / 见 config.REDIS_URL）。
LOGIN_STATE_KEY_PREFIX = "login_state:"


def _state_key(nonce: str) -> str:
    return f"{LOGIN_STATE_KEY_PREFIX}{nonce}"


class DingTalkLoginDenied(Exception):
    """解析出企业 userid 但未匹配到 active NCMU 账号 → 拒绝登录（403「未开通」）。

    与 :class:`DingTalkApiError`（上游链路失败 / 502）区分：本异常是
    **业务策略拒绝**（spec §3.4 匹配策略=拒绝登录），不是上游故障。
    """


# --------------------------------------------------------------------- #
# state —— HMAC 签名 + double-submit cookie + redis 单次性记录（防御纵深）
# --------------------------------------------------------------------- #
# TASK-STATESTORE-1：在原「签名 + double-submit cookie」之上加一层**服务端
# 单次性**——发起时把 nonce SETEX 进 redis（TTL 300s），回调时 GETDEL 原子
# 消费：存在=有效且即刻销毁（真单次），不存在=过期/已用过→拒绝。这堵住了
# 纯无状态 HMAC+cookie 在 300s 窗口内的重放（独审 LOGIN-2 panel state replay /
# spec §7 live 升级项）。签名 + cookie 校验**保留**（防御纵深，不删）。
def _sign_nonce(nonce: str, secret: str) -> str:
    return hmac.new(secret.encode(), nonce.encode(), hashlib.sha256).hexdigest()


async def generate_login_state(secret: str, redis) -> str:
    """生成 ``{nonce}.{hmac(nonce)}`` state，并把 nonce SETEX 写入 redis。

    nonce 随机；签名用 ``NCMU_JWT_SECRET``（已是服务端密钥，无需新密钥）。
    ``redis.set(login_state:{nonce}, "1", ex=300)`` 留服务端单次性记录；TTL 由
    redis 承载。返回值同时塞进授权 URL 的 ``state`` 参数与 ``LOGIN_STATE_COOKIE``
    cookie（double-submit）。
    """
    nonce = secrets.token_urlsafe(24)
    await redis.set(_state_key(nonce), "1", ex=LOGIN_STATE_TTL_S)
    return f"{nonce}{_STATE_SEP}{_sign_nonce(nonce, secret)}"


def _signature_valid(state: str, secret: str) -> bool:
    """state 自身 HMAC 签名是否有效（证明由本服务签发未被篡改）。"""
    if not state or _STATE_SEP not in state:
        return False
    nonce, _, sig = state.partition(_STATE_SEP)
    if not nonce or not sig:
        return False
    return hmac.compare_digest(sig, _sign_nonce(nonce, secret))


async def verify_login_state(
    state_from_query: str | None, state_from_cookie: str | None,
    secret: str, redis,
) -> bool:
    """回调 state 校验（防御纵深 / 任一层不过即 False）：

    1. **签名 + double-submit cookie**：query/cookie 均在、恒定时相等、且签名
       有效（证明由本服务签发未被篡改）。**不通过则不触碰 redis**——避免伪造
       state 烧掉某个合法待用 nonce。
    2. **redis 单次性消费**：``GETDEL login_state:{nonce}`` 原子 get+del。存在
       → 首次使用，有效且即刻销毁（真单次）；不存在（过期 / 已被消费 / 重放）
       → 拒绝。

    cookie 缺失（过期 / 未发起授权 / CSRF 跨站无 cookie）在第 1 层即 False。
    """
    # 第 1 层：签名 + double-submit cookie（不命中不消费 redis）。
    if not state_from_query or not state_from_cookie:
        return False
    if not hmac.compare_digest(state_from_query, state_from_cookie):
        return False
    if not _signature_valid(state_from_query, secret):
        return False
    # 第 2 层：redis 原子消费（单次性）。
    nonce = state_from_query.partition(_STATE_SEP)[0]
    consumed = await redis.getdel(_state_key(nonce))
    return consumed is not None


def build_authorize_url(settings: Settings, state: str) -> str:
    """拼钉钉授权页 URL（前端跳转用）。

    ``{auth_base}/oauth2/auth?redirect_uri=&response_type=code&client_id=
    &scope=openid&state=&prompt=consent``（spec §2）。
    """
    query = urlencode({
        "redirect_uri": settings.dingtalk_login_redirect_uri,
        "response_type": "code",
        "client_id": settings.dingtalk_app_key,
        "scope": "openid",
        "state": state,
        "prompt": "consent",
    })
    return f"{settings.dingtalk_auth_base}/oauth2/auth?{query}"


# --------------------------------------------------------------------- #
# 回调编排 —— code → 企业 userid → 匹配 → JWT
# --------------------------------------------------------------------- #
async def resolve_dingtalk_userid(
    http, settings: Settings, code: str,
) -> str:
    """① code → ② unionId → ③ 企业 userid（服务端全程解析 / 防冒充）。

    任一步失败抛 DingTalkApiError。返回的 userid **只来自钉钉服务端链路**，
    绝不来自请求入参（spec §3 防零凭据冒充）。
    """
    # 延迟 import 规避潜在循环依赖（client 不依赖本模块，纯防御）。
    from ncmu_backend.dingtalk.client import (
        exchange_user_token,
        get_access_token,
        get_login_userinfo,
        get_userid_by_unionid,
    )

    user_token = await exchange_user_token(http, settings, code)
    union_id = await get_login_userinfo(http, settings, user_token)
    corp_token = await get_access_token(http, settings)  # T01 复用（企业 token 缓存）
    return await get_userid_by_unionid(http, settings, corp_token, union_id)


async def login_via_dingtalk(
    db: AsyncSession, http, settings: Settings, code: str,
) -> tuple[User, str, datetime]:
    """完整回调登录：解析企业 userid → 匹配 active NCMU user → 签发 JWT。

    返回 ``(user, jwt_str, expires_at)``。

    - 链路失败 → DingTalkApiError 上抛（route 转 502，不静默）。
    - userid 未匹配 active 账号 → DingTalkLoginDenied（route 转 403「未开通」）。
    - 命中 → ``sign_jwt(sub=user.id, name, is_admin)``，shape 同 dev-login。
    """
    userid = await resolve_dingtalk_userid(http, settings, code)

    result = await db.execute(
        select(User).where(
            User.dingtalk_userid == userid, User.is_active.is_(True)
        )
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise DingTalkLoginDenied(
            f"dingtalk_userid not provisioned in NCMU: {userid}"
        )

    # is_admin 与 dev-login 同源：settings.admin_user_id_set（authorization
    # source-of-truth 在 auth/deps.py，claim 仅供 SPA display sync）。
    is_admin = str(user.id).lower() in settings.admin_user_id_set
    jwt_str, exp = sign_jwt(
        str(user.id),
        user.name,
        settings.NCMU_JWT_SECRET,
        ttl_hours=settings.NCMU_JWT_TTL_HOURS,
        is_admin=is_admin,
    )
    return user, jwt_str, exp
