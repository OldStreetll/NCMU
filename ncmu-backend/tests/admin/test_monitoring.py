"""TASK-MON-1 — GET /api/v1/ncmu/admin/monitoring unit tests.

Contract (spec §2/§3 + frozen MonitoringOut):
  - admin → 200 with {business, dingtalk, generated_at}
  - non-admin → 403 + detail.code == 1201 (守 PE-02 invariance)

Field-level assertions against a real ephemeral Postgres (app_client /
async_db fixtures — not mocked), 守 memory feedback_pre_existing_error
_strict_validation + feedback_tdd_mock_vs_real_api 真验三件套.

Baseline ephemeral seed (dev_users.sql): 5 users, all is_active=True,
all dingtalk_userid NULL, created_at defaults now() (within 7d). Apps /
KB applications / tags / mappings all empty. Test env leaves every
dingtalk_* config at its CHANGE_ME default → all readiness lights False.
"""
from __future__ import annotations

import types
import uuid

from sqlalchemy import text

# NOTE: ``dingtalk_config_readiness`` is imported lazily inside test (9),
# NOT at module top level. A top-level ``from ncmu_backend.admin.monitoring
# .services import ...`` runs ``admin/__init__`` → ``admin.routes`` → imports
# ``ncmu_backend.main`` mid-load, tripping a pre-existing circular import
# (apps.routes ↔ main ↔ fastgpt_readonly.routes) that only resolves when
# ``main`` is the first module imported. Every other admin test follows the
# same convention (top-level imports limited to db.models / main.app; service
# + main imports are lazy / via the app_client fixture).

ZHANGSAN = "a0000001-0000-4000-8000-000000000001"  # admin per NCMU_ADMIN_USER_IDS default
LISI = "a0000001-0000-4000-8000-000000000002"      # non-admin seed user

URL = "/api/v1/ncmu/admin/monitoring"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------- #
# (1) shape — top-level groups + generated_at present
# --------------------------------------------------------------------- #
async def test_monitoring_returns_grouped_shape(app_client, jwt_token):
    resp = await app_client.get(URL, headers=_auth(jwt_token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert {"business", "dingtalk", "generated_at"} <= body.keys()
    biz, ding = body["business"], body["dingtalk"]
    # business §2.1 fields
    assert {
        "user_total", "user_active", "user_with_dingtalk", "new_users_7d",
        "kb_application_status", "kb_pending_backlog", "new_kb_applications_7d",
        "avg_kb_processing_seconds", "app_total", "app_active",
        "last_dify_app_sync_at", "tag_bindings",
    } <= biz.keys()
    # dingtalk §2.2 fields
    assert {
        "config_readiness", "department_tag_mapping_count",
        "synced_account_count", "tags_routing_enabled",
        "routing_affected_user_count", "routing_affected_app_count",
    } <= ding.keys()
    # kb status 5 fixed keys
    assert set(biz["kb_application_status"].keys()) == {
        "pending", "in_progress", "done", "rejected", "cancelled"
    }


# --------------------------------------------------------------------- #
# (2) baseline seed values — empty DB except 5 dev users
# --------------------------------------------------------------------- #
async def test_monitoring_baseline_seed_values(app_client, jwt_token):
    resp = await app_client.get(URL, headers=_auth(jwt_token))
    assert resp.status_code == 200, resp.text
    biz = resp.json()["business"]
    ding = resp.json()["dingtalk"]

    assert biz["user_total"] == 5
    assert biz["user_active"] == 5
    assert biz["user_with_dingtalk"] == 0
    assert biz["new_users_7d"] == 5          # all 5 seeded with created_at now()
    assert biz["kb_application_status"] == {
        "pending": 0, "in_progress": 0, "done": 0, "rejected": 0, "cancelled": 0
    }
    assert biz["kb_pending_backlog"] == 0
    assert biz["new_kb_applications_7d"] == 0
    assert biz["avg_kb_processing_seconds"] is None   # no done rows → null edge
    assert biz["app_total"] == 0
    assert biz["app_active"] == 0
    assert biz["last_dify_app_sync_at"] is None
    assert biz["tag_bindings"] == []

    # readiness all False (every dingtalk_* config = CHANGE_ME default)
    assert ding["config_readiness"] == {
        "app_key": False, "app_secret": False,
        "corp_id": False, "login_redirect_uri": False,
    }
    assert ding["department_tag_mapping_count"] == 0
    assert ding["synced_account_count"] == 0
    assert ding["tags_routing_enabled"] is False
    assert ding["routing_affected_user_count"] == 0
    assert ding["routing_affected_app_count"] == 0


# --------------------------------------------------------------------- #
# (3) user counts — active/inactive + dingtalk + 7d trend boundary
# --------------------------------------------------------------------- #
async def test_monitoring_user_counts_and_trend(app_client, async_db, jwt_token):
    # 1 new active user WITH dingtalk_userid (created now → within 7d)
    await async_db.execute(
        text(
            "INSERT INTO users (id, dingtalk_userid, name, is_active) "
            "VALUES (:id, :dt, :n, true)"
        ),
        {"id": str(uuid.uuid4()), "dt": "dinguser-001", "n": "新员工"},
    )
    # 1 inactive user, created 10 days ago (NOT within 7d)
    await async_db.execute(
        text(
            "INSERT INTO users (id, dingtalk_userid, name, is_active, created_at) "
            "VALUES (:id, NULL, :n, false, now() - interval '10 days')"
        ),
        {"id": str(uuid.uuid4()), "n": "离职老员工"},
    )
    await async_db.commit()

    body = (await app_client.get(URL, headers=_auth(jwt_token))).json()["business"]
    assert body["user_total"] == 7                # 5 seed + 2
    assert body["user_active"] == 6               # 5 seed active + 1 new active
    assert body["user_with_dingtalk"] == 1
    assert body["new_users_7d"] == 6              # 5 seed + 1 new; old one excluded


# --------------------------------------------------------------------- #
# (4) KB application status distribution + pending backlog + 7d
# --------------------------------------------------------------------- #
async def test_monitoring_kb_status_distribution(app_client, async_db, jwt_token):
    # 2 pending + 1 in_progress + 1 rejected (all created now → within 7d)
    for st in ("pending", "pending", "in_progress", "rejected"):
        await async_db.execute(
            text(
                "INSERT INTO personal_kb_applications (id, user_id, status) "
                "VALUES (:id, :uid, :st)"
            ),
            {"id": str(uuid.uuid4()), "uid": ZHANGSAN, "st": st},
        )
    # 1 cancelled created 10 days ago (counts in status, NOT in new_7d)
    await async_db.execute(
        text(
            "INSERT INTO personal_kb_applications (id, user_id, status, created_at) "
            "VALUES (:id, :uid, 'cancelled', now() - interval '10 days')"
        ),
        {"id": str(uuid.uuid4()), "uid": ZHANGSAN},
    )
    await async_db.commit()

    body = (await app_client.get(URL, headers=_auth(jwt_token))).json()["business"]
    assert body["kb_application_status"] == {
        "pending": 2, "in_progress": 1, "done": 0, "rejected": 1, "cancelled": 1
    }
    assert body["kb_pending_backlog"] == 2        # == pending
    assert body["new_kb_applications_7d"] == 4    # 4 recent; 10-day-old cancelled excluded


# --------------------------------------------------------------------- #
# (5) avg processing time — done rows avg(admin_processed_at - created_at)
# --------------------------------------------------------------------- #
async def test_monitoring_avg_processing_time(app_client, async_db, jwt_token):
    # 2 done rows: 100s and 200s processing → avg 150s. Use make_interval
    # with integer seconds (asyncpg encodes ints cleanly; a parameterized
    # ``(:x)::interval`` makes asyncpg infer an interval-typed bind and
    # reject the Python str).
    for created_secs in (100, 200):
        await async_db.execute(
            text(
                "INSERT INTO personal_kb_applications "
                "(id, user_id, status, created_at, admin_processed_at) "
                "VALUES (:id, :uid, 'done', "
                "now() - make_interval(secs => :c), now())"
            ),
            {"id": str(uuid.uuid4()), "uid": ZHANGSAN, "c": created_secs},
        )
    # an in_progress row with admin_processed_at set — must be ignored by avg
    await async_db.execute(
        text(
            "INSERT INTO personal_kb_applications "
            "(id, user_id, status, created_at, admin_processed_at) "
            "VALUES (:id, :uid, 'in_progress', "
            "now() - make_interval(secs => 9999), now())"
        ),
        {"id": str(uuid.uuid4()), "uid": ZHANGSAN},
    )
    await async_db.commit()

    body = (await app_client.get(URL, headers=_auth(jwt_token))).json()["business"]
    avg = body["avg_kb_processing_seconds"]
    assert avg is not None
    assert 149.0 <= avg <= 151.0, f"expected ~150s, got {avg}"
    assert body["kb_application_status"]["done"] == 2


# --------------------------------------------------------------------- #
# (6) apps — active/inactive count + last sync timestamp
# --------------------------------------------------------------------- #
async def test_monitoring_app_counts_and_last_sync(app_client, async_db, jwt_token):
    await async_db.execute(
        text(
            "INSERT INTO dify_apps (dify_app_id, name, mode, is_active, last_synced_at) "
            "VALUES (:id, :n, 'chat', true, now() - interval '1 hour')"
        ),
        {"id": "app-active", "n": "活跃 App"},
    )
    await async_db.execute(
        text(
            "INSERT INTO dify_apps (dify_app_id, name, mode, is_active, last_synced_at) "
            "VALUES (:id, :n, 'chat', false, now())"
        ),
        {"id": "app-inactive", "n": "停用 App"},
    )
    await async_db.commit()

    body = (await app_client.get(URL, headers=_auth(jwt_token))).json()["business"]
    assert body["app_total"] == 2
    assert body["app_active"] == 1
    assert body["last_dify_app_sync_at"] is not None   # max across both rows


# --------------------------------------------------------------------- #
# (7) tag bindings + dingtalk routing/mapping counts
# --------------------------------------------------------------------- #
async def test_monitoring_tag_bindings_and_routing(app_client, async_db, jwt_token):
    tag_id = str(uuid.uuid4())
    await async_db.execute(
        text("INSERT INTO tags (id, name) VALUES (:id, :n)"),
        {"id": tag_id, "n": "工程标签"},
    )
    # 1 app bound + 1 user bound to the tag
    await async_db.execute(
        text("INSERT INTO dify_apps (dify_app_id, name, mode) VALUES (:id, :n, 'chat')"),
        {"id": "app-bound", "n": "绑定 App"},
    )
    await async_db.execute(
        text("INSERT INTO app_tags (dify_app_id, tag_id) VALUES (:a, :t)"),
        {"a": "app-bound", "t": tag_id},
    )
    await async_db.execute(
        text("INSERT INTO user_tags (user_id, tag_id) VALUES (:u, :t)"),
        {"u": ZHANGSAN, "t": tag_id},
    )
    # 1 department→tag mapping
    await async_db.execute(
        text("INSERT INTO department_tag_mappings (dept_id, tag_id) VALUES (:d, :t)"),
        {"d": 123456, "t": tag_id},
    )
    await async_db.commit()

    body = (await app_client.get(URL, headers=_auth(jwt_token))).json()
    biz, ding = body["business"], body["dingtalk"]

    assert len(biz["tag_bindings"]) == 1
    binding = biz["tag_bindings"][0]
    assert binding["name"] == "工程标签"
    assert binding["app_count"] == 1
    assert binding["user_count"] == 1
    assert binding["tag_id"] == tag_id

    assert ding["department_tag_mapping_count"] == 1
    assert ding["routing_affected_user_count"] == 1
    assert ding["routing_affected_app_count"] == 1


# --------------------------------------------------------------------- #
# (8) non-admin → 403 + detail.code == 1201 (PE-02 invariance contract)
# --------------------------------------------------------------------- #
async def test_monitoring_non_admin_returns_403(app_client, jwt_secret):
    from ncmu_backend.auth.jwt_utils import sign_jwt

    lisi_token, _ = sign_jwt(user_id=LISI, name="李四", secret=jwt_secret)
    resp = await app_client.get(URL, headers=_auth(lisi_token))
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == 1201


# --------------------------------------------------------------------- #
# (9) dingtalk_config_readiness pure-function unit — CHANGE_ME → False,
#     real value → True (covers the True path without env/cache plumbing)
# --------------------------------------------------------------------- #
def test_dingtalk_config_readiness_placeholder_vs_real():
    from ncmu_backend.admin.monitoring.services import dingtalk_config_readiness

    placeholder = types.SimpleNamespace(
        dingtalk_app_key="CHANGE_ME",
        dingtalk_app_secret="CHANGE_ME",
        dingtalk_corp_id="CHANGE_ME",
        dingtalk_login_redirect_uri="https://CHANGE_ME/api/v1/ncmu/auth/dingtalk/callback",
    )
    r = dingtalk_config_readiness(placeholder)
    assert r.app_key is False
    assert r.app_secret is False
    assert r.corp_id is False
    assert r.login_redirect_uri is False

    real = types.SimpleNamespace(
        dingtalk_app_key="dingxxxxrealkey",
        dingtalk_app_secret="realsecretvalue",
        dingtalk_corp_id="dingcorpreal",
        dingtalk_login_redirect_uri="https://ncmu.example.com/api/v1/ncmu/auth/dingtalk/callback",
    )
    r2 = dingtalk_config_readiness(real)
    assert r2.app_key is True
    assert r2.app_secret is True
    assert r2.corp_id is True
    assert r2.login_redirect_uri is True

    # mixed: empty string is also not-ready
    mixed = types.SimpleNamespace(
        dingtalk_app_key="",
        dingtalk_app_secret="realsecretvalue",
        dingtalk_corp_id="CHANGE_ME",
        dingtalk_login_redirect_uri="https://real/cb",
    )
    r3 = dingtalk_config_readiness(mixed)
    assert r3.app_key is False        # empty
    assert r3.app_secret is True
    assert r3.corp_id is False        # CHANGE_ME
    assert r3.login_redirect_uri is True
