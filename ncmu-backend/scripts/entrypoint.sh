#!/usr/bin/env sh
set -e

# D-2 修订：本脚本是整个系统里**唯一**调用 `alembic` 的地方。
# start-dev.sh、ncmu_init.py、开发者手工命令都不应重复 upgrade（幂等，但重复徒增不确定性）。
#
# TASK-25 扩展：在 alembic upgrade 之后、exec CMD 之前，dev profile 自动
# 应用 alembic/seeds/dev_users.sql。AC#12 要求容器起来后立即可 curl
# /api/v1/ncmu/auth/dev-login — 不在这里加种子，操作员每次 down -v 后
# 都得手工跑一次 psql。seeds 用 ON CONFLICT DO NOTHING，幂等。

cd /app

# Wait for pg-ncmu ready (max ~60s)
i=0
while [ "$i" -lt 30 ]; do
    if alembic -c alembic.ini current > /dev/null 2>&1; then
        break
    fi
    i=$((i+1))
    sleep 2
done

# Idempotent migrate — 唯一 alembic 调用点
alembic -c alembic.ini upgrade head

# TASK-25: dev seeds — only in dev profile, only if seed file shipped.
# psycopg is already in the wheel set (pyproject.toml dependency), so
# we use a tiny python wrapper instead of pulling in the postgres-client
# apt package just to get psql.
if [ "${DEPLOY_PROFILE:-dev}" = "dev" ] && [ -f alembic/seeds/dev_users.sql ]; then
    python3 -c "
import os, psycopg
url = os.environ['NCMU_DB_URL']
sql = open('alembic/seeds/dev_users.sql').read()
with psycopg.connect(url, autocommit=True) as conn, conn.cursor() as cur:
    cur.execute(sql)
print('[entrypoint] dev_users.sql applied (idempotent ON CONFLICT DO NOTHING).')
" || echo "[entrypoint] WARN: dev seed application failed; continuing."
fi

# Hand over to whatever was passed; default CMD in Dockerfile is uvicorn.
exec "${@:-uvicorn}"
