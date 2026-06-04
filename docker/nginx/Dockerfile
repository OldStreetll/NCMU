# =====================================================================
# ncmu-nginx : multi-stage dogfood/prod image (TASK-NGX-1)
# ---------------------------------------------------------------------
# stage1 builds the ncmu-spa SPA (`tsc --noEmit && vite build`) into
# /app/dist. stage2 is the same nginx:latest base the dev profile uses,
# with the built SPA baked into /usr/share/nginx/html and the profile
# selector + templates + proxy snippet COPY'd in. The image is
# self-contained (no host bind-mounts) — that also sidesteps the WSL2
# bind-mount remount bug we hit on recreate.
#
# Build context is the repo root (compose `context: .`) so stage1 can
# reach ncmu-spa/. See .dockerignore for what the context excludes.
# Profile selection is unchanged: 00-ncmu-profile-select.sh copies
# ${DEPLOY_PROFILE}.conf.template -> templates/default.conf.template,
# then nginx's stock 20-envsubst-on-templates.sh renders it.
# =====================================================================

# ---- stage1: build the SPA ------------------------------------------
FROM node:20-alpine AS spa-build
WORKDIR /app
# Manifests first so the npm-ci layer only re-runs when deps change,
# not on every source edit.
COPY ncmu-spa/package.json ncmu-spa/package-lock.json ./
RUN npm ci
COPY ncmu-spa/ ./
RUN npm run build

# ---- stage2: nginx serving the baked SPA ----------------------------
FROM nginx:latest
# Baked SPA static assets (served by dogfood.conf.template's `location /`).
COPY --from=spa-build /app/dist /usr/share/nginx/html
# Profile templates (dev/prod/dogfood), proxy snippet, and profile
# selector — baked in so the image is self-contained.
COPY docker/nginx/dev.conf.template \
     docker/nginx/prod.conf.template \
     docker/nginx/dogfood.conf.template \
     /etc/nginx/templates-source/
COPY docker/nginx/proxy_common.conf /etc/nginx/snippets/proxy_common.conf
COPY docker/nginx/docker-entrypoint.sh /docker-entrypoint.d/00-ncmu-profile-select.sh
RUN chmod +x /docker-entrypoint.d/00-ncmu-profile-select.sh
