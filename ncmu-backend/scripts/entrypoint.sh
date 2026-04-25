#!/usr/bin/env sh
set -e

# D-2 修订：本脚本是整个系统里**唯一**调用 `alembic` 的地方。
# start-dev.sh、ncmu_init.py、开发者手工命令都不应重复 upgrade（幂等，但重复徒增不确定性）。

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

# Hand over to whatever was passed (batch 8: bash; batch 9 TASK-25: uvicorn)
exec "${@:-bash}"
