"""ncmu_init.py — First-run system tag + FastGPT bootstrap.

Default mode: idempotently insert customer / staff / admin system tags
into pg-ncmu (skipped gracefully when the tags table is not yet
migrated by ncmu-backend).

--bootstrap-fastgpt: additionally seed FastGPT mongo team_subscriptions
and activate the default embedding + chat models via FastGPT's
admin API. Called by scripts/start-dev.sh after the FastGPT app
container reports healthy.

Env vars consumed:
  NCMU_DB_URL                  (default tag init)
  FASTGPT_BASE_URL             (--bootstrap-fastgpt; default
                                http://fastgpt:3000)
  FASTGPT_ROOT_KEY             (--bootstrap-fastgpt; reused from the
                                env that seeds the FastGPT container's
                                own ROOT_KEY)
  FASTGPT_MONGO_USER           (--bootstrap-fastgpt)
  FASTGPT_MONGO_PASSWORD       (--bootstrap-fastgpt)
  FASTGPT_MONGO_HOST           (--bootstrap-fastgpt; default fastgpt-mongo)
  FASTGPT_MONGO_PORT           (--bootstrap-fastgpt; default 27017)
  FASTGPT_MONGO_DB             (--bootstrap-fastgpt; default fastgpt)

Source: v3.3.1 §18 line 1695 + Phase 1 plan TASK-21 (B-NEW-04/05).
"""
import argparse
import datetime as _dt
import os
import sys

import psycopg2

DB_URL = os.environ.get("NCMU_DB_URL")

SYSTEM_TAGS = [
    ("customer", "客户", "外部客户访问 App（guest JWT 或钉钉扫码 OAuth2）"),
    ("staff", "员工", "内部员工，手机号 + 钉钉工作通知验证码登录"),
    ("admin", "管理员", "NCMU 管理后台访问，全局权限"),
]

# FastGPT model activation endpoint. Discovered during TASK-21-A1
# down -v probe: the per-model /api/core/ai/model/update route uses
# *login-session* auth (`authToken: true` + username == "root"), NOT
# the `rootkey:` header — so the original plan's per-model POST loop
# always returned 403 in a headless bootstrap.
#
# /api/admin/initv4820 is the rootkey-protected mass-activation route
# (`authRoot: true`). It iterates fastgpt-app's vendored config.json
# (llmModels / vectorModels / reRankModels / audioSpeechModels /
# whisperModel) and upserts every entry into mongo system_models with
# metadata.isActive = true. Calling it once per deploy idempotently
# brings every model declared in config.json online; calling it twice
# re-asserts the same state.
#
# Caveat (operator-facing, also captured in fastgpt-tag-tracking
# runbook): the v4.14.12 vendored config.json is intentionally minimal
# (only feConfigs + systemEnv). To make bge-m3 + MiniMax-M2.7 active
# automatically the operator must either (a) extend
# docker/fastgpt/config.json with the matching llmModels/vectorModels
# blocks BEFORE first boot, or (b) configure them once via the FastGPT
# admin console UI. Phase 0 deferred this pending TASK-25-style env
# unification; this script is forward-compatible — once config.json
# carries the entries, no script change is needed.
FASTGPT_MASS_ACTIVATION_PATH = "/api/admin/initv4820"


def _tags_table_exists(cur) -> bool:
    cur.execute("SELECT to_regclass(%s)", ("public.tags",))
    return cur.fetchone()[0] is not None


def _insert_system_tags(cur) -> None:
    for code, name, desc in SYSTEM_TAGS:
        cur.execute(
            "INSERT INTO tags (code, name, description, is_system) "
            "VALUES (%s, %s, %s, true) "
            "ON CONFLICT (code) DO NOTHING",
            (code, name, desc),
        )


def bootstrap_tags() -> int:
    if not DB_URL:
        print("[ERROR] NCMU_DB_URL env var not set. Set it or load via .env.")
        return 1

    conn = psycopg2.connect(DB_URL)
    try:
        with conn, conn.cursor() as cur:
            # Phase 1 ncmu-backend migration creates the tags table.
            # Skip gracefully if not present yet.
            if not _tags_table_exists(cur):
                print("[SKIP] tags table not found; run ncmu-backend migration first")
                return 0
            _insert_system_tags(cur)
            print(f"[OK] initialized {len(SYSTEM_TAGS)} system tags")
    finally:
        conn.close()
    return 0


def bootstrap_fastgpt(mongo_uri: str, fastgpt_api_base: str, fastgpt_root_token: str) -> dict:
    """Seed FastGPT mongo + activate models from config.json. Idempotent.

    Returns a dict report:
      {
        "team_subscriptions_seeded": bool,
        "models_activated_count":    int,   # mongo isActive=true total after run
        "mass_activation_response":  dict,  # raw FastGPT JSON body
      }

    Success of the mass-activation endpoint is determined by JSON body
    `code == 200` (FastGPT v4.14.10.2 returns HTTP 200 on success and
    sometimes HTTP 500 on app-level errors — both wrap a JSON body of
    shape {"code":int, "statusText":str, "message":str, "data":any},
    so the HTTP status alone is unreliable). See [DONE] summary for
    the curl-verified shape.
    """
    # Lazy imports so the default tag-init mode keeps working without
    # the optional pymongo / requests deps installed.
    import requests
    from pymongo import MongoClient

    report: dict = {
        "team_subscriptions_seeded": False,
        "models_activated_count": 0,
        "mass_activation_response": None,
    }

    # 1. team_subscriptions seed (B-NEW-04). FastGPT refuses workspace
    # actions when this collection is empty for the root team; one
    # `free`-tier doc unblocks dataset / app creation for root.
    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
    try:
        db = client.get_default_database()
        if db is None:
            # mongo_uri lacked a /<dbname> path — fall back to "fastgpt".
            db = client["fastgpt"]
        if db.team_subscriptions.count_documents({}) == 0:
            db.team_subscriptions.insert_one(
                {
                    "teamId": "root",
                    "currentSubLevel": "free",
                    "currentExtraDatasetSize": 0,
                    "currentExtraPoints": 0,
                    "expiredTime": None,
                    "createTime": _dt.datetime.now(_dt.timezone.utc),
                }
            )
            report["team_subscriptions_seeded"] = True
            print("[OK] FastGPT mongo team_subscriptions: seeded root/free")
        else:
            print("[SKIP] FastGPT mongo team_subscriptions: already populated")

        # 2. Mass model activation (B-NEW-05). Hit the rootkey-protected
        # admin migration endpoint that walks config.json's model lists
        # and upserts each into system_models with isActive=true.
        url = f"{fastgpt_api_base.rstrip('/')}{FASTGPT_MASS_ACTIVATION_PATH}"
        headers = {"rootkey": fastgpt_root_token, "Content-Type": "application/json"}
        try:
            r = requests.post(url, headers=headers, json={}, timeout=30)
            try:
                body = r.json()
            except ValueError:
                body = {"_raw_text": r.text[:200]}
            report["mass_activation_response"] = body
            code = body.get("code")
            if code == 200:
                print("[OK] FastGPT mass model activation succeeded "
                      f"(POST {FASTGPT_MASS_ACTIVATION_PATH})")
            else:
                msg = body.get("message") or body.get("statusText") or r.text[:80]
                print(f"[WARN] FastGPT mass model activation got code={code} msg={msg}")
        except requests.RequestException as exc:
            report["mass_activation_response"] = {"_request_error": str(exc)}
            print(f"[WARN] FastGPT mass model activation request error: {exc}")

        # 3. Post-activation reality check: count active models in mongo.
        # Lets the operator see at a glance whether the activation wrote
        # anything. A count of 0 is normally a sign that config.json is
        # minimal (Phase 0 default) — see DOCSTRING for FASTGPT_MASS_
        # ACTIVATION_PATH.
        report["models_activated_count"] = db.system_models.count_documents(
            {"metadata.isActive": True}
        )
        print(f"[INFO] FastGPT system_models with isActive=true: "
              f"{report['models_activated_count']}")
    finally:
        client.close()

    return report


def _build_mongo_uri_from_env() -> str:
    user = os.environ.get("FASTGPT_MONGO_USER", "fastgpt")
    pwd = os.environ.get("FASTGPT_MONGO_PASSWORD", "")
    host = os.environ.get("FASTGPT_MONGO_HOST", "fastgpt-mongo")
    port = os.environ.get("FASTGPT_MONGO_PORT", "27017")
    dbname = os.environ.get("FASTGPT_MONGO_DB", "fastgpt")
    if not pwd:
        raise SystemExit("[ERROR] FASTGPT_MONGO_PASSWORD env var not set.")
    # FastGPT mongo runs as a single-member replSet rs0; directConnection
    # is required so the pymongo driver does not try to discover other
    # members (there are none) and time out.
    return (
        f"mongodb://{user}:{pwd}@{host}:{port}/{dbname}"
        "?authSource=admin&directConnection=true"
    )


def _run_bootstrap_fastgpt() -> int:
    base = os.environ.get("FASTGPT_BASE_URL", "http://fastgpt:3000")
    token = os.environ.get("FASTGPT_ROOT_KEY")
    if not token:
        print("[ERROR] FASTGPT_ROOT_KEY env var not set.")
        return 1
    mongo_uri = _build_mongo_uri_from_env()
    report = bootstrap_fastgpt(mongo_uri, base, token)
    print(f"[REPORT] bootstrap_fastgpt: {report}")
    # Don't fail the whole start-dev pipeline on best-effort model
    # activation; log and continue. Mongo seed must succeed though —
    # bootstrap_fastgpt would have raised a pymongo exception above.
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="NCMU first-run bootstrap.")
    parser.add_argument(
        "--bootstrap-fastgpt",
        action="store_true",
        help="Additionally seed FastGPT mongo + activate default models.",
    )
    args = parser.parse_args()

    rc = bootstrap_tags()
    if rc != 0:
        return rc
    if args.bootstrap_fastgpt:
        return _run_bootstrap_fastgpt()
    return 0


if __name__ == "__main__":
    sys.exit(main())
