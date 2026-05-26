"""TASK-BUG-2 — GET /api/v1/ncmu/apps/{app_id}/parameters coverage.

Wire shape grounded against docker exec live capture (2026-05-26):
- chat / completion / agent-chat → model_config.user_input_form NESTED:
    [{"<type>": {"variable", "label", "required", "options"?, "default"?}}, ...]
- workflow / advanced-chat → workflows/draft.graph.nodes[type=start].data.variables FLAT:
    [{"variable", "label", "type", "required", "options", "placeholder", ...}, ...]

Mock pattern (守 feedback_tdd_mock_vs_real_api): respx returns real-shape
JSON the route handler actually parses; tests do not assert intermediate
state — only the HTTP response body (字段级断言 / 守 feedback_pre_existing_
error_strict_validation).
"""
from __future__ import annotations

import httpx
import pytest_asyncio
import respx
from httpx import Response
from sqlalchemy import text


ZHANGSAN_UUID = "a0000001-0000-4000-8000-000000000001"
SHARED_APP_ID = "40f7c243-cd89-4074-9f78-8c3c777308d8"  # docker exec evidence: completion
OWNER_APP_ID = "owner-app-pending"
WORKFLOW_APP_ID = "5e84f3e8-b9d4-4283-b10c-d479d5dd1e43"  # docker exec evidence: workflow


@pytest_asyncio.fixture
async def parameters_client(app_client, async_db):
    """Mirrors apps_route_client (test_routes.py) for parameters endpoint.

    Fresh per-test DifyConsoleClient (no cache leak) + real test httpx.
    """
    from ncmu_backend.apps.dify_console_client import DifyConsoleClient
    from ncmu_backend.apps.routes import get_dify_console_client
    from ncmu_backend.main import app, get_dify_client

    dcc = DifyConsoleClient(ttl_seconds=300)
    test_http = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
    app.dependency_overrides[get_dify_console_client] = lambda: dcc
    app.dependency_overrides[get_dify_client] = lambda: test_http
    try:
        yield app_client, async_db
    finally:
        app.dependency_overrides.pop(get_dify_console_client, None)
        app.dependency_overrides.pop(get_dify_client, None)
        await test_http.aclose()


async def _seed_owner(db, app_id: str, name: str, mode: str = "chat") -> None:
    await db.execute(text(
        "INSERT INTO dify_apps (dify_app_id, name, mode) "
        "VALUES (:aid, :name, :mode) ON CONFLICT (dify_app_id) DO NOTHING"
    ), {"aid": app_id, "name": name, "mode": mode})
    await db.execute(text(
        "INSERT INTO app_owners (app_id, owner_user_id, visibility) "
        "VALUES (:aid, :uid, 'owner_only')"
    ), {"aid": app_id, "uid": ZHANGSAN_UUID})
    await db.commit()


def _mock_list_returns(rx: respx.Router, app_id: str, *, name: str = "[KB]demo") -> None:
    """Mock /console/api/apps?... so user_can_access_app shared path admits."""
    rx.get(url__regex=r".*/console/api/apps\?.*").mock(
        return_value=Response(
            200,
            json={"data": [{"id": app_id, "name": name, "mode": "chat"}]},
        )
    )


# ── Happy: completion mode (chat-shape NESTED user_input_form) ─────────────


async def test_completion_mode_returns_flat_parameter_list(
    parameters_client, jwt_token,
):
    client, _db = parameters_client
    with respx.mock(assert_all_called=True) as rx:
        _mock_list_returns(rx, SHARED_APP_ID, name="[KB]textgen")
        rx.get(url__regex=rf".*/console/api/apps/{SHARED_APP_ID}$").mock(
            return_value=Response(200, json={
                "id": SHARED_APP_ID,
                "mode": "completion",
                "model_config": {
                    "user_input_form": [
                        {"paragraph": {
                            "label": "Query",
                            "variable": "query",
                            "required": True,
                            "default": "",
                        }},
                    ],
                },
                "workflow": None,
            })
        )
        resp = await client.get(
            f"/api/v1/ncmu/apps/{SHARED_APP_ID}/parameters",
            headers={"Authorization": f"Bearer {jwt_token}"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == [{
        "variable": "query",
        "label": "Query",
        "type": "paragraph",
        "required": True,
        "options": [],
    }]


# ── Happy: workflow mode (FLAT start.variables from draft) ─────────────────


async def test_workflow_mode_returns_flat_parameter_list(
    parameters_client, jwt_token,
):
    client, _db = parameters_client
    with respx.mock(assert_all_called=True) as rx:
        _mock_list_returns(rx, WORKFLOW_APP_ID, name="[KB]wf")
        rx.get(url__regex=rf".*/console/api/apps/{WORKFLOW_APP_ID}$").mock(
            return_value=Response(200, json={
                "id": WORKFLOW_APP_ID,
                "mode": "workflow",
                "model_config": None,
                "workflow": {"id": "wf-id"},
            })
        )
        rx.get(url__regex=rf".*/console/api/apps/{WORKFLOW_APP_ID}/workflows/draft").mock(
            return_value=Response(200, json={
                "graph": {
                    "nodes": [
                        {"data": {
                            "type": "start",
                            "variables": [
                                {
                                    "variable": "x",
                                    "label": "x",
                                    "type": "number",
                                    "required": True,
                                    "options": [],
                                    "placeholder": "ignored",
                                    "default": "ignored",
                                    "hint": "ignored",
                                },
                            ],
                        }},
                        {"data": {"type": "end"}},
                    ],
                },
            })
        )
        resp = await client.get(
            f"/api/v1/ncmu/apps/{WORKFLOW_APP_ID}/parameters",
            headers={"Authorization": f"Bearer {jwt_token}"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == [{
        "variable": "x",
        "label": "x",
        "type": "number",
        "required": True,
        "options": [],
    }]


# ── Happy: advanced-chat (also workflows/draft path) ───────────────────────


async def test_advanced_chat_mode_uses_workflow_draft(parameters_client, jwt_token):
    client, _db = parameters_client
    aid = "adv-chat-1"
    with respx.mock(assert_all_called=True) as rx:
        _mock_list_returns(rx, aid, name="[KB]chatflow")
        rx.get(url__regex=rf".*/console/api/apps/{aid}$").mock(
            return_value=Response(200, json={
                "id": aid, "mode": "advanced-chat",
                "model_config": None, "workflow": {"id": "wf2"},
            })
        )
        rx.get(url__regex=rf".*/console/api/apps/{aid}/workflows/draft").mock(
            return_value=Response(200, json={
                "graph": {"nodes": [
                    {"data": {"type": "start", "variables": [
                        {"variable": "topic", "label": "话题",
                         "type": "text", "required": False, "options": []},
                    ]}},
                ]},
            })
        )
        resp = await client.get(
            f"/api/v1/ncmu/apps/{aid}/parameters",
            headers={"Authorization": f"Bearer {jwt_token}"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json() == [{
        "variable": "topic", "label": "话题", "type": "text",
        "required": False, "options": [],
    }]


# ── Edge: empty user_input_form → empty list ───────────────────────────────


async def test_chat_mode_empty_form_returns_empty_list(parameters_client, jwt_token):
    client, _db = parameters_client
    aid = SHARED_APP_ID
    with respx.mock(assert_all_called=True) as rx:
        _mock_list_returns(rx, aid)
        rx.get(url__regex=rf".*/console/api/apps/{aid}$").mock(
            return_value=Response(200, json={
                "id": aid, "mode": "chat",
                "model_config": {"user_input_form": []},
                "workflow": None,
            })
        )
        resp = await client.get(
            f"/api/v1/ncmu/apps/{aid}/parameters",
            headers={"Authorization": f"Bearer {jwt_token}"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


# ── Edge: select with options + 4 type kinds in one form ───────────────────


async def test_chat_mode_all_four_types_in_one_form(parameters_client, jwt_token):
    client, _db = parameters_client
    aid = SHARED_APP_ID
    with respx.mock(assert_all_called=True) as rx:
        _mock_list_returns(rx, aid)
        rx.get(url__regex=rf".*/console/api/apps/{aid}$").mock(
            return_value=Response(200, json={
                "id": aid, "mode": "chat",
                "model_config": {"user_input_form": [
                    {"text-input": {"variable": "name", "label": "姓名", "required": True}},
                    {"paragraph": {"variable": "bio", "label": "简介", "required": False}},
                    {"select": {"variable": "tier", "label": "套餐",
                                "required": True, "options": ["free", "pro"]}},
                    {"number": {"variable": "age", "label": "年龄", "required": False}},
                ]},
                "workflow": None,
            })
        )
        resp = await client.get(
            f"/api/v1/ncmu/apps/{aid}/parameters",
            headers={"Authorization": f"Bearer {jwt_token}"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == [
        {"variable": "name", "label": "姓名", "type": "text",
         "required": True, "options": []},
        {"variable": "bio", "label": "简介", "type": "paragraph",
         "required": False, "options": []},
        {"variable": "tier", "label": "套餐", "type": "select",
         "required": True, "options": ["free", "pro"]},
        {"variable": "age", "label": "年龄", "type": "number",
         "required": False, "options": []},
    ]


# ── Edge: unknown type silently skipped (Boss T1.2=A) ──────────────────────


async def test_chat_mode_unknown_type_silently_skipped(parameters_client, jwt_token):
    client, _db = parameters_client
    aid = SHARED_APP_ID
    with respx.mock(assert_all_called=True) as rx:
        _mock_list_returns(rx, aid)
        rx.get(url__regex=rf".*/console/api/apps/{aid}$").mock(
            return_value=Response(200, json={
                "id": aid, "mode": "chat",
                "model_config": {"user_input_form": [
                    {"file-upload": {"variable": "doc", "label": "Doc"}},  # unknown type
                    {"paragraph": {"variable": "q", "label": "Q", "required": True}},
                ]},
                "workflow": None,
            })
        )
        resp = await client.get(
            f"/api/v1/ncmu/apps/{aid}/parameters",
            headers={"Authorization": f"Bearer {jwt_token}"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json() == [{
        "variable": "q", "label": "Q", "type": "paragraph",
        "required": True, "options": [],
    }]


# ── Edge: missing variable / label fallback (Boss T1.4=B) ──────────────────


async def test_chat_mode_missing_variable_skipped_missing_label_fallback(
    parameters_client, jwt_token,
):
    client, _db = parameters_client
    aid = SHARED_APP_ID
    with respx.mock(assert_all_called=True) as rx:
        _mock_list_returns(rx, aid)
        rx.get(url__regex=rf".*/console/api/apps/{aid}$").mock(
            return_value=Response(200, json={
                "id": aid, "mode": "chat",
                "model_config": {"user_input_form": [
                    {"paragraph": {"label": "无 variable"}},   # missing variable → skip
                    {"text-input": {"variable": "no_label"}},  # missing label → fallback
                ]},
                "workflow": None,
            })
        )
        resp = await client.get(
            f"/api/v1/ncmu/apps/{aid}/parameters",
            headers={"Authorization": f"Bearer {jwt_token}"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json() == [{
        "variable": "no_label", "label": "no_label", "type": "text",
        "required": False, "options": [],
    }]


# ── Permission: user without owner/shared access → 403 ─────────────────────


async def test_user_without_access_returns_403(parameters_client, jwt_token):
    client, _db = parameters_client
    forbidden_id = "no-access-app"
    with respx.mock(assert_all_called=True) as rx:
        # shared list contains a different app → user_can_access_app returns False
        rx.get(url__regex=r".*/console/api/apps\?.*").mock(
            return_value=Response(200, json={"data": [
                {"id": "other-app", "name": "[KB]other", "mode": "chat"},
            ]})
        )
        resp = await client.get(
            f"/api/v1/ncmu/apps/{forbidden_id}/parameters",
            headers={"Authorization": f"Bearer {jwt_token}"},
        )
    assert resp.status_code == 403, resp.text
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == 1002, detail


# ── Permission: owner-path admits user even when shared cache empty ────────


async def test_owner_path_admits_user(parameters_client, jwt_token):
    client, db = parameters_client
    aid = OWNER_APP_ID
    await _seed_owner(db, aid, "私人 App", "completion")
    with respx.mock(assert_all_called=True) as rx:
        # NO list mock — owner path short-circuits before shared cache call
        # (守 services.user_can_access_app 真实分支：DB exists → True → 跳过 cache).
        rx.get(url__regex=rf".*/console/api/apps/{aid}$").mock(
            return_value=Response(200, json={
                "id": aid, "mode": "completion",
                "model_config": {"user_input_form": [
                    {"text-input": {"variable": "v", "label": "L", "required": False}},
                ]},
                "workflow": None,
            })
        )
        resp = await client.get(
            f"/api/v1/ncmu/apps/{aid}/parameters",
            headers={"Authorization": f"Bearer {jwt_token}"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json() == [{
        "variable": "v", "label": "L", "type": "text",
        "required": False, "options": [],
    }]


# ── Auth: missing JWT → 401 ────────────────────────────────────────────────


async def test_missing_jwt_returns_401(parameters_client):
    client, _db = parameters_client
    resp = await client.get(f"/api/v1/ncmu/apps/{SHARED_APP_ID}/parameters")
    assert resp.status_code == 401, resp.text


# ── Dify upstream 5xx → 502 ────────────────────────────────────────────────


async def test_upstream_500_propagates_as_502(parameters_client, jwt_token):
    client, _db = parameters_client
    aid = SHARED_APP_ID
    with respx.mock(assert_all_called=True) as rx:
        _mock_list_returns(rx, aid)
        rx.get(url__regex=rf".*/console/api/apps/{aid}$").mock(
            return_value=Response(500, json={"error": "upstream down"})
        )
        resp = await client.get(
            f"/api/v1/ncmu/apps/{aid}/parameters",
            headers={"Authorization": f"Bearer {jwt_token}"},
        )
    assert resp.status_code == 502, resp.text
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == 9001, detail
