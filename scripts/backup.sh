#!/usr/bin/env bash
# scripts/backup.sh — Phase 2A Hardening / B-NEW-13 / TASK-37
#
# 一键全量备份 4 个数据库到 data/backups/<YYYYMMDD-HHMMSS>/，含 manifest.json。
# 失败时清理半成品 snapshot 目录；成功后 prune 保留最近 RETENTION_COUNT 个。
#
# 用法：./scripts/backup.sh
# 输出：data/backups/<ts>/{dify-pg.sql.gz, fastgpt-mongo.archive.gz,
#       fastgpt-pg.sql.gz, ncmu-pg.sql.gz, manifest.json}
# AC：plan/phase2a-hardening-plan.md TASK-37 §"验收标准" 8 项。
#
# 注：postgres user/db + mongo 凭据从容器 env 实时读取（避免与 docker-compose.yml drift）。

set -eEuo pipefail

# ─── 容器名（docker ps --format '{{.Names}}' 实测命中后固化）────────────
readonly CONTAINER_PG_DIFY="ncmu-pg-dify"
readonly CONTAINER_FASTGPT_MONGO="ncmu-fastgpt-mongo"
readonly CONTAINER_PG_FASTGPT="ncmu-pg-fastgpt"
readonly CONTAINER_PG_NCMU="ncmu-pg-ncmu"

# ─── 配置 ───────────────────────────────────────────────────────────────
readonly RETENTION_COUNT=10

# ─── 路径解析 ────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKUPS_DIR="$REPO_DIR/data/backups"
TS="$(date +%Y%m%d-%H%M%S)"
TS_DIR="$BACKUPS_DIR/$TS"
TS_REL_PATH="./data/backups/$TS"

# ─── 半成品清理 trap ─────────────────────────────────────────────────────
# 任一 dump 失败 → ERR 触发 → 清半成品 → 整体退出 1（保护现有 snapshot 不 prune）
cleanup_partial() {
    local rc=$?
    echo "ERROR: backup aborted (exit $rc); cleaning partial snapshot $TS_DIR" >&2
    if [[ -d "$TS_DIR" ]]; then
        rm -rf "$TS_DIR"
    fi
    exit "$rc"
}
trap cleanup_partial ERR

# ─── 从容器 env 读取凭据（避免 docker-compose.yml drift）────────────────
read_pg_env() {
    local container="$1"
    PG_USER=$(docker exec "$container" printenv POSTGRES_USER)
    PG_DB=$(docker exec "$container" printenv POSTGRES_DB)
}

read_mongo_env() {
    MONGO_USER=$(docker exec "$CONTAINER_FASTGPT_MONGO" printenv MONGO_INITDB_ROOT_USERNAME)
    MONGO_PASS=$(docker exec "$CONTAINER_FASTGPT_MONGO" printenv MONGO_INITDB_ROOT_PASSWORD)
}

# ─── 准备 ───────────────────────────────────────────────────────────────
mkdir -p "$TS_DIR"
echo "Backup snapshot target: $TS_REL_PATH"

START_TS=$(date +%s)

# ─── 4 个 dump 串行 ──────────────────────────────────────────────────────
# 注：pg_dump | gzip 管道在 host 侧；ERR + pipefail 保证任一失败均触发清理 trap。

read_pg_env "$CONTAINER_PG_DIFY"
echo "  -> dump $CONTAINER_PG_DIFY (-U $PG_USER $PG_DB) -> dify-pg.sql.gz"
docker exec "$CONTAINER_PG_DIFY" pg_dump -U "$PG_USER" "$PG_DB" | gzip > "$TS_DIR/dify-pg.sql.gz"

read_mongo_env
echo "  -> dump $CONTAINER_FASTGPT_MONGO (-u $MONGO_USER --db fastgpt) -> fastgpt-mongo.archive.gz"
docker exec "$CONTAINER_FASTGPT_MONGO" mongodump \
    --archive --gzip \
    --db fastgpt \
    -u "$MONGO_USER" -p "$MONGO_PASS" \
    --authenticationDatabase admin \
    > "$TS_DIR/fastgpt-mongo.archive.gz"

read_pg_env "$CONTAINER_PG_FASTGPT"
echo "  -> dump $CONTAINER_PG_FASTGPT (-U $PG_USER $PG_DB) -> fastgpt-pg.sql.gz"
docker exec "$CONTAINER_PG_FASTGPT" pg_dump -U "$PG_USER" "$PG_DB" | gzip > "$TS_DIR/fastgpt-pg.sql.gz"

read_pg_env "$CONTAINER_PG_NCMU"
echo "  -> dump $CONTAINER_PG_NCMU (-U $PG_USER $PG_DB) -> ncmu-pg.sql.gz"
docker exec "$CONTAINER_PG_NCMU" pg_dump -U "$PG_USER" "$PG_DB" | gzip > "$TS_DIR/ncmu-pg.sql.gz"

# ─── manifest.json（含 sha256 + size）────────────────────────────────────
DUMP_FILES=(
    "dify-pg.sql.gz"
    "fastgpt-mongo.archive.gz"
    "fastgpt-pg.sql.gz"
    "ncmu-pg.sql.gz"
)

{
    echo "{"
    echo "  \"timestamp\": \"$TS\","
    echo "  \"created_at\": \"$(date -Iseconds)\","
    echo "  \"files\": ["
    n=${#DUMP_FILES[@]}
    for ((i=0; i<n; i++)); do
        f="${DUMP_FILES[$i]}"
        fp="$TS_DIR/$f"
        sz=$(stat -c %s "$fp")
        sha=$(sha256sum "$fp" | awk '{print $1}')
        printf '    {"path": "%s", "size_bytes": %d, "sha256": "%s"}' "$f" "$sz" "$sha"
        if (( i < n - 1 )); then echo ","; else echo ""; fi
    done
    echo "  ]"
    echo "}"
} > "$TS_DIR/manifest.json"

# 备份成功 → 解除 trap，prune 阶段失败不应删除 snapshot
trap - ERR

# ─── Prune：保留最近 RETENTION_COUNT 个 timestamp 目录 ──────────────────
# 仅匹配 ^[0-9]{8}-[0-9]{6}$ 严格 timestamp 命名，避免误删其他文件
PRUNED_COUNT=0
if [[ -d "$BACKUPS_DIR" ]]; then
    while IFS= read -r old; do
        [[ -z "$old" ]] && continue
        rm -rf "$BACKUPS_DIR/$old"
        echo "Pruned: $old"
        PRUNED_COUNT=$((PRUNED_COUNT + 1))
    done < <(ls -1 "$BACKUPS_DIR" 2>/dev/null | grep -E '^[0-9]{8}-[0-9]{6}$' | sort | head -n -"$RETENTION_COUNT")
fi

# ─── STDOUT 总结（AC#1：路径 + 各 dump 字节 + 总耗时）───────────────────
END_TS=$(date +%s)
DURATION=$((END_TS - START_TS))

echo ""
echo "Backup created: $TS_REL_PATH"
for f in "${DUMP_FILES[@]}"; do
    sz=$(stat -c %s "$TS_DIR/$f")
    printf "  %-30s %12d bytes\n" "$f" "$sz"
done
echo ""
echo "Total duration: ${DURATION}s (retention=$RETENTION_COUNT, pruned this run=$PRUNED_COUNT)"
