#!/usr/bin/env bash
# scripts/restore.sh — Phase 2A Hardening / B-NEW-13 / TASK-38
#
# 一键全量恢复：从 data/backups/<timestamp>/ 恢复 4 个数据库。
# 含 sha256 校验 + 强制确认（输入 yes）+ 部分失败时 STDERR 列已恢复 DB。
#
# 用法：./scripts/restore.sh <timestamp>
# 输入：data/backups/<ts>/{dify-pg.sql.gz, fastgpt-mongo.archive.gz,
#       fastgpt-pg.sql.gz, ncmu-pg.sql.gz, manifest.json}
# AC：plan/phase2a-hardening-plan.md TASK-38 §"验收标准" 5 项。
#
# 安全：destructive 操作（覆盖现有 DB）→ 强制确认 [[ != "yes" ]] 严格匹配；
#       任一 restore 失败立即停止 + STDERR 列已恢复 DB 列表（含被覆盖的容器）。
# 凭据：postgres user/db + mongo 凭据通过容器内 env 引用（host 侧零暴露，
#       防 ps aux 凭据可见；比 backup.sh REVIEW-37 §非阻塞建议 #1 改进）。

set -eEuo pipefail

# ─── 容器名（与 backup.sh 一致） ─────────────────────────────────────────
readonly CONTAINER_PG_DIFY="ncmu-pg-dify"
readonly CONTAINER_FASTGPT_MONGO="ncmu-fastgpt-mongo"
readonly CONTAINER_PG_FASTGPT="ncmu-pg-fastgpt"
readonly CONTAINER_PG_NCMU="ncmu-pg-ncmu"

# ─── 路径解析 ────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKUPS_DIR="$REPO_DIR/data/backups"

# ─── AC#1：参数校验 ──────────────────────────────────────────────────────
if [[ $# -lt 1 ]]; then
    echo "Usage: restore.sh <timestamp>" >&2
    exit 1
fi

TS="$1"
TS_DIR="$BACKUPS_DIR/$TS"
TS_REL_PATH="data/backups/$TS"

# ─── AC#2：snapshot 存在性 ──────────────────────────────────────────────
if [[ ! -d "$TS_DIR" ]]; then
    echo "Snapshot not found: $TS_REL_PATH" >&2
    exit 1
fi

MANIFEST="$TS_DIR/manifest.json"
if [[ ! -f "$MANIFEST" ]]; then
    echo "Snapshot not found: $TS_REL_PATH (missing manifest.json)" >&2
    exit 1
fi

# ─── AC#3(a)：sha256 校验（解析 manifest.json files[]） ─────────────────
# manifest.json 由 backup.sh L95-111 写出，每个 file 对象一行：
#   {"path": "xxx", "size_bytes": N, "sha256": "yyy"}
# 用 grep + sed 提取 path/sha256（与 backup.sh 风格一致，不引入 jq 依赖）。
echo "Verifying sha256 against manifest.json..."

FAILED_FILES=()
VERIFIED_COUNT=0
while IFS=$'\t' read -r path expected_sha; do
    [[ -z "$path" ]] && continue
    fp="$TS_DIR/$path"
    if [[ ! -f "$fp" ]]; then
        FAILED_FILES+=("$path (missing)")
        continue
    fi
    actual_sha=$(sha256sum "$fp" | awk '{print $1}')
    if [[ "$actual_sha" != "$expected_sha" ]]; then
        FAILED_FILES+=("$path (sha256 mismatch: expected=$expected_sha actual=$actual_sha)")
    else
        VERIFIED_COUNT=$((VERIFIED_COUNT + 1))
    fi
done < <(
    grep -oE '"path": *"[^"]+", *"size_bytes": *[0-9]+, *"sha256": *"[^"]+"' "$MANIFEST" \
        | sed -E 's/.*"path": *"([^"]+)".*"sha256": *"([^"]+)".*/\1\t\2/'
)

if [[ ${#FAILED_FILES[@]} -gt 0 ]]; then
    echo "ERROR: sha256 verification failed:" >&2
    for f in "${FAILED_FILES[@]}"; do
        echo "  - $f" >&2
    done
    exit 1
fi

if [[ $VERIFIED_COUNT -eq 0 ]]; then
    echo "ERROR: manifest.json has no parseable file entries" >&2
    exit 1
fi

echo "  -> sha256 OK ($VERIFIED_COUNT files)"

# ─── AC#3(b)：强制确认（destructive 警告） ──────────────────────────────
echo "" >&2
echo "⚠️  即将从 $TS_REL_PATH 恢复 4 个数据库，将覆盖所有现有数据：" >&2
echo "    - $CONTAINER_PG_DIFY        <- dify-pg.sql.gz" >&2
echo "    - $CONTAINER_FASTGPT_MONGO  <- fastgpt-mongo.archive.gz" >&2
echo "    - $CONTAINER_PG_FASTGPT     <- fastgpt-pg.sql.gz" >&2
echo "    - $CONTAINER_PG_NCMU        <- ncmu-pg.sql.gz" >&2
echo "" >&2
printf '输入 yes 确认恢复（覆盖当前数据）: ' >&2
IFS= read -r CONFIRM || CONFIRM=""

if [[ "$CONFIRM" != "yes" ]]; then
    echo "Cancelled" >&2
    exit 1
fi

# ─── AC#3(c)(d)：4 个 restore 串行 + 部分失败处理 ────────────────────────
# 凭据从容器内 env 引用（host ps aux 仅见 'sh -c 静态字符串'，无密码暴露）。
# 容器内 env：postgres 镜像有 POSTGRES_USER/POSTGRES_DB；
#             mongo 镜像有 MONGO_INITDB_ROOT_USERNAME/MONGO_INITDB_ROOT_PASSWORD。
RESTORED=()  # 已成功恢复的容器名（含数据已被覆盖）

restore_pg() {
    local container="$1"
    local file="$2"
    echo "  -> restore $container <- $file"
    gunzip -c "$TS_DIR/$file" \
        | docker exec -i "$container" sh -c 'psql -U "$POSTGRES_USER" "$POSTGRES_DB"'
}

restore_mongo() {
    local container="$1"
    local file="$2"
    echo "  -> restore $container <- $file"
    # Option A：host 不解压，原始 .gz 直接喂给容器内 mongorestore --gzip 自行解压
    # （单一压缩边界 + 与 backup.sh `mongodump --archive --gzip > x.gz` 对称；REWORK-38 修双解压 bug）
    docker exec -i "$container" sh -c 'mongorestore --archive --gzip --drop -u "$MONGO_INITDB_ROOT_USERNAME" -p "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin' \
        < "$TS_DIR/$file"
}

handle_partial_failure() {
    local failed="$1"
    echo "" >&2
    echo "ERROR: restore failed at $failed" >&2
    echo "  Restored (data already overwritten):" >&2
    if [[ ${#RESTORED[@]} -eq 0 ]]; then
        echo "    (none)" >&2
    else
        for c in "${RESTORED[@]}"; do
            echo "    - $c" >&2
        done
    fi
    echo "  Failed: $failed" >&2
    echo "" >&2
    echo "  建议：检查 docker logs $failed 后从更早 snapshot 重试整体 restore" >&2
    exit 1
}

START_TS=$(date +%s)

echo ""
echo "Starting restore from $TS_REL_PATH (4 databases serial)..."

# 顺序：dify-pg → fastgpt-mongo → fastgpt-pg → ncmu-pg（plan AC#3(c)）
if restore_pg "$CONTAINER_PG_DIFY" "dify-pg.sql.gz"; then
    RESTORED+=("$CONTAINER_PG_DIFY")
else
    handle_partial_failure "$CONTAINER_PG_DIFY"
fi

if restore_mongo "$CONTAINER_FASTGPT_MONGO" "fastgpt-mongo.archive.gz"; then
    RESTORED+=("$CONTAINER_FASTGPT_MONGO")
else
    handle_partial_failure "$CONTAINER_FASTGPT_MONGO"
fi

if restore_pg "$CONTAINER_PG_FASTGPT" "fastgpt-pg.sql.gz"; then
    RESTORED+=("$CONTAINER_PG_FASTGPT")
else
    handle_partial_failure "$CONTAINER_PG_FASTGPT"
fi

if restore_pg "$CONTAINER_PG_NCMU" "ncmu-pg.sql.gz"; then
    RESTORED+=("$CONTAINER_PG_NCMU")
else
    handle_partial_failure "$CONTAINER_PG_NCMU"
fi

# ─── AC#3(e) + 软 AC#5：成功输出 + 耗时 ─────────────────────────────────
END_TS=$(date +%s)
DURATION=$((END_TS - START_TS))

echo ""
echo "Restore complete from $TS"
echo "Total duration: ${DURATION}s"
echo ""
echo "请重启相关容器: docker compose restart ncmu-backend ncmu-pg-dify ncmu-fastgpt-mongo ncmu-pg-fastgpt ncmu-pg-ncmu"
