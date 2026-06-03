"""TASK-LOGIN-1 — 钉钉扫码登录后端骨架测试（纯 respx mock / 不打真网络）。

三组覆盖：
  A. client 三新函数单元（v1.0 userAccessToken/me + 老 oapi getbyunionid，含错误路径）
  B. state CSRF 助手（HMAC 签名 + double-submit 校验 / 篡改 / 缺失）
  C. 路由全链（app_client + respx mock 4 接口）：
     happy path 签 JWT（sub=user.id，经 get_current_user 可消费）
     / 未匹配 403 1322 / state 不符 400 1320 / 缺 cookie 400 1320
     / 缺 code 400 1321 / 上游 errcode 502 1323 / 上游 v1.0 非 2xx 502 1323

respx 不拦截 app_client 的 ASGITransport（只拦路由内新建 httpx.AsyncClient 的
真实出站调用），故 app 请求正常入栈、钉钉出站被 mock。token 缓存是进程级
module global，每测前 reset（同 tests/dingtalk/test_client.py 范式）。
"""
from __future__ import annotations

import json
import uuid

import httpx
import pytest
import pytest_asyncio
import respx
from httpx import Request, Response

from ncmu_backend.auth.dingtalk_login import LOGIN_STATE_COOKIE
from ncmu_backend.config import Settings
from ncmu_backend.db.models import User


# 默认官方域（app_client 不覆盖 DINGTALK_* env → config default 生效）。
API = "https://api.dingtalk.com"      # v1.0（userAccessToken / contact/users/me）
OAPI = "https://oapi.dingtalk.com"    # 老 oapi（gettoken / getbyunionid）


@pytest.fixture
def ding_settings() -> Settings:
    """单元测试用 Settings：指向 stub 域 + 测试凭据（init kwargs 覆盖容器 env）。"""
    return Settings(
        dingtalk_oapi_base="https://oapi-stub.example",
        dingtalk_oauth_api_base="https://api-stub.example",
        dingtalk_app_key="test-key",
        dingtalk_app_secret="test-secret",
    )


@pytest_asyncio.fixture
async def http_client():
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_token_cache():
    """进程级企业 token 缓存不得跨测试泄漏。"""
    from ncmu_backend.dingtalk import client
    client.reset_token_cache()
    yield
    client.reset_token_cache()


def _body(request: Request) -> dict:
    return json.loads(request.content or b"{}")


# ===================================================================== #
# A. client 三新函数单元
# ===================================================================== #
async def test_exchange_user_token_success(http_client, ding_settings):
    """① code → userAccessToken（v1.0，2xx）→ accessToken。"""
    from ncmu_backend.dingtalk import client
    base = ding_settings.dingtalk_oauth_api_base
    with respx.mock(assert_all_called=True) as rx:
        route = rx.post(f"{base}/v1.0/oauth2/userAccessToken").mock(
            return_value=Response(200, json={"accessToken": "USERTOK", "expireIn": 7200})
        )
        token = await client.exchange_user_token(http_client, ding_settings, "the-code")
    assert token == "USERTOK"
    # 防冒充：clientSecret/code 由服务端注入 body（不接受外部 userid）。
    sent = _body(route.calls[0].request)
    assert sent["code"] == "the-code"
    assert sent["clientId"] == "test-key"
    assert sent["grantType"] == "authorization_code"


async def test_exchange_user_token_non_2xx_raises(http_client, ding_settings):
    """v1.0 非 2xx（无 errcode 字段，{code,message} 风格）→ DingTalkApiError，不静默。"""
    from ncmu_backend.dingtalk import client
    from ncmu_backend.dingtalk.client import DingTalkApiError
    base = ding_settings.dingtalk_oauth_api_base
    with respx.mock(assert_all_called=True) as rx:
        rx.post(f"{base}/v1.0/oauth2/userAccessToken").mock(
            return_value=Response(400, json={"code": "invalidParameter", "message": "bad code"})
        )
        with pytest.raises(DingTalkApiError) as exc:
            await client.exchange_user_token(http_client, ding_settings, "bad")
    assert exc.value.errcode == 400
    assert "bad code" in exc.value.errmsg


async def test_get_login_userinfo_success(http_client, ding_settings):
    """② userAccessToken → /me → unionId。"""
    from ncmu_backend.dingtalk import client
    base = ding_settings.dingtalk_oauth_api_base
    with respx.mock(assert_all_called=True) as rx:
        route = rx.get(f"{base}/v1.0/contact/users/me").mock(
            return_value=Response(200, json={"unionId": "UNION-1", "openId": "o", "nick": "n"})
        )
        union_id = await client.get_login_userinfo(http_client, ding_settings, "USERTOK")
    assert union_id == "UNION-1"
    # 用户身份 token 经 header 传递（钉钉 v1.0 约定）。
    assert route.calls[0].request.headers["x-acs-dingtalk-access-token"] == "USERTOK"


async def test_get_login_userinfo_missing_unionid_raises(http_client, ding_settings):
    """/me 2xx 但缺 unionId → DingTalkApiError（绝不返回空冒充）。"""
    from ncmu_backend.dingtalk import client
    from ncmu_backend.dingtalk.client import DingTalkApiError
    base = ding_settings.dingtalk_oauth_api_base
    with respx.mock(assert_all_called=True) as rx:
        rx.get(f"{base}/v1.0/contact/users/me").mock(
            return_value=Response(200, json={"openId": "o", "nick": "n"})
        )
        with pytest.raises(DingTalkApiError):
            await client.get_login_userinfo(http_client, ding_settings, "USERTOK")


async def test_get_userid_by_unionid_success(http_client, ding_settings):
    """③ unionId → 企业 userid（老 oapi errcode=0）。"""
    from ncmu_backend.dingtalk import client
    base = ding_settings.dingtalk_oapi_base
    with respx.mock(assert_all_called=True) as rx:
        route = rx.post(url__regex=rf"{base}/topapi/user/getbyunionid.*").mock(
            return_value=Response(200, json={"errcode": 0, "result": {"userid": "emp-007"}})
        )
        userid = await client.get_userid_by_unionid(
            http_client, ding_settings, "CORP-TOKEN", "UNION-1"
        )
    assert userid == "emp-007"
    assert _body(route.calls[0].request)["unionid"] == "UNION-1"


async def test_get_userid_by_unionid_errcode_raises(http_client, ding_settings):
    """getbyunionid errcode≠0 → DingTalkApiError（老 oapi 错误路径）。"""
    from ncmu_backend.dingtalk import client
    from ncmu_backend.dingtalk.client import DingTalkApiError
    base = ding_settings.dingtalk_oapi_base
    with respx.mock(assert_all_called=True) as rx:
        rx.post(url__regex=rf"{base}/topapi/user/getbyunionid.*").mock(
            return_value=Response(200, json={"errcode": 60121, "errmsg": "user not in org"})
        )
        with pytest.raises(DingTalkApiError) as exc:
            await client.get_userid_by_unionid(
                http_client, ding_settings, "CORP-TOKEN", "UNION-X"
            )
    assert exc.value.errcode == 60121


# ===================================================================== #
# B. state CSRF 助手单元（无网络）
# ===================================================================== #
SECRET = "unit-state-secret-32-bytes-min-xxxxxxxx"


def test_state_roundtrip_valid():
    from ncmu_backend.auth.dingtalk_login import generate_login_state, verify_login_state
    state = generate_login_state(SECRET)
    # double-submit：query 与 cookie 同值 + 签名有效 → True。
    assert verify_login_state(state, state, SECRET) is True


def test_state_mismatch_query_ne_cookie():
    from ncmu_backend.auth.dingtalk_login import generate_login_state, verify_login_state
    s1 = generate_login_state(SECRET)
    s2 = generate_login_state(SECRET)
    assert verify_login_state(s1, s2, SECRET) is False


def test_state_missing_cookie_false():
    from ncmu_backend.auth.dingtalk_login import generate_login_state, verify_login_state
    state = generate_login_state(SECRET)
    assert verify_login_state(state, None, SECRET) is False
    assert verify_login_state(None, state, SECRET) is False


def test_state_tampered_signature_false():
    from ncmu_backend.auth.dingtalk_login import generate_login_state, verify_login_state
    state = generate_login_state(SECRET)
    nonce = state.split(".")[0]
    forged = f"{nonce}.deadbeef"  # query==cookie 但签名错 → 篡改/伪造
    assert verify_login_state(forged, forged, SECRET) is False


def test_state_wrong_secret_false():
    from ncmu_backend.auth.dingtalk_login import generate_login_state, verify_login_state
    state = generate_login_state(SECRET)
    assert verify_login_state(state, state, "a-different-secret-totally") is False


# ===================================================================== #
# C. 路由全链（app_client + respx mock）
# --------------------------------------------------------------------- #
# 端点：GET /api/v1/ncmu/auth/dingtalk/{login,callback}
# ===================================================================== #
LOGIN_URL = "/api/v1/ncmu/auth/dingtalk/login"
CALLBACK_URL = "/api/v1/ncmu/auth/dingtalk/callback"


def _state_cookie_cleared(resp) -> bool:
    """响应是否带 state cookie 的删除头（Max-Age=0 / 过期）。

    REWORK-INDEP #1：错误路径也必须清 state cookie（真正单次消费 / 防重放）。
    delete_cookie 发 ``ncmu_ding_login_state=""; Max-Age=0; ...``。
    """
    for h in resp.headers.get_list("set-cookie"):
        if h.startswith(f"{LOGIN_STATE_COOKIE}=") and "max-age=0" in h.lower():
            return True
    return False


def _mock_chain(rx, *, userid: str = "ding-emp-001"):
    """注册完整 4 接口 happy mock（gettoken/getbyunionid 老 oapi / 前两步 v1.0）。"""
    rx.post(f"{API}/v1.0/oauth2/userAccessToken").mock(
        return_value=Response(200, json={"accessToken": "USERTOK", "expireIn": 7200})
    )
    rx.get(f"{API}/v1.0/contact/users/me").mock(
        return_value=Response(200, json={"unionId": "UNION-1", "openId": "o", "nick": "n"})
    )
    rx.get(url__regex=rf"{OAPI}/gettoken.*").mock(
        return_value=Response(200, json={"errcode": 0, "access_token": "CORP", "expires_in": 7200})
    )
    rx.post(url__regex=rf"{OAPI}/topapi/user/getbyunionid.*").mock(
        return_value=Response(200, json={"errcode": 0, "result": {"userid": userid}})
    )


async def _seed_user(async_db, dingtalk_userid: str) -> uuid.UUID:
    """插一个 dingtalk_userid 非 NULL 的 active user（dev seed 全 NULL）。"""
    uid = uuid.uuid4()
    async_db.add(User(id=uid, dingtalk_userid=dingtalk_userid, name="登录测试员",
                      dept_path="/软件开发部"))
    await async_db.commit()
    return uid


async def _get_state(app_client) -> str:
    """走 /login 拿 state（同时把 cookie 种进 app_client 的 cookie jar）。"""
    r = await app_client.get(LOGIN_URL)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["authorize_url"].startswith("https://login.dingtalk.com/oauth2/auth")
    assert "state=" in data["authorize_url"] and "scope=openid" in data["authorize_url"]
    return data["state"]


async def test_callback_happy_path_signs_consumable_jwt(app_client, async_db, jwt_secret):
    """有效 code → 匹配 user → JWT(sub=user.id，shape 同 dev-login，get_current_user 可消费)。"""
    from ncmu_backend.auth.deps import get_current_user

    uid = await _seed_user(async_db, "ding-emp-001")
    with respx.mock(assert_all_called=False) as rx:
        _mock_chain(rx, userid="ding-emp-001")
        state = await _get_state(app_client)
        resp = await app_client.get(CALLBACK_URL, params={"code": "valid-code", "state": state})

    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert set(payload) == {"jwt", "user", "expires_at"}     # 同 DevLoginResponse shape
    assert payload["user"]["id"] == str(uid)

    # 经既有 get_current_user 真消费（AC#1）：sub == user.id。
    verify_settings = Settings(NCMU_JWT_SECRET=jwt_secret)
    cu = await get_current_user(
        authorization=f"Bearer {payload['jwt']}", settings=verify_settings
    )
    assert cu.sub == str(uid)
    assert cu.name == "登录测试员"


async def test_callback_user_not_provisioned_403(app_client, async_db):
    """解析出企业 userid 但 NCMU 无此 dingtalk_userid → 403「未开通」(code 1322)。"""
    await _seed_user(async_db, "ding-emp-001")
    with respx.mock(assert_all_called=False) as rx:
        _mock_chain(rx, userid="ding-emp-NOPE")   # 链路解析出未建账号的 userid
        state = await _get_state(app_client)
        resp = await app_client.get(CALLBACK_URL, params={"code": "valid-code", "state": state})
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["code"] == 1322
    # REWORK-INDEP #1：错误路径(403)也清 state cookie。
    assert _state_cookie_cleared(resp), resp.headers.get_list("set-cookie")


async def test_callback_state_mismatch_rejected_400(app_client, async_db):
    """state（query）与下发 cookie 不符 → 400 (code 1320)，绝不进入链路。"""
    await _seed_user(async_db, "ding-emp-001")
    with respx.mock(assert_all_called=False) as rx:
        chain_token = rx.post(f"{API}/v1.0/oauth2/userAccessToken").mock(
            return_value=Response(200, json={"accessToken": "X"})
        )
        await _get_state(app_client)   # 种 cookie
        resp = await app_client.get(
            CALLBACK_URL, params={"code": "valid-code", "state": "forged.sig"}
        )
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"]["code"] == 1320
    assert not chain_token.called, "state 校验失败必须先于任何钉钉调用"
    # REWORK-INDEP #1：state 不符也清遗留 cookie（避免半残 state）。
    assert _state_cookie_cleared(resp), resp.headers.get_list("set-cookie")


async def test_callback_missing_cookie_rejected_400(app_client, async_db):
    """有 state query 但无 cookie（未发起授权 / 跨站 CSRF 无 cookie）→ 400 (1320)。"""
    await _seed_user(async_db, "ding-emp-001")
    # 不调用 /login → cookie jar 无 state cookie。
    resp = await app_client.get(
        CALLBACK_URL, params={"code": "valid-code", "state": "anything.here"}
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"]["code"] == 1320


async def test_callback_missing_code_rejected_400(app_client, async_db):
    """state 有效但回调缺 code（用户取消授权）→ 400 (code 1321)。"""
    await _seed_user(async_db, "ding-emp-001")
    with respx.mock(assert_all_called=False) as rx:
        _mock_chain(rx)
        state = await _get_state(app_client)
        resp = await app_client.get(CALLBACK_URL, params={"state": state})  # 无 code
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"]["code"] == 1321


async def test_callback_upstream_errcode_502(app_client, async_db):
    """getbyunionid errcode≠0（老 oapi 上游故障）→ 502 (code 1323)，绝不静默放行。"""
    await _seed_user(async_db, "ding-emp-001")
    with respx.mock(assert_all_called=False) as rx:
        rx.post(f"{API}/v1.0/oauth2/userAccessToken").mock(
            return_value=Response(200, json={"accessToken": "USERTOK"})
        )
        rx.get(f"{API}/v1.0/contact/users/me").mock(
            return_value=Response(200, json={"unionId": "UNION-1"})
        )
        rx.get(url__regex=rf"{OAPI}/gettoken.*").mock(
            return_value=Response(200, json={"errcode": 0, "access_token": "CORP", "expires_in": 7200})
        )
        rx.post(url__regex=rf"{OAPI}/topapi/user/getbyunionid.*").mock(
            return_value=Response(200, json={"errcode": 60121, "errmsg": "user not in org"})
        )
        state = await _get_state(app_client)
        resp = await app_client.get(CALLBACK_URL, params={"code": "valid-code", "state": state})
    assert resp.status_code == 502, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == 1323
    # REWORK-INDEP #2：上游 errcode/errmsg 不得回显浏览器（仅进 server log）。
    body = resp.text
    assert "60121" not in body and "user not in org" not in body
    assert "upstream error" not in body
    assert detail["message"] == "钉钉登录暂时不可用，请稍后重试"
    # REWORK-INDEP #1：502 错误路径也清 state cookie。
    assert _state_cookie_cleared(resp), resp.headers.get_list("set-cookie")


async def test_callback_upstream_v1_non_2xx_502(app_client, async_db):
    """第一步 userAccessToken v1.0 非 2xx（无 errcode）→ 502 (code 1323)，不静默。"""
    await _seed_user(async_db, "ding-emp-001")
    with respx.mock(assert_all_called=False) as rx:
        rx.post(f"{API}/v1.0/oauth2/userAccessToken").mock(
            return_value=Response(401, json={"code": "Unauthorized", "message": "invalid client"})
        )
        state = await _get_state(app_client)
        resp = await app_client.get(CALLBACK_URL, params={"code": "valid-code", "state": state})
    assert resp.status_code == 502, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == 1323
    # REWORK-INDEP #2：v1.0 上游 {code,message} 不得回显（"invalid client" 不泄漏）。
    body = resp.text
    assert "invalid client" not in body and "Unauthorized" not in body
    assert detail["message"] == "钉钉登录暂时不可用，请稍后重试"
    assert _state_cookie_cleared(resp), resp.headers.get_list("set-cookie")
