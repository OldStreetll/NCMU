"""TASK-PE-04 — GET /api/v1/ncmu/admin/stats unit tests.

Contract (plan §3 PE-04 line 695-754):
  - admin → 200 with {tag_count, user_count, app_count,
    pending_personal_kb_count, external_kb_config_count} all ints
  - non-admin → 403 + detail.code == 1201 (守 PE-02 invariance)

Counts on the baseline ephemeral DB seed:
  - dev_users.sql seeds 5 users (张三/李四/王五/赵六/钱七 / all is_active=True)
  - dify_apps, personal_kb_applications, dify_external_kb_configs are empty
  - Tag table doesn't exist on baseline 0008 (PE-05 alembic 0009 to add) →
    tag_count is wired to 0 (placeholder) until PE-05 lands

Field-level assertions (守 memory feedback_pre_existing_error_strict_validation
+ feedback_tdd_mock_vs_real_api SOP 真验三件套). All counts are queried
against a real ephemeral Postgres via app_client fixture (not mocked).
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text


ZHANGSAN = "a0000001-0000-4000-8000-000000000001"  # admin per dev_users.sql + NCMU_ADMIN_USER_IDS default
LISI = "a0000001-0000-4000-8000-000000000002"      # non-admin


# --------------------------------------------------------------------- #
# (1) shape + types — AC#1 字面
# --------------------------------------------------------------------- #
async def test_get_admin_stats_returns_5_int_fields(app_client, jwt_token):
    resp = await app_client.get(
        "/api/v1/ncmu/admin/stats",
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    expected = {
        "tag_count",
        "user_count",
        "app_count",
        "pending_personal_kb_count",
        "external_kb_config_count",
    }
    assert expected <= body.keys(), f"missing fields: {expected - body.keys()}"
    for k in expected:
        assert isinstance(body[k], int), f"{k} is not int: {body[k]!r}"


# --------------------------------------------------------------------- #
# (2) field-level — baseline ephemeral DB seed values
# --------------------------------------------------------------------- #
async def test_get_admin_stats_baseline_seed_values(app_client, jwt_token):
    """On fresh migrated DB + dev_users.sql seed (5 users / 0 others)
    the stats endpoint must return exact counts. Guards against silent
    SQL drift (e.g. wrong table name, missing WHERE clause)."""
    resp = await app_client.get(
        "/api/v1/ncmu/admin/stats",
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["user_count"] == 5, body  # 5 dev seed users (all is_active=True)
    assert body["app_count"] == 0, body
    assert body["pending_personal_kb_count"] == 0, body
    assert body["external_kb_config_count"] == 0, body
    assert body["tag_count"] == 0, body  # placeholder until PE-05 lands Tag table


# --------------------------------------------------------------------- #
# (3) field-level — counts respond to real seed data
# --------------------------------------------------------------------- #
async def test_get_admin_stats_counts_reflect_seeded_rows(
    app_client, async_db, jwt_token,
):
    """Seed 1 DifyApp + 1 pending PersonalKbApplication +
    1 dify_external_kb_configs row, then assert counts increment."""
    await async_db.execute(
        text(
            "INSERT INTO dify_apps (dify_app_id, name, mode) "
            "VALUES (:id, :name, :mode)"
        ),
        {"id": "app-pe04-seed", "name": "PE-04 seed app", "mode": "chat"},
    )
    await async_db.execute(
        text(
            "INSERT INTO personal_kb_applications (id, user_id, status) "
            "VALUES (:id, :uid, 'pending')"
        ),
        {"id": str(uuid.uuid4()), "uid": ZHANGSAN},
    )
    await async_db.execute(
        text(
            "INSERT INTO dify_external_kb_configs "
            "(id, external_kb_name, fastgpt_dataset_id, api_key_label) "
            "VALUES (:id, :n, :ds, :lbl)"
        ),
        {
            "id": str(uuid.uuid4()),
            "n": "pe04-seed-kb",
            "ds": "ds-001",
            "lbl": "test-key",
        },
    )
    await async_db.commit()

    resp = await app_client.get(
        "/api/v1/ncmu/admin/stats",
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["app_count"] == 1, body
    assert body["pending_personal_kb_count"] == 1, body
    assert body["external_kb_config_count"] == 1, body
    # user_count unchanged
    assert body["user_count"] == 5, body


# --------------------------------------------------------------------- #
# (4) pending_personal_kb_count excludes non-pending statuses
# --------------------------------------------------------------------- #
async def test_get_admin_stats_pending_kb_excludes_done_rejected(
    app_client, async_db, jwt_token,
):
    """Seed 1 pending + 1 done + 1 rejected + 1 in_progress + 1 cancelled
    PersonalKbApplication. pending_personal_kb_count must == 1."""
    for status in ("pending", "in_progress", "done", "rejected", "cancelled"):
        await async_db.execute(
            text(
                "INSERT INTO personal_kb_applications (id, user_id, status) "
                "VALUES (:id, :uid, :st)"
            ),
            {"id": str(uuid.uuid4()), "uid": ZHANGSAN, "st": status},
        )
    await async_db.commit()

    resp = await app_client.get(
        "/api/v1/ncmu/admin/stats",
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["pending_personal_kb_count"] == 1, body


# --------------------------------------------------------------------- #
# (5) non-admin → 403 + detail.code == 1201 (PE-02 invariance contract)
# --------------------------------------------------------------------- #
async def test_get_admin_stats_non_admin_returns_403(app_client, jwt_secret):
    from ncmu_backend.auth.jwt_utils import sign_jwt

    lisi_token, _ = sign_jwt(user_id=LISI, name="李四", secret=jwt_secret)
    resp = await app_client.get(
        "/api/v1/ncmu/admin/stats",
        headers={"Authorization": f"Bearer {lisi_token}"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == 1201
