FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Phase 1 TASK-25: full backend stack. Dependencies are sourced from
# pyproject.toml (single source of truth — alembic + asyncpg + fastapi
# share a few sub-deps that pip resolves together) instead of a
# duplicated `pip install` list here.
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir .

COPY alembic.ini ./
COPY alembic/ ./alembic/
COPY scripts/ ./scripts/

RUN chmod +x scripts/entrypoint.sh

ENTRYPOINT ["./scripts/entrypoint.sh"]
# entrypoint.sh runs `alembic upgrade head` (single authoritative caller
# per D-2) then exec's the CMD. Default CMD is uvicorn; docker-compose
# can override for one-off shells / pytest invocation.
CMD ["uvicorn", "ncmu_backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
