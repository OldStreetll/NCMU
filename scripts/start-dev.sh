#!/usr/bin/env bash
# start-dev.sh — bring up the NCMU stack in dev profile.
#
# Usage:
#   scripts/start-dev.sh
#
# Behaviour:
#   1. Verify docker + compose are usable.
#   2. Bootstrap .env from .env.example if missing (then exit; the user
#      must edit .env before re-running).
#   3. fernet_self_check — ensure DIFY_SECRET_KEY in .env is a valid
#      base64 Fernet key; if it is still the CHANGE_ME placeholder,
#      generate one in place. Other CHANGE_ME secrets are left for the
#      user to fill (they fail loudly at first call instead of silently).
#   4. On Windows/WSL2 NTFS hosts, remind about docker-compose.override.yaml.
#   5. `docker compose --profile dev up -d --wait` — Compose v2 waits for
#      every healthcheck'd container to report healthy.
#      Note (D-2): alembic upgrade is NOT invoked here. The single
#      authoritative entrypoint for ncmu-backend migrations is its
#      container's entrypoint.sh, which runs `alembic upgrade head`
#      automatically on container start (TASK-22).
#   6. bootstrap_invoke — run scripts/ncmu_init.py --bootstrap-fastgpt
#      after the FastGPT app is healthy. Idempotent: seeds the FastGPT
#      mongo team_subscriptions collection on first run and re-asserts
#      default model activations (bge-m3 + MiniMax-M2.7) on every run.
#   7. Print admin-console URLs.
#
# Exit code: 0 on success, 1 on any failure (logs printed to stderr).

set -euo pipefail

. "$(dirname "$0")/common.sh"

resolve_repo_root
cd "$REPO_ROOT"

# fernet_self_check — replace placeholder DIFY_SECRET_KEY with a real
# Fernet key in .env. Dify uses this key to decrypt at-rest secrets;
# any non-Fernet value triggers "Invalid encrypted data" at first
# console login (TASK-14 E2E §4.1). Detection logic:
#   - empty value           -> regenerate
#   - value equals CHANGE_ME -> regenerate
#   - length != 44 chars     -> regenerate (a Fernet key is exactly 44
#                                base64-url chars: 32 bytes -> 44)
# Other Dify CHANGE_ME secrets (sandbox / plugin / inner-api keys) and
# all NCMU_/FASTGPT_/DINGTALK_ CHANGE_ME values are intentionally left
# for the user to fill — auto-generating them would mask configuration
# mistakes during early dev. NCMU_JWT_SECRET in particular is NOT
# checked here because batch 8 runs before ncmu-backend exists.
fernet_self_check() {
    [ -f "$REPO_ROOT/.env" ] || return 0
    local val
    val=$(grep -E '^DIFY_SECRET_KEY=' "$REPO_ROOT/.env" | tail -n1 | cut -d= -f2-)
    local needs_regen=0
    if [ "$val" = "CHANGE_ME" ] || [ -z "$val" ] || [ ${#val} -ne 44 ]; then
        needs_regen=1
    else
        # Final structural check: try to construct Fernet(val) — catches the
        # rare case where the value is 44 chars long but contains characters
        # outside the base64url alphabet (e.g. operator typed in an
        # openssl rand value of the right length but wrong charset).
        if ! python3 -c "
import sys
from cryptography.fernet import Fernet
try:
    Fernet('$val'.encode())
except Exception:
    sys.exit(1)
" >/dev/null 2>&1; then
            warn "DIFY_SECRET_KEY in .env is 44 chars but not a valid Fernet key; regenerating."
            needs_regen=1
        fi
    fi
    if [ "$needs_regen" -eq 1 ]; then
        info "Generating Fernet key for DIFY_SECRET_KEY..."
        local new_key
        new_key=$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())') \
            || fail "python3 cryptography missing — install with: pip install cryptography"
        # Use a delimiter that cannot appear in a base64url Fernet key (|).
        sed -i "s|^DIFY_SECRET_KEY=.*|DIFY_SECRET_KEY=$new_key|" "$REPO_ROOT/.env"
        ok "DIFY_SECRET_KEY set in .env (Fernet, 44 chars)."
    fi
}

# bootstrap_invoke — wait for fastgpt-app health then run the python
# bootstrap. The compose `up -d --wait` we did above already blocks on
# *some* fastgpt healthchecks, but the app container's /api/health
# endpoint returns 404 until model registry is loaded; we poll the
# socket directly for ~2 minutes and then run the bootstrap regardless.
# Failures inside the python script log [WARN] but do not abort the
# whole boot — model activation is best-effort and idempotent.
bootstrap_invoke() {
    info "Waiting up to 120s for fastgpt-app TCP port to accept connections..."
    local i=0
    while [ $i -lt 60 ]; do
        if docker compose exec -T fastgpt sh -c \
                'curl -sf -o /dev/null -m 2 http://localhost:3000/ || nc -z localhost 3000' \
                >/dev/null 2>&1; then
            break
        fi
        i=$((i+1)); sleep 2
    done
    info "Running ncmu_init.py --bootstrap-fastgpt inside ncmu-init service..."
    if ! docker compose run --rm ncmu-init python3 /app/scripts/ncmu_init.py --bootstrap-fastgpt; then
        warn "bootstrap_fastgpt returned non-zero — check logs above. Continuing."
    fi
}

check_docker
ensure_env_file
fernet_self_check
warn_if_ntfs_without_override

info "Starting NCMU stack — dev profile."
compose_up_wait dev 900

bootstrap_invoke

ok "All services healthy."
compose_status
print_dev_urls
