# ncmu-backend

Phase 1 NCMU backend — FastAPI + async SQLAlchemy on top of pg-ncmu.

- **TASK-22** built Alembic + schema (users / chat_sessions /
  dify_external_kb_configs / dify_app_kb_bindings) + CHECK constraints
  + partial indexes + dev-seed users.
- **TASK-25** added the FastAPI app, JWT auth, dev-login, router
  auto-discovery, async session, and the docker-compose service.
- **TASK-26 / 27 / 28** add `apps`, `chat_sessions`, `admin` modules
  by dropping new `routes.py` files into `src/ncmu_backend/<mod>/` —
  no main.py edit required.

## Migrations

`alembic upgrade head` is the **single authoritative migration entry
point**. The container's `scripts/entrypoint.sh` runs it on startup
(idempotent), then in dev profile loads `alembic/seeds/dev_users.sql`
(idempotent via `ON CONFLICT DO NOTHING`), then `exec`s the CMD
(uvicorn). No other script calls `alembic`.

## Running the stack

```bash
cd NCMU
docker compose --profile dev up -d ncmu-backend
curl http://localhost/api/v1/ncmu/dev/users        # via ncmu-nginx
curl http://localhost:8000/healthz                  # direct (host-bound port)
```

## Tests

Two fixture stacks live in `tests/conftest.py`:

| Fixture | Driver | Purpose |
|---|---|---|
| `db_session` | sync psycopg | Schema-layer (CHECK / partial-idx / alembic) — TASK-22 |
| `async_db` + `app_client` | asyncpg + httpx ASGI | Backend business tests (TASK-25 / 26 / 27 / 28) |

Both share the `postgresql_noproc` factory so every test gets a fresh
ephemeral DB. `pytest-asyncio` is in `asyncio_mode = "auto"` — `async
def test_x(...)` works without an explicit decorator.

```bash
# host venv (pg-ncmu must be reachable on localhost:5432):
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest -v
```

Override the connection via `PYTEST_PG_HOST` / `PYTEST_PG_PORT` /
`PYTEST_PG_USER` / `PYTEST_PG_PASSWORD` env vars if needed.

Inside a built container:

```bash
docker compose --profile dev run --rm --entrypoint "" ncmu-backend \
    sh -c "pip install -e '.[dev]' && pytest -v"
```

## OpenAPI export + SPA mock server (TASK-25 AC#11 / N-7)

The `scripts/export-openapi.sh` helper captures the live
`/openapi.json` and writes it to
`NCMU-Wiki/sources/phase1/openapi-phase1.json`. Optionally it then
starts a [Stoplight Prism](https://stoplight.io/open-source/prism)
mock server so the SPA team can develop against a stable contract
without waiting for the backend.

**N-7 修订 — when to run it.**

> `export-openapi.sh` is invoked AFTER batch 9 is fully PASS (TASK-25
> + 26 + 27 + 28 all reviewed PASS). The output schema then contains
> all 9 Phase 1 endpoints. Running it earlier publishes a half-baked
> contract the SPA cannot finish a real integration against.

```bash
# After batch 9 PASS:
cd NCMU
./ncmu-backend/scripts/export-openapi.sh                # write schema only
./ncmu-backend/scripts/export-openapi.sh --serve-mock   # also run prism on :4010
```

In air-gapped environments precache the prism CLI:
```bash
npm install -g @stoplight/prism-cli
```
(S-IND-5 修订: first `npx` invocation downloads ~80 MB.)

## Adding a new endpoint module (TASK-26 / 27 / 28 pattern)

1. Create `src/ncmu_backend/<mod>/__init__.py` + `routes.py`.
2. In `routes.py` export `router = APIRouter(tags=["<mod>"])` and add
   handlers with the full path: `@router.get("/api/v1/ncmu/<mod>/...")`.
3. Optionally add `src/ncmu_backend/schemas/<mod>.py` for the
   pydantic request/response models.
4. Restart `ncmu-backend` (or `uvicorn --reload`) — the auto-discovery
   in `main._discover_and_include_routers` picks the module up; no
   change to `main.py` is needed.
