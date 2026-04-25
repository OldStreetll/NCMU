# ncmu-backend

Phase 1 skeleton. TASK-22 establishes Alembic + pg-ncmu schema (4 tables,
CHECK constraints, partial indexes, dev-seed users). FastAPI + endpoints land
in TASK-25+.

## Migrations

`alembic upgrade head` is the **single authoritative migration entry point**.
The ncmu-backend container `scripts/entrypoint.sh` runs it on startup
(idempotent). No other script calls `alembic`.

## Tests (Phase 1 schema layer)

Tests use `pytest-postgresql` with `postgresql_noproc` fixture against the
live pg-ncmu cluster (from Phase 0 compose). Each test gets a fresh
ephemeral database.

```bash
# host venv:
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"

# assumes pg-ncmu is running on localhost:5432 (docker compose up -d pg-ncmu)
pytest -v
```

Override connection via `PYTEST_PG_HOST` / `PYTEST_PG_PORT` /
`PYTEST_PG_USER` / `PYTEST_PG_PASSWORD` env if needed.
