#!/usr/bin/env bash
# export-openapi.sh — capture the live ncmu-backend OpenAPI schema and
# (optionally) start a Stoplight Prism mock server for the SPA team.
#
# N-7 修订: invoked AFTER batch 9 is fully PASS (TASK-25/26/27/28 all
# reviewed PASS). The output schema then contains all 9 Phase 1
# endpoints, not just /auth/dev-login. Running this script earlier
# would publish a half-baked contract the SPA could not finish a real
# integration against.
#
# Usage:
#   ./scripts/export-openapi.sh                  # write schema only
#   ./scripts/export-openapi.sh --serve-mock     # also start prism on :4010
#
# Output:
#   NCMU-Wiki/sources/phase1/openapi-phase1.json
#   (path is git-tracked; downstream SPA repo can curl it directly)

set -euo pipefail

# ---------------------------------------------------------------------
# Resolve paths.
#   This script lives at NCMU/ncmu-backend/scripts/export-openapi.sh.
#   We anchor everything off ncmu-backend/, climb to NCMU/, and then
#   to the NCMU-Wiki sibling.
# ---------------------------------------------------------------------
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
NCMU_BACKEND_DIR=$(cd "$SCRIPT_DIR/.." && pwd)
NCMU_REPO=$(cd "$NCMU_BACKEND_DIR/.." && pwd)
NCMU_WIKI=$(cd "$NCMU_REPO/../NCMU-Wiki" && pwd)

OUT_DIR="$NCMU_WIKI/sources/phase1"
OUT_FILE="$OUT_DIR/openapi-phase1.json"

mkdir -p "$OUT_DIR"

# ---------------------------------------------------------------------
# 1. Make sure the ncmu-backend container is up + healthy.
# ---------------------------------------------------------------------
echo "[export-openapi] Checking docker-compose stack ..."
cd "$NCMU_REPO"

if ! docker compose --profile dev ps ncmu-backend --format '{{.Status}}' \
        | grep -q '(healthy)'; then
    echo "[export-openapi] ncmu-backend not healthy — bringing up dev stack."
    docker compose --profile dev up -d --wait ncmu-backend
fi

# ---------------------------------------------------------------------
# 2. Curl /openapi.json from inside the cluster, write to the wiki.
#    We go through ncmu-nginx so the output reflects the routes the
#    SPA actually calls (and any nginx-level rewrites).
# ---------------------------------------------------------------------
echo "[export-openapi] Capturing OpenAPI schema..."
TMP=$(mktemp)
docker compose --profile dev exec -T ncmu-backend \
    python -c "import http.client,sys;
c=http.client.HTTPConnection('localhost',8000,timeout=5);
c.request('GET','/openapi.json');
r=c.getresponse();
sys.stdout.buffer.write(r.read())" > "$TMP"

# Pretty-print so the diff between batches is human-reviewable.
if command -v python3 >/dev/null 2>&1; then
    python3 -c "import json,sys;d=json.load(open('$TMP'));json.dump(d,open('$OUT_FILE','w'),indent=2,ensure_ascii=False,sort_keys=True);print('[export-openapi] wrote',len(json.dumps(d)),'bytes to','$OUT_FILE')"
else
    cp "$TMP" "$OUT_FILE"
    echo "[export-openapi] wrote raw schema to $OUT_FILE"
fi
rm -f "$TMP"

# ---------------------------------------------------------------------
# 3. Optional: start a Prism mock server.
# ---------------------------------------------------------------------
if [ "${1:-}" = "--serve-mock" ]; then
    echo "[export-openapi] Starting Stoplight Prism mock on :4010 ..."
    echo "[export-openapi]   (Ctrl-C to stop; SPA team points VITE_API_BASE_URL at it.)"
    # First run downloads ~80 MB; precache with `npm i -g @stoplight/prism-cli`
    # in air-gapped environments (S-IND-5 修订).
    exec npx -y @stoplight/prism-cli mock "$OUT_FILE" --host 0.0.0.0 --port 4010
fi

echo "[export-openapi] Done."
