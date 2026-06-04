#!/bin/sh
# =====================================================================
# ncmu-nginx profile selector
# ---------------------------------------------------------------------
# Placed under /docker-entrypoint.d/ so the nginx:latest image picks it
# up before its own 20-envsubst-on-templates.sh runs. Copies the
# ${DEPLOY_PROFILE}.conf.template from the Pane-2-vendored sources
# directory into the standard templates dir, then lets the stock
# entrypoint envsubst it into /etc/nginx/conf.d/default.conf.
# =====================================================================
set -eu

# Default = dogfood (baked-SPA HTTP build, TASK-NGX-1). dev/prod are
# opt-in via DEPLOY_PROFILE=dev (vite reverse-proxy) / =prod (TLS).
PROFILE="${DEPLOY_PROFILE:-dogfood}"
SRC_DIR="/etc/nginx/templates-source"
DST_DIR="/etc/nginx/templates"
SRC="${SRC_DIR}/${PROFILE}.conf.template"
DST="${DST_DIR}/default.conf.template"

mkdir -p "${DST_DIR}"

if [ ! -f "${SRC}" ]; then
    echo "[ncmu-nginx] ERROR: template for profile '${PROFILE}' not found at ${SRC}" >&2
    echo "[ncmu-nginx] Available profiles in ${SRC_DIR}:" >&2
    ls -1 "${SRC_DIR}" >&2 || true
    exit 1
fi

echo "[ncmu-nginx] DEPLOY_PROFILE=${PROFILE} -> rendering ${SRC}"
cp "${SRC}" "${DST}"
