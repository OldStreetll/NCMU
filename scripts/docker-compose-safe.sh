#!/usr/bin/env bash
# scripts/docker-compose-safe.sh
#
# Wrapper around `docker compose` that intercepts destructive
# `down -v` / `down --volumes` invocations with a double confirmation
# prompt and an audit log entry. All other invocations pass through
# unchanged via `exec docker compose "$@"`.
#
# Required confirmation phrase: yes-i-want-to-destroy-all-volumes
# Audit log: data/backups/destructive.log

set -eEuo pipefail

readonly AUDIT_LOG_DIR="data/backups"
readonly AUDIT_LOG_FILE="${AUDIT_LOG_DIR}/destructive.log"
readonly CONFIRM_PHRASE="yes-i-want-to-destroy-all-volumes"
readonly VOLUME_FILTER='pg-(ncmu|dify|fastgpt)|fastgpt-mongo'

is_destructive_down() {
    local arg has_down=0 has_volumes=0
    for arg in "$@"; do
        case "$arg" in
            down)        has_down=1 ;;
            -v|--volumes) has_volumes=1 ;;
        esac
    done
    [[ $has_down -eq 1 && $has_volumes -eq 1 ]]
}

write_audit() {
    local result="$1"
    shift
    mkdir -p "$AUDIT_LOG_DIR"
    local ts user cmd
    ts=$(date "+%Y-%m-%d %H:%M:%S")
    user=$(id -un 2>/dev/null || whoami)
    cmd="$*"
    printf '%s user=%s cmd="%s" result=%s\n' \
        "$ts" "$user" "$cmd" "$result" >> "$AUDIT_LOG_FILE"
}

if is_destructive_down "$@"; then
    {
        echo "⚠️  即将执行 docker compose down -v，会删除所有 named volume 数据"
        echo
        echo "当前 volume 列表："
    } >&2
    docker volume ls --format '{{.Name}}\t{{.Driver}}' \
        | grep -E "$VOLUME_FILTER" >&2 || true
    echo >&2
    echo "如确定，输入完整字符串 ${CONFIRM_PHRASE} 继续：" >&2

    reply=""
    read -r reply || true

    if [[ "$reply" == "$CONFIRM_PHRASE" ]]; then
        write_audit "CONFIRMED" "$@"
        exec docker compose "$@"
    fi

    write_audit "CANCELLED" "$@"
    echo "Cancelled" >&2
    exit 1
fi

exec docker compose "$@"
