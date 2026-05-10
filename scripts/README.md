# NCMU Infra Scripts

First-run and ops helpers for the NCMU v3.3.1 infrastructure repo.

## ncmu_init.py

Seeds the three system tags (`customer` / `staff` / `admin`) into
`pg-ncmu.tags`. Idempotent — re-runs are no-ops via `ON CONFLICT DO NOTHING`.

### When to run

1. After `ncmu-backend` applies its database migrations (Phase 1) — that
   migration is authoritative for the `tags` table schema.
2. On first deploy of a new environment, once `pg-ncmu` is up.

If the `tags` table does not yet exist (e.g., migration hasn't run), the
script prints `[SKIP]` and exits 0 so it can be wired into startup ordering
without failing the pipeline.

### Usage

```bash
# From the NCMU infra repo root:
python3 -m venv .venv
source .venv/bin/activate
pip install -r scripts/requirements.txt

export NCMU_DB_URL="postgresql://ncmu_app:<password>@<host>:5432/ncmu"
python3 scripts/ncmu_init.py
```

Expected output:

- `[OK] initialized 3 system tags` — success (or idempotent re-run).
- `[SKIP] tags table not found; run ncmu-backend migration first` — schema
  not ready yet; run `ncmu-backend` migrations first.
- `[ERROR] NCMU_DB_URL env var not set.` — exits 1; set the env var.

### Requirements

- Python 3.11+
- `psycopg2-binary` (pinned in `requirements.txt`)
- Network access to `pg-ncmu`

## start-dev.sh / start-prod.sh / stop.sh

One-shot orchestration helpers around `docker compose`. They keep the
boot order and health-wait logic out of human muscle memory.

### Quick start (dev)

```bash
scripts/start-dev.sh
```

On first run the script copies `.env.example` to `.env`, prints a
warning telling you to fill in real passwords / API keys, and exits.
Edit `.env` (every `CHANGE_ME` placeholder needs a real value) and
re-run.

When the stack is up the script prints the admin-console URLs:

```
[ OK ] Stack is up. Admin consoles:
       Dify         : http://localhost:8080/
       FastGPT      : http://localhost:3000/
       NCMU gateway : http://localhost/        # only when ncmu-nginx is deployed
```

### Prod variant

```bash
scripts/start-prod.sh
```

Adds two extra checks compared to the dev script:

- If `NCMU_NGINX_CERT_PATH` / `NCMU_NGINX_KEY_PATH` are set in `.env`,
  both files must exist on disk.
- Front-door URL is read from `FRONTEND_URL` (defaults to
  `https://ncmu.example.com`).

### Stopping the stack

```bash
scripts/stop.sh                # stop containers, keep volumes
scripts/stop.sh --volumes      # also wipe named volumes (interactive confirm)
scripts/stop.sh --help         # short usage
```

`stop.sh` is idempotent — running it against an already-empty stack
exits 0 with `[INFO] No NCMU containers running.`

### Windows / WSL2 reminder

When the repo lives on `/mnt/<drive>/...` (NTFS), `start-dev.sh`
prints a warning if `docker-compose.override.yaml` is missing. The
override switches the postgres data directories from bind mounts to
named volumes — without it the postgres containers fail with
`could not change permissions` at `initdb`. See
`NCMU-Wiki/sources/phase0/prerequisites-2026-04-21.md` §2 for the
underlying reproduction.

### Implementation notes

- `scripts/common.sh` holds shared helpers (logging, docker pre-flight,
  `.env` bootstrap, `compose_up_wait`). It is sourced — not executed —
  by the three top-level scripts.
- `compose_up_wait` uses `docker compose --profile X up -d --wait`,
  which Compose v2 (>=2.20) implements as "block until every healthcheck
  reports healthy, or one fails." Default timeout is 900s (15 min) to
  cover the first-boot TEI model download (~1.3 GiB).
- All scripts use `set -euo pipefail`; exit code is `1` on any failure
  with the relevant logs printed to stderr.

## recover-dify-provider.sh — down -v 后恢复 Dify provider (B-NEW-51)

`docker compose down -v` and Docker Desktop / `wsl --shutdown` cycles
leave the NCMU stack in a broken state several layers deep:

1. `ncmu-dify-nginx` / `ncmu-nginx` / `ncmu-dify-ssrf-proxy` exit 127
   because their host bind-mounts go stale.
2. `ncmu-kb-adapter` exits 137 (verified `OOMKilled=false` — it is a
   normal SIGTERM shutdown, not OOM; root cause is a missing
   `restart: unless-stopped` policy and the fact that kb-adapter is
   started outside of `docker-compose.yml`).
3. The Dify console JWT in `.env` (`DIFY_CONSOLE_API_KEY`) expires.
4. The admin password may not match `part_a.sh`'s default any more.
5. The tenant's RSA private key (used to encrypt LLM credentials) is
   wiped along with the storage volume → `PrivkeyNotFoundError` on any
   model-providers GET/POST.
6. `provider_models` / `providers` rows are gone → Dify reports
   `Provider langgenius/openai_api_compatible/openai_api_compatible
   does not exist` for every chat-messages call.

`recover-dify-provider.sh` is the idempotent end-to-end fix for this
exact failure mode.

### Triggers (run when you see any of)

- `chat-messages` returns `{"code":"invalid_param","message":"Provider
  langgenius/openai_api_compatible/openai_api_compatible does not
  exist."}` after a Docker Desktop or WSL2 restart.
- `docker ps -a` shows any of `ncmu-dify-nginx`, `ncmu-nginx`,
  `ncmu-dify-ssrf-proxy`, `ncmu-kb-adapter` in `Exited` state.
- Dify console returns 500 with `PrivkeyNotFoundError` in the
  `ncmu-dify-api` container logs.
- You just ran `docker compose --profile dev down -v` in dev.

### Usage

```bash
# Preview every action without mutating state:
bash scripts/recover-dify-provider.sh --dry-run

# Apply fixes:
bash scripts/recover-dify-provider.sh
```

The script is fully idempotent — re-running on a healthy stack is a
no-op that still verifies `chat-messages` end-to-end.

### What it does (in order)

| # | Stage | Action |
|---|-------|--------|
| A | Infra container self-heal | Recreate any of `ncmu-dify-nginx`, `ncmu-nginx`, `ncmu-dify-ssrf-proxy`, `ncmu-kb-adapter` that are not running (uses `docker compose up --force-recreate` for compose-managed services and `docker start` for kb-adapter, which has no compose label). |
| B.0 | Console auth | Three-tier fallback: `.env DIFY_CONSOLE_API_KEY` → admin login → fail with `flask reset-password` hint. |
| B.0.5 | RSA keypair | If model-providers GET returns 500 with `PrivkeyNotFoundError`, run `flask reset-encrypt-key-pair --yes` inside `ncmu-dify-api`. |
| B.1 | Plugin install | Install `langgenius/openai_api_compatible` and `langgenius/minimax` from `marketplace.dify.ai` if missing. Skips if already present. |
| B.2 | Model register | Register `MiniMax-M2.7` (chat, 128K ctx, endpoint `http://111.172.214.40:32086/v1`) under the openai_api_compatible provider. Skips if already registered. |
| Verify | chat-messages | POST `/v1/chat-messages` with `DIFY_APP_DEFAULT_TOKEN` and assert HTTP 200 **and** non-empty `answer` field (field-level — HTTP 200 alone is not sufficient). |

### Prerequisites

- Containers `ncmu-dify-api`, `ncmu-dify-plugin-daemon`,
  `ncmu-dify-web`, `ncmu-pg-dify`, `ncmu-redis` (and the FastGPT
  stack) are already up. Stage A only heals the four containers
  listed above; broader recovery is out of scope.
- `.env` contains a valid `DIFY_APP_DEFAULT_TOKEN` (the per-app
  bearer token; survives `down -v` because it lives in the
  `apps`/`api_tokens` tables, not the wiped storage volume).
- Outbound internet access to `https://marketplace.dify.ai` from
  both the host and the `ncmu-dify-ssrf-proxy` container.

### Default credentials and `flask reset-password`

If the admin password has been rotated away from the dev default,
Tier 2 login fails. Restore it before re-running:

```bash
docker exec -w /app/api ncmu-dify-api \
  /app/api/.venv/bin/flask reset-password \
  --email admin@ncmu.local \
  --new-password 'Ncmu@E2E2026' \
  --password-confirm 'Ncmu@E2E2026'
```

Or override the credentials the script uses without changing them:

```bash
DIFY_ADMIN_EMAIL='admin@ncmu.local' \
DIFY_ADMIN_PASSWORD='<real-password>' \
  bash scripts/recover-dify-provider.sh
```

### ⚠️ Production deployment must override `DIFY_ADMIN_PASSWORD`

The dev default `Ncmu@E2E2026` is hard-coded into this script as a
fallback ONLY for local recovery convenience. In production:

- Inject `DIFY_ADMIN_PASSWORD` (and `DIFY_ADMIN_EMAIL` if the admin
  account differs) via `.env.prod` / `docker compose --env-file` /
  the orchestrator's secret manager — never via this script's
  hard-coded fallback.
- The script reads `DIFY_ADMIN_PASSWORD` from the environment first
  and only falls back to the hard-coded default if unset.

### ⚠️ Production: avoid `down -v` against a tenant with credentials

`docker compose down -v` wipes the storage volume that holds the
tenant's RSA private key. After `down -v`:

- All previously-saved LLM credentials become unrecoverable
  ciphertext.
- Stage B.0.5 detects this and runs `reset-encrypt-key-pair --yes`,
  which generates a fresh keypair. This is **safe in dev** because
  no surviving ciphertext exists after the wipe.
- In **production**, back up the storage volume separately (or run
  `docker compose down` without `-v` and use a dedicated cleanup
  step) so credentials can be recovered. Running this script's
  Stage B.0.5 against a production tenant with surviving
  ciphertext would render those credentials permanently unreadable.

### Expected output (healthy run)

```
===== Stage A — infra container self-heal =====
[INFO] ncmu-dify-nginx: running
[INFO] ncmu-nginx: running
[INFO] ncmu-dify-ssrf-proxy: running
[INFO] ncmu-kb-adapter: running
[ OK ] all infra containers already running, skip

===== Stage B.0 — Dify console auth =====
[ OK ] Tier 2: admin login succeeded (admin@ncmu.local)

===== Stage B.0.5 — RSA keypair detect-and-reset =====
[ OK ] RSA keypair present (model-providers GET healthy), skip

===== Stage B.1 — plugin install =====
[SKIP] plugin already installed: langgenius/openai_api_compatible
[SKIP] plugin already installed: langgenius/minimax
[ OK ] all required plugins already installed

===== Stage B.2 — provider/model register ... =====
[SKIP] model MiniMax-M2.7 already registered ...

===== Verify — /v1/chat-messages (field-level assertion) =====
[ OK ] chat-messages 200 + answer non-empty (len=...)

===== DONE — Dify recovery complete =====
```
