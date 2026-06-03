"""TASK-PC2-C — apps/services.py 双轨权限路由业务层 tests.

Phase 2C paradigm shift（spec §3.3 / plan §4.4 PC2-C 字面）：
- shared 路径 = 既有 DifyConsoleClient real-time + 5min lru cache (dify_console_client.py 字面禁区)
- owner 路径 = app_owners INNER JOIN dify_apps (alembic 0007 + 0004/0005/0006)
- union + dedupe by app_id (防御 / owner App 不应被 Dify Console 返回)

Test 覆盖：
- list_apps_for_user — 7 case (shared/owner/both/dedupe/empty/visibility filter/INNER JOIN orphan)
- user_can_access_app — 5 case (plan AC#3 字面)
- DifyConsoleClient lru cache 不动验证 (AC#6 5 bullet)
- owner SQL EXPLAIN grounded (AC#6 SQL grounded / I-INDEP-1)
- owner SQL perf baseline ≤ 30ms (AC#9)
"""
from __future__ import annotations

import time
import uuid

import httpx
import pytest
import pytest_asyncio
import respx
from fastapi import HTTPException
from httpx import Response
from sqlalchemy import text

from ncmu_backend.apps.dify_console_client import DifyConsoleClient
from ncmu_backend.config import Settings


# ── Dev seed users (conftest.async_db applies alembic/seeds/dev_users.sql) ──
ZHANGSAN_UUID = "a0000001-0000-4000-8000-000000000001"
LISI_UUID = "a0000001-0000-4000-8000-000000000002"


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def test_settings(monkeypatch) -> Settings:
    """Settings stub for services-level tests (no FastAPI app needed)."""
    monkeypatch.setenv("NCMU_DB_URL", "postgresql://dummy:dummy@localhost/dummy")
    monkeypatch.setenv("NCMU_JWT_SECRET", "test-secret-deterministic-32-bytes-long-xx")
    from ncmu_backend.config import reset_settings_cache, get_settings
    reset_settings_cache()
    s = get_settings()
    # Force deterministic values regardless of host env
    s = s.model_copy(update={
        "DIFY_BASE_URL": "http://test-dify:5001",
        "DIFY_CONSOLE_API_KEY": "test-console-key",
        "DIFY_TENANT_ID": "test-tenant-id",
    })
    yield s
    reset_settings_cache()


@pytest_asyncio.fixture
async def http_client():
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as c:
        yield c


@pytest.fixture
def fresh_cache():
    """Fresh DifyConsoleClient — no cache leak across tests."""
    return DifyConsoleClient(ttl_seconds=300)


async def _seed_owner(
    db, *, app_id: str, owner_uuid: str,
    visibility: str = "owner_only",
    cache_dify_app: bool = True,
    name: str | None = None,
    mode: str = "chat",
) -> None:
    """Seed one app_owners row + (optionally) the matching dify_apps cache row.

    cache_dify_app=False simulates an orphan app_owners row whose dify_apps
    cache entry is missing — INNER JOIN should exclude these.
    """
    if cache_dify_app:
        await db.execute(text(
            "INSERT INTO dify_apps (dify_app_id, name, mode) "
            "VALUES (:aid, :name, :mode) "
            "ON CONFLICT (dify_app_id) DO NOTHING"
        ), {"aid": app_id, "name": name or f"private-{app_id}", "mode": mode})
    await db.execute(text(
        "INSERT INTO app_owners (app_id, owner_user_id, visibility) "
        "VALUES (:aid, :uid, :v)"
    ), {"aid": app_id, "uid": owner_uuid, "v": visibility})
    await db.commit()


# ── list_apps_for_user — 7 cases ───────────────────────────────────────────


async def test_shared_only_returns_dify_kb_apps(
    async_db, http_client, fresh_cache, test_settings,
):
    """shared has 2 [KB] apps, owner has none → returns 2."""
    from ncmu_backend.apps import services
    with respx.mock(assert_all_called=True) as rx:
        rx.get(url__regex=r".*/console/api/apps.*").mock(
            return_value=Response(200, json={
                "data": [
                    {"id": "shared-1", "name": "[KB]员工手册", "mode": "chat"},
                    {"id": "shared-2", "name": "[KB]财务", "mode": "advanced-chat"},
                ],
            })
        )
        out = await services.list_apps_for_user(
            db=async_db,
            user_id=ZHANGSAN_UUID,
            http=http_client,
            cache=fresh_cache,
            settings=test_settings,
        )
    assert {a["id"] for a in out} == {"shared-1", "shared-2"}


async def test_shared_filters_non_kb_apps(
    async_db, http_client, fresh_cache, test_settings,
):
    """shared returns mix; only [KB] tag/prefix apps surface (守 detect_app_type)."""
    from ncmu_backend.apps import services
    with respx.mock(assert_all_called=True) as rx:
        rx.get(url__regex=r".*/console/api/apps.*").mock(
            return_value=Response(200, json={
                "data": [
                    {"id": "shared-1", "name": "[KB]员工手册", "mode": "chat"},
                    {"id": "shared-2", "name": "营销助手", "mode": "chat", "tags": []},
                    {"id": "shared-3", "name": "客服", "mode": "chat",
                     "tags": [{"name": "kb-qa", "type": "app"}]},
                ],
            })
        )
        out = await services.list_apps_for_user(
            db=async_db,
            user_id=ZHANGSAN_UUID,
            http=http_client,
            cache=fresh_cache,
            settings=test_settings,
        )
    # shared-2 ("营销助手" / no [KB] prefix / no kb-qa tag) filtered out
    assert {a["id"] for a in out} == {"shared-1", "shared-3"}


async def test_owner_only_no_shared(
    async_db, http_client, fresh_cache, test_settings,
):
    """shared empty, owner has 2 private apps → returns 2 owner apps."""
    from ncmu_backend.apps import services
    await _seed_owner(async_db, app_id="owner-app-1", owner_uuid=ZHANGSAN_UUID, name="personal-1")
    await _seed_owner(async_db, app_id="owner-app-2", owner_uuid=ZHANGSAN_UUID, name="personal-2")
    with respx.mock(assert_all_called=True) as rx:
        rx.get(url__regex=r".*/console/api/apps.*").mock(
            return_value=Response(200, json={"data": []})
        )
        out = await services.list_apps_for_user(
            db=async_db,
            user_id=ZHANGSAN_UUID,
            http=http_client,
            cache=fresh_cache,
            settings=test_settings,
        )
    assert {a["id"] for a in out} == {"owner-app-1", "owner-app-2"}


async def test_both_paths_union_no_overlap(
    async_db, http_client, fresh_cache, test_settings,
):
    """shared=[A] + owner=[B] → returns both / order shared-first (plan AC#1 字面)."""
    from ncmu_backend.apps import services
    await _seed_owner(async_db, app_id="owner-only", owner_uuid=ZHANGSAN_UUID)
    with respx.mock(assert_all_called=True) as rx:
        rx.get(url__regex=r".*/console/api/apps.*").mock(
            return_value=Response(200, json={
                "data": [{"id": "shared-only", "name": "[KB]A", "mode": "chat"}],
            })
        )
        out = await services.list_apps_for_user(
            db=async_db,
            user_id=ZHANGSAN_UUID,
            http=http_client,
            cache=fresh_cache,
            settings=test_settings,
        )
    ids = [a["id"] for a in out]
    assert ids == ["shared-only", "owner-only"], (
        f"expected shared-first ordering, got {ids}"
    )


async def test_dedupe_by_app_id_defensive(
    async_db, http_client, fresh_cache, test_settings,
):
    """同 app_id 在 shared 和 owner 两边都出现 → 返回 1 个 (字段级 unique 守 AC#4)。"""
    from ncmu_backend.apps import services
    await _seed_owner(async_db, app_id="both-paths-app", owner_uuid=ZHANGSAN_UUID,
                      name="owner-name", mode="advanced-chat")
    with respx.mock(assert_all_called=True) as rx:
        rx.get(url__regex=r".*/console/api/apps.*").mock(
            return_value=Response(200, json={
                "data": [{"id": "both-paths-app", "name": "[KB]shared-name", "mode": "chat"}],
            })
        )
        out = await services.list_apps_for_user(
            db=async_db,
            user_id=ZHANGSAN_UUID,
            http=http_client,
            cache=fresh_cache,
            settings=test_settings,
        )
    assert len(out) == 1
    ids = [a["id"] for a in out]
    assert len(ids) == len(set(ids)), f"duplicates in result: {ids}"
    # shared-first wins on dedupe (plan AC#1 字面：response.body shared 先 / owner 后)
    assert out[0]["id"] == "both-paths-app"
    assert out[0]["name"] == "[KB]shared-name", (
        "shared entry should win dedupe — Dify Console is source of truth"
    )


async def test_both_empty_returns_empty_list(
    async_db, http_client, fresh_cache, test_settings,
):
    """shared 空 + owner 空 → []."""
    from ncmu_backend.apps import services
    with respx.mock(assert_all_called=True) as rx:
        rx.get(url__regex=r".*/console/api/apps.*").mock(
            return_value=Response(200, json={"data": []})
        )
        out = await services.list_apps_for_user(
            db=async_db,
            user_id=ZHANGSAN_UUID,
            http=http_client,
            cache=fresh_cache,
            settings=test_settings,
        )
    assert out == []


async def test_owner_visibility_filter_only_owner_only(
    async_db, http_client, fresh_cache, test_settings,
):
    """app_owners.visibility='shared' 行不返回 (守 plan AC#2 WHERE 字面)."""
    from ncmu_backend.apps import services
    await _seed_owner(async_db, app_id="owner-1", owner_uuid=ZHANGSAN_UUID,
                      visibility="owner_only")
    await _seed_owner(async_db, app_id="shared-vis", owner_uuid=ZHANGSAN_UUID,
                      visibility="shared")
    await _seed_owner(async_db, app_id="public-vis", owner_uuid=ZHANGSAN_UUID,
                      visibility="public")
    with respx.mock(assert_all_called=True) as rx:
        rx.get(url__regex=r".*/console/api/apps.*").mock(
            return_value=Response(200, json={"data": []})
        )
        out = await services.list_apps_for_user(
            db=async_db,
            user_id=ZHANGSAN_UUID,
            http=http_client,
            cache=fresh_cache,
            settings=test_settings,
        )
    assert {a["id"] for a in out} == {"owner-1"}, (
        "only visibility='owner_only' rows should surface"
    )


async def test_inner_join_skips_orphan_app_owners_row(
    async_db, http_client, fresh_cache, test_settings,
):
    """app_owners 行无 dify_apps 缓存对应 → INNER JOIN 排除 (防御)."""
    from ncmu_backend.apps import services
    await _seed_owner(async_db, app_id="has-cache", owner_uuid=ZHANGSAN_UUID,
                      cache_dify_app=True)
    await _seed_owner(async_db, app_id="orphan-no-cache", owner_uuid=ZHANGSAN_UUID,
                      cache_dify_app=False)
    with respx.mock(assert_all_called=True) as rx:
        rx.get(url__regex=r".*/console/api/apps.*").mock(
            return_value=Response(200, json={"data": []})
        )
        out = await services.list_apps_for_user(
            db=async_db,
            user_id=ZHANGSAN_UUID,
            http=http_client,
            cache=fresh_cache,
            settings=test_settings,
        )
    assert {a["id"] for a in out} == {"has-cache"}, (
        f"INNER JOIN should drop orphans; got {out}"
    )


async def test_owner_scoped_to_user(
    async_db, http_client, fresh_cache, test_settings,
):
    """owner 路径只返回当前 user 的 owned apps — 别人的不漏。"""
    from ncmu_backend.apps import services
    await _seed_owner(async_db, app_id="my-app", owner_uuid=ZHANGSAN_UUID)
    await _seed_owner(async_db, app_id="someone-else-app", owner_uuid=LISI_UUID)
    with respx.mock(assert_all_called=True) as rx:
        rx.get(url__regex=r".*/console/api/apps.*").mock(
            return_value=Response(200, json={"data": []})
        )
        out = await services.list_apps_for_user(
            db=async_db,
            user_id=ZHANGSAN_UUID,
            http=http_client,
            cache=fresh_cache,
            settings=test_settings,
        )
    assert {a["id"] for a in out} == {"my-app"}, (
        "owner 路径必须 user_id 参数化 (守 plan AC#2)"
    )


# ── user_can_access_app — plan AC#3 5 case ─────────────────────────────────


async def test_can_access_owner_hit(
    async_db, http_client, fresh_cache, test_settings,
):
    """owner 路径命中 → True；不调 Dify Console (节省 RTT)."""
    from ncmu_backend.auth.permissions import user_can_access_app
    await _seed_owner(async_db, app_id="my-private-app", owner_uuid=ZHANGSAN_UUID)
    with respx.mock(assert_all_called=False) as rx:
        # The Dify Console mock is registered but NOT expected to be called
        dify_route = rx.get(url__regex=r".*/console/api/apps.*").mock(
            return_value=Response(200, json={"data": []})
        )
        ok = await user_can_access_app(
            db=async_db,
            user_id=ZHANGSAN_UUID,
            app_id="my-private-app",
            http=http_client,
            cache=fresh_cache,
            settings=test_settings,
        )
    assert ok is True
    assert dify_route.call_count == 0, (
        "owner hit short-circuits — should not call Dify Console"
    )


async def test_can_access_shared_hit(
    async_db, http_client, fresh_cache, test_settings,
):
    """owner miss + Dify Console returns matching id → True."""
    from ncmu_backend.auth.permissions import user_can_access_app
    with respx.mock(assert_all_called=True) as rx:
        rx.get(url__regex=r".*/console/api/apps.*").mock(
            return_value=Response(200, json={
                "data": [{"id": "shared-kb-1", "name": "[KB]A", "mode": "chat"}],
            })
        )
        ok = await user_can_access_app(
            db=async_db,
            user_id=ZHANGSAN_UUID,
            app_id="shared-kb-1",
            http=http_client,
            cache=fresh_cache,
            settings=test_settings,
        )
    assert ok is True


async def test_can_access_neither_returns_false(
    async_db, http_client, fresh_cache, test_settings,
):
    """owner miss + Dify Console has nothing matching → False."""
    from ncmu_backend.auth.permissions import user_can_access_app
    with respx.mock(assert_all_called=True) as rx:
        rx.get(url__regex=r".*/console/api/apps.*").mock(
            return_value=Response(200, json={
                "data": [{"id": "other-app", "name": "[KB]X", "mode": "chat"}],
            })
        )
        ok = await user_can_access_app(
            db=async_db,
            user_id=ZHANGSAN_UUID,
            app_id="non-existent",
            http=http_client,
            cache=fresh_cache,
            settings=test_settings,
        )
    assert ok is False


async def test_can_access_dify_5xx_propagates(
    async_db, http_client, fresh_cache, test_settings,
):
    """owner miss + Dify 5xx → HTTPException 502 propagates (守 routes 错误码语义)."""
    from ncmu_backend.auth.permissions import user_can_access_app
    with respx.mock(assert_all_called=True) as rx:
        rx.get(url__regex=r".*/console/api/apps.*").mock(
            return_value=Response(500, json={"error": "upstream broken"})
        )
        with pytest.raises(HTTPException) as exc_info:
            await user_can_access_app(
                db=async_db,
                user_id=ZHANGSAN_UUID,
                app_id="anything",
                http=http_client,
                cache=fresh_cache,
                settings=test_settings,
            )
    assert exc_info.value.status_code == 502
    assert exc_info.value.detail["code"] == 9001, (
        "5xx upstream maps to NCMU code 9001 (守 dify_status_to_ncmu)"
    )


async def test_can_access_empty_app_id_returns_false(
    async_db, http_client, fresh_cache, test_settings,
):
    """app_id 空串 → False (plan AC#6 case 'app_id 格式错' / 不抛错优雅退化)."""
    from ncmu_backend.auth.permissions import user_can_access_app
    with respx.mock(assert_all_called=False) as rx:
        dify_route = rx.get(url__regex=r".*/console/api/apps.*").mock(
            return_value=Response(200, json={"data": []})
        )
        ok = await user_can_access_app(
            db=async_db,
            user_id=ZHANGSAN_UUID,
            app_id="",
            http=http_client,
            cache=fresh_cache,
            settings=test_settings,
        )
    assert ok is False
    assert dify_route.call_count == 0, (
        "empty app_id should short-circuit (no DB / no upstream)"
    )


# ── DifyConsoleClient lru cache 不动验证 (plan AC#6 5 bullet) ──────────────


async def test_dify_console_lru_cache_intact(
    async_db, http_client, fresh_cache, test_settings,
):
    """同 user_id + 同 5 kwargs 调 2 次 services.list_apps_for_user → 仅 1 次
    underlying Dify Console HTTP (既有 5min lru cache 不动 / 守 dify_console_client.py 字面禁区)。
    """
    from ncmu_backend.apps import services
    with respx.mock(assert_all_called=True) as rx:
        route = rx.get(url__regex=r".*/console/api/apps.*").mock(
            return_value=Response(200, json={
                "data": [{"id": "cached-1", "name": "[KB]A", "mode": "chat"}],
            })
        )
        # Call twice
        out1 = await services.list_apps_for_user(
            db=async_db,
            user_id=ZHANGSAN_UUID,
            http=http_client,
            cache=fresh_cache,
            settings=test_settings,
        )
        out2 = await services.list_apps_for_user(
            db=async_db,
            user_id=ZHANGSAN_UUID,
            http=http_client,
            cache=fresh_cache,
            settings=test_settings,
        )
    assert out1 == out2
    assert route.call_count == 1, (
        f"DifyConsoleClient lru cache 不动：2 calls same user_id → 1 underlying HTTP; "
        f"got {route.call_count}"
    )


# ── owner SQL EXPLAIN grounded (plan AC#6 SQL grounded / I-INDEP-1) ────────


async def test_owner_sql_explain_uses_dify_apps_no_cast(async_db):
    """EXPLAIN (FORMAT JSON) on owner SQL — confirm:
    - plan references both `app_owners` and `dify_apps` tables
    - no implicit text/varchar coercion in JOIN (守 spec §1.5 调和 #6
      ao.app_id String(64) ↔ da.dify_app_id String(64) 类型对齐)

    Use ORM-built stmt mirroring services.py:query_owned_apps so column
    drift / WHERE drift propagates via attribute access (rename catches
    at compile time, not as silently-passing hardcoded SQL). REWORK-INDEP
    I1 fix — replace plan-literal hardcoded 5-col SQL with structural
    mirror of production 3-col projection.
    """
    from sqlalchemy import select
    from ncmu_backend.db.models import AppOwner, DifyApp

    stmt = (
        select(AppOwner.app_id, DifyApp.name, DifyApp.mode)
        .join(DifyApp, AppOwner.app_id == DifyApp.dify_app_id)
        .where(AppOwner.owner_user_id == uuid.UUID(ZHANGSAN_UUID))
        .where(AppOwner.visibility == "owner_only")
    )
    compiled = stmt.compile(
        dialect=async_db.bind.dialect,
        compile_kwargs={"literal_binds": True},
    )
    result = await async_db.execute(text(f"EXPLAIN (FORMAT JSON) {compiled}"))
    plan_rows = result.scalars().all()
    plan_str = str(plan_rows)
    # Both tables present
    assert "app_owners" in plan_str, f"app_owners absent from plan: {plan_str}"
    assert "dify_apps" in plan_str, f"dify_apps absent from plan: {plan_str}"
    # REWORK-INDEP I2 fix (Boss 拍板路径 B): 语义化断言守 spec §1.5 调和 #6
    # 真意 — String(64) ↔ String(64) 类型对齐让 JOIN 走索引主路径
    # (Hash Join / Nested Loop)，而非类型不匹配强制的 cross-join + Filter。
    # 注：PG EXPLAIN 中 "(col)::text" 是 varchar(n) 列的 op-family 标准
    # 注法（varchar 是带长度约束的 text 子类型），不是 implicit cast 类型
    # 不匹配；故不再用字面 "::text not in" 检（INDEP 原 or 短路弃，参
    # T2 INTENT-CHECK 2026-05-21 回复）。
    assert "Hash Join" in plan_str or "Nested Loop" in plan_str, (
        f"JOIN not using Hash/NestedLoop strategy "
        f"(type mismatch would force cross-join + Filter): {plan_str}"
    )


# ── TASK-2D-A1 — flag-ON tag routing (user_tags ∩ app_tags) ─────────────────
# spec §3 / §6 #1: flag ON → shared = shared_raw 中 dify_app_id 落在
# 「该 user 的 user_tags 与该 app 的 app_tags 有交集」的 App (含 [KB]，
# 统一标签控制，不再走 detect_app_type 名字过滤) ∪ owned。merge/dedupe/
# order 与 flag-OFF 现状一致 (shared 先 / owner 后 / shared 优先)。
# flag OFF 行为不变 (上面 7 case 已覆盖 [KB] 名字过滤 / 本批不动)。


async def _seed_tag(async_db, *, name: str) -> str:
    """Insert one tag (server_default gen_random_uuid()), return its id (str)."""
    row = (await async_db.execute(text(
        "INSERT INTO tags (name) VALUES (:name) RETURNING id"
    ), {"name": name})).first()
    await async_db.commit()
    return str(row.id)


async def _bind_user_tag(async_db, *, user_uuid: str, tag_id: str) -> None:
    await async_db.execute(text(
        "INSERT INTO user_tags (user_id, tag_id) VALUES (:u, :t)"
    ), {"u": user_uuid, "t": tag_id})
    await async_db.commit()


async def _bind_app_tag(async_db, *, app_id: str, tag_id: str) -> None:
    await async_db.execute(text(
        "INSERT INTO app_tags (dify_app_id, tag_id) VALUES (:a, :t)"
    ), {"a": app_id, "t": tag_id})
    await async_db.commit()


def _routing_on(settings) -> Settings:
    """Copy test settings with the routing flag flipped ON — NEVER touches
    prod default (config.py:110 stays False). spec §3 flag 策略 / AC#3."""
    return settings.model_copy(update={"tags_routing_enabled": True})


async def test_flag_on_shared_filtered_by_tag_intersection(
    async_db, http_client, fresh_cache, test_settings,
):
    """flag ON：仅 user_tags ∩ app_tags 非空的共享 App 可见 / 未匹配不可见 /
    过滤靠标签不靠名字 (3 个都是非-[KB] 名字)。"""
    from ncmu_backend.apps import services
    t_hr = await _seed_tag(async_db, name="HR")
    t_fin = await _seed_tag(async_db, name="Finance")
    await _bind_user_tag(async_db, user_uuid=ZHANGSAN_UUID, tag_id=t_hr)
    # shared-1 bound HR (user has HR) → visible
    await _bind_app_tag(async_db, app_id="shared-1", tag_id=t_hr)
    # shared-2 bound Finance (user lacks) → not visible
    await _bind_app_tag(async_db, app_id="shared-2", tag_id=t_fin)
    # shared-3 no tags → not visible
    with respx.mock(assert_all_called=True) as rx:
        rx.get(url__regex=r".*/console/api/apps.*").mock(
            return_value=Response(200, json={
                "data": [
                    {"id": "shared-1", "name": "营销助手", "mode": "chat"},
                    {"id": "shared-2", "name": "财务报表", "mode": "chat"},
                    {"id": "shared-3", "name": "通用问答", "mode": "chat"},
                ],
            })
        )
        out = await services.list_apps_for_user(
            db=async_db,
            user_id=ZHANGSAN_UUID,
            http=http_client,
            cache=fresh_cache,
            settings=_routing_on(test_settings),
        )
    assert {a["id"] for a in out} == {"shared-1"}, (
        f"flag ON should surface only user_tag ∩ app_tag match; got {out}"
    )


async def test_flag_on_kb_app_also_tag_filtered(
    async_db, http_client, fresh_cache, test_settings,
):
    """flag ON：[KB] 名 App 不开特例——无匹配 tag 则不可见；非-[KB] 名 App
    有匹配 tag 则可见。证明统一标签控制 (spec §2 决策 / §3)。"""
    from ncmu_backend.apps import services
    t_hr = await _seed_tag(async_db, name="HR")
    await _bind_user_tag(async_db, user_uuid=ZHANGSAN_UUID, tag_id=t_hr)
    # [KB]-named app but NO matching tag → must be hidden (no name 特例)
    # non-[KB] app WITH matching tag → visible
    await _bind_app_tag(async_db, app_id="plain-with-tag", tag_id=t_hr)
    with respx.mock(assert_all_called=True) as rx:
        rx.get(url__regex=r".*/console/api/apps.*").mock(
            return_value=Response(200, json={
                "data": [
                    {"id": "kb-no-tag", "name": "[KB]员工手册", "mode": "chat"},
                    {"id": "plain-with-tag", "name": "营销助手", "mode": "chat"},
                ],
            })
        )
        out = await services.list_apps_for_user(
            db=async_db,
            user_id=ZHANGSAN_UUID,
            http=http_client,
            cache=fresh_cache,
            settings=_routing_on(test_settings),
        )
    ids = {a["id"] for a in out}
    assert "kb-no-tag" not in ids, (
        "[KB] name must NOT bypass tag filter under flag ON (统一标签控制)"
    )
    assert ids == {"plain-with-tag"}, f"expected only tag-matched app; got {out}"


async def test_flag_on_owned_path_unaffected(
    async_db, http_client, fresh_cache, test_settings,
):
    """flag ON：owner 路径不受 flag 影响 (private owner_only App 照常返回，
    即使 user 无任何 tag 匹配)。spec §3 / AC#2 owned 不受 flag 影响。"""
    from ncmu_backend.apps import services
    # user has NO tags; owner has a private app
    await _seed_tag(async_db, name="Finance")  # exists but unbound to张三
    await _seed_owner(async_db, app_id="my-private", owner_uuid=ZHANGSAN_UUID,
                      name="私有助手")
    with respx.mock(assert_all_called=True) as rx:
        rx.get(url__regex=r".*/console/api/apps.*").mock(
            return_value=Response(200, json={
                "data": [{"id": "shared-x", "name": "[KB]A", "mode": "chat"}],
            })
        )
        out = await services.list_apps_for_user(
            db=async_db,
            user_id=ZHANGSAN_UUID,
            http=http_client,
            cache=fresh_cache,
            settings=_routing_on(test_settings),
        )
    ids = {a["id"] for a in out}
    assert "my-private" in ids, "owner_only App must surface regardless of flag/tags"
    assert "shared-x" not in ids, "shared App without matching tag hidden under ON"
    assert ids == {"my-private"}


async def test_flag_on_dedupe_and_order_preserved(
    async_db, http_client, fresh_cache, test_settings,
):
    """flag ON：dedupe by id + 顺序 (shared 先 / owner 后 / shared 优先)
    与 flag-OFF 现状一致。"""
    from ncmu_backend.apps import services
    t_hr = await _seed_tag(async_db, name="HR")
    await _bind_user_tag(async_db, user_uuid=ZHANGSAN_UUID, tag_id=t_hr)
    await _bind_app_tag(async_db, app_id="dup-app", tag_id=t_hr)
    await _bind_app_tag(async_db, app_id="shared-match", tag_id=t_hr)
    # owner also owns dup-app (collision) + an owner-only app
    await _seed_owner(async_db, app_id="dup-app", owner_uuid=ZHANGSAN_UUID,
                      name="owner-name", mode="advanced-chat")
    await _seed_owner(async_db, app_id="owner-only", owner_uuid=ZHANGSAN_UUID,
                      name="私有")
    with respx.mock(assert_all_called=True) as rx:
        rx.get(url__regex=r".*/console/api/apps.*").mock(
            return_value=Response(200, json={
                "data": [
                    {"id": "shared-match", "name": "营销", "mode": "chat"},
                    {"id": "dup-app", "name": "shared-dup-name", "mode": "chat"},
                ],
            })
        )
        out = await services.list_apps_for_user(
            db=async_db,
            user_id=ZHANGSAN_UUID,
            http=http_client,
            cache=fresh_cache,
            settings=_routing_on(test_settings),
        )
    ids = [a["id"] for a in out]
    assert ids == ["shared-match", "dup-app", "owner-only"], (
        f"order must be shared-first then owner; got {ids}"
    )
    assert len(ids) == len(set(ids)), f"duplicates: {ids}"
    # shared entry wins dedupe (source of truth) — same rule as flag-OFF
    dup = next(a for a in out if a["id"] == "dup-app")
    assert dup["name"] == "shared-dup-name", "shared entry should win dedupe"


async def test_flag_on_empty_user_tags_hides_all_shared(
    async_db, http_client, fresh_cache, test_settings,
):
    """flag ON + user 无任何 tag → 共享全隐 (restrictive 空集短路)。
    锁 REWORK Minor #1a：空 user_tag_ids 直接 shared=[]，即便 App 已打标签。"""
    from ncmu_backend.apps import services
    # An app IS tagged, but the user has NO tags at all.
    t_hr = await _seed_tag(async_db, name="HR")
    await _bind_app_tag(async_db, app_id="tagged-app", tag_id=t_hr)
    with respx.mock(assert_all_called=True) as rx:
        rx.get(url__regex=r".*/console/api/apps.*").mock(
            return_value=Response(200, json={
                "data": [
                    {"id": "tagged-app", "name": "营销助手", "mode": "chat"},
                    {"id": "kb-app", "name": "[KB]手册", "mode": "chat"},
                ],
            })
        )
        out = await services.list_apps_for_user(
            db=async_db,
            user_id=ZHANGSAN_UUID,
            http=http_client,
            cache=fresh_cache,
            settings=_routing_on(test_settings),
        )
    assert out == [], (
        "user with no tags must see zero shared apps under flag ON (restrictive)"
    )


async def test_flag_off_ignores_tags_keeps_kb_name_filter(
    async_db, http_client, fresh_cache, test_settings,
):
    """flag OFF (prod 现状)：完全忽略 tag 表，仍走 [KB] 名字过滤。
    证明 ON 分支不泄漏到 OFF 路径 (AC#1 prod 零回归 sentinel)。"""
    from ncmu_backend.apps import services
    t_hr = await _seed_tag(async_db, name="HR")
    await _bind_user_tag(async_db, user_uuid=ZHANGSAN_UUID, tag_id=t_hr)
    # non-[KB] app WITH matching tag — under OFF this must STILL be hidden
    await _bind_app_tag(async_db, app_id="tagged-plain", tag_id=t_hr)
    with respx.mock(assert_all_called=True) as rx:
        rx.get(url__regex=r".*/console/api/apps.*").mock(
            return_value=Response(200, json={
                "data": [
                    {"id": "tagged-plain", "name": "营销助手", "mode": "chat"},
                    {"id": "kb-app", "name": "[KB]手册", "mode": "chat"},
                ],
            })
        )
        out = await services.list_apps_for_user(
            db=async_db,
            user_id=ZHANGSAN_UUID,
            http=http_client,
            cache=fresh_cache,
            settings=test_settings,  # flag OFF (default False)
        )
    assert {a["id"] for a in out} == {"kb-app"}, (
        "flag OFF must keep [KB] name filter and ignore tags entirely"
    )


async def test_owner_sql_perf_baseline_under_30ms(async_db):
    """plan AC#9 — owner SQL ≤ 30ms with 100 owned apps (pgbench-style)."""
    # Seed 100 owned apps for 张三 + matching dify_apps rows
    for i in range(100):
        aid = f"perf-app-{i:03d}"
        await async_db.execute(text(
            "INSERT INTO dify_apps (dify_app_id, name, mode) "
            "VALUES (:aid, :name, 'chat') ON CONFLICT (dify_app_id) DO NOTHING"
        ), {"aid": aid, "name": f"perf-name-{i}"})
        await async_db.execute(text(
            "INSERT INTO app_owners (app_id, owner_user_id, visibility) "
            "VALUES (:aid, :uid, 'owner_only')"
        ), {"aid": aid, "uid": uuid.UUID(ZHANGSAN_UUID)})
    await async_db.commit()

    query = text("""
        SELECT ao.app_id, da.name, da.mode, da.api_token, ao.visibility
        FROM app_owners ao
        INNER JOIN dify_apps da ON ao.app_id = da.dify_app_id
        WHERE ao.owner_user_id = :uid AND ao.visibility = 'owner_only'
    """)
    # Warm cache (avoid cold-cache plan compilation latency)
    await async_db.execute(query, {"uid": uuid.UUID(ZHANGSAN_UUID)})

    # Take median of 5 runs to dampen GC / scheduler noise
    samples = []
    for _ in range(5):
        t0 = time.monotonic()
        result = await async_db.execute(query, {"uid": uuid.UUID(ZHANGSAN_UUID)})
        rows = result.all()
        elapsed_ms = (time.monotonic() - t0) * 1000
        assert len(rows) == 100, f"expected 100 rows, got {len(rows)}"
        samples.append(elapsed_ms)
    samples.sort()
    median = samples[2]
    assert median <= 30.0, (
        f"owner SQL perf regression: median {median:.2f}ms > 30ms baseline; "
        f"samples={samples}"
    )


# ── TASK-2DA1-GATE — gate 可见性 == 列表可见性（修 over-share / spec §7.1）──────
# Boss 2026-06-03 决策 B：保留 owner EXISTS 短路（owner 命中即 True / Dify-
# independent），只把非-owner 的 shared 判定从「raw Dify 共享列表」换成
# services.list_apps_for_user（owned ∪ 过滤后 shared / 消费 flag）。验收核心 =
# 「能访问 ⟺ 在该 user 的可见列表里」，flag OFF + flag ON 两态都要对齐。


async def test_gate_flag_off_visibility_equals_list_and_fixes_overshare(
    async_db, http_client, fresh_cache, test_settings,
):
    """AC#1 flag OFF：gate 判定 == 列表成员，逐 app_id 对齐。

    关键修复（regression-lock）：非-[KB] 的 raw 共享 App（列表里看不到的）
    旧实现门禁放行 = over-share；本修后 gate 必须 False。
    同时验证 [KB] 共享 App 仍可访问（dogfood 不误伤）+ owned 仍可访问。
    """
    from ncmu_backend.apps import services
    from ncmu_backend.auth.permissions import user_can_access_app

    await _seed_owner(async_db, app_id="owner-1", owner_uuid=ZHANGSAN_UUID,
                      name="私有助手")
    shared_payload = {
        "data": [
            {"id": "kb-1", "name": "[KB]员工手册", "mode": "chat"},
            {"id": "plain-1", "name": "营销助手", "mode": "chat"},  # 非-[KB] raw 共享
        ],
    }
    with respx.mock(assert_all_called=True) as rx:
        rx.get(url__regex=r".*/console/api/apps.*").mock(
            return_value=Response(200, json=shared_payload)
        )
        visible = await services.list_apps_for_user(
            db=async_db, user_id=ZHANGSAN_UUID, http=http_client,
            cache=fresh_cache, settings=test_settings,
        )
        visible_ids = {a["id"] for a in visible}

        # 逐 app_id：gate 判定必须 == 列表成员判定
        for app_id in ("kb-1", "plain-1", "owner-1", "ghost-never-seen"):
            ok = await user_can_access_app(
                db=async_db, user_id=ZHANGSAN_UUID, app_id=app_id,
                http=http_client, cache=fresh_cache, settings=test_settings,
            )
            assert ok == (app_id in visible_ids), (
                f"gate/list 裂开：app_id={app_id} gate={ok} "
                f"in_list={app_id in visible_ids} visible={visible_ids}"
            )

    # 字面断言修复语义（不只是 == 列表，固化预期值）
    assert visible_ids == {"kb-1", "owner-1"}, (
        f"flag OFF 可见集应 = [KB]∪owned；got {visible_ids}"
    )
    # over-share 已修：plain-1 在列表外 → gate False（旧实现这里会 True）
    overshare = await user_can_access_app(
        db=async_db, user_id=ZHANGSAN_UUID, app_id="plain-1",
        http=http_client, cache=fresh_cache, settings=test_settings,
    )
    assert overshare is False, "over-share 未修：非-[KB] raw 共享 App 仍过门禁"
    # [KB] 共享 App 仍可访问（dogfood 不误伤）
    kb_ok = await user_can_access_app(
        db=async_db, user_id=ZHANGSAN_UUID, app_id="kb-1",
        http=http_client, cache=fresh_cache, settings=test_settings,
    )
    assert kb_ok is True, "误伤：现有 [KB] 共享应用应仍可访问"


async def test_gate_flag_off_owner_short_circuit_no_dify_call(
    async_db, http_client, fresh_cache, test_settings,
):
    """owner 短路保留：owned App → True 且不调 Dify Console（决策 B 硬要求 /
    与既有 test_can_access_owner_hit call_count==0 同语义，本批不破）。"""
    from ncmu_backend.auth.permissions import user_can_access_app
    await _seed_owner(async_db, app_id="owned-no-dify", owner_uuid=ZHANGSAN_UUID)
    with respx.mock(assert_all_called=False) as rx:
        dify_route = rx.get(url__regex=r".*/console/api/apps.*").mock(
            return_value=Response(200, json={"data": []})
        )
        ok = await user_can_access_app(
            db=async_db, user_id=ZHANGSAN_UUID, app_id="owned-no-dify",
            http=http_client, cache=fresh_cache, settings=test_settings,
        )
    assert ok is True
    assert dify_route.call_count == 0, (
        "owner 短路被破坏：owned App 不应触发 Dify Console RTT"
    )


async def test_gate_flag_on_visibility_equals_list_tag_intersection(
    async_db, http_client, fresh_cache, test_settings,
):
    """AC#1 flag ON：gate == 列表，按 user_tags ∩ app_tags 判定。

    含 [KB] 名无 tag 也挡（统一标签控制 / 不走名字特例）；owned 不受 flag 影响。
    """
    from ncmu_backend.apps import services
    from ncmu_backend.auth.permissions import user_can_access_app

    t_hr = await _seed_tag(async_db, name="HR")
    await _seed_tag(async_db, name="Finance")  # 存在但不绑张三
    await _bind_user_tag(async_db, user_uuid=ZHANGSAN_UUID, tag_id=t_hr)
    # has-hr 绑 HR（张三有）→ 可见；has-fin 绑 Finance（张三无）→ 不可见
    await _bind_app_tag(async_db, app_id="has-hr", tag_id=t_hr)
    t_fin = await _seed_tag(async_db, name="FinanceBind")
    await _bind_app_tag(async_db, app_id="has-fin", tag_id=t_fin)
    # kb-notag：[KB] 名但无 tag → flag ON 必须挡（无名字特例）
    await _seed_owner(async_db, app_id="owner-on", owner_uuid=ZHANGSAN_UUID,
                      name="私有")

    settings_on = _routing_on(test_settings)
    shared_payload = {
        "data": [
            {"id": "has-hr", "name": "营销助手", "mode": "chat"},
            {"id": "has-fin", "name": "财务报表", "mode": "chat"},
            {"id": "kb-notag", "name": "[KB]员工手册", "mode": "chat"},
        ],
    }
    with respx.mock(assert_all_called=True) as rx:
        rx.get(url__regex=r".*/console/api/apps.*").mock(
            return_value=Response(200, json=shared_payload)
        )
        visible = await services.list_apps_for_user(
            db=async_db, user_id=ZHANGSAN_UUID, http=http_client,
            cache=fresh_cache, settings=settings_on,
        )
        visible_ids = {a["id"] for a in visible}

        for app_id in ("has-hr", "has-fin", "kb-notag", "owner-on", "ghost"):
            ok = await user_can_access_app(
                db=async_db, user_id=ZHANGSAN_UUID, app_id=app_id,
                http=http_client, cache=fresh_cache, settings=settings_on,
            )
            assert ok == (app_id in visible_ids), (
                f"flag ON gate/list 裂开：app_id={app_id} gate={ok} "
                f"in_list={app_id in visible_ids} visible={visible_ids}"
            )

    assert visible_ids == {"has-hr", "owner-on"}, (
        f"flag ON 可见集应 = tag 交集 ∪ owned；got {visible_ids}"
    )
    # [KB] 名无 tag 必挡
    kb_blocked = await user_can_access_app(
        db=async_db, user_id=ZHANGSAN_UUID, app_id="kb-notag",
        http=http_client, cache=fresh_cache, settings=settings_on,
    )
    assert kb_blocked is False, "flag ON：[KB] 名 App 无匹配 tag 必须挡（统一标签控制）"


async def test_gate_flag_on_owner_unaffected_no_tag_no_dify(
    async_db, http_client, fresh_cache, test_settings,
):
    """flag ON：owner 路径不受 flag 影响——user 无任何 tag，owned App 仍 True
    且不调 Dify（owner 短路在 flag ON 下同样保留）。"""
    from ncmu_backend.auth.permissions import user_can_access_app
    await _seed_owner(async_db, app_id="owner-on-notag", owner_uuid=ZHANGSAN_UUID,
                      name="私有助手")
    settings_on = _routing_on(test_settings)
    with respx.mock(assert_all_called=False) as rx:
        dify_route = rx.get(url__regex=r".*/console/api/apps.*").mock(
            return_value=Response(200, json={"data": []})
        )
        ok = await user_can_access_app(
            db=async_db, user_id=ZHANGSAN_UUID, app_id="owner-on-notag",
            http=http_client, cache=fresh_cache, settings=settings_on,
        )
    assert ok is True
    assert dify_route.call_count == 0, (
        "flag ON 下 owner 短路应保留：owned App 不应触发 Dify Console RTT"
    )
