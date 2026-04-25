"""TASK-28 AC#1-5 — sessions endpoint behaviour + ownership 404 防探测.

Test layout (8 tests):
  AC#1 — list returns my sessions only, newest first, soft-deleted hidden
  AC#2 — list isolation: zhangsan's call never sees lisi's rows
  AC#3 — messages: ownership 404 (lisi accessing zhangsan's session)
  AC#4 — messages: real session passthrough Dify (respx mock)
  AC#5 — patch rename: title updated + updated_at bumped + 404 cross-user
  AC#6 — delete: soft delete writes deleted_at + 404 on second access
  AC#7 — delete: cross-user 404
  AC#8 — patch: validation 422 on empty title
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
import respx
from httpx import Response
from sqlalchemy import text

# Dev seed users (alembic/seeds/dev_users.sql).
ZHANGSAN = "a0000001-0000-4000-8000-000000000001"  # admin
LISI = "a0000001-0000-4000-8000-000000000002"      # non-admin
WANGWU = "a0000001-0000-4000-8000-000000000003"

DIFY_APP_ID = "1ee8a2c4-ce74-429b-b2b3-93de7b837a94"  # TASK-24 [KB]员工手册


def _sign_for(user_id: str, name: str, secret: str) -> str:
    """Helper: sign a JWT for a given seed user (mirrors auth.deps contract)."""
    from ncmu_backend.auth.jwt_utils import sign_jwt
    token, _ = sign_jwt(user_id=user_id, name=name, secret=secret)
    return token


async def _insert_session(
    async_db,
    *,
    user_id: str,
    title: str = "测试会话",
    deleted: bool = False,
    minutes_ago: int = 0,
) -> str:
    """Create a chat_sessions row for a given user; return the new id."""
    sid = str(uuid4())
    dify_conv = str(uuid4())
    deleted_clause = (
        ", deleted_at = NOW() - interval '5 minutes'" if deleted else ""
    )
    await async_db.execute(
        text(
            f"""
            INSERT INTO chat_sessions
              (id, user_id, dify_app_id, dify_conversation_id, title,
               created_at, updated_at)
            VALUES
              (:id, :uid, :app, :conv, :title,
               NOW() - interval '{minutes_ago} minutes',
               NOW() - interval '{minutes_ago} minutes')
            """
        ),
        {
            "id": sid,
            "uid": user_id,
            "app": DIFY_APP_ID,
            "conv": dify_conv,
            "title": title,
        },
    )
    if deleted:
        await async_db.execute(
            text("UPDATE chat_sessions SET deleted_at = NOW() WHERE id = :id"),
            {"id": sid},
        )
    await async_db.commit()
    return sid


# ===================================================================== AC#1
async def test_list_returns_my_sessions_newest_first_excluding_deleted(
    app_client, async_db, jwt_token,
):
    """张三 has 2 active + 1 soft-deleted; list shows the 2 active, newest first."""
    # 30 minutes ago, then 5 minutes ago, then deleted (latest minute_ago).
    s_old = await _insert_session(async_db, user_id=ZHANGSAN, title="old", minutes_ago=30)
    s_new = await _insert_session(async_db, user_id=ZHANGSAN, title="new", minutes_ago=5)
    s_del = await _insert_session(async_db, user_id=ZHANGSAN, title="deleted", deleted=True)

    r = await app_client.get(
        "/api/v1/ncmu/sessions",
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    ids = [item["id"] for item in body["items"]]
    assert ids == [s_new, s_old], "newest first; soft-deleted must be filtered out"
    assert s_del not in ids
    assert body["next_cursor"] is None  # < limit items, no next page


# ===================================================================== AC#2
async def test_list_user_isolation_no_cross_user_leak(
    app_client, async_db, jwt_secret,
):
    """list 调用必须只返回 jwt 持有者自己的会话；他人的不出现。"""
    mine = await _insert_session(async_db, user_id=LISI, title="lisi's session")
    foreign = await _insert_session(async_db, user_id=ZHANGSAN, title="zhangsan's session")

    lisi_token = _sign_for(LISI, "李四", jwt_secret)
    r = await app_client.get(
        "/api/v1/ncmu/sessions",
        headers={"Authorization": f"Bearer {lisi_token}"},
    )
    assert r.status_code == 200
    ids = [item["id"] for item in r.json()["items"]]
    assert mine in ids
    assert foreign not in ids


# ===================================================================== AC#3
async def test_messages_cross_user_returns_404(
    app_client, async_db, jwt_secret,
):
    """李四 试图读张三的 session messages → 404 + code 1300（不暴露存在性）。"""
    sid = await _insert_session(async_db, user_id=ZHANGSAN, title="zhangsan's secret")

    lisi_token = _sign_for(LISI, "李四", jwt_secret)
    r = await app_client.get(
        f"/api/v1/ncmu/sessions/{sid}/messages",
        headers={"Authorization": f"Bearer {lisi_token}"},
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == 1300


# ===================================================================== AC#4
async def test_messages_passthrough_dify_via_respx_mock(
    app_client, async_db, jwt_token,
):
    """命中所有权 → 透传 Dify /v1/messages（respx mock 假上游）。"""
    sid = await _insert_session(async_db, user_id=ZHANGSAN, title="real one")

    fake_dify_payload = {
        "data": [
            {
                "id": "msg-1",
                "conversation_id": "conv-fake",
                "query": "Hi",
                "answer": "Hello",
                "created_at": 1777088888,
            },
            {
                "id": "msg-2",
                "conversation_id": "conv-fake",
                "query": "Bye",
                "answer": "Goodbye",
                "created_at": 1777088999,
            },
        ],
        "has_more": False,
        "limit": 20,
    }

    # The conftest's app_client doesn't run the FastAPI lifespan (httpx
    # ASGITransport limitation), so app.state.dify_client is unset by
    # default. Inject one directly so the lazy lookup in the handler
    # finds it; respx will intercept the actual HTTP call regardless.
    import httpx as _httpx
    from ncmu_backend.main import app
    app.state.dify_client = _httpx.AsyncClient(timeout=_httpx.Timeout(10.0))

    try:
        with respx.mock(assert_all_called=False) as router:
            # Match any GET to Dify /v1/messages regardless of host (test
            # config uses DIFY_BASE_URL=http://dify-api:5001 default).
            router.get(url__regex=r".*/v1/messages.*").mock(
                return_value=Response(200, json=fake_dify_payload)
            )

            r = await app_client.get(
                f"/api/v1/ncmu/sessions/{sid}/messages?limit=20",
                headers={"Authorization": f"Bearer {jwt_token}"},
            )
    finally:
        await app.state.dify_client.aclose()
        del app.state.dify_client

    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["data"]) == 2
    assert body["data"][0]["id"] == "msg-1"
    assert body["has_more"] is False
    assert body["limit"] == 20


# ===================================================================== AC#5
async def test_patch_rename_updates_title_and_bumps_updated_at(
    app_client, async_db, jwt_token,
):
    """重命名 → title 改 + updated_at 新 + GET list 后该 session 在最前。"""
    sid_a = await _insert_session(async_db, user_id=ZHANGSAN, title="A", minutes_ago=10)
    sid_b = await _insert_session(async_db, user_id=ZHANGSAN, title="B", minutes_ago=2)
    # Initially B is newer; rename A -> A_new should bump A to top.

    r = await app_client.patch(
        f"/api/v1/ncmu/sessions/{sid_a}",
        json={"title": "A_renamed"},
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "A_renamed"
    assert r.json()["id"] == sid_a

    list_r = await app_client.get(
        "/api/v1/ncmu/sessions",
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    items = list_r.json()["items"]
    assert items[0]["id"] == sid_a, "renamed session should now be on top"
    assert items[0]["title"] == "A_renamed"


async def test_patch_cross_user_returns_404(
    app_client, async_db, jwt_secret,
):
    """李四 试图改张三的 session.title → 404，且 DB 中 title 不变。"""
    sid = await _insert_session(async_db, user_id=ZHANGSAN, title="ORIG")
    lisi_token = _sign_for(LISI, "李四", jwt_secret)

    r = await app_client.patch(
        f"/api/v1/ncmu/sessions/{sid}",
        json={"title": "HACKED"},
        headers={"Authorization": f"Bearer {lisi_token}"},
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == 1300

    # DB row untouched.
    row = (
        await async_db.execute(
            text("SELECT title FROM chat_sessions WHERE id = :id"), {"id": sid}
        )
    ).scalar_one()
    assert row == "ORIG", "title must not be modified on a 404'd PATCH"


# ===================================================================== AC#6
async def test_delete_writes_deleted_at_and_subsequent_404(
    app_client, async_db, jwt_token,
):
    """DELETE → 204 + deleted_at 写入 + 再读 messages 返 404 + list 不出现。"""
    sid = await _insert_session(async_db, user_id=ZHANGSAN, title="to-delete")

    r = await app_client.delete(
        f"/api/v1/ncmu/sessions/{sid}",
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert r.status_code == 204

    deleted_at = (
        await async_db.execute(
            text("SELECT deleted_at FROM chat_sessions WHERE id = :id"), {"id": sid}
        )
    ).scalar_one()
    assert deleted_at is not None, "DELETE must write deleted_at column"
    assert (datetime.now(timezone.utc) - deleted_at).total_seconds() < 30

    # Second access: PATCH the now-deleted session must 404 (the same
    # ownership filter excludes deleted_at IS NOT NULL rows). We use
    # PATCH instead of /messages to avoid pulling in Dify (no need for
    # an upstream client just to assert "this row is gone").
    second = await app_client.patch(
        f"/api/v1/ncmu/sessions/{sid}",
        json={"title": "should-fail"},
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert second.status_code == 404
    assert second.json()["detail"]["code"] == 1300

    # list now empty for zhangsan
    list_r = await app_client.get(
        "/api/v1/ncmu/sessions",
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    ids = [item["id"] for item in list_r.json()["items"]]
    assert sid not in ids


# ===================================================================== AC#7
async def test_delete_cross_user_returns_404_and_keeps_row(
    app_client, async_db, jwt_secret,
):
    """李四 试图 DELETE 张三的 session → 404 + DB row 仍未删除。"""
    sid = await _insert_session(async_db, user_id=ZHANGSAN, title="zhangsan-keep")
    lisi_token = _sign_for(LISI, "李四", jwt_secret)

    r = await app_client.delete(
        f"/api/v1/ncmu/sessions/{sid}",
        headers={"Authorization": f"Bearer {lisi_token}"},
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == 1300

    deleted_at = (
        await async_db.execute(
            text("SELECT deleted_at FROM chat_sessions WHERE id = :id"), {"id": sid}
        )
    ).scalar_one()
    assert deleted_at is None, "row must remain alive on a cross-user 404'd DELETE"


# ===================================================================== AC#8
async def test_patch_empty_title_returns_422(app_client, async_db, jwt_token):
    """空 title → pydantic validation → 422（min_length=1）。"""
    sid = await _insert_session(async_db, user_id=ZHANGSAN, title="non-empty")
    r = await app_client.patch(
        f"/api/v1/ncmu/sessions/{sid}",
        json={"title": ""},
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert r.status_code == 422
