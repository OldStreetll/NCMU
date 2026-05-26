#!/usr/bin/env bash
# scripts/e2e_workflow_modes.sh — Phase 2B B4 / TASK-79 5 mode 真容器 E2E
#
# 串行跑 5 mode (chat / advanced-chat / completion / workflow / agent-chat)
# 真 App E2E + 字段级 grep 计数 + DB workflow_runs 实测 + SUMMARY。
#
# 用法：./scripts/e2e_workflow_modes.sh
# 退出：5 mode 全 PASS → 0；任一 FAIL → 1
#
# 先决条件（plan §先决条件）：
#   (a) docker stack 6 service (pg-ncmu / pg-dify / pg-fastgpt / redis /
#       ncmu-backend / ncmu-spa) 全 (healthy) — 脚本前置 H4 named check
#   (b) Boss 在 Dify Console 手建 4 新 mode App + /admin/sync_apps 同步
#   (c) tests/e2e/workflow_modes_fixtures.json 5 entry 全填 app_id (替换
#       <boss-fills> 占位) — 脚本前置 M10 enforce check
#
# AC：plan/2026-05-08-phase2b-workflow-support-plan.md TASK-79 §"验收标准" 9 项。

# memory feedback_bash_set_E_errtrace：set -E 必须保留，否则 ERR trap 不被
# functions / command-sub 继承（NCMU TASK-37 backup.sh 教训）。
set -Eeuo pipefail
trap 'echo "[ERR] line $LINENO (cmd: $BASH_COMMAND)" >&2' ERR

# ─── 路径解析 ────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
FIXTURES="${FIXTURES:-$REPO_DIR/tests/e2e/workflow_modes_fixtures.json}"
TS="$(date +%Y%m%d-%H%M%S)"
RESULT_LOG="/tmp/phase2b-e2e-${TS}.log"
PER_MODE_LOG_PREFIX="/tmp/phase2b-e2e-${TS}"

# memory feedback_pre_existing_error_strict_validation — plan §修法 line
# 2747-2751 用单一 RESULT_LOG 累积 grep 会让 mode N 误计 mode N-1 事件
# (mode 3 completion WF≥1 可被 mode 2 advanced-chat WF 虚 PASS)。本脚本
# 用 per-mode log (PER_MODE_LOG_PREFIX-<mode>.log) 隔离，主 RESULT_LOG 仍
# tee 聚合保留供 boss 整体排查。
echo "[INFO] result aggregate log: $RESULT_LOG" | tee -a "$RESULT_LOG" >&2
echo "[INFO] per-mode logs: ${PER_MODE_LOG_PREFIX}-<mode>.log" | tee -a "$RESULT_LOG" >&2

# ─── 0. 预检：Stack 健康度 (per-service named — H4 修订) ─────────────────
# plan H4 修订：不再用模糊 grep -c '(healthy)' (任何 service 名/状态出现
# 'healthy' 字串都误命中)，改 per-service named lookup + State.Health 字段
# 严格判定。
#
# ★[INTENT-CHECK] T2 / Pane 0 路径 A：plan AC#3 字面 "6 service 全 healthy"
# 与现实冲突 — ncmu-spa (vite dev server) 在 docker-compose.yml:896-913 无
# healthcheck stanza (vite 无 health endpoint convention) → Health 字段永
# 空 → 字面要求不可达。Pane 0 决策路径 A：5 service named health + 1 conn
# check (curl http://localhost:5173/) = 6 项预检 / 与 TASK-75 AC#7 等价证据
# 策略一致 / "fail-fast on missing dep" 防御性保持。
REQUIRED_SERVICES=("pg-ncmu" "pg-dify" "pg-fastgpt" "redis" "ncmu-backend")
SPA_URL="${SPA_URL:-http://localhost:5173/}"

echo "=== Preflight: docker compose ps ===" | tee -a "$RESULT_LOG" >&2
# 仅写入 RESULT_LOG (单条 ps json 30KB+, 写 stderr 会淹没 [OK]/[FATAL] 信号行)
docker compose ps --format json >> "$RESULT_LOG" 2>&1 || true

HEALTHY_COUNT=0
for svc in "${REQUIRED_SERVICES[@]}"; do
  HEALTH=$(docker compose ps --format json 2>/dev/null \
    | jq -r --arg s "$svc" 'select(.Service == $s) | .Health // "none"' \
    | head -1)
  HEALTH="${HEALTH:-missing}"
  if [[ "$HEALTH" == "healthy" ]]; then
    HEALTHY_COUNT=$((HEALTHY_COUNT + 1))
    echo "[OK] $svc Health=$HEALTH" | tee -a "$RESULT_LOG" >&2
  else
    echo "[FATAL] service '$svc' Health='$HEALTH' (expected 'healthy')" \
      | tee -a "$RESULT_LOG" >&2
  fi
done
if [[ "$HEALTHY_COUNT" -lt "${#REQUIRED_SERVICES[@]}" ]]; then
  echo "[FATAL] only $HEALTHY_COUNT/${#REQUIRED_SERVICES[@]} required services healthy — abort E2E" \
    | tee -a "$RESULT_LOG" >&2
  exit 1
fi
echo "[OK] stack healthy: $HEALTHY_COUNT/${#REQUIRED_SERVICES[@]} required services" \
  | tee -a "$RESULT_LOG" >&2

# ncmu-spa connectivity check (替代 health stanza — Pane 0 路径 A)
# REWORK-79-A: curl -w "%{http_code}" 字面已输出 "000" 在连接失败时，
# 旧 fallback (echo 与 -w 字面值同形) 双发让 RC = "000000" (诊断混乱)。
# 改 || true + ${RC:-000} 兜底。
SPA_RC=$(curl -sf -o /dev/null -w "%{http_code}" --max-time 5 "$SPA_URL" 2>/dev/null \
  || true)
SPA_RC="${SPA_RC:-000}"
if [[ "$SPA_RC" != "200" ]]; then
  echo "[FATAL] ncmu-spa connectivity failed: GET $SPA_URL → HTTP $SPA_RC (expected 200) — abort E2E" \
    | tee -a "$RESULT_LOG" >&2
  exit 1
fi
echo "[OK] ncmu-spa connectivity: GET $SPA_URL → HTTP $SPA_RC" \
  | tee -a "$RESULT_LOG" >&2

# ─── 0.5 fixtures.json 占位 enforce check (M10 修订) ─────────────────────
# 避免 boss 忘填 <boss-fills> 拼到 URL 后报 404 误诊为后端 bug。
if [[ ! -f "$FIXTURES" ]]; then
  echo "[FATAL] fixtures file missing: $FIXTURES" >&2
  exit 1
fi
if jq -r '.[].app_id' "$FIXTURES" 2>/dev/null | grep -qx '<boss-fills>'; then
  echo "[FATAL] $FIXTURES contains <boss-fills> placeholder — fill all 5 mode app_id before running" >&2
  exit 1
fi
FIX_LEN=$(jq 'length' "$FIXTURES")
if [[ "$FIX_LEN" -ne 5 ]]; then
  echo "[FATAL] $FIXTURES expected 5 entries, got $FIX_LEN" >&2
  exit 1
fi
echo "[OK] fixtures: 5 entries, no placeholder" | tee -a "$RESULT_LOG" >&2

# ─── 1. dev-login JWT ────────────────────────────────────────────────────
JWT_RAW=$(curl -s -X POST http://localhost/api/v1/ncmu/auth/dev-login \
  -H "Content-Type: application/json" \
  -d '{"user_id":"a0000001-0000-4000-8000-000000000001"}')
JWT=$(echo "$JWT_RAW" | jq -r '.jwt // empty')
if [[ -z "$JWT" || "$JWT" == "null" ]]; then
  echo "[FATAL] dev-login failed; response: $JWT_RAW" >&2
  exit 1
fi
echo "[OK] dev-login JWT acquired (len=${#JWT})" | tee -a "$RESULT_LOG" >&2

# ─── 2. 5 mode 串行 ──────────────────────────────────────────────────────
declare -A MODE_AC_PASS=()

for MODE in chat advanced-chat completion workflow agent-chat; do
  APP_ID=$(jq -r --arg m "$MODE" '.[] | select(.mode == $m) | .app_id' "$FIXTURES")
  PER_LOG="${PER_MODE_LOG_PREFIX}-${MODE}.log"
  echo "=== MODE=$MODE APP_ID=$APP_ID PER_LOG=$PER_LOG ===" | tee -a "$RESULT_LOG"

  if [[ "$MODE" == "chat" ]]; then
    # baseline smoke：调 chat 端点 + 期望 200。
    # plan L-NEW-3 修订：query 从 fixture 读，与 4 新 mode 单一来源。
    Q=$(jq -r --arg m "$MODE" '.[] | select(.mode == $m) | .inputs.query' "$FIXTURES")
    # REWORK-79-A: 同 SPA_RC pattern — fallback 与 -w 字面值双发 → "000000"。
    RC=$(curl -s -o "$PER_LOG" -w "%{http_code}" -X POST \
      "http://localhost/api/v1/ncmu/chat/$APP_ID" \
      -H "Authorization: Bearer $JWT" \
      -H "Content-Type: application/json" \
      -d "$(jq -n --arg q "$Q" '{query:$q}')" || true)
    RC="${RC:-000}"
    cat "$PER_LOG" >> "$RESULT_LOG" 2>/dev/null || true
    if [[ "$RC" == "200" ]]; then
      MODE_AC_PASS[$MODE]="PASS (HTTP=$RC)"
    else
      MODE_AC_PASS[$MODE]="FAIL (HTTP=$RC)"
    fi
    continue
  fi

  # SCOPE-CHANGE-79-SCRIPT-SLEEP (Boss 显式授权 2026-05-12)：4 mode 串行调 Dify
  # 时 plugin daemon 上游 model provider connection pool 紧张 (实证 dify-api log
  # InvokeConnectionError: HTTPConnectionPool Read timed out)，触 400 BAD REQUEST
  # 全 mode FAIL。单 mode manual 不复现。mode 间 sleep 5s 给上游池恢复窗口。
  # 仅 4 新 mode 加 sleep；chat smoke 走上方 if/continue 分支不触此。
  # IMP-INDEP-3 (TASK-79-DEEPDIVE 后回归 5s)：DEEPDIVE 已证 batch fail 真因 =
  # fixture 缺最外层 inputs 包裹 (非 timing / 非 state pollution)。修 fixture 后
  # 5s 已实测够缓解 plugin daemon 池紧张；30s 是 SLEEP-30-VERIFICATION 诊断遗留
  # 不再需要 (sleep 5 vs 30 batch SUMMARY 完全一致已实证 / 字面对账 timing 不是
  # 因子)。回 5s 缩 batch ~100s 总耗时。
  sleep 5

  # 4 新 mode：调 workflow run 端点 (SSE 流，curl -N 不缓冲)
  curl -sN -X POST "http://localhost/api/v1/ncmu/workflow/apps/$APP_ID/run" \
    -H "Authorization: Bearer $JWT" \
    -H "Content-Type: application/json" \
    -d "$(jq -c --arg m "$MODE" '.[] | select(.mode == $m) | .inputs' "$FIXTURES")" \
    > "$PER_LOG" 2>&1 || true
  cat "$PER_LOG" >> "$RESULT_LOG" 2>/dev/null || true

  # 期望事件计数 (从 fixture 读，未声明默认 0)
  EXPECT_NS=$(jq -r --arg m "$MODE" '.[] | select(.mode == $m) | .expected.node_started // 0' "$FIXTURES")
  EXPECT_NF=$(jq -r --arg m "$MODE" '.[] | select(.mode == $m) | .expected.node_finished // 0' "$FIXTURES")
  EXPECT_WF=$(jq -r --arg m "$MODE" '.[] | select(.mode == $m) | .expected.workflow_finished // 0' "$FIXTURES")
  EXPECT_AT=$(jq -r --arg m "$MODE" '.[] | select(.mode == $m) | .expected.agent_thought // 0' "$FIXTURES")
  EXPECT_TC=$(jq -r --arg m "$MODE" '.[] | select(.mode == $m) | .expected.tool_call // 0' "$FIXTURES")

  # plan L-NEW-4 修订：用 SSE event line 字面 grep ('event: <name>')，
  # 不依赖 inner JSON 结构稳定 (避免未来 NCMU schema 在 inner data 加
  # event_type 字段误计数)。grep 在 PER_LOG (本 mode 隔离) 而非聚合
  # RESULT_LOG，避免跨 mode 累积假 PASS。
  #
  # REWORK-79-A (Pane 5 实测复现 Critical bug)：grep -c 在 0 匹配时字面
  # 输出 "0\n" + exit 1 → 旧 fallback 再输出一个 "0\n" → 命令替换合并 =
  # "0\n0" 三字符 → 后续 [[ "$ACT" -ge "$EXPECT" ]] 算术上下文报
  # "syntax error in expression"。每个 mode 都有 EXPECT=0 字段必触发 →
  # AC#4 字面不可达。改：|| true 不再追加，再用 ${VAR:-0} 兜底空值情形。
  ACT_NS=$(grep -c '^event: node_started$' "$PER_LOG" 2>/dev/null || true)
  ACT_NS="${ACT_NS:-0}"
  ACT_NF=$(grep -c '^event: node_finished$' "$PER_LOG" 2>/dev/null || true)
  ACT_NF="${ACT_NF:-0}"
  ACT_WF=$(grep -c '^event: workflow_finished$' "$PER_LOG" 2>/dev/null || true)
  ACT_WF="${ACT_WF:-0}"
  ACT_AT=$(grep -c '^event: agent_thought$' "$PER_LOG" 2>/dev/null || true)
  ACT_AT="${ACT_AT:-0}"
  ACT_TC=$(grep -c '^event: tool_call$' "$PER_LOG" 2>/dev/null || true)
  ACT_TC="${ACT_TC:-0}"

  PASS=true
  [[ "$ACT_NS" -ge "$EXPECT_NS" ]] || PASS=false
  [[ "$ACT_NF" -ge "$EXPECT_NF" ]] || PASS=false
  [[ "$ACT_WF" -ge "$EXPECT_WF" ]] || PASS=false
  [[ "$ACT_AT" -ge "$EXPECT_AT" ]] || PASS=false
  [[ "$ACT_TC" -ge "$EXPECT_TC" ]] || PASS=false

  # TASK-BUG-4 (2026-05-26): content 字段级断言（防 Bug 3 同型 mock-vs-real
  # wire-shape 错位类 bug — event 计数对但 outputs.answer 永空 / outputs={}
  # 假 PASS）。守 feedback_tdd_mock_vs_real_api 三支柱「test mock + plan 字面
  # 字段 + upstream 真代码」全验。
  #
  # Wire shape 字面对账（守 feedback_evidence_first）：
  #   chat/sse.py:24-31  SSE 帧 = `event: <type>\ndata: <JSON>\n\n`
  #   schemas/sse_events.py:145-166  NcmuSseEvent envelope = event_type/run_id/timestamp/data
  #   workflow_finished data.outputs.answer 真路径：
  #     advanced_chat.py:143  outputs={"answer": accumulated_text}
  #     completion.py:87      outputs={"answer": accumulated_text}
  #     agent_chat.py:143     outputs={"answer": accumulated_text}
  #     workflow.py:103       outputs=data.get("outputs") or {}（无 answer 强约束 → 用 keys count 兜底）
  #
  # 解析方式：awk 抓 PER_LOG 中首个 `event: workflow_finished` 下一行 `data: <JSON>`，
  # printf '%s' 喂 jq 避免空 stdin 解析报错；守 feedback_pipe_to_head_masks_exit_code
  # 不用 `cmd | head` 屏蔽 exit code（awk exit 0 退出本身即首帧截断）。
  WF_DATA=$(awk 'prev=="event: workflow_finished" && /^data: / {
                    sub(/^data: /, ""); print; exit
                }
                { prev=$0 }' "$PER_LOG")
  EXPECT_AML=$(jq -r --arg m "$MODE" '.[] | select(.mode == $m) | .expected.answer_min_length // 0' "$FIXTURES")
  EXPECT_OMK=$(jq -r --arg m "$MODE" '.[] | select(.mode == $m) | .expected.outputs_min_keys // 0' "$FIXTURES")
  if [[ -n "$WF_DATA" ]]; then
    ACT_AML=$(printf '%s' "$WF_DATA" | jq -r '.data.outputs.answer // "" | length' 2>/dev/null || true)
    ACT_AML="${ACT_AML:-0}"
    ACT_OMK=$(printf '%s' "$WF_DATA" | jq -r '.data.outputs // {} | length' 2>/dev/null || true)
    ACT_OMK="${ACT_OMK:-0}"
  else
    ACT_AML=0
    ACT_OMK=0
  fi
  [[ "$ACT_AML" -ge "$EXPECT_AML" ]] || PASS=false
  [[ "$ACT_OMK" -ge "$EXPECT_OMK" ]] || PASS=false

  # DB 实测：workflow_runs 表新增本 app_id ≥1 行 (status 终态)
  # REWORK-79-A 防御性顺手修 (Pane 5 7 处审外 +1)：psql count(*) 当前不会
  # "stdout 输出 + exit 非 0" 双发 (count 总返 1 行)，但与 grep -c 同 anti-
  # pattern；为来日 psql 行为变化兜底，同样改 || true + ${VAR:-0}。
  DB_RUNS=$(docker exec ncmu-pg-ncmu psql -U ncmu_app -d ncmu -tAc \
    "SELECT count(*) FROM workflow_runs WHERE app_id='$APP_ID' AND status IN ('succeeded','failed')" \
    2>/dev/null | tr -d ' ' || true)
  DB_RUNS="${DB_RUNS:-0}"
  [[ "$DB_RUNS" -ge 1 ]] || PASS=false

  STATS="NS=$ACT_NS/$EXPECT_NS NF=$ACT_NF/$EXPECT_NF WF=$ACT_WF/$EXPECT_WF AT=$ACT_AT/$EXPECT_AT TC=$ACT_TC/$EXPECT_TC AML=$ACT_AML/$EXPECT_AML OMK=$ACT_OMK/$EXPECT_OMK DB=$DB_RUNS"
  if $PASS; then
    MODE_AC_PASS[$MODE]="PASS ($STATS)"
  else
    MODE_AC_PASS[$MODE]="FAIL ($STATS)"
  fi
done

# ─── 3. 总结 ─────────────────────────────────────────────────────────────
echo "=== SUMMARY ===" | tee -a "$RESULT_LOG"
for MODE in chat advanced-chat completion workflow agent-chat; do
  echo "$MODE: ${MODE_AC_PASS[$MODE]}" | tee -a "$RESULT_LOG"
done

# 任一 FAIL → exit 1
EXIT_RC=0
for MODE in chat advanced-chat completion workflow agent-chat; do
  [[ "${MODE_AC_PASS[$MODE]}" == PASS* ]] || EXIT_RC=1
done
echo "[INFO] exit code: $EXIT_RC" | tee -a "$RESULT_LOG"
exit "$EXIT_RC"
