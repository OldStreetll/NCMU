"""TASK-28 AC#6-7 — admin endpoint behaviour + admin gate.

Test layout (5 tests):
  AC#1 — admin GET /admin/kb-configs returns the seeded rows
  AC#2 — admin GET /admin/dify-app-bindings returns the seeded rows
  AC#3 — non-admin (李四) gets 403 + code 1201 from kb-configs
  AC#4 — non-admin gets 403 from dify-app-bindings
  AC#5 — missing JWT (no Authorization header) → 401 + code 1102
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text

ZHANGSAN = "a0000001-0000-4000-8000-000000000001"  # admin (NCMU_ADMIN_USER_IDS default)
LISI = "a0000001-0000-4000-8000-000000000002"      # non-admin


def _sign_for(user_id: str, name: str, secret: str) -> str:
    from ncmu_backend.auth.jwt_utils import sign_jwt
    token, _ = sign_jwt(user_id=user_id, name=name, secret=secret)
    return token


async def _seed_kb_configs(async_db) -> tuple[str, str]:
    """Insert two rows into dify_external_kb_configs for the listing tests."""
    cid_hr = str(uuid4())
    cid_fin = str(uuid4())
    await async_db.execute(
        text(
            """
            INSERT INTO dify_external_kb_configs
              (id, external_kb_name, fastgpt_dataset_id, api_key_label, notes)
            VALUES
              (:id_hr, 'hr-handbook', 'fgpt-ds-hr-001', 'hr-label', 'TASK-28 seed'),
              (:id_fin, 'finance-handbook', 'fgpt-ds-fin-001', 'fin-label', NULL)
            """
        ),
        {"id_hr": cid_hr, "id_fin": cid_fin},
    )
    await async_db.commit()
    return cid_hr, cid_fin


async def _seed_bindings(async_db, kb_config_id: str) -> str:
    """Insert one binding (Dify App ↔ KB config)."""
    bid = str(uuid4())
    await async_db.execute(
        text(
            """
            INSERT INTO dify_app_kb_bindings
              (id, dify_app_id, external_kb_config_id)
            VALUES
              (:id, '1ee8a2c4-ce74-429b-b2b3-93de7b837a94', :cid)
            """
        ),
        {"id": bid, "cid": kb_config_id},
    )
    await async_db.commit()
    return bid


# ===================================================================== AC#1
async def test_admin_kb_configs_returns_seeded_rows(
    app_client, async_db, jwt_token,
):
    """张三 (admin) → 200 + 看到 hr / finance 两条 + 字段全。"""
    cid_hr, cid_fin = await _seed_kb_configs(async_db)

    r = await app_client.get(
        "/api/v1/ncmu/admin/kb-configs",
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert r.status_code == 200, r.text
    rows = r.json()
    names = {row["external_kb_name"] for row in rows}
    assert names == {"hr-handbook", "finance-handbook"}
    hr = next(r for r in rows if r["external_kb_name"] == "hr-handbook")
    assert hr["api_key_label"] == "hr-label"
    assert hr["fastgpt_dataset_id"] == "fgpt-ds-hr-001"
    assert hr["notes"] == "TASK-28 seed"


# ===================================================================== AC#2
async def test_admin_bindings_returns_seeded_rows(
    app_client, async_db, jwt_token,
):
    """张三 (admin) → 200 + 看到一条 binding 记录。"""
    cid_hr, _ = await _seed_kb_configs(async_db)
    bid = await _seed_bindings(async_db, cid_hr)

    r = await app_client.get(
        "/api/v1/ncmu/admin/dify-app-bindings",
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["id"] == bid
    assert rows[0]["dify_app_id"] == "1ee8a2c4-ce74-429b-b2b3-93de7b837a94"
    assert rows[0]["external_kb_config_id"] == cid_hr


# ===================================================================== AC#3
async def test_non_admin_kb_configs_returns_403(
    app_client, async_db, jwt_secret,
):
    """李四 (非 admin) → 403 + code 1201 + 不读到任何数据。"""
    await _seed_kb_configs(async_db)
    lisi_token = _sign_for(LISI, "李四", jwt_secret)

    r = await app_client.get(
        "/api/v1/ncmu/admin/kb-configs",
        headers={"Authorization": f"Bearer {lisi_token}"},
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == 1201


# ===================================================================== AC#4
async def test_non_admin_bindings_returns_403(
    app_client, async_db, jwt_secret,
):
    """李四 → 403 from /admin/dify-app-bindings。"""
    cid_hr, _ = await _seed_kb_configs(async_db)
    await _seed_bindings(async_db, cid_hr)
    lisi_token = _sign_for(LISI, "李四", jwt_secret)

    r = await app_client.get(
        "/api/v1/ncmu/admin/dify-app-bindings",
        headers={"Authorization": f"Bearer {lisi_token}"},
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == 1201


# ===================================================================== AC#5
async def test_missing_jwt_returns_401(app_client):
    """无 Authorization 头 → 401 + code 1102。"""
    r = await app_client.get("/api/v1/ncmu/admin/kb-configs")
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == 1102
