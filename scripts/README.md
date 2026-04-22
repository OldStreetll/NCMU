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
