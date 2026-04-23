#!/usr/bin/env bash
# common.sh — shared helpers for start-dev / start-prod / stop scripts.
# Source this file from another bash script: . "$(dirname "$0")/common.sh"
#
# Provides:
#   info / warn / err / ok / fail   — colourised logging
#   resolve_repo_root               — sets $REPO_ROOT to the NCMU/ dir
#   check_docker                    — verifies docker + compose are usable
#   ensure_env_file                 — copies .env.example -> .env if missing
#   warn_if_ntfs_without_override   — Windows/WSL2 NTFS prereq reminder
#   compose_service_exists NAME     — returns 0/1 by docker compose config
#   compose_up_wait PROFILE         — `up -d --wait` with timeout
#   compose_status                  — short ps table
#   print_dev_urls / print_prod_urls — admin console URL hints
#
# This file is intentionally NOT executable on its own.

# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  C_RESET=$'\033[0m'
  C_INFO=$'\033[36m'   # cyan
  C_OK=$'\033[32m'     # green
  C_WARN=$'\033[33m'   # yellow
  C_ERR=$'\033[31m'    # red
else
  C_RESET=""; C_INFO=""; C_OK=""; C_WARN=""; C_ERR=""
fi

info() { printf '%s[INFO]%s %s\n' "$C_INFO" "$C_RESET" "$*"; }
ok()   { printf '%s[ OK ]%s %s\n' "$C_OK"   "$C_RESET" "$*"; }
warn() { printf '%s[WARN]%s %s\n' "$C_WARN" "$C_RESET" "$*" >&2; }
err()  { printf '%s[ERR ]%s %s\n' "$C_ERR"  "$C_RESET" "$*" >&2; }
fail() { err "$*"; exit 1; }

# ---------------------------------------------------------------------------
# Repo root resolution
#   scripts/ lives directly under NCMU/. We resolve REPO_ROOT to the NCMU
#   directory so callers can `cd "$REPO_ROOT"` before running compose.
# ---------------------------------------------------------------------------

resolve_repo_root() {
  local script_path
  if command -v readlink >/dev/null 2>&1 && readlink -f / >/dev/null 2>&1; then
    script_path=$(readlink -f "${BASH_SOURCE[1]:-$0}")
  else
    script_path="${BASH_SOURCE[1]:-$0}"
  fi
  REPO_ROOT=$(cd "$(dirname "$script_path")/.." && pwd)
  export REPO_ROOT
}

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------

check_docker() {
  command -v docker >/dev/null 2>&1 \
    || fail "docker not found in PATH. Install Docker Desktop or docker-ce."
  docker info >/dev/null 2>&1 \
    || fail "docker daemon not reachable. Start Docker Desktop / dockerd."
  docker compose version >/dev/null 2>&1 \
    || fail "docker compose plugin missing. Need Compose v2 (>=2.20)."
  [ -f "$REPO_ROOT/docker-compose.yml" ] \
    || fail "docker-compose.yml not found at $REPO_ROOT. Run from the NCMU repo."
}

ensure_env_file() {
  local env_path="$REPO_ROOT/.env"
  local example_path="$REPO_ROOT/.env.example"
  if [ -f "$env_path" ]; then
    return 0
  fi
  if [ ! -f "$example_path" ]; then
    fail ".env missing AND .env.example missing — broken repo layout."
  fi
  cp "$example_path" "$env_path"
  warn ".env was missing; copied from .env.example."
  warn "Edit $env_path now and replace every CHANGE_ME with a real value"
  warn "  (DB passwords, API keys, JWT/Fernet secrets) before continuing."
  warn "Aborting boot. Re-run this script after editing .env."
  exit 1
}

# Detect Windows/WSL2 + NTFS repo path; remind about the named-volume override.
warn_if_ntfs_without_override() {
  local override_path="$REPO_ROOT/docker-compose.override.yaml"
  local override_example="$REPO_ROOT/docker-compose.override.yaml.example"
  if [ -f "$override_path" ]; then
    return 0
  fi
  case "$REPO_ROOT" in
    /mnt/[a-z]/*)
      warn "NCMU repo lives on a Windows-mounted path ($REPO_ROOT)."
      warn "Postgres bind mounts will fail with 'could not change permissions'."
      warn "Enable the named-volume override before booting:"
      warn "  cp $override_example $override_path"
      warn "See NCMU-Wiki/sources/phase0/prerequisites-2026-04-21.md §2."
      ;;
  esac
}

# ---------------------------------------------------------------------------
# Compose helpers
# ---------------------------------------------------------------------------

compose_service_exists() {
  local svc="$1"
  docker compose config --services 2>/dev/null | grep -qx "$svc"
}

# compose_up_wait PROFILE [TIMEOUT_SECONDS]
#   Boots all services in the given profile with `up -d --wait`. Compose v2
#   blocks until every container with a healthcheck reports healthy (or
#   one fails). Tail the most recent logs on failure.
compose_up_wait() {
  local profile="$1"
  local timeout="${2:-600}"
  info "docker compose --profile $profile up -d --wait (timeout ${timeout}s)"
  if ! timeout "$timeout" docker compose --profile "$profile" up -d --wait; then
    err "Stack failed to reach healthy state within ${timeout}s."
    err "Last 50 lines of unhealthy/exited containers:"
    docker compose ps --format "table {{.Service}}\t{{.Status}}" >&2 || true
    docker compose logs --tail=50 >&2 || true
    return 1
  fi
}

compose_status() {
  docker compose ps --format "table {{.Service}}\t{{.Status}}\t{{.Ports}}"
}

# ---------------------------------------------------------------------------
# URL hints
# ---------------------------------------------------------------------------

# Read a value from .env (returns the literal after =), defaulting to $2.
env_get() {
  local key="$1" default="$2"
  local val
  val=$(grep -E "^${key}=" "$REPO_ROOT/.env" 2>/dev/null | tail -n1 | cut -d= -f2-)
  printf '%s' "${val:-$default}"
}

print_dev_urls() {
  local dify_port fastgpt_port
  dify_port=$(env_get DIFY_PORT 8080)
  fastgpt_port=$(env_get FASTGPT_PORT 3000)
  printf '\n'
  ok  "Stack is up. Admin consoles:"
  printf '       Dify         : http://localhost:%s/  (first-run wizard at /install)\n' "$dify_port"
  printf '       FastGPT      : http://localhost:%s/  (root login per FASTGPT_DEFAULT_ROOT_PSW)\n' "$fastgpt_port"
  if compose_service_exists ncmu-nginx; then
    printf '       NCMU gateway : http://localhost/  (front of /api and SPA)\n'
  fi
  printf '\n'
}

print_prod_urls() {
  local frontend_url
  frontend_url=$(env_get FRONTEND_URL "https://ncmu.example.com")
  printf '\n'
  ok  "Stack is up (prod profile). Front-door:"
  printf '       NCMU gateway : %s\n' "$frontend_url"
  printf '       (Dify and FastGPT consoles are NOT exposed in prod;\n'
  printf '        SSH-tunnel into the host to reach them on internal ports.)\n'
  printf '\n'
}
