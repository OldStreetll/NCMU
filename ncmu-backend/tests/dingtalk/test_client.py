"""TASK-2DA2S-01 — DingTalk oapi client tests (pure respx mock / 不依赖真钉钉).

钉钉 oapi 三能力（计划 2026-06-01-phase2d-a2-sync-plan.md §TASK-2DA2S-01）：
- get_access_token：GET /gettoken；errcode≠0 抛 DingTalkApiError；进程内缓存
- list_departments：递归 POST /topapi/v2/department/listsub 拉全树，扁平返回
- list_department_users：分页 POST /topapi/v2/user/list（cursor/has_more）聚合

全部 respx mock；token 缓存是进程级 module global，每测前 reset。
"""
from __future__ import annotations

import json

import httpx
import pytest
import pytest_asyncio
import respx
from httpx import Request, Response

from ncmu_backend.config import Settings


OAPI = "https://test-ding.example"


@pytest.fixture
def ding_settings() -> Settings:
    """Deterministic Settings with DingTalk creds + test oapi base.
    init kwargs override any container env (pydantic-settings priority)."""
    return Settings(
        dingtalk_oapi_base=OAPI,
        dingtalk_app_key="test-key",
        dingtalk_app_secret="test-secret",
    )


@pytest_asyncio.fixture
async def http_client():
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_token_cache():
    """Process-level token cache must not leak across tests (AC#2 counts HTTP)."""
    from ncmu_backend.dingtalk import client
    client.reset_token_cache()
    yield
    client.reset_token_cache()


def _body(request: Request) -> dict:
    return json.loads(request.content or b"{}")


# ── get_access_token — AC#1 + AC#2 ──────────────────────────────────────────


async def test_get_access_token_success(http_client, ding_settings):
    """gettoken errcode=0 → 返回 access_token。"""
    from ncmu_backend.dingtalk import client
    with respx.mock(assert_all_called=True) as rx:
        rx.get(url__regex=rf"{OAPI}/gettoken.*").mock(
            return_value=Response(200, json={
                "errcode": 0, "errmsg": "ok",
                "access_token": "TOKEN-ABC", "expires_in": 7200,
            })
        )
        token = await client.get_access_token(http_client, ding_settings)
    assert token == "TOKEN-ABC"


async def test_get_access_token_errcode_raises(http_client, ding_settings):
    """gettoken errcode≠0 → 抛 DingTalkApiError(errcode, errmsg)。"""
    from ncmu_backend.dingtalk import client
    from ncmu_backend.dingtalk.client import DingTalkApiError
    with respx.mock(assert_all_called=True) as rx:
        rx.get(url__regex=rf"{OAPI}/gettoken.*").mock(
            return_value=Response(200, json={
                "errcode": 40001, "errmsg": "invalid appsecret",
            })
        )
        with pytest.raises(DingTalkApiError) as exc:
            await client.get_access_token(http_client, ding_settings)
    assert exc.value.errcode == 40001
    assert "invalid appsecret" in exc.value.errmsg


async def test_get_access_token_cached_single_http(http_client, ding_settings):
    """连续 2 次 get_access_token → 仅 1 次底层 HTTP (进程内缓存 / AC#2)。"""
    from ncmu_backend.dingtalk import client
    with respx.mock(assert_all_called=True) as rx:
        route = rx.get(url__regex=rf"{OAPI}/gettoken.*").mock(
            return_value=Response(200, json={
                "errcode": 0, "access_token": "TOKEN-CACHED", "expires_in": 7200,
            })
        )
        t1 = await client.get_access_token(http_client, ding_settings)
        t2 = await client.get_access_token(http_client, ding_settings)
    assert t1 == t2 == "TOKEN-CACHED"
    assert route.call_count == 1, (
        f"token should be cached → 1 HTTP for 2 calls; got {route.call_count}"
    )


# ── list_departments — AC#3 递归 ────────────────────────────────────────────


async def test_list_departments_recursive_flatten(http_client, ding_settings):
    """2 层部门树 → 扁平全集 (含 parent_id)，递归 listsub 拉全树。

    树：root=1 → [2 中央研究院, 3 财务部] ; 2 → [4 软件开发部] ; 3/4 → 叶
    期望扁平：{2,3,4}（listsub 返子部门 / 根 1 不在结果）。
    """
    from ncmu_backend.dingtalk import client
    tree = {
        1: [{"dept_id": 2, "name": "中央研究院", "parent_id": 1},
            {"dept_id": 3, "name": "财务部", "parent_id": 1}],
        2: [{"dept_id": 4, "name": "软件开发部", "parent_id": 2}],
        3: [],
        4: [],
    }

    def _listsub(request: Request) -> Response:
        dept_id = _body(request).get("dept_id")
        return Response(200, json={"errcode": 0, "result": tree.get(dept_id, [])})

    with respx.mock(assert_all_called=True) as rx:
        rx.post(url__regex=rf"{OAPI}/topapi/v2/department/listsub.*").mock(
            side_effect=_listsub
        )
        depts = await client.list_departments(
            http_client, ding_settings, token="T", root_dept_id=1
        )
    by_id = {d["dept_id"]: d for d in depts}
    assert set(by_id) == {2, 3, 4}, f"flat dept set wrong: {by_id}"
    assert by_id[4]["name"] == "软件开发部"
    assert by_id[4]["parent_id"] == 2
    assert by_id[2]["parent_id"] == 1


async def test_list_departments_errcode_raises(http_client, ding_settings):
    """listsub errcode≠0 → DingTalkApiError。"""
    from ncmu_backend.dingtalk import client
    from ncmu_backend.dingtalk.client import DingTalkApiError
    with respx.mock(assert_all_called=True) as rx:
        rx.post(url__regex=rf"{OAPI}/topapi/v2/department/listsub.*").mock(
            return_value=Response(200, json={"errcode": 60020, "errmsg": "forbidden"})
        )
        with pytest.raises(DingTalkApiError) as exc:
            await client.list_departments(http_client, ding_settings, token="T")
    assert exc.value.errcode == 60020


# ── list_department_users — AC#4 分页 ───────────────────────────────────────


async def test_list_department_users_paginated(http_client, ding_settings):
    """has_more=true 两页 → 聚合全员 / 无重复 / 顺序稳定。"""
    from ncmu_backend.dingtalk import client
    pages = {
        0: {"list": [{"userid": "u1", "name": "张三"},
                     {"userid": "u2", "name": "李四"}],
            "has_more": True, "next_cursor": 2},
        2: {"list": [{"userid": "u3", "name": "王五"}],
            "has_more": False},
    }

    def _userlist(request: Request) -> Response:
        cursor = _body(request).get("cursor", 0)
        return Response(200, json={"errcode": 0, "result": pages[cursor]})

    with respx.mock(assert_all_called=True) as rx:
        rx.post(url__regex=rf"{OAPI}/topapi/v2/user/list.*").mock(
            side_effect=_userlist
        )
        users = await client.list_department_users(
            http_client, ding_settings, token="T", dept_id=4
        )
    ids = [u["userid"] for u in users]
    assert ids == ["u1", "u2", "u3"], f"order/aggregation wrong: {ids}"
    assert len(ids) == len(set(ids)), f"duplicates: {ids}"
    assert users[2]["name"] == "王五"


async def test_list_department_users_single_page(http_client, ding_settings):
    """has_more=false 单页 → 不再翻页 (1 次 HTTP)。"""
    from ncmu_backend.dingtalk import client
    with respx.mock(assert_all_called=True) as rx:
        route = rx.post(url__regex=rf"{OAPI}/topapi/v2/user/list.*").mock(
            return_value=Response(200, json={
                "errcode": 0,
                "result": {"list": [{"userid": "only", "name": "独苗"}],
                           "has_more": False},
            })
        )
        users = await client.list_department_users(
            http_client, ding_settings, token="T", dept_id=4
        )
    assert [u["userid"] for u in users] == ["only"]
    assert route.call_count == 1
