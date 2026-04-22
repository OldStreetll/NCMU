#!/usr/bin/env bash
# start-prod.sh — bring up the NCMU stack in prod profile.
#
# Usage:
#   scripts/start-prod.sh
#
# Behaviour:
#   1. Verify docker + compose are usable.
#   2. Bootstrap .env from .env.example if missing (then exit; the user
#      must edit .env before re-running).
#   3. Verify TLS material exists when NCMU_NGINX_CERT_PATH /
#      NCMU_NGINX_KEY_PATH are set in .env.
#   4. `docker compose --profile prod up -d --wait`.
#   5. Print front-door URL (HTTPS).
#
# Exit code: 0 on success, 1 on any failure.

set -euo pipefail

. "$(dirname "$0")/common.sh"

resolve_repo_root
cd "$REPO_ROOT"

check_docker
ensure_env_file

# TLS material check. These vars are added by TASK-09's nginx work; they
# may not exist yet in older .env files, which is fine for Phase 0 dev
# rehearsals — we only enforce when the user has wired them up.
cert_path=$(env_get NCMU_NGINX_CERT_PATH "")
key_path=$(env_get NCMU_NGINX_KEY_PATH "")
if [ -n "$cert_path" ] || [ -n "$key_path" ]; then
  [ -n "$cert_path" ] || fail "NCMU_NGINX_CERT_PATH is empty but NCMU_NGINX_KEY_PATH is set."
  [ -n "$key_path" ]  || fail "NCMU_NGINX_KEY_PATH is empty but NCMU_NGINX_CERT_PATH is set."
  [ -f "$cert_path" ] || fail "TLS cert file not found: $cert_path"
  [ -f "$key_path"  ] || fail "TLS key  file not found: $key_path"
  ok "TLS material verified ($cert_path + $key_path)."
else
  warn "NCMU_NGINX_CERT_PATH / NCMU_NGINX_KEY_PATH not set in .env."
  warn "Prod profile will boot but the front-door nginx may serve plaintext only."
fi

info "Starting NCMU stack — prod profile."
compose_up_wait prod 900

ok "All services healthy."
compose_status
print_prod_urls
