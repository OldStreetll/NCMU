FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Phase 1 TASK-22 minimal image: alembic + sqlalchemy + psycopg only.
# TASK-25 will extend with uvicorn / fastapi / httpx / pyjwt / etc.
RUN pip install --no-cache-dir \
    "alembic>=1.13,<2" \
    "sqlalchemy>=2.0,<2.1" \
    "psycopg[binary]>=3.1,<3.3"

COPY alembic.ini ./
COPY alembic/ ./alembic/
COPY scripts/ ./scripts/

RUN chmod +x scripts/entrypoint.sh

ENTRYPOINT ["./scripts/entrypoint.sh"]
# Phase 1 batch 8: no web app yet; TASK-25 overrides CMD to uvicorn.
CMD ["bash"]
