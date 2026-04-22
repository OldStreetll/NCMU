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
#   3. On Windows/WSL2 NTFS hosts, remind about docker-compose.override.yaml.
#   4. `docker compose --profile dev up -d --wait` — Compose v2 waits for
#      every healthcheck'd container to report healthy.
#   5. Print admin-console URLs.
#
# Exit code: 0 on success, 1 on any failure (logs printed to stderr).

set -euo pipefail

. "$(dirname "$0")/common.sh"

resolve_repo_root
cd "$REPO_ROOT"

check_docker
ensure_env_file
warn_if_ntfs_without_override

info "Starting NCMU stack — dev profile."
compose_up_wait dev 900

ok "All services healthy."
compose_status
print_dev_urls
