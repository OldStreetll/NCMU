"""ncmu_init.py — First-run system tag + FastGPT bootstrap + token sync.

Default mode: idempotently insert customer / staff / admin system tags
into pg-ncmu (skipped gracefully when the tags table is not yet
migrated by ncmu-backend).

--bootstrap-fastgpt: additionally seed FastGPT mongo team_subscriptions
and activate the default embedding + chat models via FastGPT's
admin API. Called by scripts/start-dev.sh after the FastGPT app
container reports healthy.

Token-sync flow (TASK-42 / B-NEW-14, dev mode only):
  Before any init step, detect ``CHANGE_ME``-prefixed token keys in
  ``.env`` (PLACEHOLDER_TOKEN_KEYS list). If found and DEPLOY_PROFILE
  != "prod", read admin credentials from ``.env.bootstrap``, log into
  Dify Console (``POST /console/api/login`` w/ base64 password),
  fetch DIFY_CONSOLE_API_KEY (access_token JWT) + DIFY_APP_DEFAULT_TOKEN
  (per-App api-key from ``/console/api/apps/{app_id}/api-keys``), and
  atomically write back to ``.env``. Operator is then prompted to
  ``docker compose restart ncmu-backend`` and ncmu-init exits cleanly
  without continuing the rest of the init flow. Failure paths warn on
  STDERR and continue init when nothing was written.

Env vars consumed:
  NCMU_DB_URL                  (default tag init)
  DEPLOY_PROFILE               (token sync; "dev"|"prod"; default dev;
                                read from os.environ first then .env)
  DIFY_BASE_URL                (token sync; default http://dify-api:5001)
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

Source: v3.3.1 §18 line 1695 + Phase 1 plan TASK-21 (B-NEW-04/05) +
        Phase 2A plan TASK-42 (B-NEW-14 token sync).
"""
import argparse
import base64
import datetime as _dt
import os
import sys
from pathlib import Path

# psycopg2 / requests / pymongo are imported lazily inside the functions
# that need them. Keeps the unit-test path (which only exercises the
# token-sync flow) free of optional runtime deps that ship via the
# ncmu-init docker image rather than the backend venv.

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


# ─────────────────────────────────────────────────────────────────────
# TASK-42 token-sync (B-NEW-14) — dev-mode placeholder detect + sync
# ─────────────────────────────────────────────────────────────────────

# Repo paths anchored to scripts/ncmu_init.py location.
REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = REPO_ROOT / ".env"
ENV_BOOTSTRAP_PATH = REPO_ROOT / ".env.bootstrap"

# Token keys eligible for placeholder detection. Curated from .env.example
# (2026-04-29 grep + TASK-42 派发前二次确认 2026-05-05). NCMU_JWT_SECRET,
# DIFY_SECRET_KEY, FERNET_KEY etc. are NOT in this list — they are
# deployment-time secrets, not API tokens fetchable from a running service.
PLACEHOLDER_TOKEN_KEYS = [
    "DIFY_APP_DEFAULT_TOKEN",
    "DIFY_CONSOLE_API_KEY",
    "FASTGPT_API_KEY",
    "FASTGPT_ROOT_KEY",
    "FASTGPT_TOKEN_KEY",
    "FASTGPT_FILE_TOKEN_KEY",
    "SILICONFLOW_API_KEY",  # errata-11 衔接 — manual sync only (warn, no fetch)
]
SILICONFLOW_KEY = "SILICONFLOW_API_KEY"

# Hint shown when SILICONFLOW_API_KEY is detected as a placeholder.
SILICONFLOW_GUIDANCE = (
    f"[WARN] {SILICONFLOW_KEY} cannot be auto-synced; please obtain an "
    f"API Key at https://siliconflow.cn and set the value in "
    f".env.bootstrap (or directly in .env)."
)


def _parse_env_line(line: str):
    """Parse a single ``KEY=value`` env file line.

    Returns ``(key, value)`` tuple or ``None`` for blank/comment/malformed
    lines. Matches the shell-style tolerant parsing used by docker-compose
    (no quoting / escaping support — values are raw text after ``=``).
    """
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    key, _, value = stripped.partition("=")
    return key.strip(), value


def _parse_env_file(path: Path) -> dict:
    """Load a ``.env`` style file into a dict. Returns empty dict if missing.

    Used by ncmu-init pre-boot (no backend Settings cache available, so
    we cannot import the pydantic-settings layer). Mirrors the lightweight
    parsing style of :func:`_build_mongo_uri_from_env`.
    """
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        parsed = _parse_env_line(line)
        if parsed:
            out[parsed[0]] = parsed[1]
    return out


def _read_deploy_profile() -> str:
    """Read DEPLOY_PROFILE from os.environ, fall back to .env, default 'dev'.

    ncmu-init runs before backend boot — backend Settings cache is unavailable
    here. We honor whichever value reaches us first via:

        1. ``os.environ["DEPLOY_PROFILE"]`` (docker compose env_file or shell)
        2. ``DEPLOY_PROFILE=`` line in .env (file at REPO_ROOT)
        3. Fallback default ``"dev"`` (matches backend Settings default)
    """
    val = os.environ.get("DEPLOY_PROFILE")
    if val:
        return val
    parsed = _parse_env_file(ENV_PATH)
    return parsed.get("DEPLOY_PROFILE", "dev")


def check_env_placeholders(env_path=None) -> list:
    """Return token keys whose .env value starts with ``CHANGE_ME``.

    Detection scope is restricted to :data:`PLACEHOLDER_TOKEN_KEYS` to avoid
    flagging deployment-time secrets (NCMU_JWT_SECRET, DIFY_SECRET_KEY, etc.)
    that should never be auto-synced. Both the bare ``CHANGE_ME`` and any
    suffixed variant (e.g. ``CHANGE_ME_FERNET_OR_OPENSSL_RAND_BASE64_32_CHARS_``)
    qualify because the check uses ``str.startswith``.

    ``env_path`` defaults to the module-level :data:`ENV_PATH` resolved at
    call time, so test harnesses can ``monkeypatch.setattr(ncmu_init,
    "ENV_PATH", tmp)`` and have it take effect without leaking writes onto
    the real repo .env.

    Phase X TODO: replace local .env with Vault/sops via TOKEN_SOURCE env.
    """
    if env_path is None:
        env_path = ENV_PATH
    if not env_path.exists():
        return []
    found = []
    for line in env_path.read_text(encoding="utf-8").splitlines():
        parsed = _parse_env_line(line)
        if not parsed:
            continue
        key, value = parsed
        if key in PLACEHOLDER_TOKEN_KEYS and value.startswith("CHANGE_ME"):
            found.append(key)
    return found


def _dify_login(base_url: str, email: str, password_plain: str):
    """POST /console/api/login with base64-encoded password.

    Returns ``(cookies_dict, access_token_jwt)`` on success, or ``None`` on
    any failure (network, 4xx/5xx, JSON parse, missing token in response).

    Per recover-dify-fastgpt-sop §5.2: setup expects a plain password but
    login requires a base64-encoded password (``api/libs/encryption.py:17``).
    """
    import requests
    try:
        pw_b64 = base64.b64encode(password_plain.encode("utf-8")).decode("ascii")
    except Exception:
        return None
    body = {
        "email": email,
        "password": pw_b64,
        "language": "en-US",
        "remember_me": True,
    }
    url = f"{base_url.rstrip('/')}/console/api/login"
    try:
        r = requests.post(url, json=body, timeout=10)
        r.raise_for_status()
        token = r.cookies.get("access_token")
        if not token:
            try:
                data = r.json().get("data") or {}
            except ValueError:
                data = {}
            if isinstance(data, dict):
                token = data.get("access_token")
        if not token:
            return None
        return dict(r.cookies), token
    except requests.RequestException:
        return None


def _dify_list_apps(base_url: str, cookies: dict, access_token: str):
    """GET /console/api/apps. Returns ``data`` list or ``None`` on failure."""
    import requests
    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"{base_url.rstrip('/')}/console/api/apps"
    try:
        r = requests.get(
            url,
            headers=headers,
            cookies=cookies,
            timeout=10,
            params={"page": 1, "limit": 100},
        )
        r.raise_for_status()
        return r.json().get("data") or []
    except (requests.RequestException, ValueError):
        return None


def _dify_get_app_keys(base_url: str, cookies: dict, access_token: str, app_id: str):
    """GET /console/api/apps/{app_id}/api-keys. Returns list or ``None``."""
    import requests
    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"{base_url.rstrip('/')}/console/api/apps/{app_id}/api-keys"
    try:
        r = requests.get(url, headers=headers, cookies=cookies, timeout=10)
        r.raise_for_status()
        return r.json().get("data") or []
    except (requests.RequestException, ValueError):
        return None


def sync_tokens_to_env(
    missing_keys: list,
    bootstrap_path=None,
    dify_base_url: str = None,
) -> dict:
    """Try to fetch real tokens from Dify Console (+ FastGPT pending).

    Returns ``{key: real_token}`` for successfully fetched keys; failed keys
    are excluded. STDERR warnings are emitted for each failure case so the
    operator can diagnose without parsing the dict shape.

    Failure semantics (TASK-42 AC#4):
      * SILICONFLOW_API_KEY in ``missing_keys`` → STDERR guidance, key skipped.
      * .env.bootstrap missing → STDERR warn, return empty dict.
      * Dify login fails → STDERR warn, no Dify keys synced.
      * FastGPT keys present → STDERR warn (endpoint spike pending H1).

    Phase X TODO: replace local .env with Vault/sops via TOKEN_SOURCE env.
    """
    if bootstrap_path is None:
        bootstrap_path = ENV_BOOTSTRAP_PATH
    remaining = list(missing_keys)

    # SILICONFLOW: emit guidance + skip (cannot auto-sync per errata-11).
    if SILICONFLOW_KEY in remaining:
        print(SILICONFLOW_GUIDANCE, file=sys.stderr)
        remaining = [k for k in remaining if k != SILICONFLOW_KEY]

    if not remaining:
        return {}

    creds = _parse_env_file(bootstrap_path)
    if not creds:
        print(
            "[WARN] .env.bootstrap not found or empty; cannot sync tokens. "
            "Copy .env.bootstrap.example and fill credentials.",
            file=sys.stderr,
        )
        return {}

    if dify_base_url is None:
        dify_base_url = os.environ.get("DIFY_BASE_URL", "http://dify-api:5001")

    updates: dict = {}

    # Dify keys: login → fetch console JWT + per-App api-key.
    dify_keys = [k for k in remaining if k in ("DIFY_APP_DEFAULT_TOKEN", "DIFY_CONSOLE_API_KEY")]
    if dify_keys:
        admin_email = creds.get("DIFY_ADMIN_EMAIL")
        admin_password = creds.get("DIFY_ADMIN_PASSWORD")
        if not admin_email or not admin_password:
            print(
                "[WARN] DIFY_ADMIN_EMAIL/DIFY_ADMIN_PASSWORD missing in "
                ".env.bootstrap; skipping Dify key sync.",
                file=sys.stderr,
            )
        else:
            login_result = _dify_login(dify_base_url, admin_email, admin_password)
            if login_result is None:
                print(
                    "[WARN] Dify Console login failed; skipping Dify key sync.",
                    file=sys.stderr,
                )
            else:
                cookies, access_token = login_result
                if "DIFY_CONSOLE_API_KEY" in dify_keys:
                    updates["DIFY_CONSOLE_API_KEY"] = access_token
                if "DIFY_APP_DEFAULT_TOKEN" in dify_keys:
                    apps = _dify_list_apps(dify_base_url, cookies, access_token)
                    if apps is None:
                        print(
                            "[WARN] DIFY_APP_DEFAULT_TOKEN sync failed: "
                            "could not list Dify Apps.",
                            file=sys.stderr,
                        )
                    else:
                        kb_app = next(
                            (a for a in apps if "[KB]" in (a.get("name") or "")),
                            None,
                        )
                        if kb_app is None:
                            print(
                                "[WARN] DIFY_APP_DEFAULT_TOKEN sync failed: "
                                "no [KB]-prefixed App found "
                                "(run TASK-24 KB App setup first).",
                                file=sys.stderr,
                            )
                        else:
                            keys = _dify_get_app_keys(
                                dify_base_url, cookies, access_token, kb_app["id"]
                            )
                            if not keys:
                                print(
                                    "[WARN] DIFY_APP_DEFAULT_TOKEN sync "
                                    "failed: no api-keys for [KB] App "
                                    f"({kb_app['id']}).",
                                    file=sys.stderr,
                                )
                            else:
                                updates["DIFY_APP_DEFAULT_TOKEN"] = keys[0]["token"]

    # FastGPT keys: pending v4.14.x team_api_keys endpoint spike (TASK-42 H1).
    # Per plan AC#4, partial-fail still writes the keys we did sync.
    fastgpt_keys = [k for k in remaining if k.startswith("FASTGPT_")]
    if fastgpt_keys:
        print(
            "[WARN] FastGPT auto-sync not implemented (v4.14.x team_api_keys "
            f"endpoint pending spike); placeholders remain: {fastgpt_keys}. "
            "Operator must seed these directly in .env until follow-up lands.",
            file=sys.stderr,
        )

    return updates


def atomic_write_env(updates: dict, env_path=None) -> None:
    """Atomically replace given keys in ``.env``, preserving order/comments.

    Reads ``.env``, swaps the target keys' values (keeping every other line —
    comments, blank lines, unrelated assignments — verbatim), writes a sibling
    ``.env.tmp`` and ``os.replace``s it onto ``.env``. Keys absent from ``.env``
    are appended at the end (rare; most call sites pre-filter via
    :func:`check_env_placeholders` which only returns keys already present).

    9p/NTFS note (INDEP-PLAN-2 A3 修订): ``os.replace``'s POSIX atomicity
    relies on the underlying mount honoring rename-as-atomic-overwrite. WSL2
    9p shares to NTFS preserve the rename semantics for single-process callers
    without concurrent readers (which is the dev case here). The prod upgrade
    path lifts secrets to Vault/sops and does not depend on filesystem
    atomicity at all (errata-09-10-11 §3 衔接清单).

    Phase X TODO: replace local .env with Vault/sops via TOKEN_SOURCE env.
    """
    if env_path is None:
        env_path = ENV_PATH
    if not updates:
        return
    if not env_path.exists():
        raise OSError(f"{env_path} not found; cannot atomic-write")

    raw = env_path.read_text(encoding="utf-8")
    # splitlines(keepends=True) preserves trailing newlines (incl. CRLF) so
    # the output round-trips byte-for-byte for unaltered lines.
    lines = raw.splitlines(keepends=True)

    seen = set()
    new_lines = []
    for line in lines:
        # Detect the exact EOL so the rewritten line keeps the same one.
        if line.endswith("\r\n"):
            eol, bare = "\r\n", line[:-2]
        elif line.endswith("\n"):
            eol, bare = "\n", line[:-1]
        else:
            eol, bare = "", line
        parsed = _parse_env_line(bare)
        if parsed and parsed[0] in updates and parsed[0] not in seen:
            new_lines.append(f"{parsed[0]}={updates[parsed[0]]}{eol}")
            seen.add(parsed[0])
        else:
            new_lines.append(line)
    # Append any keys that weren't already in .env (best-effort fallback).
    trailing_eol = "\n" if (not new_lines or new_lines[-1].endswith(("\n", "\r\n"))) else "\n"
    for key, val in updates.items():
        if key not in seen:
            new_lines.append(f"{key}={val}{trailing_eol}")

    tmp = env_path.with_name(env_path.name + ".tmp")
    tmp.write_text("".join(new_lines), encoding="utf-8")
    try:
        os.replace(str(tmp), str(env_path))
    except OSError:
        # Cleanup the half-written temp file before propagating the error.
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def run_token_sync_pre_init():
    """Detect placeholders + try sync (dev-only). Returns int|None.

    Behavior matrix (TASK-42 AC#4):

    +-----------------------------------+---------------------+-----------------+
    | State                             | .env touched?       | Return value    |
    +===================================+=====================+=================+
    | DEPLOY_PROFILE == "prod"          | no                  | None (continue) |
    | No placeholders                   | no                  | None (continue) |
    | Sync wrote ≥1 token               | yes                 | 0 (exit clean)  |
    | Sync wrote 0 tokens (all failed)  | no                  | None (continue) |
    | Atomic write raised OSError       | no (tmp cleaned up) | 1 (exit error)  |
    +-----------------------------------+---------------------+-----------------+

    The caller (``main``) treats ``None`` as "no early exit, continue normal
    init flow"; an integer is propagated as the script exit code.

    Phase X TODO: replace local .env with Vault/sops via TOKEN_SOURCE env.
    """
    profile = _read_deploy_profile()
    if profile == "prod":
        return None

    placeholders = check_env_placeholders()
    if not placeholders:
        return None

    print(f"[INFO] Detected token placeholders: {placeholders}")
    updates = sync_tokens_to_env(placeholders)
    if not updates:
        # All sync attempts failed; STDERR warns already emitted. Continue
        # normal init so the operator's existing 8-class debug pipeline still
        # surfaces the underlying problem (e.g. backend boot will fail loudly
        # with a CHANGE_ME-token error rather than be silently swallowed).
        return None

    try:
        atomic_write_env(updates)
    except OSError as exc:
        print(
            f"[ERROR] atomic write to .env failed: {exc}; .env unchanged.",
            file=sys.stderr,
        )
        return 1

    synced_keys = sorted(updates.keys())
    print(
        f"✅ Tokens synced: {synced_keys}. "
        f"Please restart ncmu-backend: docker compose restart ncmu-backend"
    )
    return 0


# ─────────────────────────────────────────────────────────────────────
# Existing init flow (unchanged from Phase 1)
# ─────────────────────────────────────────────────────────────────────


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

    import psycopg2

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
    parser.add_argument(
        "--skip-token-sync",
        action="store_true",
        help="Skip the dev-mode token-sync pre-step (useful in tests / CI).",
    )
    args = parser.parse_args()

    # Token-sync runs first in dev mode (TASK-42 / B-NEW-14). When the sync
    # writes any real token to .env, ncmu-init exits 0 immediately so the
    # operator can `docker compose restart ncmu-backend` with the new env
    # before attempting the rest of init. When nothing is written (no
    # placeholders, all-failed sync, or prod profile) we fall through to the
    # normal init flow.
    if not args.skip_token_sync:
        sync_rc = run_token_sync_pre_init()
        if sync_rc is not None:
            return sync_rc

    rc = bootstrap_tags()
    if rc != 0:
        return rc
    if args.bootstrap_fastgpt:
        return _run_bootstrap_fastgpt()
    return 0


if __name__ == "__main__":
    sys.exit(main())
