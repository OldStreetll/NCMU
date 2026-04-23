#!/usr/bin/env bash
# stop.sh — gracefully stop the NCMU stack.
#
# Usage:
#   scripts/stop.sh             # stop containers, keep volumes (data safe)
#   scripts/stop.sh --volumes   # stop containers AND wipe named volumes
#   scripts/stop.sh -v          # short form for --volumes
#
# Idempotent: safe to run when no containers are up (compose down on an
# empty stack exits 0).

set -euo pipefail

. "$(dirname "$0")/common.sh"

resolve_repo_root
cd "$REPO_ROOT"

WIPE_VOLUMES=false
case "${1:-}" in
  ""|--keep-volumes) WIPE_VOLUMES=false ;;
  -v|--volumes)      WIPE_VOLUMES=true ;;
  -h|--help)
    sed -n '2,12p' "$0"
    exit 0 ;;
  *) fail "unknown argument: $1 (try --help)" ;;
esac

check_docker

if $WIPE_VOLUMES; then
  warn "About to delete ALL named volumes (postgres data, mongo data,"
  warn "TEI model cache, etc.). This is irreversible."
  printf '       Type "wipe" to confirm: '
  read -r confirmation
  if [ "$confirmation" != "wipe" ]; then
    info "Aborted; no changes made."
    exit 0
  fi
  info "docker compose down --volumes --remove-orphans"
  docker compose down --volumes --remove-orphans
else
  info "docker compose down --remove-orphans (volumes preserved)"
  docker compose down --remove-orphans
fi

ok "Stack stopped."
remaining=$(docker compose ps -q | wc -l)
if [ "$remaining" -gt 0 ]; then
  warn "$remaining container(s) still listed by 'docker compose ps':"
  compose_status
else
  info "No NCMU containers running."
fi
