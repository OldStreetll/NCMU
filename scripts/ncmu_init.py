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
  FASTGPT_ROOT_TOKEN           (--bootstrap-fastgpt)
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

# FastGPT default models to activate on a fresh deploy. Pairs of
# (provider, model). Both must exist in FastGPT's built-in catalog —
# bge-m3 ships out of the box; MiniMax-M2.7 is the LLM endpoint
# wired up by .env (FASTGPT_LLM_*). Activating a model that does not
# exist returns body.code != 200 and is logged but does not abort.
DEFAULT_FASTGPT_MODELS = [
    ("openai", "bge-m3"),
    ("openai", "MiniMax-M2.7"),
]


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
    """Seed FastGPT mongo + activate default models. Idempotent.

    Returns a dict report:
      {
        "team_subscriptions_seeded": bool,
        "models_activated":   [provider/model, ...],
        "models_failed":      [{"key": "p/m", "code": int, "msg": str}, ...],
      }

    Success of /api/core/ai/model/update is determined by JSON body
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
        "models_activated": [],
        "models_failed": [],
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
                    "createTime": _dt.datetime.utcnow(),
                }
            )
            report["team_subscriptions_seeded"] = True
            print("[OK] FastGPT mongo team_subscriptions: seeded root/free")
        else:
            print("[SKIP] FastGPT mongo team_subscriptions: already populated")
    finally:
        client.close()

    # 2. Default model activation (B-NEW-05). Each POST is harmless to
    # repeat — the API just re-asserts isActive=true.
    url = f"{fastgpt_api_base.rstrip('/')}/api/core/ai/model/update"
    headers = {"rootkey": fastgpt_root_token, "Content-Type": "application/json"}
    for provider, model in DEFAULT_FASTGPT_MODELS:
        key = f"{provider}/{model}"
        try:
            r = requests.post(
                url,
                headers=headers,
                json={"provider": provider, "model": model, "isActive": True},
                timeout=10,
            )
            try:
                body = r.json()
            except ValueError:
                body = {}
            code = body.get("code")
            if code == 200:
                report["models_activated"].append(key)
                print(f"[OK] FastGPT model activated: {key}")
            else:
                msg = body.get("message") or body.get("statusText") or r.text[:80]
                report["models_failed"].append({"key": key, "code": code, "msg": msg})
                print(f"[WARN] FastGPT model activation failed: {key} (code={code} msg={msg})")
        except requests.RequestException as exc:
            report["models_failed"].append({"key": key, "code": None, "msg": str(exc)})
            print(f"[WARN] FastGPT model activation request error: {key} ({exc})")

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
    token = os.environ.get("FASTGPT_ROOT_TOKEN")
    if not token:
        print("[ERROR] FASTGPT_ROOT_TOKEN env var not set.")
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
