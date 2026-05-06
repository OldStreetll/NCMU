# Token Auto-Sync Runbook (TASK-42 / B-NEW-14)

> Audience: NCMU 开发/运维操作员 — dev 部署时 `.env` 含 `CHANGE_ME` 占位需自动同步真实 token 的场景。
> Status: active（Phase 2A H1 / 2026-05-06）。
> 关联：`scripts/ncmu_init.py` token-sync 模块；`.env.bootstrap.example`；NCMU-Wiki `reference/recover-dify-fastgpt-sop.md`。

## 1. 概述 / 何时触发

dev 部署时（`DEPLOY_PROFILE != "prod"`），ncmu-init 容器启动会先跑 token-sync 模块：

* 扫 `.env` 中 `PLACEHOLDER_TOKEN_KEYS`（7 个）值是否以 `CHANGE_ME` 起头
* 命中 → 读 `.env.bootstrap` 凭据 → 调 Dify Console API 拿真 token → 原子写回 `.env`

7 个 token key（`scripts/ncmu_init.py:109-117`）：

```python
PLACEHOLDER_TOKEN_KEYS = [
    "DIFY_APP_DEFAULT_TOKEN",
    "DIFY_CONSOLE_API_KEY",
    "FASTGPT_API_KEY",
    "FASTGPT_ROOT_KEY",
    "FASTGPT_TOKEN_KEY",
    "FASTGPT_FILE_TOKEN_KEY",
    "SILICONFLOW_API_KEY",  # errata-11 衔接 — manual sync only
]
```

**何时触发**：

* fresh 部署（首次 clone 仓库 + 首次 `scripts/start-dev.sh`），`.env.example` 复制为 `.env` 时 7 keys 仍为 `CHANGE_ME` 占位
* 中途强制刷新某 key（手动改 `.env` 的某行回 `CHANGE_ME` 后重跑 ncmu-init）

**不触发**：

* `DEPLOY_PROFILE=prod` —— prod 走 Vault/sops（见 §7）
* `--skip-token-sync` flag（CI / test 隔离）
* 7 keys 全部已是真实值（非 `CHANGE_ME` 起头）

跳过整段 token-sync 的命令例：

```bash
python scripts/ncmu_init.py --skip-token-sync
```

## 2. `.env.bootstrap` 配置

`.env.bootstrap` 是 token-sync 的凭据来源；**已 gitignored 不入仓**。模板在 `.env.bootstrap.example`（43 行带注释）。

### 2.1 复制模板 + 填值

```bash
cd /opt/ncmu          # 替换为你 clone 的 NCMU 仓库根目录
cp .env.bootstrap.example .env.bootstrap
chmod 600 .env.bootstrap   # 限制读权限（凭据文件）
```

然后编辑 `.env.bootstrap`，三类字段填实值：

```ini
# === Dify admin credentials ===
DIFY_ADMIN_EMAIL=admin@ncmu.local
DIFY_ADMIN_PASSWORD=Ncmu@E2E2026

# === FastGPT root credentials ===
FASTGPT_ADMIN_EMAIL=root
FASTGPT_ADMIN_PASSWORD=<your-fastgpt-root-password>

# === SiliconFlow embedding API key ===
SILICONFLOW_API_KEY=sk-<paste-from-siliconflow-console>
```

### 2.2 SILICONFLOW_API_KEY 手动获取流程

token-sync **不能自动取** `SILICONFLOW_API_KEY`（外部服务无 admin login API）。手动获取步骤：

1. 打开 https://siliconflow.cn 注册账号（手机号 / 邮箱均可）
2. 登录后进入「后台 → API Keys → 新建 API Key」（含免费额度）
3. 复制生成的 `sk-...` 字符串
4. 写入 `.env.bootstrap` 的 `SILICONFLOW_API_KEY=` 行
5. 同步**手动**写入 `.env` —— token-sync 命中此 key 时**仅打印 STDERR 引导**（见 §6 路径 1 + `scripts/ncmu_init.py:116-125`），不会从 `.env.bootstrap` 复制到 `.env`

### 2.3 FastGPT 凭据（占位）

`FASTGPT_ADMIN_EMAIL` / `FASTGPT_ADMIN_PASSWORD` 当前**仅作占位**（v4.14.x `team_api_keys` endpoint spike 留作 `REWORK-42-FASTGPT` 增量）。
4 个 FASTGPT_* token（`FASTGPT_API_KEY` / `FASTGPT_ROOT_KEY` / `FASTGPT_TOKEN_KEY` / `FASTGPT_FILE_TOKEN_KEY`）目前要求操作员**手动**写入 `.env`，token-sync 命中时仅 STDERR warn 不阻断（详见 §6 路径 3）。

## 3. admin credentials 来源（INDEP-PLAN-2 A4 修订）

`DIFY_ADMIN_EMAIL` / `DIFY_ADMIN_PASSWORD` = Dify Console **首次 setup 时**设置的凭据。

**来源依据**：[[recover-dify-fastgpt-sop]] §5.2（NCMU-Wiki `reference/recover-dify-fastgpt-sop.md`） — Dify v1.13.3 setup POST `/console/api/setup` 字面接收 plain password；后续 login POST `/console/api/login` 字面接收 base64-encoded password（Dify upstream `api/libs/encryption.py:17`）。`scripts/ncmu_init.py:207-243` 落地此约定（`base64.b64encode(password_plain.encode("utf-8")).decode("ascii")`）。

**fresh 部署场景**（无既有 Dify 数据库）：

1. 启 docker compose，让 dify-api 容器先 up：

   ```bash
   docker compose up -d dify-api
   ```

2. 浏览器访问 `http://<dev-host>:3000/install`（默认 dev host 端口 3000）
3. 表单填 admin email / password → 完成 setup（数据写入 dify-pg 容器）
4. 把同样的 email / password 写入 `.env.bootstrap`
5. 跑 ncmu-init → token-sync 用这对凭据 login，拿 access_token JWT + per-App api-key

**已有部署**（dify-pg 已含 admin row）：直接把已知凭据写入 `.env.bootstrap`，跳 web UI `/install`。

> ⚠️ 跳过 web UI `/install` 直接跑 token-sync → login 401 → STDERR warn `Dify Console login failed`（见 §6 路径 5）。

## 4. 同步流程（4 步）

`scripts/ncmu_init.py` 中 `run_token_sync_pre_init()` 在 ncmu-init 容器主流程**前**执行：

| # | 步骤 | 函数 | 关键调用 |
|---|------|------|----------|
| 1 | **detect** | `check_env_placeholders()` | 扫 `.env` 7 keys，返回 `value.startswith("CHANGE_ME")` 命中列表 |
| 2 | **login** | `_dify_login()` | POST `<DIFY_BASE_URL>/console/api/login` w/ `{email, password=base64(plain), language, remember_me}` → 返 `(cookies, access_token)` |
| 3 | **fetch** | `_dify_list_apps()` + `_dify_get_app_keys()` | (a) `DIFY_CONSOLE_API_KEY = access_token` JWT；(b) GET `/console/api/apps` 找 `[KB]` 前缀 App → GET `/console/api/apps/{app_id}/api-keys` → `data[0].token` 作 `DIFY_APP_DEFAULT_TOKEN` |
| 4 | **atomic-write** | `atomic_write_env()` | 写 `.env.tmp` → `os.replace(.env.tmp, .env)`（POSIX rename 原子）；保留所有未触行（注释 / 空行 / 其他 key）字节级一致 |

入口命令：

```bash
# 完整 ncmu-init（推荐 — 容器内跑）
docker compose run --rm ncmu-init

# 或 host 直接跑（venv 须装 requests + psycopg2）
python scripts/ncmu_init.py
```

`DIFY_BASE_URL` 默认值（`scripts/ncmu_init.py:319`）：容器内 `http://dify-api:5001`；host 跑须显式 set：

```bash
DIFY_BASE_URL=http://localhost:3000 python scripts/ncmu_init.py
```

## 5. 成功输出示例

bootstrap 已配置 + Dify 已 setup + `[KB]` App 已建（TASK-24 已落地）的成功 case：

```
$ python scripts/ncmu_init.py
[INFO] Detected token placeholders: ['DIFY_APP_DEFAULT_TOKEN', 'DIFY_CONSOLE_API_KEY']
✅ Tokens synced: ['DIFY_APP_DEFAULT_TOKEN', 'DIFY_CONSOLE_API_KEY']. Please restart ncmu-backend: docker compose restart ncmu-backend
$ echo $?
0
```

bootstrap 缺 + 4 FASTGPT_* 命中的 partial-friendly case（TASK-42 [DONE] §smoke 实测，commit `d92a47e`）：

```
$ python scripts/ncmu_init.py
[INFO] Detected token placeholders: ['FASTGPT_API_KEY', 'FASTGPT_ROOT_KEY', 'FASTGPT_TOKEN_KEY', 'FASTGPT_FILE_TOKEN_KEY']
[WARN] .env.bootstrap not found or empty; cannot sync tokens. Copy .env.bootstrap.example and fill credentials.
[ERROR] NCMU_DB_URL env var not set. Set it or load via .env.
$ echo $?
1
```

> 注：第二例 `exit 1` 来自 `bootstrap_tags()` 主流程因 `NCMU_DB_URL` 未配置而失败 —— **token-sync 本身未阻断**（无新写入即 return None 让主流程继续）。

行为矩阵（`run_token_sync_pre_init()` docstring + [REVIEW-42] §AC#4 字面）：

| 状态 | `.env` 是否被改 | 返回值 |
|------|----------------|--------|
| `DEPLOY_PROFILE=prod` | 否 | `None`（主流程继续） |
| 无占位 | 否 | `None` |
| sync 写 ≥1 token | 是 | `0`（clean exit） |
| sync 写 0 token（全失败） | 否 | `None` |
| atomic-write OSError | 否（tmp 已清） | `1`（error exit） |

## 6. 失败排查（5 路径）

### 6.1 路径 1：`.env.bootstrap` 缺 / 空

**症状**：

```
[WARN] .env.bootstrap not found or empty; cannot sync tokens. Copy .env.bootstrap.example and fill credentials.
```

**`.env` 状态**：未变（`git diff .env` = 0；`run_token_sync_pre_init()` 返 `None` 让主流程继续）。

**修复**：

```bash
cp .env.bootstrap.example .env.bootstrap
# 编辑 .env.bootstrap 填实凭据（参 §2.1）
chmod 600 .env.bootstrap
python scripts/ncmu_init.py
```

> SILICONFLOW_API_KEY 单独 case：即便 `.env.bootstrap` 已配置，`scripts/ncmu_init.py:302-304` 检测到此 key 后只打印 STDERR `[WARN] SILICONFLOW_API_KEY cannot be auto-synced; please obtain an API Key at https://siliconflow.cn ...`，仍要求操作员手动写到 `.env`。

### 6.2 路径 2：Dify login 失败

**症状**：

```
[WARN] Dify Console login failed; skipping Dify key sync.
```

**`.env` 状态**：Dify 部分全跳；FastGPT/SILICONFLOW 仍按各自规则处置（partial-write 友好）。

**常见根因 + 修复**：

| 根因 | 检测 | 修复 |
|------|------|------|
| Dify 容器未 up | `docker compose ps dify-api` 无 `healthy` | `docker compose up -d dify-api` |
| `DIFY_BASE_URL` 错（host vs 容器内） | 容器内默认 `http://dify-api:5001`；host 跑须 `DIFY_BASE_URL=http://localhost:3000` | 设 env var 后重跑 |
| 凭据错 | curl 手动 login 见返 401 | 修 `.env.bootstrap` |
| Dify 未 setup（fresh 部署） | 见路径 5 | 见路径 5 |

curl 手动 login 验证（host 跑示例）：

```bash
PW_B64=$(printf '%s' 'Ncmu@E2E2026' | base64)
curl -sf -X POST http://localhost:3000/console/api/login \
     -H 'Content-Type: application/json' \
     -d "{\"email\":\"admin@ncmu.local\",\"password\":\"${PW_B64}\",\"language\":\"en-US\",\"remember_me\":true}"
```

返 `200` + body 含 `data.access_token` → 凭据 OK；`401` / `403` → 凭据错或 Dify 未 setup（路径 5）。

### 6.3 路径 3：FastGPT API 失败（4 keys 全 pending）

**症状**（`scripts/ncmu_init.py:382-388`）：

```
[WARN] FastGPT auto-sync not implemented (v4.14.x team_api_keys endpoint pending spike); placeholders remain: ['FASTGPT_API_KEY', 'FASTGPT_ROOT_KEY', 'FASTGPT_TOKEN_KEY', 'FASTGPT_FILE_TOKEN_KEY']. Operator must seed these directly in .env until follow-up lands.
```

**`.env` 状态**：FastGPT 4 keys **不动**；本轮 Dify 已成功的 keys **仍写入**（partial-write 友好，不阻断）。

**修复**（手动 seed 4 keys）：

1. FastGPT Console（默认 `http://localhost:3001`）登录 root 账号
2. 导航「账号 → API 密钥 → 新建」生成 `fastgpt-...` token
3. 写入 `.env`：

   ```ini
   FASTGPT_API_KEY=fastgpt-<paste>
   FASTGPT_ROOT_KEY=<see FastGPT docker-compose.yml CONFIG_JSON>
   FASTGPT_TOKEN_KEY=<同上>
   FASTGPT_FILE_TOKEN_KEY=<同上>
   ```

4. `docker compose restart ncmu-backend`

> 进度：`REWORK-42-FASTGPT` 已立 backlog，spike v4.14.x `team_api_keys` endpoint 后会自动化此路径；届时本节将由 runbook 增量更新。

### 6.4 路径 4：atomic write 失败（OSError）

**症状**：

```
[ERROR] atomic write to .env failed: <OSError detail>; .env unchanged.
$ echo $?
1
```

**`.env` 状态**：完整未变 —— `os.replace` 失败前 `.env.tmp` 已清（`scripts/ncmu_init.py:445-455`），原 `.env` 字节级未触。

**常见根因**：

* `.env` 文件权限被改成 `0444` / 不可写 → `chmod 600 .env`
* 磁盘满 → `df -h .` 检查 → 清空间
* 9p/NTFS 挂载异常（WSL2 偶发 `EBUSY`）→ `wsl --shutdown` 后重启 WSL 再跑

修复后重跑：

```bash
python scripts/ncmu_init.py
```

### 6.5 路径 5：Dify 未 setup（fresh 部署 web UI 跳过）

**症状**：login POST 返 `401` 或 `data.access_token` 缺失 → 表现为路径 2 的 warn。**根因不同** —— Dify admin row 还没建，任何凭据都 login 不进。

**检测 setup 状态**：

```bash
curl -sf http://localhost:3000/console/api/setup
# 返 {"step":"finished"}    = 已 setup
# 返 {"step":"not_started"} = 未 setup（即此路径根因）
```

**修复**：

1. 浏览器访问 `http://localhost:3000/install`
2. 表单填 admin email / password（**务必与 `.env.bootstrap` 一致**）
3. 完成后重跑：

   ```bash
   python scripts/ncmu_init.py
   ```

> 关联：[[recover-dify-fastgpt-sop]] §5.2（NCMU-Wiki `reference/recover-dify-fastgpt-sop.md`）含 setup → login → keys 完整 21 步重建 SOP。

## 7. prod 升级路径占位（Vault / sops）

当前 `.env.bootstrap` + `.env` 方案是 **dev-only**：凭据明文落盘 + 9p/NTFS 文件原子重命名 + 单进程假设。prod 须替换为外部 secrets store。

**升级方向**（Phase X 预留 — `scripts/ncmu_init.py` 4 处 `Phase X TODO` 标记对应：line 190 / 295 / 409 / 476）：

* **HashiCorp Vault**：ncmu-init 从 Vault KV v2 拉 `secret/data/ncmu/dify`、`secret/data/ncmu/fastgpt` 等 path → 注入容器 env，**不**落 `.env`
* **Mozilla sops + age**：`.env.sops.yaml` 入仓（age 加密），ncmu-init 启动时 `sops -d` 解到内存 → 注入 env
* **K8s Secret + projected volume**：容器 `/var/run/secrets/ncmu/*` 直接挂载，ncmu-init 改读路径

切换信号：`DEPLOY_PROFILE=prod` → `run_token_sync_pre_init()` 早返 `None` 跳过整段 dev 逻辑（`scripts/ncmu_init.py:478-480`）：

```python
profile = _read_deploy_profile()
if profile == "prod":
    return None
```

prod 切换时**需移除**：

* `.env.bootstrap.example`（dev 模板）
* `.gitignore` 的 `.env.bootstrap` 行
* `scripts/ncmu_init.py` token-sync sync 实现段（保留 detect 用于诊断，替换 sync 为 secret loader）

并**新增**：`scripts/secret_loader_<vault|sops|k8s>.py` 注入 env（不写盘）。

> 关联：errata-09-10-11 §3 衔接清单（NCMU-Wiki `sources/errata-09-10-11.md`） — 列 Phase 2D+ 凭据治理升级清单。

## 8. 团队挂载 cron 自动备份（可选）

> Boss 2026-04-30 Q-C 答案后追加 — 自己技术团队运维场景；**本期 NCMU 默认不挂 cron**。

### 8.1 何时启用

默认手动跑：

```bash
./scripts/backup.sh
```

**何时改挂 cron**：

* 团队多人轮值，无固定操作人
* Boss 不在场的连续多天，怕忘备份
* 部署在长期运行的 dev/staging host

不属上述情形 → 保持手动模式（守 dev 不依赖 daemon 原则）。

### 8.2 crontab 配置示例

`crontab -e` 加入：

```cron
# 每天凌晨 2:30 跑全量备份（4 dump，~60s）
30 2 * * * cd /opt/ncmu && ./scripts/backup.sh >> ./data/backups/cron.log 2>&1
```

字段：分 时 日 月 周 + 命令。`/opt/ncmu` 替换为实际仓库路径；`>> cron.log 2>&1` 把 stdout/stderr 累加到日志。

### 8.3 注意事项

* **服务必须在线**：cron 跑 `backup.sh` 时需 4 容器（pg-ncmu / pg-dify / pg-fastgpt / fastgpt-mongo）`up`；服务全停时 cron 跑空 dump（`manifest.json` 文件大小 ~0），不影响正确性但下次 restore 找不到数据
* **磁盘**：4 dump 平均每天约 50MB；30 天积累约 1.5GB
* **权限**：cron 默认以当前用户跑；若以 root 跑须保证 docker 组身份相同（否则 `docker compose ...` 会因 socket 权限失败）
* **日志**：

  ```bash
  tail -f data/backups/cron.log
  ```

  脚本本身已 `set -eEuo pipefail` + ERR trap，失败有明确 STDERR

### 8.4 回退手动模式

```bash
crontab -e
# 删除 backup.sh 一行后保存
```

`scripts/backup.sh` 本身手动跑不变，无需改脚本。

### 8.5 不强制启用

本期 NCMU 默认**不挂** cron —— 守 dev 不依赖 daemon 原则。仅作团队运维选项写入此 runbook，操作员按需开启。

## 9. 参考

* `scripts/ncmu_init.py` token-sync 模块（line 97-509）
* `.env.bootstrap.example` — 43 行模板含三类字段注释
* `docs/runbooks/fastgpt-tag-tracking.md` — 同档运维 runbook 风格参考
* `docs/runbooks/backup-restore-runbook.md` — backup.sh / restore.sh 操作手册（TASK-40）
* NCMU-Wiki `reference/recover-dify-fastgpt-sop.md`（wikilink: [[recover-dify-fastgpt-sop]]） — Dify+FastGPT+kb-adapter+backend 21 步重建 SOP（含 §5.2 setup ↔ login 凭据与 base64 编码字面证据）
* NCMU-Wiki `sources/phase2a-hardening-spec.md` — Phase 2A Hardening 4 项必闭环
* NCMU-Wiki `sources/errata-09-10-11.md` — Phase 2D+ 凭据治理升级清单（§3 衔接）
* TASK-42 `[DONE]` 摘要（commit `d92a47e`）含 smoke test 真实输出
* `[REVIEW-42] PASS WITH COMMENTS` 含 5 路径行为矩阵 + 9 项关注实证
