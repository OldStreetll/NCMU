FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Phase 1 TASK-25: full backend stack. Dependencies are sourced from
# pyproject.toml (single source of truth — alembic + asyncpg + fastapi
# share a few sub-deps that pip resolves together) instead of a
# duplicated `pip install` list here.
#
# TASK-FU-TEST-IMG: install the `[dev]` extra (pytest / pytest-asyncio /
# pytest-postgresql / respx) so the image is test-ready out of the box —
# no ad-hoc `pip install pytest` after every rebuild. The tests/ tree is
# COPY'd in below so `docker exec ncmu-backend pytest tests/...` works with
# zero manual setup. A single-stage bake is intentional: there is no
# separate prod build pipeline yet (dev + prod share image ncmu-backend
# :phase1), so the test deps ride along until prod hardening (backlog
# FU-NGINX-prod) splits a clean prod stage.
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir .[dev]

COPY alembic.ini ./
COPY alembic/ ./alembic/
COPY scripts/ ./scripts/

# tests/ COPY'd after pip install so editing tests doesn't bust the
# dependency layer cache. Provides the baseline suite in-image; the dev
# workflow still `docker cp`s a worktree's changed test files over this.
COPY tests/ ./tests/

RUN chmod +x scripts/entrypoint.sh

ENTRYPOINT ["./scripts/entrypoint.sh"]
# entrypoint.sh runs `alembic upgrade head` (single authoritative caller
# per D-2) then exec's the CMD. Default CMD is uvicorn; docker-compose
# can override for one-off shells / pytest invocation.
CMD ["uvicorn", "ncmu_backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
