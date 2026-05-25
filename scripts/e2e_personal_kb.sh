#!/usr/bin/env bash
# scripts/e2e_personal_kb.sh — Phase 2C Batch C4 / TASK-PC4 真容器 E2E
#
# 完整业务流真容器 E2E：
#   - 启动前预检（5 service health + alembic head + env + seed）
#   - 黄金路径 11 step（员工提交 → admin claim/dispatch → 员工对话/下载）
#   - 6 异常分支字段级 AC（FastGPT 503 / >50MB / >10 file / .exe / feature flag / 越权）
#   - 收尾清理（DB + 上游 / 幂等）
#
# 用法：
#   make e2e-personal-kb                  # 推荐
#   ./scripts/e2e_personal_kb.sh          # 直跑（需 .env 中 FASTGPT/DIFY 密钥已注入）
#
# 退出：黄金 + 6 异常全 PASS → 0；任一 FAIL → 1
#
# 先决条件（脚本前置 preflight 段强 enforce）：
#   (a) 5 service docker stack (ncmu-backend / pg-ncmu / fastgpt-app /
#       dify-api / kb-adapter) 全 healthy — Boss 负责启动
#   (b) NCMU_ENABLE_DEV_LOGIN=true / NCMU_ADMIN_USER_IDS 含
#       a0000001-0000-4000-8000-000000000001（默认值即满足）
#   (c) FASTGPT_API_KEY / DIFY_CONSOLE_API_KEY / DIFY_TENANT_ID 已注入 .env
#       （dev token-sync 已跑过）
#   (d) alembic upgrade head 跑到 0008（personal_kb 3 表 + 0008 column alter）
#
# AC：plan/2026-05-20-phase2c-personal-kb-plan.md §4.9 字面 + spec §6.3 Q10-B 矩阵。
# 守 memory：
#   - feedback_bash_set_E_errtrace        — set -E 必保留
#   - feedback_pipe_to_head_masks_exit_code — 验证类命令禁 cmd | head 直判
#   - feedback_pre_existing_error_strict_validation — 字段级断言
#   - feedback_inline_cleanup_pitfall     — cleanup 独立 step / EXIT trap
#   - feedback_e2e_plan_ac_pitfalls       — stack 健康度预检 必须前置

set -Eeuo pipefail
set -o pipefail
trap 'echo "[ERR] line $LINENO (cmd: $BASH_COMMAND)" >&2' ERR

# ─── 路径与常量 ──────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
FIXTURES_SRC_DIR="$SCRIPT_DIR/e2e_personal_kb_fixtures"
TS="$(date +%Y%m%d-%H%M%S)"
PC4_TMP_DIR="${PC4_TMP_DIR:-/tmp/pc4-fixtures-${TS}-$$}"
RESULT_LOG="/tmp/pc4-e2e-${TS}.log"
PER_STEP_PREFIX="/tmp/pc4-step-${TS}"

# Hosts / ports（与 docker-compose.yml + nginx dev.conf.template 字面一致）
NCMU_BASE="${NCMU_BASE:-http://localhost}"                       # ncmu-nginx :80 → ncmu-backend
FASTGPT_BASE="${FASTGPT_BASE:-http://localhost:3000}"            # fastgpt-app :3000
DIFY_BASE="${DIFY_BASE:-http://localhost:8080}"                  # dify-nginx :8080
KB_ADAPTER_BASE="${KB_ADAPTER_BASE:-http://localhost:8001}"      # kb-adapter
PG_NCMU_CONTAINER="${PG_NCMU_CONTAINER:-ncmu-pg-ncmu}"
PG_NCMU_USER="${PG_NCMU_USER:-ncmu_app}"
PG_NCMU_DB="${PG_NCMU_DB:-ncmu}"
FASTGPT_CONTAINER="${FASTGPT_CONTAINER:-ncmu-fastgpt-app}"
NCMU_BACKEND_CONTAINER="${NCMU_BACKEND_CONTAINER:-ncmu-backend}"
KB_ADAPTER_CONTAINER="${KB_ADAPTER_CONTAINER:-ncmu-kb-adapter}"

# 必需 service 名（docker compose ps 字面 Service 列）
REQUIRED_SERVICES=("ncmu-backend" "pg-ncmu" "fastgpt-app" "dify-api" "kb-adapter")

# 测试用户（来自 fixtures/seed.sql）
ADMIN_UUID="a0000001-0000-4000-8000-000000000001"
EMPLOYEE_A_UUID="b0000001-0000-4000-8000-000000000001"
EMPLOYEE_B_UUID="b0000001-0000-4000-8000-000000000002"

# 唯一资源名（避免多次跑冲突）
KB_NAME_FINAL="pc4-e2e-${TS}"
DIFY_APP_NAME="[KB] ${KB_NAME_FINAL}"

# 全局可变状态（cleanup 用 / 默认空）
APPLICATION_ID=""
DATASET_ID=""
DIFY_APP_ID=""
GOLDEN_SHA256=""
FASTGPT_STOPPED=0

# 字段级 PASS/FAIL 汇总
declare -A STEP_RESULT=()
declare -A ANTI_RESULT=()

# ─── 日志 helpers（沿用 common.sh 风格 + per-step log 隔离）─────────────────
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  C_RESET=$'\033[0m'; C_INFO=$'\033[36m'; C_OK=$'\033[32m'
  C_WARN=$'\033[33m'; C_ERR=$'\033[31m'
else
  C_RESET=""; C_INFO=""; C_OK=""; C_WARN=""; C_ERR=""
fi
log_info() { printf '%s[INFO]%s %s\n' "$C_INFO" "$C_RESET" "$*" | tee -a "$RESULT_LOG"; }
log_ok()   { printf '%s[ OK ]%s %s\n' "$C_OK"   "$C_RESET" "$*" | tee -a "$RESULT_LOG"; }
log_warn() { printf '%s[WARN]%s %s\n' "$C_WARN" "$C_RESET" "$*" | tee -a "$RESULT_LOG" >&2; }
log_err()  { printf '%s[ERR ]%s %s\n' "$C_ERR"  "$C_RESET" "$*" | tee -a "$RESULT_LOG" >&2; }
log_step() { printf '\n===== %s =====\n' "$*" | tee -a "$RESULT_LOG"; }
log_fail() {
  # 失败时打印明确：哪个 Step + 实际响应 + DB 快照（AC#6 字面）
  log_err "$1"
  shift
  for f in "$@"; do
    [[ -f "$f" ]] || continue
    log_err "--- snapshot: $f (head -200) ---"
    head -200 "$f" 2>/dev/null | tee -a "$RESULT_LOG" >&2 || true
  done
}

# ─── HTTP helpers ──────────────────────────────────────────────────────
# 用 `>log 2>&1; echo $?; tail -N` 三件套替代 cmd | head（守 feedback_pipe_to_head_masks_exit_code）。
http_status() {
  # http_status URL METHOD [JWT] [DATA_JSON] [EXTRA_HEADERS...]
  local url="$1" method="$2" jwt="${3:-}" data="${4:-}" out="$5"; shift 5 || true
  local args=(-s -o "$out" -w "%{http_code}" -X "$method" --max-time 60)
  [[ -n "$jwt" ]] && args+=(-H "Authorization: Bearer $jwt")
  if [[ -n "$data" ]]; then
    args+=(-H "Content-Type: application/json" -d "$data")
  fi
  for h in "$@"; do args+=(-H "$h"); done
  local rc
  rc=$(curl "${args[@]}" "$url" 2>/dev/null || true)
  printf '%s' "${rc:-000}"
}

http_status_multipart() {
  # http_status_multipart URL JWT OUT FORM_ARGS...
  local url="$1" jwt="$2" out="$3"; shift 3
  local args=(-s -o "$out" -w "%{http_code}" -X POST --max-time 120
              -H "Authorization: Bearer $jwt")
  for a in "$@"; do args+=(-F "$a"); done
  local rc
  rc=$(curl "${args[@]}" "$url" 2>/dev/null || true)
  printf '%s' "${rc:-000}"
}

psql_exec() {
  # psql_exec "SQL" → stdout 单行结果
  local sql="$1"
  docker exec "$PG_NCMU_CONTAINER" psql -U "$PG_NCMU_USER" -d "$PG_NCMU_DB" -tAc "$sql" \
    2>/dev/null | tr -d ' ' || true
}

# ─── EXIT trap：cleanup 独立 step（守 feedback_inline_cleanup_pitfall）─────
cleanup_on_exit() {
  local rc=$?
  log_step "Cleanup (rc=$rc)"

  # 重启 fastgpt-app（如异常分支 a 停了 / 容器 start 不是 compose up 不违反 dev pane 禁令）
  if [[ "$FASTGPT_STOPPED" -eq 1 ]]; then
    if docker start "$FASTGPT_CONTAINER" >/dev/null 2>&1; then
      log_ok "fastgpt-app started (restore after anti-a)"
    else
      log_warn "fastgpt-app start failed; manual intervention may be needed"
    fi
  fi

  # 删 Dify App（admin console api）
  if [[ -n "$DIFY_APP_ID" && -n "${DIFY_CONSOLE_API_KEY:-}" ]]; then
    local del_rc
    del_rc=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 \
      -X DELETE "$DIFY_BASE/console/api/apps/$DIFY_APP_ID" \
      -H "Authorization: Bearer $DIFY_CONSOLE_API_KEY" 2>/dev/null || echo "000")
    if [[ "$del_rc" == "204" || "$del_rc" == "200" ]]; then
      log_ok "Dify App deleted: $DIFY_APP_ID"
    else
      log_warn "Dify App delete HTTP=$del_rc (id=$DIFY_APP_ID) — manual cleanup needed"
    fi
  fi

  # 删 FastGPT dataset
  if [[ -n "$DATASET_ID" && -n "${FASTGPT_API_KEY:-}" ]]; then
    local del_rc
    del_rc=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 \
      -X DELETE "$FASTGPT_BASE/api/core/dataset/delete?id=$DATASET_ID" \
      -H "Authorization: Bearer $FASTGPT_API_KEY" 2>/dev/null || echo "000")
    if [[ "$del_rc" == "200" ]]; then
      log_ok "FastGPT dataset deleted: $DATASET_ID"
    else
      log_warn "FastGPT dataset delete HTTP=$del_rc (id=$DATASET_ID) — manual cleanup needed"
    fi
  fi

  # 清 DB：app_owners + dify_app_kb_bindings + dify_external_kb_configs + personal_kb_applications
  # 不依赖 admin UI；外键 ondelete=CASCADE 让 files 自动跟删。
  if [[ -n "$APPLICATION_ID" ]]; then
    psql_exec "DELETE FROM personal_kb_applications WHERE id='$APPLICATION_ID'" >/dev/null
    log_ok "personal_kb_applications deleted: $APPLICATION_ID (cascade files)"
  fi
  if [[ -n "$DIFY_APP_ID" ]]; then
    psql_exec "DELETE FROM app_owners WHERE app_id='$DIFY_APP_ID'" >/dev/null
    psql_exec "DELETE FROM dify_app_kb_bindings WHERE dify_app_id='$DIFY_APP_ID'" >/dev/null
  fi
  if [[ -n "$DATASET_ID" ]]; then
    psql_exec "DELETE FROM dify_external_kb_configs WHERE fastgpt_dataset_id='$DATASET_ID'" >/dev/null
  fi

  # 清 fixtures 临时目录
  if [[ -d "$PC4_TMP_DIR" ]]; then
    rm -rf "$PC4_TMP_DIR" || true
    log_ok "tmp fixtures dir removed: $PC4_TMP_DIR"
  fi

  log_info "result log: $RESULT_LOG"
  exit "$rc"
}
trap cleanup_on_exit EXIT

# ─── .env 加载（FASTGPT_API_KEY / DIFY_CONSOLE_API_KEY 等）─────────────────
load_env() {
  local env_file="$REPO_DIR/.env"
  [[ -f "$env_file" ]] || {
    log_err ".env not found: $env_file — run scripts/start-dev.sh once to bootstrap"
    exit 1
  }
  # 只挑当前脚本用到的 key，避免 export 全量 env（防 CHANGE_ME 等噪声污染 trap）
  local keys=(FASTGPT_API_KEY DIFY_CONSOLE_API_KEY DIFY_TENANT_ID
              FASTGPT_PORT DIFY_PORT NCMU_ENABLE_DEV_LOGIN
              FASTGPT_DEFAULT_ROOT_PSW)
  local k v
  for k in "${keys[@]}"; do
    v=$(grep -E "^${k}=" "$env_file" 2>/dev/null | tail -n1 | cut -d= -f2-)
    if [[ -n "$v" ]]; then
      export "$k=$v"
    fi
  done
}

# ─── Preflight (AC#1) ──────────────────────────────────────────────────
preflight() {
  log_step "Preflight"
  load_env

  # 1. 5 service health
  log_info "docker compose ps 字段级 named lookup（per-service Health）"
  local healthy=0 svc health
  for svc in "${REQUIRED_SERVICES[@]}"; do
    health=$(docker compose ps --format json 2>/dev/null \
      | jq -r --arg s "$svc" 'select(.Service == $s) | .Health // "none"' \
      | head -1)
    health="${health:-missing}"
    if [[ "$health" == "healthy" ]]; then
      healthy=$((healthy + 1))
      log_ok "$svc Health=$health"
    else
      log_err "$svc Health=$health (expected 'healthy')"
    fi
  done
  if [[ "$healthy" -lt "${#REQUIRED_SERVICES[@]}" ]]; then
    log_err "[FAIL] only $healthy/${#REQUIRED_SERVICES[@]} required services healthy — abort E2E"
    exit 1
  fi

  # 2. 5 service URL 字面 endpoint 验
  local f="${PER_STEP_PREFIX}-preflight.log"
  local rc
  rc=$(http_status "$NCMU_BASE/api/v1/ncmu/healthz" "GET" "" "" "$f")
  [[ "$rc" == "200" ]] || { log_fail "[FAIL] ncmu-backend GET /api/v1/ncmu/healthz HTTP=$rc" "$f"; exit 1; }
  log_ok "ncmu-backend GET /api/v1/ncmu/healthz → 200"

  # postgres pg_isready
  if docker exec "$PG_NCMU_CONTAINER" pg_isready -U "$PG_NCMU_USER" >/dev/null 2>&1; then
    log_ok "pg-ncmu pg_isready OK"
  else
    log_err "[FAIL] pg-ncmu pg_isready failed"
    exit 1
  fi

  rc=$(http_status "$FASTGPT_BASE/api/common/system/version" "GET" "" "" "$f")
  [[ "$rc" == "200" ]] || { log_fail "[FAIL] fastgpt GET /api/common/system/version HTTP=$rc" "$f"; exit 1; }
  log_ok "fastgpt-app GET /api/common/system/version → 200"

  rc=$(http_status "$DIFY_BASE/console/api/setup" "GET" "" "" "$f")
  # Dify /console/api/setup 返 200 (init) 或 401/403（已 setup），都说明 dify-nginx 通
  [[ "$rc" =~ ^(200|401|403)$ ]] \
    || { log_fail "[FAIL] dify-api GET /console/api/setup HTTP=$rc" "$f"; exit 1; }
  log_ok "dify-api GET /console/api/setup → $rc (reachable)"

  rc=$(http_status "$KB_ADAPTER_BASE/healthz" "GET" "" "" "$f")
  if [[ "$rc" != "200" ]]; then
    # kb-adapter 可能改 /health 而非 /healthz — 兜底再试
    rc=$(http_status "$KB_ADAPTER_BASE/health" "GET" "" "" "$f")
  fi
  [[ "$rc" == "200" ]] \
    || { log_fail "[FAIL] kb-adapter GET /healthz|/health HTTP=$rc" "$f"; exit 1; }
  log_ok "kb-adapter health endpoint → 200"

  # 3. alembic head 含 0007 + 0008（personal_kb 3 表 + external_kb_name VARCHAR(200)）
  # 字段级断言（不接受"非 404"模糊判定 / 守 feedback_pre_existing_error_strict_validation）：
  # 3 张新表全要存在
  local t row_count
  for t in app_owners personal_kb_applications personal_kb_application_files; do
    row_count=$(psql_exec "SELECT count(*) FROM information_schema.tables WHERE table_name='$t'")
    if [[ "$row_count" != "1" ]]; then
      log_err "[FAIL] alembic table missing: $t (information_schema count=$row_count)"
      exit 1
    fi
    log_ok "alembic table present: $t"
  done
  # external_kb_name VARCHAR(200)（0008 alter）
  local kb_name_len
  kb_name_len=$(psql_exec \
    "SELECT character_maximum_length FROM information_schema.columns \
     WHERE table_name='dify_external_kb_configs' AND column_name='external_kb_name'")
  if [[ "$kb_name_len" != "200" ]]; then
    log_err "[FAIL] dify_external_kb_configs.external_kb_name length=$kb_name_len (expected 200 / 0008 not applied)"
    exit 1
  fi
  log_ok "0008 applied: external_kb_name VARCHAR(200)"

  # 4. FASTGPT_API_KEY 注入双源（docker exec env 字面验 — 守任务模板 AC#1）
  local key_in_backend key_in_adapter
  key_in_backend=$(docker exec "$NCMU_BACKEND_CONTAINER" sh -c 'printf "%s" "${FASTGPT_API_KEY:-}"' 2>/dev/null || echo "")
  key_in_adapter=$(docker exec "$KB_ADAPTER_CONTAINER" sh -c 'printf "%s" "${FASTGPT_API_KEY:-}"' 2>/dev/null || echo "")
  if [[ -z "$key_in_backend" || "$key_in_backend" == "CHANGE_ME" ]]; then
    log_err "[FAIL] FASTGPT_API_KEY not injected into $NCMU_BACKEND_CONTAINER (got='$key_in_backend')"
    exit 1
  fi
  log_ok "FASTGPT_API_KEY in $NCMU_BACKEND_CONTAINER (len=${#key_in_backend})"
  if [[ -z "$key_in_adapter" || "$key_in_adapter" == "CHANGE_ME" ]]; then
    log_warn "FASTGPT_API_KEY not in $KB_ADAPTER_CONTAINER (got='$key_in_adapter') — kb-adapter may use different key"
  else
    log_ok "FASTGPT_API_KEY in $KB_ADAPTER_CONTAINER (len=${#key_in_adapter})"
  fi
  # 同步到本进程供后续 curl 用（载 env 已做但兜底）
  : "${FASTGPT_API_KEY:=$key_in_backend}"
  export FASTGPT_API_KEY

  # 5. seed users（idempotent apply + count ≥ 4）
  if ! docker exec -i "$PG_NCMU_CONTAINER" \
        psql -U "$PG_NCMU_USER" -d "$PG_NCMU_DB" -q < "$FIXTURES_SRC_DIR/seed.sql" \
        >/dev/null 2>&1; then
    log_err "[FAIL] seed.sql apply failed"
    exit 1
  fi
  local user_count
  user_count=$(psql_exec "SELECT count(*) FROM users")
  if [[ -z "$user_count" || "$user_count" -lt 4 ]]; then
    log_err "[FAIL] users count=$user_count (expected ≥ 4 after seed)"
    exit 1
  fi
  log_ok "users seeded: count=$user_count (admin + 3 PC4 testers + 既有 dev users)"

  # 6. NCMU_ENABLE_DEV_LOGIN 真在容器 env（dev-login 端点开关）
  local dev_login_flag
  dev_login_flag=$(docker exec "$NCMU_BACKEND_CONTAINER" \
    sh -c 'printf "%s" "${NCMU_ENABLE_DEV_LOGIN:-}"' 2>/dev/null || echo "")
  if [[ "$dev_login_flag" != "true" && "$dev_login_flag" != "1" ]]; then
    log_err "[FAIL] NCMU_ENABLE_DEV_LOGIN='$dev_login_flag' in $NCMU_BACKEND_CONTAINER (need 'true' or '1')"
    exit 1
  fi
  log_ok "NCMU_ENABLE_DEV_LOGIN=$dev_login_flag in $NCMU_BACKEND_CONTAINER"

  log_ok "Preflight all PASS"
}

# ─── Fixture 生成（dd / python heredoc / 0 外部依赖）─────────────────────
gen_fixtures() {
  log_step "Generate fixtures into $PC4_TMP_DIR"
  mkdir -p "$PC4_TMP_DIR"

  # golden.pdf — minimal valid PDF + 嵌入 KB 检索测试文本（FastGPT v4.14 能解析）
  python3 - <<'PY' > "$PC4_TMP_DIR/golden.pdf"
import sys
body = (
    b"%PDF-1.4\n"
    b"1 0 obj <</Type /Catalog /Pages 2 0 R>> endobj\n"
    b"2 0 obj <</Type /Pages /Kids [3 0 R] /Count 1>> endobj\n"
    b"3 0 obj <</Type /Page /Parent 2 0 R /Resources <</Font <</F1 4 0 R>>>> "
    b"/MediaBox [0 0 612 792] /Contents 5 0 R>> endobj\n"
    b"4 0 obj <</Type /Font /Subtype /Type1 /BaseFont /Helvetica>> endobj\n"
)
# Stream 含 KB 测试文本（Step 9 检索关键词锚 = "NCMU PC4 personal KB test marker"）
text = b"BT /F1 18 Tf 72 720 Td (NCMU PC4 personal KB test marker for retrieval) Tj ET\n"
stream_obj = b"5 0 obj <</Length %d>> stream\n%sendstream endobj\n" % (len(text), text)
body += stream_obj
xref_offset = len(body)
# 用占位算 xref 表 — 这里给 stub 让 v4.14 解析器跳到 trailer
trailer = (
    b"xref\n0 6\n0000000000 65535 f \n"
    + b"".join(b"%010d 00000 n \n" % o for o in [9, 56, 111, 217, 270])
    + b"trailer <</Size 6 /Root 1 0 R>>\n"
    + b"startxref\n%d\n%%EOF\n" % xref_offset
)
sys.stdout.buffer.write(body + trailer)
PY

  # 51MB 拒绝（dd 零填充，重命名 .pdf 让 extension 通过 → 让 size check 触发）
  dd if=/dev/zero of="$PC4_TMP_DIR/bigfile_51mb.pdf" bs=1M count=51 status=none

  # .exe 拒绝（小文件，靠 extension 触发）
  dd if=/dev/zero of="$PC4_TMP_DIR/forbidden.exe" bs=1024 count=1 status=none

  # 11 文件（>10 触发 count check）
  local i
  for i in $(seq 0 10); do
    dd if=/dev/zero of="$PC4_TMP_DIR/count_overflow_${i}.txt" bs=1024 count=1 status=none
  done

  # 8 格式各 1 候选池（备用 / AC 未直接用 / 守任务模板 "8 格式各 1"）
  local ext
  for ext in txt docx csv xlsx md html pptx; do
    dd if=/dev/zero of="$PC4_TMP_DIR/sample.$ext" bs=1024 count=1 status=none
  done

  log_ok "fixtures ready in $PC4_TMP_DIR ($(find "$PC4_TMP_DIR" -maxdepth 1 -type f | wc -l) files)"

  # golden sha256（Step 3 admin download + Step 11 employee download byte-exact 锚）
  GOLDEN_SHA256=$(sha256sum "$PC4_TMP_DIR/golden.pdf" | awk '{print $1}')
  log_info "golden.pdf sha256=$GOLDEN_SHA256"
}

# ─── Dev-login helper ───────────────────────────────────────────────────
dev_login() {
  # dev_login UUID → 输出 JWT 到 stdout
  local uuid="$1"
  local f="${PER_STEP_PREFIX}-devlogin-${uuid}.json"
  local rc
  rc=$(http_status "$NCMU_BASE/api/v1/ncmu/auth/dev-login" "POST" "" \
    "{\"user_id\":\"$uuid\"}" "$f")
  if [[ "$rc" != "200" ]]; then
    log_fail "[FAIL] dev-login uuid=$uuid HTTP=$rc" "$f"
    return 1
  fi
  jq -r '.jwt // empty' "$f"
}

# ─── 黄金路径 11 step（AC#2）────────────────────────────────────────────
golden_path() {
  log_step "Golden Path (employee_a → admin → employee_a)"
  local JWT_A JWT_ADMIN JWT_B
  JWT_A=$(dev_login "$EMPLOYEE_A_UUID") || exit 1
  JWT_ADMIN=$(dev_login "$ADMIN_UUID") || exit 1
  log_ok "dev-login: employee_a + admin JWT acquired"

  # ── Step 1: 员工 POST /personal-kb/applications multipart（1 pdf 2MB 见 [DONE] 说明）
  local f="${PER_STEP_PREFIX}-step1.json" rc
  rc=$(http_status_multipart \
    "$NCMU_BASE/api/v1/ncmu/personal-kb/applications" "$JWT_A" "$f" \
    "files=@$PC4_TMP_DIR/golden.pdf;type=application/pdf" \
    "kb_name_suggested=PC4 黄金路径 KB")
  if [[ "$rc" != "201" ]]; then
    log_fail "[FAIL] Step1 POST applications HTTP=$rc (expected 201)" "$f"
    STEP_RESULT[1]="FAIL HTTP=$rc"; return 1
  fi
  local status_field
  # 守 set -e：jq 解析失败时不让 trap 抢走 log_fail / 改用 2>/dev/null || echo 兜底
  status_field=$(jq -r '.status' "$f" 2>/dev/null || echo "")
  if [[ "$status_field" != "pending" ]]; then
    log_fail "[FAIL] Step1 .status='$status_field' (expected 'pending')" "$f"
    STEP_RESULT[1]="FAIL status=$status_field"; return 1
  fi
  APPLICATION_ID=$(jq -r '.id' "$f" 2>/dev/null || echo "")
  # DB 1 row 字段级断言
  local db_count
  db_count=$(psql_exec "SELECT count(*) FROM personal_kb_applications WHERE id='$APPLICATION_ID'")
  if [[ "$db_count" != "1" ]]; then
    log_fail "[FAIL] Step1 DB row count=$db_count (expected 1)" "$f"
    STEP_RESULT[1]="FAIL db_count=$db_count"; return 1
  fi
  STEP_RESULT[1]="PASS (id=$APPLICATION_ID status=pending db=1)"
  log_ok "Step1: application created id=$APPLICATION_ID status=pending"

  # ── Step 2: admin GET /admin/personal-kb/applications?status=pending
  f="${PER_STEP_PREFIX}-step2.json"
  rc=$(http_status "$NCMU_BASE/api/v1/ncmu/admin/personal-kb/applications?status=pending" \
    "GET" "$JWT_ADMIN" "" "$f")
  if [[ "$rc" != "200" ]]; then
    log_fail "[FAIL] Step2 GET admin list HTTP=$rc" "$f"
    STEP_RESULT[2]="FAIL HTTP=$rc"; return 1
  fi
  local list_len
  list_len=$(jq --arg id "$APPLICATION_ID" '[.[] | select(.id == $id)] | length' "$f" 2>/dev/null || echo "0")
  if [[ "$list_len" != "1" ]]; then
    log_fail "[FAIL] Step2 list contains target id $APPLICATION_ID count=$list_len (expected 1)" "$f"
    STEP_RESULT[2]="FAIL list_len=$list_len"; return 1
  fi
  STEP_RESULT[2]="PASS (target in pending list)"
  log_ok "Step2: admin sees pending application"

  # ── Step 3: admin GET /files/{file_id}/download → 200 + byte-exact sha256
  local file_id
  # REWORK-PC4-INDEP C1: admin detail 返 schemas/personal_kb.py:59 FileMeta（字段名 `.id`，
  # 不是 fastgpt_readonly/client.py:61-66 FileMeta 的 `.file_id`）。同名类两个字段集；
  # admin 路径用 `.id`，Step 10 /kbs/files 路径才用 `.file_id`。
  # 保留 Step 1 fast-path 兼容未来 ApplicationOut schema 演进，0 行为差异。
  file_id=$(jq -r '.files[0].id // empty' "${PER_STEP_PREFIX}-step1.json" 2>/dev/null || echo "")
  if [[ -z "$file_id" ]]; then
    # ApplicationOut 字面无 files 字段 → 必走 detail fallback
    f="${PER_STEP_PREFIX}-step3-detail.json"
    rc=$(http_status "$NCMU_BASE/api/v1/ncmu/admin/personal-kb/applications/$APPLICATION_ID" \
      "GET" "$JWT_ADMIN" "" "$f")
    if [[ "$rc" != "200" ]]; then
      log_fail "[FAIL] Step3 GET application detail HTTP=$rc" "$f"
      STEP_RESULT[3]="FAIL detail HTTP=$rc"; return 1
    fi
    file_id=$(jq -r '.files[0].id // empty' "$f" 2>/dev/null || echo "")
  fi
  if [[ -z "$file_id" || "$file_id" == "null" ]]; then
    log_fail "[FAIL] Step3 no file_id resolved from application detail" "${PER_STEP_PREFIX}-step3-detail.json"
    STEP_RESULT[3]="FAIL no file_id"; return 1
  fi
  local dl_file="${PER_STEP_PREFIX}-step3-download.bin"
  rc=$(curl -s -o "$dl_file" -w "%{http_code}" --max-time 60 \
    -H "Authorization: Bearer $JWT_ADMIN" \
    "$NCMU_BASE/api/v1/ncmu/admin/personal-kb/applications/$APPLICATION_ID/files/$file_id/download" \
    2>/dev/null || echo "000")
  if [[ "$rc" != "200" ]]; then
    log_fail "[FAIL] Step3 admin download HTTP=$rc" "$dl_file"
    STEP_RESULT[3]="FAIL HTTP=$rc"; return 1
  fi
  local dl_sha
  dl_sha=$(sha256sum "$dl_file" | awk '{print $1}')
  if [[ "$dl_sha" != "$GOLDEN_SHA256" ]]; then
    log_fail "[FAIL] Step3 byte-exact mismatch: dl=$dl_sha vs golden=$GOLDEN_SHA256" "$dl_file"
    STEP_RESULT[3]="FAIL sha256 mismatch"; return 1
  fi
  STEP_RESULT[3]="PASS (sha256 byte-exact)"
  log_ok "Step3: admin download byte-exact sha256=$dl_sha"

  # ── Step 4: admin POST /claim → 200 + status pending→in_progress + admin_processed_by 非空
  f="${PER_STEP_PREFIX}-step4.json"
  rc=$(http_status "$NCMU_BASE/api/v1/ncmu/admin/personal-kb/applications/$APPLICATION_ID/claim" \
    "POST" "$JWT_ADMIN" "" "$f")
  if [[ "$rc" != "200" ]]; then
    log_fail "[FAIL] Step4 claim HTTP=$rc" "$f"
    STEP_RESULT[4]="FAIL HTTP=$rc"; return 1
  fi
  local s4_status s4_by
  s4_status=$(jq -r '.status' "$f" 2>/dev/null || echo "")
  s4_by=$(jq -r '.admin_processed_by // empty' "$f" 2>/dev/null || echo "")
  if [[ "$s4_status" != "in_progress" ]]; then
    log_fail "[FAIL] Step4 .status='$s4_status' (expected 'in_progress')" "$f"
    STEP_RESULT[4]="FAIL status=$s4_status"; return 1
  fi
  if [[ -z "$s4_by" || "$s4_by" == "null" ]]; then
    log_fail "[FAIL] Step4 .admin_processed_by empty (expected non-empty)" "$f"
    STEP_RESULT[4]="FAIL admin_processed_by empty"; return 1
  fi
  # DB admin_processed_at 非空
  local s4_at_db
  s4_at_db=$(psql_exec "SELECT (admin_processed_at IS NOT NULL)::int FROM personal_kb_applications WHERE id='$APPLICATION_ID'")
  if [[ "$s4_at_db" != "1" ]]; then
    log_fail "[FAIL] Step4 DB admin_processed_at IS NULL (expected non-null)" "$f"
    STEP_RESULT[4]="FAIL admin_processed_at null"; return 1
  fi
  STEP_RESULT[4]="PASS (in_progress + admin_processed_by=$s4_by + processed_at non-null)"
  log_ok "Step4: claim → in_progress / admin_processed_by=$s4_by"

  # ── Step 5: mock 跳后台 — FastGPT 建 dataset（admin token）
  f="${PER_STEP_PREFIX}-step5.json"
  if [[ -z "${FASTGPT_API_KEY:-}" ]]; then
    log_fail "[FAIL] Step5 FASTGPT_API_KEY empty (preflight should have caught — likely .env stale)" "$f"
    STEP_RESULT[5]="FAIL no api key"; return 1
  fi
  rc=$(curl -s -o "$f" -w "%{http_code}" --max-time 60 \
    -X POST "$FASTGPT_BASE/api/core/dataset/create" \
    -H "Authorization: Bearer $FASTGPT_API_KEY" \
    -H "Content-Type: application/json" \
    -d "$(jq -nc --arg n "$KB_NAME_FINAL" \
      '{parentId:"", type:"dataset", name:$n, intro:"PC4 E2E", avatar:"/icon/logo.svg"}')" \
    2>/dev/null || echo "000")
  if [[ "$rc" != "200" ]]; then
    log_fail "[FAIL] Step5 FastGPT dataset create HTTP=$rc" "$f"
    STEP_RESULT[5]="FAIL HTTP=$rc"; return 1
  fi
  DATASET_ID=$(jq -r '.data._id // .data // empty' "$f" 2>/dev/null || echo "")
  if [[ -z "$DATASET_ID" ]]; then
    log_fail "[FAIL] Step5 dataset_id not parseable from response" "$f"
    STEP_RESULT[5]="FAIL no dataset_id"; return 1
  fi
  STEP_RESULT[5]="PASS (dataset_id=$DATASET_ID)"
  log_ok "Step5: FastGPT dataset created id=$DATASET_ID"

  # 上传 golden.pdf 到 dataset + 等 embedding（≤ 60s 轮询）
  local up="${PER_STEP_PREFIX}-step5-upload.json"
  rc=$(curl -s -o "$up" -w "%{http_code}" --max-time 120 \
    -X POST "$FASTGPT_BASE/api/core/dataset/collection/create/localFile" \
    -H "Authorization: Bearer $FASTGPT_API_KEY" \
    -F "data={\"datasetId\":\"$DATASET_ID\",\"parentId\":null,\"trainingType\":\"chunk\",\"chunkSize\":512,\"chunkSplitter\":\"\"};type=application/json" \
    -F "file=@$PC4_TMP_DIR/golden.pdf;type=application/pdf" \
    2>/dev/null || echo "000")
  if [[ "$rc" != "200" ]]; then
    log_warn "Step5 FastGPT upload HTTP=$rc (continuing — Step9 retrieval may degrade)"
  else
    log_ok "Step5: file uploaded to dataset; waiting embedding (≤60s poll)"
    local embed_done=0
    local _i
    for _i in $(seq 1 12); do
      : "$_i"   # silence SC2034 — loop count is the semantics
      sleep 5
      local stat="${PER_STEP_PREFIX}-step5-stat.json"
      curl -s --max-time 10 \
        -X POST "$FASTGPT_BASE/api/core/dataset/collection/list" \
        -H "Authorization: Bearer $FASTGPT_API_KEY" \
        -H "Content-Type: application/json" \
        -d "$(jq -nc --arg ds "$DATASET_ID" '{datasetId:$ds, parentId:null, pageNum:1, pageSize:30}')" \
        > "$stat" 2>/dev/null || true
      local trained
      trained=$(jq -r '[.data.list[]? | select(.trainingAmount == 0)] | length' "$stat" 2>/dev/null || echo "x")
      if [[ "$trained" == "1" || "$trained" == "0" ]]; then
        embed_done=1; break
      fi
    done
    if [[ "$embed_done" == "1" ]]; then
      log_ok "Step5: embedding ready"
    else
      # REWORK-PC4-INDEP I1 路径 (a)：embedding poll timeout 必须 fail-fast。
      # 原 warn-and-continue 会让 Step 10 命中 in-progress collection / FastGPT
      # response 缺 content_type → schemas FileMeta content_type:str non-Optional
      # → pydantic raise → route 500 != 200 / 违 feedback_pre_existing_error_strict_validation
      # 字段级断言原则。本步骤超时即阻塞整体 E2E，等同 AC#1 启动预检纪律。
      log_err "[FAIL] Step5: embedding poll timeout (60s) — abort E2E (would otherwise leak in-progress collection into Step 10/route 500)"
      STEP_RESULT[5]="FAIL embedding timeout"
      return 1
    fi
  fi

  # ── Step 6: mock 跳后台 — Dify Console 建 chat App
  f="${PER_STEP_PREFIX}-step6.json"
  if [[ -z "${DIFY_CONSOLE_API_KEY:-}" ]]; then
    log_fail "[FAIL] Step6 DIFY_CONSOLE_API_KEY empty (token sync not run)" "$f"
    STEP_RESULT[6]="FAIL no console key"; return 1
  fi
  rc=$(curl -s -o "$f" -w "%{http_code}" --max-time 30 \
    -X POST "$DIFY_BASE/console/api/apps" \
    -H "Authorization: Bearer $DIFY_CONSOLE_API_KEY" \
    -H "Content-Type: application/json" \
    -d "$(jq -nc --arg n "$DIFY_APP_NAME" \
      '{name:$n, mode:"chat", icon_type:"emoji", icon:"🤖",
        icon_background:"#FFEAD5", description:"PC4 E2E"}')" \
    2>/dev/null || echo "000")
  if [[ "$rc" != "201" && "$rc" != "200" ]]; then
    log_fail "[FAIL] Step6 Dify App create HTTP=$rc" "$f"
    STEP_RESULT[6]="FAIL HTTP=$rc"; return 1
  fi
  DIFY_APP_ID=$(jq -r '.id // empty' "$f" 2>/dev/null || echo "")
  if [[ -z "$DIFY_APP_ID" ]]; then
    log_fail "[FAIL] Step6 dify_app_id not parseable" "$f"
    STEP_RESULT[6]="FAIL no app_id"; return 1
  fi
  STEP_RESULT[6]="PASS (dify_app_id=$DIFY_APP_ID)"
  log_ok "Step6: Dify App created id=$DIFY_APP_ID"

  # ── Step 7: admin POST /dispatch → 200 + 4 SQL 操作生效（3 INSERT + 1 UPDATE）
  # 字段级断言（守 feedback_pre_existing_error_strict_validation）：
  #   - response.status='done'
  #   - dify_external_kb_configs +1 行 (fastgpt_dataset_id=DATASET_ID)
  #   - dify_app_kb_bindings +1 行 (dify_app_id=DIFY_APP_ID)
  #   - app_owners +1 行 (app_id=DIFY_APP_ID, owner_user_id=EMPLOYEE_A_UUID)
  #   - personal_kb_applications UPDATE status='done' + dify_app_id + processed_at
  local pre_kbcfg pre_binding pre_owner
  pre_kbcfg=$(psql_exec "SELECT count(*) FROM dify_external_kb_configs WHERE fastgpt_dataset_id='$DATASET_ID'")
  pre_binding=$(psql_exec "SELECT count(*) FROM dify_app_kb_bindings WHERE dify_app_id='$DIFY_APP_ID'")
  pre_owner=$(psql_exec "SELECT count(*) FROM app_owners WHERE app_id='$DIFY_APP_ID'")

  f="${PER_STEP_PREFIX}-step7.json"
  rc=$(http_status "$NCMU_BASE/api/v1/ncmu/admin/personal-kb/applications/$APPLICATION_ID/dispatch" \
    "POST" "$JWT_ADMIN" \
    "$(jq -nc --arg ds "$DATASET_ID" --arg ap "$DIFY_APP_ID" --arg kn "$KB_NAME_FINAL" \
      '{dataset_id:$ds, dify_app_id:$ap, kb_name_final:$kn}')" "$f")
  if [[ "$rc" != "200" ]]; then
    log_fail "[FAIL] Step7 dispatch HTTP=$rc" "$f"
    STEP_RESULT[7]="FAIL HTTP=$rc"; return 1
  fi
  local s7_status
  s7_status=$(jq -r '.status' "$f" 2>/dev/null || echo "")
  if [[ "$s7_status" != "done" ]]; then
    log_fail "[FAIL] Step7 .status='$s7_status' (expected 'done')" "$f"
    STEP_RESULT[7]="FAIL status=$s7_status"; return 1
  fi

  # 4 SQL effects
  local post_kbcfg post_binding post_owner s7_app_id_db
  post_kbcfg=$(psql_exec "SELECT count(*) FROM dify_external_kb_configs WHERE fastgpt_dataset_id='$DATASET_ID'")
  post_binding=$(psql_exec "SELECT count(*) FROM dify_app_kb_bindings WHERE dify_app_id='$DIFY_APP_ID'")
  post_owner=$(psql_exec "SELECT count(*) FROM app_owners WHERE app_id='$DIFY_APP_ID' AND owner_user_id='$EMPLOYEE_A_UUID'")
  s7_app_id_db=$(psql_exec "SELECT dify_app_id FROM personal_kb_applications WHERE id='$APPLICATION_ID'")

  if [[ "$post_kbcfg" -le "$pre_kbcfg" ]]; then
    log_fail "[FAIL] Step7 dify_external_kb_configs not incremented ($pre_kbcfg → $post_kbcfg)" "$f"
    STEP_RESULT[7]="FAIL kbcfg=$post_kbcfg"; return 1
  fi
  if [[ "$post_binding" -le "$pre_binding" ]]; then
    log_fail "[FAIL] Step7 dify_app_kb_bindings not incremented ($pre_binding → $post_binding)" "$f"
    STEP_RESULT[7]="FAIL binding=$post_binding"; return 1
  fi
  if [[ "$post_owner" -le "$pre_owner" ]]; then
    log_fail "[FAIL] Step7 app_owners not incremented for ($DIFY_APP_ID,$EMPLOYEE_A_UUID)" "$f"
    STEP_RESULT[7]="FAIL owner=$post_owner"; return 1
  fi
  if [[ "$s7_app_id_db" != "$DIFY_APP_ID" ]]; then
    log_fail "[FAIL] Step7 UPDATE personal_kb_applications.dify_app_id='$s7_app_id_db' (expected '$DIFY_APP_ID')" "$f"
    STEP_RESULT[7]="FAIL pkb.dify_app_id"; return 1
  fi
  STEP_RESULT[7]="PASS (done + 3 INSERT (kbcfg+binding+owner) + 1 UPDATE (pkb.status+dify_app_id))"
  log_ok "Step7: dispatch → done + 4 SQL effects verified"

  # ── Step 8: employee_a GET /apps → 含 dify_app_id（owner 路径命中）
  f="${PER_STEP_PREFIX}-step8.json"
  rc=$(http_status "$NCMU_BASE/api/v1/ncmu/apps" "GET" "$JWT_A" "" "$f")
  if [[ "$rc" != "200" ]]; then
    log_fail "[FAIL] Step8 GET /apps HTTP=$rc" "$f"
    STEP_RESULT[8]="FAIL HTTP=$rc"; return 1
  fi
  local s8_hit
  s8_hit=$(jq --arg id "$DIFY_APP_ID" '[.[]? | select(.id == $id or .dify_app_id == $id)] | length' "$f" 2>/dev/null || echo "0")
  if [[ "$s8_hit" -lt 1 ]]; then
    log_fail "[FAIL] Step8 dify_app_id=$DIFY_APP_ID not in /apps response (hit=$s8_hit)" "$f"
    STEP_RESULT[8]="FAIL not in list"; return 1
  fi
  STEP_RESULT[8]="PASS (owner path: dify_app_id in /apps)"
  log_ok "Step8: employee_a sees app via owner path"

  # ── Step 9: 员工 POST chat → 200 + 检索（如 embedding 就绪）
  # Plan 字面 `/v1/messages` — NCMU 真路径 `/api/v1/ncmu/chat/{app_id}`（[DONE] 说明）。
  f="${PER_STEP_PREFIX}-step9.json"
  rc=$(http_status "$NCMU_BASE/api/v1/ncmu/chat/$DIFY_APP_ID" "POST" "$JWT_A" \
    "$(jq -nc '{query:"请引用 NCMU PC4 personal KB test marker 文档原文"}')" "$f")
  if [[ "$rc" != "200" ]]; then
    log_fail "[FAIL] Step9 chat HTTP=$rc" "$f"
    STEP_RESULT[9]="FAIL HTTP=$rc"; return 1
  fi
  # 字段级断言：HTTP 200 + body 非空（含 SSE event 流 或 'answer' 字段）
  local s9_bytes
  s9_bytes=$(wc -c < "$f" | tr -d ' ')
  if [[ "$s9_bytes" -lt 50 ]]; then
    log_fail "[FAIL] Step9 response body too small ($s9_bytes bytes) — likely failed silently" "$f"
    STEP_RESULT[9]="FAIL body too small"; return 1
  fi
  STEP_RESULT[9]="PASS (HTTP=200 + body ${s9_bytes}B; semantic retrieval = informational not blocking)"
  log_ok "Step9: chat 200 + body ${s9_bytes}B"

  # ── Step 10: employee_a GET /kbs/{dify_app_id}/files → 字段级 5 字段全非空
  f="${PER_STEP_PREFIX}-step10.json"
  rc=$(http_status "$NCMU_BASE/api/v1/ncmu/kbs/$DIFY_APP_ID/files" "GET" "$JWT_A" "" "$f")
  if [[ "$rc" != "200" ]]; then
    log_fail "[FAIL] Step10 GET kbs/files HTTP=$rc" "$f"
    STEP_RESULT[10]="FAIL HTTP=$rc"; return 1
  fi
  local s10_len
  s10_len=$(jq 'length' "$f" 2>/dev/null || echo "0")
  if [[ "$s10_len" -lt 1 ]]; then
    log_fail "[FAIL] Step10 file list empty (expected ≥1; embedding may have failed)" "$f"
    STEP_RESULT[10]="FAIL empty list"; return 1
  fi
  # 字段级断言 5 字段全非空
  local s10_field_check
  s10_field_check=$(jq -r '
    .[0] |
    (.file_id != null and .file_id != "") and
    (.original_filename != null and .original_filename != "") and
    (.size_bytes != null) and
    (.uploaded_at != null and .uploaded_at != "") and
    (.content_type != null and .content_type != "")
  ' "$f" 2>/dev/null || echo "false")
  if [[ "$s10_field_check" != "true" ]]; then
    log_fail "[FAIL] Step10 字段级 5 字段非空检查 failed (.file_id/.original_filename/.size_bytes/.uploaded_at/.content_type)" "$f"
    STEP_RESULT[10]="FAIL field check"; return 1
  fi
  local s10_file_id
  s10_file_id=$(jq -r '.[0].file_id // empty' "$f" 2>/dev/null || echo "")
  STEP_RESULT[10]="PASS (len=$s10_len + 5 字段全非空; first file_id=$s10_file_id)"
  log_ok "Step10: kbs/files len=$s10_len; 5 字段断言 PASS"

  # ── Step 11: employee_a GET /kbs/{app_id}/files/{file_id}/download → byte-exact sha256
  local dl11="${PER_STEP_PREFIX}-step11-download.bin"
  rc=$(curl -s -o "$dl11" -w "%{http_code}" --max-time 60 \
    -H "Authorization: Bearer $JWT_A" \
    "$NCMU_BASE/api/v1/ncmu/kbs/$DIFY_APP_ID/files/$s10_file_id/download" \
    2>/dev/null || echo "000")
  if [[ "$rc" != "200" ]]; then
    log_fail "[FAIL] Step11 employee download HTTP=$rc" "$dl11"
    STEP_RESULT[11]="FAIL HTTP=$rc"; return 1
  fi
  local s11_sha
  s11_sha=$(sha256sum "$dl11" | awk '{print $1}')
  if [[ "$s11_sha" != "$GOLDEN_SHA256" ]]; then
    log_fail "[FAIL] Step11 sha256 mismatch dl=$s11_sha vs golden=$GOLDEN_SHA256 (end-to-end byte-exact failed)" "$dl11"
    STEP_RESULT[11]="FAIL sha256"; return 1
  fi
  STEP_RESULT[11]="PASS (byte-exact end-to-end: upload sha256 == FastGPT download sha256)"
  log_ok "Step11: end-to-end byte-exact verified"

  log_ok "Golden Path: 11/11 PASS"
}

# ─── 异常分支 6 个（AC#3 / Q10-B 矩阵）─────────────────────────────────────
anti_paths() {
  log_step "Anti-paths (6 branches, field-level assertions)"
  local JWT_A JWT_B
  JWT_A=$(dev_login "$EMPLOYEE_A_UUID") || return 1
  JWT_B=$(dev_login "$EMPLOYEE_B_UUID") || return 1

  # ── a. FastGPT 停 → 员工 GET /kbs/.../files → 503 + 字面 "知识库服务暂时不可用"
  local f rc detail
  if [[ -z "$DIFY_APP_ID" ]]; then
    log_warn "anti-a: skip (golden path Step6 未成功 / DIFY_APP_ID 空)"
    ANTI_RESULT[a]="SKIP (no DIFY_APP_ID)"
  else
    log_info "anti-a: docker stop $FASTGPT_CONTAINER"
    if ! docker stop "$FASTGPT_CONTAINER" >/dev/null 2>&1; then
      log_warn "anti-a: docker stop failed; skip"
      ANTI_RESULT[a]="SKIP (docker stop failed)"
    else
      FASTGPT_STOPPED=1
      # REWORK-PC4-INDEP I3: httpx AsyncClient 默认 keepalive_expiry=5.0s。
      # sleep 2 后 pool 仍可能持半闭连接 → 首请求 RemoteProtocolError → 路由抛 500 != 503
      # → anti-a 误判 FAIL。sleep 5 与 keepalive_expiry 对齐，确保连接已 expire。
      sleep 5
      f="${PER_STEP_PREFIX}-anti-a.json"
      rc=$(http_status "$NCMU_BASE/api/v1/ncmu/kbs/$DIFY_APP_ID/files" "GET" "$JWT_A" "" "$f")
      detail=$(jq -r '.detail // empty' "$f" 2>/dev/null || true)
      if [[ "$rc" == "503" ]] && grep -q '知识库服务暂时不可用' "$f"; then
        ANTI_RESULT[a]="PASS (HTTP=503 + 字面 '知识库服务暂时不可用')"
        log_ok "anti-a: HTTP=503 + detail 含字面 '知识库服务暂时不可用'"
      else
        log_fail "[FAIL] anti-a: HTTP=$rc detail='$detail'" "$f"
        ANTI_RESULT[a]="FAIL HTTP=$rc detail=$detail"
      fi
      # 立即恢复 fastgpt（防后续 step 受影响）
      docker start "$FASTGPT_CONTAINER" >/dev/null 2>&1 || log_warn "anti-a: docker start failed (cleanup trap 兜底)"
      FASTGPT_STOPPED=0
      log_info "anti-a: fastgpt-app restarted (in-band)"
    fi
  fi

  # ── b. >50MB → 422 + 字面包含 "50 MB"
  f="${PER_STEP_PREFIX}-anti-b.json"
  rc=$(http_status_multipart \
    "$NCMU_BASE/api/v1/ncmu/personal-kb/applications" "$JWT_A" "$f" \
    "files=@$PC4_TMP_DIR/bigfile_51mb.pdf;type=application/pdf")
  detail=$(jq -r '.detail // empty' "$f" 2>/dev/null || true)
  if [[ "$rc" == "422" ]] && grep -q '50 MB' "$f"; then
    ANTI_RESULT[b]="PASS (HTTP=422 + 字面包含 '50 MB')"
    log_ok "anti-b: 51MB upload → 422 + '50 MB' detail"
  else
    log_fail "[FAIL] anti-b: HTTP=$rc detail='$detail'" "$f"
    ANTI_RESULT[b]="FAIL HTTP=$rc"
  fi

  # ── c. >10 文件 → 422 + 字面包含 "10 个文件"
  f="${PER_STEP_PREFIX}-anti-c.json"
  local form_args=()
  local i
  for i in $(seq 0 10); do
    form_args+=("files=@$PC4_TMP_DIR/count_overflow_${i}.txt;type=text/plain")
  done
  rc=$(http_status_multipart \
    "$NCMU_BASE/api/v1/ncmu/personal-kb/applications" "$JWT_A" "$f" "${form_args[@]}")
  detail=$(jq -r '.detail // empty' "$f" 2>/dev/null || true)
  if [[ "$rc" == "422" ]] && grep -q '10 个文件' "$f"; then
    ANTI_RESULT[c]="PASS (HTTP=422 + 字面包含 '10 个文件')"
    log_ok "anti-c: 11 file upload → 422 + '10 个文件' detail"
  else
    log_fail "[FAIL] anti-c: HTTP=$rc detail='$detail'" "$f"
    ANTI_RESULT[c]="FAIL HTTP=$rc"
  fi

  # ── d. .exe → 422 + 字面包含 "不支持的文件格式"
  f="${PER_STEP_PREFIX}-anti-d.json"
  rc=$(http_status_multipart \
    "$NCMU_BASE/api/v1/ncmu/personal-kb/applications" "$JWT_A" "$f" \
    "files=@$PC4_TMP_DIR/forbidden.exe;type=application/octet-stream")
  detail=$(jq -r '.detail // empty' "$f" 2>/dev/null || true)
  if [[ "$rc" == "422" ]] && grep -q '不支持的文件格式' "$f"; then
    ANTI_RESULT[d]="PASS (HTTP=422 + 字面包含 '不支持的文件格式')"
    log_ok "anti-d: .exe upload → 422 + '不支持的文件格式' detail"
  else
    log_fail "[FAIL] anti-d: HTTP=$rc detail='$detail'" "$f"
    ANTI_RESULT[d]="FAIL HTTP=$rc"
  fi

  # ── e. feature flag false + employee_b 无 owned app → 200 + 仅 DifyConsole [KB] apps
  # tags_routing_enabled 默认 false（per PC2-D / 永久兜底）→ shared apps 不通过 tag 路径下发，
  # 仅 admin Dify Console 中标 'kb-qa' 的 [KB] apps 进 employee_b 的 /apps 列表。
  # 字段级断言：所有返回项 .name 必以 '[KB] ' 起头（或不含 DIFY_APP_ID 这种 owner 私有 app）。
  f="${PER_STEP_PREFIX}-anti-e.json"
  rc=$(http_status "$NCMU_BASE/api/v1/ncmu/apps" "GET" "$JWT_B" "" "$f")
  if [[ "$rc" != "200" ]]; then
    log_fail "[FAIL] anti-e: HTTP=$rc (expected 200)" "$f"
    ANTI_RESULT[e]="FAIL HTTP=$rc"
  else
    # 必须不含 owner 私有 app DIFY_APP_ID（owner=employee_a / employee_b 无权）
    local owner_leak
    owner_leak=$(jq --arg id "$DIFY_APP_ID" \
      '[.[]? | select(.id == $id or .dify_app_id == $id)] | length' "$f" 2>/dev/null || echo "0")
    if [[ "$owner_leak" -gt 0 ]]; then
      log_fail "[FAIL] anti-e: owner-private app $DIFY_APP_ID 泄漏给 employee_b (tags_routing_enabled false 仍命中 owner 路径)" "$f"
      ANTI_RESULT[e]="FAIL owner leak"
    else
      local list_total
      list_total=$(jq 'length' "$f" 2>/dev/null || echo "0")
      ANTI_RESULT[e]="PASS (HTTP=200 + owner-private app 0 leak; list_total=$list_total = 仅 console [KB] apps)"
      log_ok "anti-e: employee_b /apps 仅含 console [KB] apps; owner 私有 0 泄漏"
    fi
  fi

  # ── f. employee_b 越权查 employee_a 的 KB → 403 + 字面包含 "无权访问"
  f="${PER_STEP_PREFIX}-anti-f.json"
  if [[ -z "$DIFY_APP_ID" ]]; then
    ANTI_RESULT[f]="SKIP (no DIFY_APP_ID)"
    log_warn "anti-f: skip (no DIFY_APP_ID)"
  else
    rc=$(http_status "$NCMU_BASE/api/v1/ncmu/kbs/$DIFY_APP_ID/files" "GET" "$JWT_B" "" "$f")
    detail=$(jq -r '.detail // empty' "$f" 2>/dev/null || true)
    if [[ "$rc" == "403" ]] && grep -q '无权访问' "$f"; then
      ANTI_RESULT[f]="PASS (HTTP=403 + 字面包含 '无权访问')"
      log_ok "anti-f: employee_b → 403 + '无权访问' detail"
    else
      log_fail "[FAIL] anti-f: HTTP=$rc detail='$detail'" "$f"
      ANTI_RESULT[f]="FAIL HTTP=$rc"
    fi
  fi
}

# ─── Summary + exit code ────────────────────────────────────────────────
summary() {
  log_step "SUMMARY"
  local exit_rc=0
  local k
  for k in 1 2 3 4 5 6 7 8 9 10 11; do
    local v="${STEP_RESULT[$k]:-MISSING}"
    printf 'Step %2d: %s\n' "$k" "$v" | tee -a "$RESULT_LOG"
    [[ "$v" == PASS* ]] || exit_rc=1
  done
  for k in a b c d e f; do
    local v="${ANTI_RESULT[$k]:-MISSING}"
    printf 'Anti %s : %s\n' "$k" "$v" | tee -a "$RESULT_LOG"
    [[ "$v" == PASS* || "$v" == SKIP* ]] || exit_rc=1
  done
  log_info "exit code: $exit_rc"
  return "$exit_rc"
}

# ─── Main ───────────────────────────────────────────────────────────────
main() {
  log_info "RESULT_LOG=$RESULT_LOG"
  log_info "PC4_TMP_DIR=$PC4_TMP_DIR"
  preflight
  gen_fixtures
  golden_path
  anti_paths
  summary
}

main "$@"
