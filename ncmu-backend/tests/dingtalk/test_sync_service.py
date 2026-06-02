"""TASK-2DA2S-03 — DingTalk 通讯录同步编排测试。

两组：
1. ``sync_contacts`` 直调单测（respx mock 钉钉 oapi + 真 async_db）—— AC#1~#4b。
2. admin 路由测（app_client + respx）—— AC#5（403/200 + shape）。

钉钉接口**全 respx mock**（权限生效前不依赖真钉钉）。token 缓存是 client.py
进程级 module global，每测前 reset（防跨测泄漏 / 影响 gettoken 计数与幂等语义）。

respx 在 ASGITransport（app_client 跑 app 自身）之外只 patch 默认
AsyncHTTPTransport，故路由内 ``httpx.AsyncClient()`` 调钉钉被拦、app_client 自身
ASGI 调用不受影响。
"""
from __future__ import annotations

import json
import uuid

import httpx
import pytest
import pytest_asyncio
import respx
from httpx import Request, Response
from sqlalchemy import func, select

from ncmu_backend.config import Settings
from ncmu_backend.db.models import DepartmentTagMapping, Tag, User, UserTag


OAPI = "https://test-ding.example"


# --------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------- #
@pytest.fixture
def ding_settings() -> Settings:
    """指向测试 oapi base 的确定性 Settings（init kwargs 覆盖容器 env）。"""
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
    """进程级 token 缓存不得跨测泄漏（幂等 / gettoken 计数语义）。"""
    from ncmu_backend.dingtalk import client

    client.reset_token_cache()
    yield
    client.reset_token_cache()


def _body(request: Request) -> dict:
    return json.loads(request.content or b"{}")


def _mock_dingtalk(rx, base: str, tree: dict, members: dict) -> None:
    """注册 gettoken + listsub + user/list 三接口的 respx mock。

    ``tree``: ``{dept_id: [{dept_id,name,parent_id}, ...]}`` 子部门（listsub 语义）。
    ``members``: ``{dept_id: [{userid,name}, ...]}`` 直属成员（单页）。
    缺省 dept 返回空。
    """
    rx.get(url__regex=rf"{base}/gettoken.*").mock(
        return_value=Response(200, json={
            "errcode": 0, "access_token": "TOKEN", "expires_in": 7200,
        })
    )

    def _listsub(request: Request) -> Response:
        dept_id = _body(request).get("dept_id")
        return Response(200, json={"errcode": 0, "result": tree.get(dept_id, [])})

    rx.post(url__regex=rf"{base}/topapi/v2/department/listsub.*").mock(side_effect=_listsub)

    def _userlist(request: Request) -> Response:
        dept_id = _body(request).get("dept_id")
        return Response(200, json={
            "errcode": 0,
            "result": {"list": members.get(dept_id, []), "has_more": False},
        })

    rx.post(url__regex=rf"{base}/topapi/v2/user/list.*").mock(side_effect=_userlist)


async def _new_tag(async_db, name: str) -> uuid.UUID:
    tag = Tag(name=name)
    async_db.add(tag)
    await async_db.commit()
    await async_db.refresh(tag)
    return tag.id


async def _user_by_ding(async_db, ding_uid: str) -> User | None:
    return (
        await async_db.execute(select(User).where(User.dingtalk_userid == ding_uid))
    ).scalar_one_or_none()


async def _tag_ids_of(async_db, user_id) -> set:
    rows = (
        await async_db.execute(select(UserTag.tag_id).where(UserTag.user_id == user_id))
    ).scalars().all()
    return set(rows)


# ===================================================================== #
# AC#1 — 建/更 + 字段落库 + 计数
# ===================================================================== #
async def test_sync_creates_and_updates_with_fields_and_counts(
    async_db, http_client, ding_settings
):
    """子树：root=100 → 子部门 101「后端组」。mapping 100→A, 101→B。
    成员：100=[既有 ding-exist, 新 ding-n1]，101=[新 ding-n2]。
    断言 update 既有 / INSERT 新行（dingtalk_userid+name+dept_path，无凭据）/
    user_tags 按 mapping / created=2 updated=1。"""
    from ncmu_backend.dingtalk.sync_service import sync_contacts

    tag_a = await _new_tag(async_db, "标签A")
    tag_b = await _new_tag(async_db, "标签B")
    async_db.add_all([
        DepartmentTagMapping(dept_id=100, tag_id=tag_a),
        DepartmentTagMapping(dept_id=101, tag_id=tag_b),
    ])
    # 既有 NCMU user（B 策略 update 分支）
    existing = User(id=uuid.uuid4(), dingtalk_userid="ding-exist", name="老名", dept_path=None)
    async_db.add(existing)
    await async_db.commit()
    existing_id = existing.id

    tree = {100: [{"dept_id": 101, "name": "后端组", "parent_id": 100}], 101: []}
    members = {
        100: [{"userid": "ding-exist", "name": "新名"}, {"userid": "ding-n1", "name": "员工1"}],
        101: [{"userid": "ding-n2", "name": "员工2"}],
    }

    with respx.mock(assert_all_called=False) as rx:
        _mock_dingtalk(rx, OAPI, tree, members)
        result = await sync_contacts(async_db, http_client, ding_settings, root_dept_id=100)

    assert result.departments == 2  # 101 子部门 ∪ {100 root}
    assert result.users_seen == 3
    assert result.users_created == 2
    assert result.users_updated == 1
    assert result.tags_written == 3  # exist→A, n1→A, n2→B

    # 既有 user 走 update：名字被钉钉名覆盖、打上 A、行未重复建
    upd = await _user_by_ding(async_db, "ding-exist")
    assert upd.id == existing_id
    assert upd.name == "新名"
    assert await _tag_ids_of(async_db, upd.id) == {tag_a}

    # 新 user INSERT：dingtalk_userid + name + dept_path 落库，user_tags 正确
    n1 = await _user_by_ding(async_db, "ding-n1")
    assert n1 is not None and n1.name == "员工1"
    assert n1.dept_path is None  # 仅属 root(100)，无命名子部门 → None
    assert not hasattr(n1, "password") and not hasattr(n1, "email")  # 模型无凭据列
    assert await _tag_ids_of(async_db, n1.id) == {tag_a}

    n2 = await _user_by_ding(async_db, "ding-n2")
    assert n2 is not None and n2.dept_path == "/后端组"  # 101 命名子部门链
    assert await _tag_ids_of(async_db, n2.id) == {tag_b}


# ===================================================================== #
# AC#2 — 幂等：连跑 2 次不重复建 / tags 无重复
# ===================================================================== #
async def test_sync_idempotent_second_run_all_update(
    async_db, http_client, ding_settings
):
    from ncmu_backend.dingtalk.sync_service import sync_contacts

    tag_a = await _new_tag(async_db, "标签A")
    async_db.add(DepartmentTagMapping(dept_id=100, tag_id=tag_a))
    await async_db.commit()

    tree = {100: []}
    members = {100: [{"userid": "ding-x", "name": "甲"}, {"userid": "ding-y", "name": "乙"}]}

    with respx.mock(assert_all_called=False) as rx:
        _mock_dingtalk(rx, OAPI, tree, members)
        r1 = await sync_contacts(async_db, http_client, ding_settings, root_dept_id=100)
        r2 = await sync_contacts(async_db, http_client, ding_settings, root_dept_id=100)

    assert r1.users_created == 2 and r1.users_updated == 0
    assert r2.users_created == 0 and r2.users_updated == 2  # 第 2 次全 update

    # user 行数不增（按 dingtalk_userid 唯一）
    total_synced = await async_db.scalar(
        select(func.count()).select_from(User).where(
            User.dingtalk_userid.in_(["ding-x", "ding-y"])
        )
    )
    assert total_synced == 2
    # user_tags 无重复（每 user 1 个 A，replace-all）
    ux = await _user_by_ding(async_db, "ding-x")
    assert await _tag_ids_of(async_db, ux.id) == {tag_a}
    assert await async_db.scalar(
        select(func.count()).select_from(UserTag).where(UserTag.user_id == ux.id)
    ) == 1


# ===================================================================== #
# AC#3 — 多部门归属 → user_tags 并集
# ===================================================================== #
async def test_sync_multi_department_union_tags(
    async_db, http_client, ding_settings
):
    """user 同时属子树内 2 个有 mapping 的部门 → user_tags = 并集。"""
    from ncmu_backend.dingtalk.sync_service import sync_contacts

    tag_a = await _new_tag(async_db, "标签A")
    tag_b = await _new_tag(async_db, "标签B")
    async_db.add_all([
        DepartmentTagMapping(dept_id=100, tag_id=tag_a),
        DepartmentTagMapping(dept_id=101, tag_id=tag_b),
    ])
    await async_db.commit()

    tree = {100: [{"dept_id": 101, "name": "后端组", "parent_id": 100}], 101: []}
    # 同一 userid 出现在 100 和 101 两个部门
    members = {
        100: [{"userid": "ding-multi", "name": "多部门"}],
        101: [{"userid": "ding-multi", "name": "多部门"}],
    }

    with respx.mock(assert_all_called=False) as rx:
        _mock_dingtalk(rx, OAPI, tree, members)
        result = await sync_contacts(async_db, http_client, ding_settings, root_dept_id=100)

    assert result.users_seen == 1  # 去重后 1 个 user
    u = await _user_by_ding(async_db, "ding-multi")
    assert await _tag_ids_of(async_db, u.id) == {tag_a, tag_b}  # 并集


# ===================================================================== #
# AC#4 — scope 隔离：子树外既有 user 不被碰
# ===================================================================== #
async def test_sync_scope_isolation_outside_user_untouched(
    async_db, http_client, ding_settings
):
    from ncmu_backend.dingtalk.sync_service import sync_contacts

    tag_a = await _new_tag(async_db, "标签A")
    tag_c = await _new_tag(async_db, "标签C")
    async_db.add(DepartmentTagMapping(dept_id=100, tag_id=tag_a))
    # 子树外既有 user（dingtalk_userid 不在任何 mock 成员里）+ 预置 tag_c + dept_path
    outsider = User(
        id=uuid.uuid4(), dingtalk_userid="ding-outside", name="局外人", dept_path="/别处"
    )
    async_db.add(outsider)
    await async_db.commit()
    outsider_id = outsider.id
    async_db.add(UserTag(user_id=outsider_id, tag_id=tag_c))
    await async_db.commit()

    tree = {100: []}
    members = {100: [{"userid": "ding-inside", "name": "组内人"}]}

    with respx.mock(assert_all_called=False) as rx:
        _mock_dingtalk(rx, OAPI, tree, members)
        await sync_contacts(async_db, http_client, ding_settings, root_dept_id=100)

    # 局外人 tags / dept_path / name 全不变
    out = await _user_by_ding(async_db, "ding-outside")
    assert out.id == outsider_id
    assert out.name == "局外人"
    assert out.dept_path == "/别处"
    assert await _tag_ids_of(async_db, out.id) == {tag_c}


# ===================================================================== #
# AC#4b — 根部门成员被同步（反例：证明 depts ∪ {root} 而非只子部门）
# ===================================================================== #
async def test_sync_root_direct_member_is_synced(
    async_db, http_client, ding_settings
):
    """root=200 → 子部门 201（无成员）；成员只直属 root 200。
    若实现只遍历子部门则该 user 被漏；断言其被建 + 打 root mapping tag。"""
    from ncmu_backend.dingtalk.sync_service import sync_contacts

    tag_root = await _new_tag(async_db, "根标签")
    async_db.add(DepartmentTagMapping(dept_id=200, tag_id=tag_root))
    await async_db.commit()

    tree = {200: [{"dept_id": 201, "name": "子组", "parent_id": 200}], 201: []}
    members = {200: [{"userid": "ding-root-direct", "name": "根直属"}], 201: []}

    with respx.mock(assert_all_called=False) as rx:
        _mock_dingtalk(rx, OAPI, tree, members)
        result = await sync_contacts(async_db, http_client, ding_settings, root_dept_id=200)

    assert result.users_created == 1
    u = await _user_by_ding(async_db, "ding-root-direct")
    assert u is not None  # 没被漏
    assert await _tag_ids_of(async_db, u.id) == {tag_root}  # 打上 root mapping


# ===================================================================== #
# AC#5 — admin 路由门禁 + shape（app_client + respx）
# ===================================================================== #
PROD_OAPI = "https://oapi.dingtalk.com"  # app_client 用容器 env 默认 oapi base
LISI = "a0000001-0000-4000-8000-000000000002"  # 非 admin dev id


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_dingtalk_endpoints_non_admin_403(app_client, jwt_secret):
    from ncmu_backend.auth.jwt_utils import sign_jwt

    lisi, _ = sign_jwt(user_id=LISI, name="李四", secret=jwt_secret)
    get = await app_client.get(
        "/api/v1/ncmu/admin/dingtalk/departments", headers=_auth(lisi)
    )
    post = await app_client.post(
        "/api/v1/ncmu/admin/dingtalk/sync", headers=_auth(lisi), json={"root_dept_id": 100}
    )
    assert get.status_code == 403 and post.status_code == 403
    assert get.json()["detail"]["code"] == 1201
    assert post.json()["detail"]["code"] == 1201


async def test_discover_departments_admin_200_shape(app_client, jwt_token):
    tree = {1: [{"dept_id": 2, "name": "中央研究院", "parent_id": 1}], 2: []}
    with respx.mock(assert_all_called=False) as rx:
        _mock_dingtalk(rx, PROD_OAPI, tree, {})
        resp = await app_client.get(
            "/api/v1/ncmu/admin/dingtalk/departments", headers=_auth(jwt_token)
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "departments" in body
    ids = {d["dept_id"] for d in body["departments"]}
    assert 2 in ids


async def test_trigger_sync_admin_200_shape(app_client, jwt_token, async_db):
    tree = {300: []}
    members = {300: [{"userid": "ding-route", "name": "路由测"}]}
    with respx.mock(assert_all_called=False) as rx:
        _mock_dingtalk(rx, PROD_OAPI, tree, members)
        resp = await app_client.post(
            "/api/v1/ncmu/admin/dingtalk/sync",
            headers=_auth(jwt_token),
            json={"root_dept_id": 300},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {
        "root_dept_id", "departments", "users_seen",
        "users_created", "users_updated", "tags_written",
    }
    assert body["root_dept_id"] == 300
    assert body["users_created"] == 1
    # 真落库（route 用 async_db override）
    assert await _user_by_ding(async_db, "ding-route") is not None
