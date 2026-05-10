#!/usr/bin/env bash
#
# recover-dify-provider.sh — idempotent Dify provider/model recovery (B-NEW-51)
#
# Two stages:
#   A) Infra container self-heal — recreate any container left stopped
#      after `docker compose down -v` or a Docker Desktop / `wsl --shutdown`
#      cycle. Two known shapes:
#        - bind-mount stale → exit 127  (nginx, dify-nginx, dify-ssrf-proxy)
#        - normal SIGTERM shutdown not auto-restarted → exit 137 with
#          OOMKilled=false (kb-adapter; permanent fix would be adding
#          `restart: unless-stopped` in docker-compose.yml).
#      `DETECT_ONLY_CONTAINERS` is reserved for containers that should
#      NOT be auto-recreated (e.g. real OOM cases) — currently empty.
#   B) Dify console-auth + plugin install + provider/model register, then
#      smoke /v1/chat-messages with field-level assertion (answer non-empty).
#
# Usage:
#   bash scripts/recover-dify-provider.sh            # apply
#   bash scripts/recover-dify-provider.sh --dry-run  # preview only
#   bash scripts/recover-dify-provider.sh --help
#
# Re-running is a no-op: every step short-circuits when the desired state
# already exists. See scripts/README.md "down -v 后恢复 Dify provider" for
# triggers, prerequisites, and expected output.

# bash 4 is fine for everything we use here. -E ensures the ERR trap fires
# from inside functions and command substitutions.
# (memory feedback_bash_set_E_errtrace — NCMU TASK-37 backup.sh fix.)
set -Eeuo pipefail

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly ENV_FILE="$REPO_ROOT/.env"

readonly DIFY_HOST_URL="http://localhost:8080"
# Containers safely recreated by this script when found stopped.
#   - nginx / dify-nginx / dify-ssrf-proxy: bind-mount goes stale after
#     `wsl --shutdown` / Docker Desktop restart, exit 127.
#   - kb-adapter: exits 137 with OOMKilled=false (NOT an OOM kill —
#     verified inspect showed memory limit=0 and logs ending in
#     "Application shutdown complete"); root cause is the absence of a
#     `restart: unless-stopped` policy in docker-compose.yml. Recreate
#     is harmless. Permanent fix lives in compose, out of this script's
#     scope.
readonly INFRA_RECREATE_CONTAINERS=(
  "ncmu-dify-nginx"
  "ncmu-nginx"
  "ncmu-dify-ssrf-proxy"
  "ncmu-kb-adapter"
)
# Containers we deliberately DO NOT auto-recreate — reserved for cases
# where the failure mode genuinely needs operator attention (e.g. real
# OOM kills under memory pressure). Empty today; kept as an extension
# point so future "do not silently recreate" cases have a clear home.
readonly DETECT_ONLY_CONTAINERS=()
readonly DIFY_API_CONTAINER="ncmu-dify-api"

readonly DIFY_ADMIN_EMAIL="${DIFY_ADMIN_EMAIL:-admin@ncmu.local}"
readonly DIFY_ADMIN_PASSWORD="${DIFY_ADMIN_PASSWORD:-Ncmu@E2E2026}"

readonly MARKETPLACE_API="https://marketplace.dify.ai/api/v1"
readonly PLUGINS_REQUIRED=(
  "langgenius/openai_api_compatible"
  "langgenius/minimax"
)

readonly PROVIDER_ID="langgenius/openai_api_compatible/openai_api_compatible"
readonly MODEL_NAME="MiniMax-M2.7"
readonly MODEL_TYPE="llm"
readonly MODEL_ENDPOINT="http://111.172.214.40:32086/v1"

readonly NGINX_HEALTHY_TIMEOUT=30
readonly PLUGIN_INSTALL_TIMEOUT=180

# Runtime state
DRY_RUN=0
COOKIE_JAR=""
TOK=""
CSRF=""

# ---------------------------------------------------------------------------
# Logging + error trap
# ---------------------------------------------------------------------------
say()   { printf '\n===== %s =====\n' "$*"; }
info()  { printf '[INFO] %s\n' "$*"; }
warn()  { printf '[WARN] %s\n' "$*" >&2; }
ok()    { printf '[ OK ] %s\n' "$*"; }
fail()  { printf '[FAIL] %s\n' "$*" >&2; exit 1; }
skip()  { printf '[SKIP] %s\n' "$*"; }

on_err() {
  local exit_code=$?
  printf '\n[FAIL] line %s: %s (exit %s)\n' "$1" "$2" "$exit_code" >&2
  exit "$exit_code"
}
trap 'on_err "$LINENO" "$BASH_COMMAND"' ERR

# Cleanup runs out-of-line via a dedicated function — never inline a
# backup/cleanup step in the same bash block as the work it's protecting.
# (memory feedback_inline_cleanup_pitfall — NCMU 2026-05-07 .env.bak incident.)
cleanup() {
  if [[ -n "$COOKIE_JAR" && -f "$COOKIE_JAR" ]]; then
    rm -f "$COOKIE_JAR"
  fi
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Args
# ---------------------------------------------------------------------------
print_usage() {
  cat <<'EOF'
Usage: recover-dify-provider.sh [--dry-run] [--help]

Recovers Dify infrastructure after `docker compose down -v` or a Docker
Desktop / `wsl --shutdown` cycle (B-NEW-51 and same-shape exit-127/137):

  Stage A  Infra container self-heal — recreate stopped infra
           (nginx, dify-nginx, dify-ssrf-proxy, kb-adapter)
  Stage B  Dify console auth → plugin install → provider/model register
  Verify   /v1/chat-messages 200 with non-empty `answer` field

Idempotent: re-running is a no-op when the stack is already healthy.

Options:
  --dry-run   Print every action that would be taken; do not mutate state.
  --help      Show this message.

Environment:
  DIFY_ADMIN_EMAIL      default admin@ncmu.local
  DIFY_ADMIN_PASSWORD   default Ncmu@E2E2026
EOF
}

while (($#)); do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --help|-h) print_usage; exit 0 ;;
    *)         warn "unknown arg: $1"; print_usage; exit 2 ;;
  esac
  shift
done

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "missing required command: $1"
}

# Read a key=value line from .env without sourcing the file (.env contains
# special chars that break `source`).
env_get() {
  local key="$1"
  [[ -f "$ENV_FILE" ]] || return 1
  awk -F= -v k="$key" '$1==k {sub(/^[^=]+=/,""); print; exit}' "$ENV_FILE"
}

# JSON helper — we depend on python3 (already used in part_a/part_b).
json_get() {
  python3 -c '
import sys, json
data = json.load(sys.stdin)
for key in sys.argv[1].split("."):
    if isinstance(data, list):
        data = data[int(key)]
    else:
        data = data.get(key) if data else None
    if data is None:
        sys.exit(0)
print(data if not isinstance(data, (dict, list)) else json.dumps(data))
' "$1"
}

# Run mutating commands only when not in dry-run.
do_or_show() {
  if (( DRY_RUN )); then
    printf '[DRY-RUN] would run: %s\n' "$*"
  else
    "$@"
  fi
}

# ---------------------------------------------------------------------------
# Stage A — nginx self-heal
# ---------------------------------------------------------------------------
container_status() {
  # prints "running" / "exited" / "unhealthy" / "missing"
  local name="$1"
  if ! docker inspect "$name" >/dev/null 2>&1; then
    echo "missing"
    return
  fi
  local state
  state=$(docker inspect --format '{{.State.Status}}' "$name" 2>/dev/null || echo "unknown")
  echo "$state"
}

container_health() {
  # prints "healthy" / "unhealthy" / "none" / "missing"
  local name="$1"
  if ! docker inspect "$name" >/dev/null 2>&1; then
    echo "missing"
    return
  fi
  local h
  h=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$name" 2>/dev/null || echo "none")
  echo "$h"
}

stage_a_infra_self_heal() {
  say "Stage A — infra container self-heal"
  local to_recreate=()

  for c in "${INFRA_RECREATE_CONTAINERS[@]}"; do
    local s
    s=$(container_status "$c")
    case "$s" in
      running)
        info "$c: running"
        ;;
      missing|exited|created|dead|paused|restarting)
        warn "$c: status=$s — needs recreate"
        to_recreate+=("$c")
        ;;
      *)
        warn "$c: status=$s (unknown) — recreate to be safe"
        to_recreate+=("$c")
        ;;
    esac
  done

  if (( ${#to_recreate[@]} == 0 )); then
    ok "all infra containers already running, skip"
    return 0
  fi

  if (( DRY_RUN )); then
    info "[DRY-RUN] will recreate: ${to_recreate[*]}"
    return 0
  fi

  # Two restart shapes:
  #   - container has compose label → recreate via `docker compose up`
  #     (preferred; honours profile + healthcheck wait below)
  #   - container has NO compose label (e.g. kb-adapter is a manually
  #     started image not in docker-compose.yml) → plain `docker start`
  local services=() raw_starts=()
  for c in "${to_recreate[@]}"; do
    local svc
    svc=$(docker inspect "$c" --format '{{index .Config.Labels "com.docker.compose.service"}}' 2>/dev/null || echo "")
    if [[ -n "$svc" ]]; then
      services+=("$svc")
    else
      raw_starts+=("$c")
    fi
  done
  if (( ${#services[@]} > 0 )); then
    ( cd "$REPO_ROOT" && docker compose --profile dev up -d --force-recreate "${services[@]}" )
  fi
  for c in "${raw_starts[@]}"; do
    info "no compose label on $c — using \`docker start\`"
    docker start "$c" >/dev/null
  done

  # Wait for any container with healthcheck to report healthy.
  local deadline=$(( SECONDS + NGINX_HEALTHY_TIMEOUT ))
  for c in "${to_recreate[@]}"; do
    local h
    h=$(container_health "$c")
    if [[ "$h" == "none" ]]; then
      info "$c: no healthcheck — assume up after recreate"
      continue
    fi
    while (( SECONDS < deadline )); do
      h=$(container_health "$c")
      if [[ "$h" == "healthy" ]]; then break; fi
      sleep 2
    done
    [[ "$h" == "healthy" ]] || fail "$c did not become healthy within ${NGINX_HEALTHY_TIMEOUT}s (last=$h)"
  done

  ok "infra self-heal complete (${to_recreate[*]})"
}

# Detect-only check for containers we will NOT auto-recreate. If unhealthy,
# print a clear remediation hint and exit 1 — letting the operator decide
# (e.g., kb-adapter Exit 137 = OOM, recreate alone won't fix the root cause).
precheck_detect_only_containers() {
  say "Precheck — detect-only containers (no auto-recreate)"
  local issues=()
  for c in "${DETECT_ONLY_CONTAINERS[@]}"; do
    local s
    s=$(container_status "$c")
    case "$s" in
      running)
        info "$c: running"
        ;;
      missing)
        warn "$c: not present"
        issues+=("$c (missing)")
        ;;
      *)
        warn "$c: status=$s — needs operator action"
        issues+=("$c ($s)")
        ;;
    esac
  done

  if (( ${#issues[@]} == 0 )); then
    ok "all detect-only containers healthy"
    return 0
  fi

  if (( DRY_RUN )); then
    info "[DRY-RUN] would fail-fast on: ${issues[*]}"
    return 0
  fi

  cat >&2 <<EOF

[FAIL] One or more detect-only containers are not running:
  ${issues[*]}

These are NOT auto-recreated by this script because the failure mode is
not bind-mount stale. For example, ncmu-kb-adapter exits 137 (SIGKILL =
OOM) under memory pressure; a plain recreate would not address the root
cause and the container would die again. Investigate first, then:

  docker compose --profile dev up -d --force-recreate kb-adapter

(Inspect logs and resource limits before re-running this script.)

EOF
  exit 1
}

# ---------------------------------------------------------------------------
# Stage B.0 — console auth (three-tier fallback)
# ---------------------------------------------------------------------------
auth_probe() {
  # Returns 0 if the current $TOK + $CSRF + $COOKIE_JAR can hit a protected
  # endpoint. Uses /workspaces/current/plugin/list as a cheap probe.
  local code
  code=$(curl -sS -o /dev/null -w '%{http_code}' \
    -b "$COOKIE_JAR" \
    -H "Authorization: Bearer $TOK" \
    -H "X-CSRF-Token: $CSRF" \
    "$DIFY_HOST_URL/console/api/workspaces/current/plugin/list?page=1&page_size=1" || true)
  [[ "$code" == "200" ]]
}

# Tier 1: existing JWT in .env + freshly minted CSRF (CSRF JWT can be derived
# from a /console/api/account/profile call when the access_token cookie is
# valid). For simplicity, Tier 1 just probes whatever is in .env directly; if
# Dify's csrf middleware rejects it, we fall through to Tier 2 login.
console_auth_tier1_env() {
  local env_tok
  env_tok=$(env_get DIFY_CONSOLE_API_KEY 2>/dev/null || true)
  [[ -n "$env_tok" && "$env_tok" != "CHANGE_ME" ]] || return 1

  COOKIE_JAR=$(mktemp -t recover-dify.cookies.XXXXXX)
  TOK="$env_tok"
  CSRF=""

  # Without CSRF, the probe is GET — Dify only enforces CSRF on mutating
  # methods, so this still validates the token.
  if auth_probe; then
    ok "Tier 1: .env DIFY_CONSOLE_API_KEY accepted"
    return 0
  fi
  return 1
}

console_auth_tier2_login() {
  # INDEP-OPS-1 M-2: Tier 1 may have minted a cookie jar then failed; drop
  # it before allocating a fresh one or the trap-based cleanup loses the
  # old reference (only the latest COOKIE_JAR value gets rm'd on EXIT).
  if [[ -n "$COOKIE_JAR" && -f "$COOKIE_JAR" ]]; then
    rm -f "$COOKIE_JAR"
  fi
  COOKIE_JAR=$(mktemp -t recover-dify.cookies.XXXXXX)
  local b64pw
  b64pw=$(printf '%s' "$DIFY_ADMIN_PASSWORD" | base64 -w0)
  local resp
  resp=$(curl -sS -c "$COOKIE_JAR" -X POST "$DIFY_HOST_URL/console/api/login" \
    -H 'Content-Type: application/json' \
    -d "{\"email\":\"$DIFY_ADMIN_EMAIL\",\"password\":\"$b64pw\",\"language\":\"en-US\",\"remember_me\":true}" \
    || true)

  case "$resp" in
    *'"result":"success"'*)
      # INDEP-OPS-1 I-2: don't pin the HttpOnly prefix — Dify could flip
      # csrf_token to HttpOnly (or unflip access_token) in a future minor
      # release and the CSRF capture would silently return empty. Match
      # by cookie name only.
      TOK=$(awk  '$6=="access_token" {print $7}' "$COOKIE_JAR")
      CSRF=$(awk '$6=="csrf_token"   {print $7}' "$COOKIE_JAR")
      [[ -n "$TOK" && -n "$CSRF" ]] || return 1
      if auth_probe; then
        ok "Tier 2: admin login succeeded ($DIFY_ADMIN_EMAIL)"
        return 0
      fi
      ;;
  esac
  return 1
}

console_auth() {
  say "Stage B.0 — Dify console auth"

  if console_auth_tier1_env; then return 0; fi
  info "Tier 1 (.env DIFY_CONSOLE_API_KEY) unusable — trying Tier 2 login"
  if console_auth_tier2_login; then return 0; fi

  cat >&2 <<EOF

[FAIL] All auth tiers failed.

The admin password is not '$DIFY_ADMIN_PASSWORD'. Reset it back to the
default before re-running this script:

  docker exec -w /app/api $DIFY_API_CONTAINER \\
    /app/api/.venv/bin/flask reset-password \\
    --email '$DIFY_ADMIN_EMAIL' \\
    --new-password '$DIFY_ADMIN_PASSWORD' \\
    --password-confirm '$DIFY_ADMIN_PASSWORD'

Or override the credentials this script uses:

  DIFY_ADMIN_PASSWORD='<real-password>' bash scripts/recover-dify-provider.sh

EOF
  exit 1
}

# Mutating console call — always sends Authorization + X-CSRF-Token + cookies.
console_post() {
  local path="$1"
  local body="${2:-}"
  curl -sS -b "$COOKIE_JAR" \
    -H "Authorization: Bearer $TOK" \
    -H "X-CSRF-Token: $CSRF" \
    -H 'Content-Type: application/json' \
    -X POST "$DIFY_HOST_URL/console/api$path" \
    ${body:+-d "$body"}
}

console_get() {
  local path="$1"
  curl -sS -b "$COOKIE_JAR" \
    -H "Authorization: Bearer $TOK" \
    -H "X-CSRF-Token: $CSRF" \
    "$DIFY_HOST_URL/console/api$path"
}

# ---------------------------------------------------------------------------
# Stage B.0.5 — RSA keypair detect-and-reset
# ---------------------------------------------------------------------------
# `docker compose down -v` wipes the storage volume that holds each tenant's
# RSA private key (used to encrypt LLM credentials). Symptom is a 500 from
# any model-providers GET/POST with this traceback in dify-api logs:
#   libs.rsa.PrivkeyNotFoundError: Private key not found, tenant_id: ...
# Fix is a one-shot `flask reset-encrypt-key-pair --yes` inside the dify-api
# container. This is safe ONLY when no encrypted credentials still exist
# (after `down -v` they're gone too); in production with ciphertext present,
# reset would render existing credentials unrecoverable.
stage_b05_rsa_keypair() {
  say "Stage B.0.5 — RSA keypair detect-and-reset"

  # INDEP-OPS-1 I-1 (b): anchor a timestamp BEFORE probing so the log
  # search later only considers entries written by this very probe. A
  # plain `docker logs --tail 50` would otherwise let an old, stale
  # PrivkeyNotFoundError trigger a destructive reset on every run.
  local since_iso
  since_iso=$(date -u +%Y-%m-%dT%H:%M:%SZ)

  # Probe: any model-providers GET that decrypts will hit the Privkey path.
  local resp
  resp=$(console_get "/workspaces/current/model-providers/$PROVIDER_ID/models" || true)
  case "$resp" in
    *'"data":'*)
      ok "RSA keypair present (model-providers GET healthy), skip"
      return 0
      ;;
    *'Internal Server Error'*|*'"status":500'*)
      info "model-providers GET 500 — checking dify-api logs for PrivkeyNotFoundError"
      ;;
    *)
      # INDEP-OPS-1 I-1 (a): only a confirmed 500 should fall through to
      # the log check. 401 / 403 / timeouts / redirects / arbitrary network
      # errors must NOT route into a destructive reset path — fail safe.
      warn "model-providers GET unexpected non-500 response — skipping keypair reset"
      return 0
      ;;
  esac

  # INDEP-OPS-1 I-1 (b): bound the log search to entries since `since_iso`
  # so a stale PrivkeyNotFoundError from a previous run cannot trigger
  # rotation. In prod a single mistaken rotation makes every saved
  # credential ciphertext permanently unreadable.
  if ! docker logs --since "$since_iso" --tail 50 "$DIFY_API_CONTAINER" 2>&1 | grep -q PrivkeyNotFoundError; then
    warn "500 not caused by a fresh PrivkeyNotFoundError — leaving keypair alone"
    return 0
  fi

  if (( DRY_RUN )); then
    info "[DRY-RUN] would run: flask reset-encrypt-key-pair --yes (PrivkeyNotFoundError detected)"
    return 0
  fi

  warn "PrivkeyNotFoundError detected — running flask reset-encrypt-key-pair --yes"
  warn "Safe in dev (no surviving ciphertext). PRODUCTION: back up storage volume,"
  warn "DO NOT routinely run \`docker compose down -v\` against a tenant with credentials."

  local rkp_out rkp_rc
  if ! rkp_out=$(docker exec -w /app/api "$DIFY_API_CONTAINER" \
        /app/api/.venv/bin/flask reset-encrypt-key-pair --yes 2>&1); then
    rkp_rc=$?
    printf '%s\n' "$rkp_out" >&2
    fail "reset-encrypt-key-pair failed (rc=$rkp_rc)"
  fi
  printf '%s\n' "$rkp_out"
  ok "RSA keypair reset"
}

# ---------------------------------------------------------------------------
# Stage B.1 — plugin install (idempotent)
# ---------------------------------------------------------------------------
plugin_is_installed() {
  local plugin_id="$1"
  console_get "/workspaces/current/plugin/list?page=1&page_size=256" \
    | python3 -c '
import sys, json
target = sys.argv[1]
data = json.load(sys.stdin)
for p in data.get("plugins", []):
    if p.get("plugin_id") == target:
        sys.exit(0)
sys.exit(1)
' "$plugin_id"
}

marketplace_unique_id() {
  local plugin_id="$1"
  # INDEP-OPS-1 M-1: capture stdout via subshell instead of round-tripping
  # through a predictable /tmp path — the predictable path TOCTOU-races
  # with concurrent script instances and is inconsistent with the mktemp
  # approach used elsewhere in this script (cookie jars, chat-messages
  # response capture).
  local ver
  ver=$(curl -sS -m 30 "$MARKETPLACE_API/plugins/$plugin_id" \
    | python3 -c '
import sys, json
r = json.load(sys.stdin)
print(r.get("data", {}).get("plugin", {}).get("latest_version") or "")
')
  [[ -n "$ver" ]] || { warn "marketplace: no latest_version for $plugin_id"; return 1; }

  curl -sS -m 30 "$MARKETPLACE_API/plugins/$plugin_id/$ver" \
    | python3 -c '
import sys, json
r = json.load(sys.stdin)
print(r.get("data", {}).get("version", {}).get("unique_identifier") or "")
'
}

wait_plugin_task_complete() {
  local task_id="$1"
  local deadline=$(( SECONDS + PLUGIN_INSTALL_TIMEOUT ))
  while (( SECONDS < deadline )); do
    local resp status
    resp=$(console_get "/workspaces/current/plugin/tasks/$task_id" || true)
    status=$(printf '%s' "$resp" | python3 -c '
import sys, json
try:
    print(json.load(sys.stdin).get("task",{}).get("status",""))
except Exception:
    print("")
' || true)
    case "$status" in
      success) return 0 ;;
      failed)  warn "plugin task $task_id failed: $resp"; return 1 ;;
      "")      sleep 3 ;;
      *)       sleep 3 ;;
    esac
  done
  warn "plugin task $task_id timed out after ${PLUGIN_INSTALL_TIMEOUT}s"
  return 1
}

stage_b1_install_plugins() {
  say "Stage B.1 — plugin install"

  local will_install=()
  for p in "${PLUGINS_REQUIRED[@]}"; do
    if plugin_is_installed "$p"; then
      skip "plugin already installed: $p"
    else
      will_install+=("$p")
    fi
  done

  if (( DRY_RUN )); then
    if (( ${#will_install[@]} == 0 )); then
      info "[DRY-RUN] no plugins to install"
    else
      info "[DRY-RUN] will install plugins: ${will_install[*]}"
    fi
    return 0
  fi

  if (( ${#will_install[@]} == 0 )); then
    ok "all required plugins already installed"
    return 0
  fi

  for p in "${will_install[@]}"; do
    info "fetching marketplace unique_identifier for $p"
    local uid
    uid=$(marketplace_unique_id "$p")
    [[ -n "$uid" ]] || fail "marketplace lookup failed for $p"
    info "installing $uid"
    local resp
    resp=$(console_post "/workspaces/current/plugin/install/marketplace" \
      "{\"plugin_unique_identifiers\":[\"$uid\"]}")
    local task_id
    task_id=$(printf '%s' "$resp" | python3 -c '
import sys, json
r = json.load(sys.stdin)
# response shape: {"all_installed": false, "task_id": "..."} or similar
print(r.get("task_id") or r.get("all_installed", ""))
' || true)
    if [[ "$task_id" == "True" || "$task_id" == "true" ]]; then
      ok "$p installed (synchronous)"
      continue
    fi
    if [[ -z "$task_id" || "$task_id" == "False" || "$task_id" == "false" ]]; then
      warn "install response (no task_id): $resp"
      # Still continue — plugin may have been queued; we'll re-check below.
    else
      info "polling task $task_id..."
      wait_plugin_task_complete "$task_id" || fail "$p install task did not finish cleanly"
    fi
    plugin_is_installed "$p" || fail "$p still not installed after task completed"
    ok "$p installed"
  done
}

# ---------------------------------------------------------------------------
# Stage B.2 — provider / model register (idempotent)
# ---------------------------------------------------------------------------
model_already_registered() {
  # GET /workspaces/current/model-providers/<provider>/models returns:
  #   {"data": [{"model": "MiniMax-M2.7", "model_type": "llm", ...}, ...]}
  console_get "/workspaces/current/model-providers/$PROVIDER_ID/models" \
    | python3 -c '
import sys, json
target = sys.argv[1]
data = json.load(sys.stdin).get("data", [])
for m in data:
    if m.get("model") == target:
        sys.exit(0)
sys.exit(1)
' "$MODEL_NAME"
}

stage_b2_register_model() {
  say "Stage B.2 — provider/model register ($MODEL_NAME via $PROVIDER_ID)"

  if model_already_registered; then
    skip "model $MODEL_NAME already registered under $PROVIDER_ID"
    return 0
  fi

  if (( DRY_RUN )); then
    info "[DRY-RUN] will register model: $MODEL_NAME (endpoint=$MODEL_ENDPOINT, type=$MODEL_TYPE)"
    return 0
  fi

  # openai_api_compatible expects per-model credentials; minimum viable set:
  #   api_key (may be empty), endpoint_url, mode, context_size, max_tokens_to_sample
  # MiniMax-M2.7 served by vLLM is OpenAI-compatible chat with 128K ctx.
  local body
  # NOTE: openai_api_compatible v0.0.47 schema quirks:
  #   - agent_thought_support / stream_function_calling / structured_output_support
  #     accept 'not_supported' (NOT 'not_support')
  #   - vision_support accepts 'no_support' (NOT 'not_supported') — yes, the
  #     plugin uses different vocabulary for the same concept.
  #   - max_chunks is required even if not used.
  body=$(MNAME="$MODEL_NAME" MTYPE="$MODEL_TYPE" MENDPT="$MODEL_ENDPOINT" \
    python3 -c '
import json, os
print(json.dumps({
  "model":      os.environ["MNAME"],
  "model_type": os.environ["MTYPE"],
  "credentials": {
    "endpoint_url":              os.environ["MENDPT"],
    "api_key":                   "",
    "mode":                      "chat",
    "context_size":              "128000",
    "max_tokens_to_sample":      "8192",
    "max_chunks":                "1",
    "function_calling_type":     "no_call",
    "stream_function_calling":   "not_supported",
    "vision_support":            "no_support",
    "agent_thought_support":     "not_supported",
    "structured_output_support": "not_supported",
    "stream_mode_delimiter":     "\\n\\n"
  },
  "name": os.environ["MNAME"]
}))
')

  info "POST .../models/credentials"
  local resp
  resp=$(console_post \
    "/workspaces/current/model-providers/$PROVIDER_ID/models/credentials" \
    "$body")
  case "$resp" in
    *'"result":"success"'*) ok "model $MODEL_NAME credential created" ;;
    *)                      fail "model credential create failed: $resp" ;;
  esac

  model_already_registered || fail "model $MODEL_NAME absent from list after register"
}

# ---------------------------------------------------------------------------
# Stage C — verification: chat-messages with field-level assertion
# ---------------------------------------------------------------------------
verify_chat_messages() {
  say "Verify — /v1/chat-messages (field-level assertion)"

  if (( DRY_RUN )); then
    info "[DRY-RUN] will POST /v1/chat-messages and assert body.answer non-empty"
    return 0
  fi

  local app_token
  app_token=$(env_get DIFY_APP_DEFAULT_TOKEN || true)
  [[ -n "$app_token" && "$app_token" != "CHANGE_ME" ]] \
    || fail "DIFY_APP_DEFAULT_TOKEN not set in $ENV_FILE"

  local resp http_code
  local tmpf
  tmpf=$(mktemp -t recover-dify.chat.XXXXXX.json)
  http_code=$(curl -sS -o "$tmpf" -w '%{http_code}' \
    -X POST "$DIFY_HOST_URL/v1/chat-messages" \
    -H "Authorization: Bearer $app_token" \
    -H 'Content-Type: application/json' \
    -d '{"inputs":{},"query":"ping","response_mode":"blocking","conversation_id":"","user":"recover-script"}' || true)
  resp=$(cat "$tmpf")
  rm -f "$tmpf"

  printf '\n--- chat-messages response (http=%s) ---\n%s\n--- end ---\n\n' "$http_code" "$resp"

  [[ "$http_code" == "200" ]] \
    || fail "chat-messages HTTP $http_code (expected 200) — provider/plugin recovery did not unblock end-to-end path"

  # Field-level: body must have non-empty `answer`. Just `HTTP 200` is not
  # sufficient (memory feedback_pre_existing_error_strict_validation —
  # NCMU dev-login `username→user_id` lived through 4 INDEP rounds with
  # only HTTP-200-style verification).
  local answer
  answer=$(printf '%s' "$resp" | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
except Exception as e:
    sys.exit("not JSON: " + str(e))
ans = d.get("answer", "")
if not isinstance(ans, str) or not ans.strip():
    sys.exit("answer field missing or empty")
print(ans)
')
  ok "chat-messages 200 + answer non-empty (len=${#answer})"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
  require_cmd docker
  require_cmd curl
  require_cmd python3
  require_cmd base64
  require_cmd awk
  [[ -d "$REPO_ROOT" ]]   || fail "REPO_ROOT not found: $REPO_ROOT"
  [[ -f "$ENV_FILE" ]]    || fail ".env not found: $ENV_FILE"

  if (( DRY_RUN )); then
    info "DRY-RUN mode — no mutating calls will be made"
  fi

  stage_a_infra_self_heal
  console_auth
  stage_b05_rsa_keypair
  stage_b1_install_plugins
  stage_b2_register_model
  precheck_detect_only_containers
  verify_chat_messages

  say "DONE — Dify recovery complete"
}

main "$@"
