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
