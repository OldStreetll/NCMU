# FastGPT v4.14 `/api/core/dataset/create` 403 / unAuthorization 调研报告

**Task**: TASK-FASTGPT-403 (Pane 2 / 调研类 / 0 行代码)
**Worktree**: `NCMU-fastgpt-research` @ `followup/fastgpt-403` 起点 `c330e30`
**Date**: 2026-05-25
**Trigger**: PC4 runtime smoke Step 5 — `POST $FASTGPT_BASE/api/core/dataset/create` 返 500 / body `{"code": 403, "statusText": "unAuthorization"}`

> 守 `feedback_evidence_first_cross_phase_universal`：每条结论后挂 `file:line` 或 命令输出。
> 守 `feedback_prompt_field_name_schema_grounding`：提到的 env var / API path / 字段名都已 grep 真代码验。
> 守 `feedback_three_component_separation_test_discipline`：用 Boss/用户口吻陈述发现，不替 FastGPT 官方文档说话；外推处显式标"未实证"。

---

## 0. 任务字面 vs 实证路径校正（evidence-first 起手）

任务模板字面路径与 worktree 真实路径偏差 3 处，逐条记录避免歧义：

| 任务字面 | 实证真值 | 证据 |
|---|---|---|
| `backend/app/integrations/fastgpt_readonly/client.py` | `ncmu-backend/src/ncmu_backend/fastgpt_readonly/client.py` | `find` 全 worktree 仅此 1 个 `client.py` 在 `fastgpt_readonly/` |
| `client.py:13` 注释 "知识库 API key vs 团队 API key" | `client.py:8-9` 注释字面 = `"Auth: Authorization: Bearer {FASTGPT_API_KEY} (plan AC#2 / reuses the same env var as kb-adapter — baseline §3.1 doesn't grant a new key)"`；**无** "知识库 vs 团队" 字面字 | 读 `client.py:1-23` 全段 |
| `scripts/e2e_personal_kb.sh:307-318` 是 Step 5 | 真值 `546-567`；307-318 是 preflight 第 4 项 `FASTGPT_API_KEY` 双源注入检（`docker exec` 字面验 backend / kb-adapter 容器 env） | 读 `e2e_personal_kb.sh:305-321` + `544-567` |

**结论**：任务字面 "client.py:13 注释提示存在 '知识库 API key vs 团队 API key' scope 差异" 与真代码不符。client.py 注释只提"reuses same env var as kb-adapter"（即同一 key 给两个消费者），**没有**明确区分 scope 类型。task 立项时的"scope 差异"提示属推断不是注释引用。这一发现本身即调研重点 — 当前代码 **0 处**显式区分 FastGPT key scope，整套系统只用 1 个 `FASTGPT_API_KEY` env var。

---

## 1. A：FastGPT v4.14 `/api/core/dataset/create` 真实 auth scope

### 1.1 已实证 FastGPT 端口的 auth 模式

FastGPT v4.14.10.2（`.env.example:411` `FASTGPT_IMAGE_TAG=v4.14.10.2`；本地镜像 `docker images` 返 `ghcr.io/labring/fastgpt:v4.14.10.2`）至少存在 **2 个独立 auth surface**：

| Auth Surface | Header 字面 | 端点示例 | 当前 NCMU 真代码使用 |
|---|---|---|---|
| **rootkey 全局管理** | `rootkey: <FASTGPT_ROOT_KEY>` | `/api/admin/initv4820`（模型批量激活） | `scripts/ncmu_init.py:842-843` `headers = {"rootkey": fastgpt_root_token, ...}` |
| **Bearer "用户/团队/资源" API key** | `Authorization: Bearer <FASTGPT_API_KEY>` | `/api/core/dataset/*`（list / create / delete / collection/list / collection/create/localFile） | `client.py:78` `self._headers = {"Authorization": f"Bearer {api_key}"}` + `e2e_personal_kb.sh:552 / 573 / 589` |

`rootkey` 体系证据链：
- `ncmu_init.py:74` 注释 `the rootkey: header`
- `ncmu_init.py:77` 注释 `/api/admin/initv4820 is the rootkey-protected mass-activation route`
- `ncmu_init.py:94` `FASTGPT_MASS_ACTIVATION_PATH = "/api/admin/initv4820"`
- `ncmu_init.py:843` `headers = {"rootkey": fastgpt_root_token, "Content-Type": "application/json"}`
- `.env.example:242-245` `FASTGPT_ROOT_KEY: dual-use — seeds the FastGPT container's ROOT_KEY env on first boot AND is read by scripts/ncmu_init.py bootstrap_fastgpt() as the rootkey: header value`

Bearer 体系证据链：
- `.env.example:210-213` `FASTGPT_API_KEY is generated in FastGPT admin and is INDEPENDENT of the keys placed in KB_ADAPTER_ALLOWED_KEYS`（明确两件事：(1) FastGPT 后台生成；(2) 跟 Dify→kb-adapter 的 key 互不相干）
- `client.py:7-9` `Auth: Authorization: Bearer {FASTGPT_API_KEY} (plan AC#2 / reuses the same env var as kb-adapter — baseline §3.1 doesn't grant a new key)`
- `errors.py:31` `"Upstream returned 401/403 — FASTGPT_API_KEY misconfigured."`
- `errors.py:55` `log.error("FastGPT 401/403 — check FASTGPT_API_KEY configuration: %s", exc)`

### 1.2 FastGPT v4.14 Bearer key 已知 3 个 sub-scope（**外推 / 未实证**）

> 以下分类基于 FastGPT 项目开源文档常识（labring/FastGPT 仓库 README / docs / 社区文档常见叙述）+ NCMU 内部 `ncmu_init.py:823-833` 真代码对 `team_subscriptions` collection 写 `teamId: "root", currentSubLevel: "free"` 的 evidence 反推；**没有**直接读 FastGPT v4.14.10.2 容器内 source（受 CLAUDE.md §11 dev pane 不允许直接 docker exec 约束 / 任务 plan §"注意事项" 字面）。

FastGPT 后台 UI 通常存在以下 key 类别（FastGPT v4.x 项目常识 / 需 Boss 在浏览器登录 `http://localhost:3000` 进 FastGPT admin 字面验）：

| Sub-scope | UI 入口（推断） | 权限范围（推断） | 能否调 `/api/core/dataset/create` |
|---|---|---|---|
| (a) **应用 API key** (App-bound) | 应用详情 → API 访问 → 创建 key | 仅对该应用 chat completion / 单 dataset 检索 | 否（无法在 team 根目录建 dataset） |
| (b) **团队 API key** (Team-wide) | 账号设置 → API 密钥 / 团队 API key | 整个 team 内 dataset / app CRUD | **是**（最可能修法） |
| (c) **知识库 API key** (Dataset-bound) | 单 dataset 详情 → API 访问 | 仅对该 dataset 的 collection CRUD | 否（dataset 必须先存在） |
| (d) **rootkey** | `.env` 直配 `FASTGPT_ROOT_KEY` / 无 UI | 全局 admin（migration / mass-activation） | 不通过 Bearer 头传，无对照 |

> ⚠️ 上表 4 行的 (a)/(b)/(c) **未在 NCMU 代码或文档中显式落地** — 整套 NCMU 系统当前只有 1 个 `FASTGPT_API_KEY` env var（grep 全 worktree 仅 `config.py:30,95` + `routes.py:58` + `client.py` + 多处 test/script），**没有** `FASTGPT_TEAM_API_KEY` / `FASTGPT_APP_API_KEY` 之类区分。

### 1.3 当前 PC4 smoke "Step 5 dataset/create 403" 最可能根因（推断）

**根因 hypothesis**：`.env` `FASTGPT_API_KEY` 是 FastGPT admin UI 生成的 **应用级或资源级 key (scope (a) 或 (c))**，**不是** team 根目录级 key (scope (b))。`/api/core/dataset/create` 是 team-scope 写操作（在 team 根目录建一个新 dataset），需要 team 级权限；当前 key 只有 app/dataset 内部权限，FastGPT auth middleware 直接返 `{code: 403, statusText: "unAuthorization"}`。

**支持证据**：
1. NCMU 历史 evidence：`ncmu_init.py:823-833` 直接 mongo 写 `team_subscriptions` collection `{teamId: "root", currentSubLevel: "free"}` 来"seed root team" — 这说明 FastGPT v4.14 有 team 概念，且 root team 是 NCMU 部署默认。
2. `client.py:17-22` 注释承认 `v4.14.x has no dedicated public health endpoint`，作者用 `/api/core/dataset/collection/list?datasetId=__healthcheck__` 探活 + 接受 "任何非 404 + 非 connect-error" — 包括 auth 拒绝的 500+JSON — 都算 "alive"。**意味着 PC4 smoke 之前从未通过任何 FastGPT 真路径的 Bearer auth 验证**，只是验证了 FastGPT 容器在线。
3. Step 5 是整套 PC4 测试中**首个**真正需要 FastGPT auth 通过的请求（Step 1-4 全是 NCMU backend 内部 `/api/v1/ncmu/*` 路径，0 FastGPT 调用 — 见 `e2e_personal_kb.sh:430-541` 全段）。所以 4/11 PASS **不能**用来证明 read 路径 auth 通；只能证明 NCMU backend 4 步骤自身正确。

**反证 / 未排除的可能性**（守 evidence-first 不绝对化）：
- 反证 (i)：可能 `.env` 的 `FASTGPT_API_KEY` 值本身就是占位 `CHANGE_ME`（`.env.example:220` default = `CHANGE_ME` / `e2e_personal_kb.sh:309` preflight 字面检 `[[ -z "$key_in_backend" || "$key_in_backend" == "CHANGE_ME" ]]`）。**已被 preflight 排除** —— Step 5 能跑到说明 preflight 4 项已 PASS（含 KEY 注入双源非空 + 非 CHANGE_ME），所以 KEY 是真值字符串。
- 反证 (ii)：可能 FastGPT v4.14 dataset/create 还需 body 里带 `teamId` 字段。e2e:554-555 字面 body = `{parentId:"", type:"dataset", name:$n, intro:"PC4 E2E", avatar:"/icon/logo.svg"}` —— **无 `teamId` 字段**。如果 FastGPT v4.14 把 teamId 从 header/cookie 推到 body required，那 422 应是 "missing field" 而非 403 unAuthorization；403 字面说"权限不足"而非"参数缺失"，所以这条反证可能性较低，但**未排除**。
- 反证 (iii)：可能 FastGPT v4.14 还要求 `x-team-id` 或 cookie `fastgpt_token` 这类辅助 header。e2e:551-555 字面只发 `Authorization: Bearer` + `Content-Type: application/json`，**无**其他 header。

### 1.4 A 段结论

- **已实证**：FastGPT v4.14 至少 2 个 auth surface（rootkey / Bearer）；当前 NCMU 整套系统只配 1 个 Bearer key。
- **强 hypothesis**（未实证）：`/api/core/dataset/create` 需 team-wide Bearer key；当前 `.env` 配的是更窄 scope 的 key。
- **必须 Boss 端在 FastGPT admin UI 字面验**：root 账号登录后看 (i) 当前 `FASTGPT_API_KEY` 字面值在哪个页面生成 / (ii) 该 key 的 permission 字段是什么。

---

## 2. B：当前 `FASTGPT_API_KEY` 实际 scope 实证

### 2.1 真代码引用点（grep 全 worktree）

| 文件 | 行号 | 用法 |
|---|---|---|
| `ncmu-backend/src/ncmu_backend/config.py` | 30, 95 | Pydantic Settings 字段定义 default=`"CHANGE_ME"` + REQUIRED env var 检 |
| `ncmu-backend/src/ncmu_backend/fastgpt_readonly/routes.py` | 58 | `api_key=settings.FASTGPT_API_KEY` 注入只读 client |
| `ncmu-backend/src/ncmu_backend/fastgpt_readonly/client.py` | 8, 78 | docstring + `Authorization: Bearer` 头构造 |
| `ncmu-backend/src/ncmu_backend/fastgpt_readonly/errors.py` | 31, 55 | 401/403 错误 handler 文案 |
| `scripts/e2e_personal_kb.sh` | 21, 166-170, 211, 305-321, 546-555, 572-575, 587-591 | e2e 测试中读 .env + 双源注入检 + Step 5 create + Step 5 upload + Step 5 polling |
| `scripts/ncmu_init.py` | — | 0 引用（init 用 ROOT_KEY 不用 API_KEY）|

**唯一消费者：fastgpt_readonly 只读 client + e2e Step 5 写路径 + e2e cleanup 删路径**。

### 2.2 .env 真值实证（仅描述特征，不写值）

`.env` 真位置 = `/mnt/d/Project/AIConsProject/NCMU_Proj/NCMU/.env`（主 worktree，AC#3 守"不写不 commit"，本任务 worktree 0 `.env` 文件）。任务模板字面"读 .env"在 worktree 隔离下需通过主 worktree 路径；本调研不实读真值（避免泄密），改用 evidence-first 推断：

- **若 .env 真值是 CHANGE_ME**：preflight `e2e_personal_kb.sh:309/314` 会直接 FAIL "FASTGPT_API_KEY not injected"。Step 5 能跑到 = preflight PASS = 真值非 CHANGE_ME 非空。
- **若 .env 真值是有效 Bearer key**：Step 5 的 500/code:403 不是 "key 字面错" 而是 "key scope 不足"。

### 2.3 client.py 注释"reuses same env var as kb-adapter"含义

`client.py:8-9` 字面 `(plan AC#2 / reuses the same env var as kb-adapter — baseline §3.1 doesn't grant a new key)` —— 这指 NCMU baseline §3.1（NCMU-Wiki 历史 plan）**没有**为 fastgpt_readonly 单独再开一个新 env var，复用 kb-adapter 已有的同一个 `FASTGPT_API_KEY`。

**关键含义**：原 baseline 设计假设 = "kb-adapter 用什么 key 读 FastGPT，fastgpt_readonly 就用同一个 key 读"。这套假设对 **read** 路径成立（只读 list/detail）；但 e2e Step 5 把同一个 key 用到 **write** 路径（dataset/create）— 这是 **NCMU baseline 设计未覆盖的 use case**。kb-adapter 本身不建 dataset（它只把 Dify→FastGPT 的查询转译），所以 baseline 没考虑写权限。

### 2.4 B 段结论

- **已实证**：当前 `FASTGPT_API_KEY` 全 NCMU 只 1 个 env var，5 个真代码消费点（含 e2e 写路径）。
- **已实证**：NCMU baseline §3.1（client.py 注释字面引用）原设计假设是只读复用 kb-adapter 同 key，**没考虑 dataset/create 写场景**。
- **未实证**（需 Boss 在 FastGPT UI 查）：该 key 字面真值在 FastGPT admin 哪个页面创建？key 详情里的 "permission" / "scope" 字段是什么？

---

## 3. C：FastGPT v4.14 key 获取流程（推断 / 需 Boss 实操确认）

### 3.1 推断流程（基于 FastGPT v4.x 项目常识 / 未实证 v4.14.10.2 UI 字面）

需 Boss 浏览器登录 FastGPT root 账号（`http://localhost:3000` / 用户名见 `.env.example:246` `FASTGPT_DEFAULT_ROOT_PSW` 配对的 root 账号）：

| 步骤 | 操作 | 目的 |
|---|---|---|
| 1 | 浏览器登录 `http://localhost:3000` 用 root 账号 | 进 FastGPT admin |
| 2 | 进 "账号设置" / "Account" 页面 | 找 team-wide API key 入口 |
| 3 | 找 "API 密钥" / "团队 API key" sub-menu | 区分 (b) team key 而非 (a) 应用 key |
| 4 | 创建新 key，**勾选最大权限范围**（如有 permission 多选） | 保 team-level 写权限 |
| 5 | 复制 key 字面值 | 待替换 `.env` 真值 |
| 6 | （可选）查现有 `FASTGPT_API_KEY` 真值字符串是否在 key 列表里 / 显示什么 scope | 字面确认现 key scope |

### 3.2 Boss 字面验需要做的事（按优先级）

- **必做**（无此即无法确诊）：步骤 6 — 拿现 `FASTGPT_API_KEY` 字面值对照 FastGPT admin "API 密钥列表"，**截图给 Pane 0** 看真实 scope 标签。
- **如根因确认是 scope 不够**：步骤 1-5 创建新 team key。
- **如根因不是 scope**（如 (ii) teamId body 字段 / (iii) 辅助 header）：需进一步定位（见 §4）。

### 3.3 容器内查 source 路径（受任务限制不实操）

任务"注意事项"明确"若 FastGPT 容器内查文档需 Boss 帮忙 docker exec / 不允许 dev pane 直接 docker exec"。如 Boss 愿意支持，可执行（建议在另一 pane）：

```bash
# 进 FastGPT 容器看 v4.14.10.2 真实 dataset/create 路由实现
docker exec -it ncmu-fastgpt-app sh -c 'find / -name "create.ts" -path "*dataset*" 2>/dev/null | head'
docker exec -it ncmu-fastgpt-app sh -c 'find / -name "*.ts" -path "*api/core/dataset*" 2>/dev/null | head'
# 看 auth middleware 真实校验逻辑
docker exec -it ncmu-fastgpt-app sh -c 'find / -name "auth*.ts" 2>/dev/null | head'
```

实际镜像路径需 Boss 进容器探索（FastGPT 是 Node.js / Next.js / `/app` 通常是根）。

---

## 4. D：替代方案探索（4 路径 / Boss 决策）

### 路径 1（推荐 / 最对齐 baseline）：换 team-level Bearer key

**做法**：Boss 按 §3.1 流程在 FastGPT admin 创建 team-wide API key，替换 `.env` `FASTGPT_API_KEY` 真值，`docker compose up -d` force recreate ncmu-backend 和 e2e 用 host shell（守 `feedback_docker_compose_restart_no_env_reload` —— 不允许 `restart`，必须 `up -d` 触发 env 重载）。

**改动量**：0 行代码 / 1 行 `.env` 改 / 1 次 docker compose up -d。
**风险**：低 — 现有 `client.py` + `routes.py` + `e2e_personal_kb.sh` 全部复用 `FASTGPT_API_KEY` 同一 env var，换值即生效。
**前提**：FastGPT admin UI 真存在 team-wide key 创建入口（§3.1 步骤 2-3 — 未实证）。
**验证**：跑 `bash scripts/e2e_personal_kb.sh`，Step 5 期望返 HTTP 200 + `.data._id` 非空 MongoDB ObjectId。

### 路径 2（备选 / 工艺侵入小但分层不优雅）：dataset/create 用 rootkey 头

**做法**：改 `e2e_personal_kb.sh:551-555` Step 5 改用 `rootkey: $FASTGPT_ROOT_KEY` 头（不用 Authorization Bearer）；同改 Step 5 upload + polling + cleanup (delete) — 全部 4 处 FastGPT 写/读调用切 rootkey。

**改动量**：~12-15 行 e2e 脚本改 / `.env` 已有 `FASTGPT_ROOT_KEY`（line 245）无需新增。
**风险**：中 — (i) 假设 FastGPT v4.14 `/api/core/dataset/*` 接受 `rootkey:` 头（未实证 / `ncmu_init.py` 只证明 `/api/admin/*` 接受 rootkey 头）；(ii) rootkey 是全局 admin 等于"用超级权限做日常操作"反 least-privilege；(iii) 不解决 production runtime（backend client.py 不可能跑 root key）。
**适用**：仅 e2e 测试场景临时绕过 — 不应进 prod 链路。

### 路径 3（代码层 fallback / scope adapter）

**做法**：在 `fastgpt_readonly` 旁建 `fastgpt_admin` 新模块，注入新 env var `FASTGPT_ADMIN_API_KEY`，专供写操作。`client.py` 保持 read-only 设计不动（守 `client.py:3-6` hard-coded GET grep self-check）。

**改动量**：新 module ~80 行 + config.py 加 1 field + .env.example 加 1 var + 调用方改 1-2 处。
**风险**：高 — (i) 没解决 "key 字面是谁" 问题（还是要 Boss 在 FastGPT admin 生成 team key）；(ii) 引入新 env var 切割 read/write 但工艺复杂；(iii) 与 baseline §3.1 "复用同一 key" 假设冲突。
**适用**：长期演进；不适合解 PC4 smoke 当下问题。

### 路径 4（最便宜 / deprecate Step 5）

**做法**：标 PC4 smoke Step 5-11 全是 "FastGPT 集成 follow-up 待 FASTGPT-403 修后启用"，把 4/11 PASS 改成 "4/4 NCMU backend AC + 7 FastGPT-blocked"。当前 PC4 baseline 已是 follow-up backlog（per task 字面"Step 5 FastGPT 入 follow-up backlog"），实际工艺等价。

**改动量**：0 行代码 / 文档更新 1-2 处。
**风险**：低 — 但延后 FastGPT 真路径 e2e 覆盖，不解决问题只承认。
**适用**：仅 Boss 决定 PC4 follow-up 优先级低于其他时。

### 4.x D 段推荐

**推荐顺序**：路径 1 > 路径 4 > 路径 2 > 路径 3。

**Pane 2 主推**：**路径 1（换 team Bearer key）**。理由：
- 改动最小（1 行 .env），与 baseline 设计假设最对齐
- 解决根因（key scope 不足）而非绕过
- 同 key 同时修 read + write 两条路径（虽然 read 路径目前没被 e2e 真验证，但生产 backend client.py:114 list_collections 真触发时需要的也是同样的 read 权限）
- **前提是 FastGPT admin UI 字面存在 team key 入口**——这点必须 Boss 先做 §3.1 步骤 6 实操确认。如不存在，回退路径 2 临时解 + 路径 3 长期解。

---

## 5. 调研空白 / Pane 2 没做到的事（诚实声明）

守 `feedback_evidence_first_cross_phase_universal`：

1. **未读 FastGPT v4.14.10.2 容器内 source**：dev pane 受 CLAUDE.md §11 + 任务"注意事项"约束不许 `docker exec`。所有 "FastGPT 端" 的 scope 区分（§1.2 / §3.1）属推断，需 Boss docker exec 或浏览器 UI 实操验。
2. **未读 .env 真值**：避免泄密 + worktree 隔离下不在本 worktree。
3. **未跑真 curl 验**：脚本里的 `/api/core/dataset/create` 字面 body / header 真实在 FastGPT v4.14.10.2 上的响应，没有再次 reproduce — 完全依赖 PC4 smoke 的"500/code:403" 单点报告。如 Boss 需要二次实证，可在另一 pane 跑：
   ```bash
   # 真 reproduce（用 docker exec 或 host shell 都行）
   curl -v -X POST http://localhost:3000/api/core/dataset/create \
     -H "Authorization: Bearer <真 key>" \
     -H "Content-Type: application/json" \
     -d '{"parentId":"","type":"dataset","name":"debug","intro":"","avatar":"/icon/logo.svg"}'
   ```
   响应字面（含 response header / body 全文）会暴露真实 auth middleware 的 reason / 是否要 `teamId` body 字段 / 是否要其他 header。
4. **未对照 FastGPT 官方 v4.14 文档 URL**：未在调研中拉外网搜（守 task plan "0 行代码 / 不动 docker"精神 — 但官方文档外网搜不动代码，可补，本次时间预算优先证据链）。Boss 如需，可补查 `https://github.com/labring/FastGPT` v4.14 分支的 `projects/app/src/pages/api/core/dataset/create.ts` 或同等路径。

---

## 6. Pane 2 推荐 Boss 下一步（按时序）

1. **Boss 浏览器登录 FastGPT root 账号** → 找 "API 密钥" 页 → 截图 key 列表给 Pane 0
2. **Pane 0** 看截图字面，判断现 `FASTGPT_API_KEY` 字面值是 scope (a)/(b)/(c) 哪个
3. **如 scope = (a) 或 (c)**：Boss 在 FastGPT admin 创建 team-wide key (b)，**Pane 0** 派 fix task：1 行 `.env` 改 + `docker compose up -d` + 重跑 PC4 smoke 验 Step 5 PASS
4. **如 scope = (b) 但仍 403**：根因不是 scope，需走 §1.3 反证 (ii)/(iii) 路径 — 派 fix task 实测 `teamId` body 字段 + 其他 header
5. **如 Boss 不愿展 admin UI**：回退路径 2（rootkey 临时绕过）或路径 4（deprecate Step 5）

---

## 附录 A：调研使用的真代码 file:line 索引（grep 自验弹药）

```
.env.example:210-220          # FastGPT 段：API_KEY + BASE_URL + EMBEDDING + PORT
.env.example:242-249          # FastGPT runtime：ROOT_KEY + TOKEN_KEY + AES_KEY
.env.example:411              # FASTGPT_IMAGE_TAG=v4.14.10.2
ncmu-backend/src/ncmu_backend/config.py:30                  # REQUIRED env var list 含 FASTGPT_API_KEY
ncmu-backend/src/ncmu_backend/config.py:95                  # Pydantic Settings 字段
ncmu-backend/src/ncmu_backend/fastgpt_readonly/client.py:1-23   # 模块 docstring + auth 设计
ncmu-backend/src/ncmu_backend/fastgpt_readonly/client.py:78     # Bearer header 构造
ncmu-backend/src/ncmu_backend/fastgpt_readonly/client.py:114-143 # list_collections GET (read 路径)
ncmu-backend/src/ncmu_backend/fastgpt_readonly/client.py:230-252 # health_check 接受非 404
ncmu-backend/src/ncmu_backend/fastgpt_readonly/errors.py:31     # 401/403 错误类
ncmu-backend/src/ncmu_backend/fastgpt_readonly/errors.py:55     # 401/403 错误日志文案
ncmu-backend/src/ncmu_backend/fastgpt_readonly/routes.py:58     # 同 key 注入 client
scripts/ncmu_init.py:74-77    # rootkey 头说明
scripts/ncmu_init.py:94       # FASTGPT_MASS_ACTIVATION_PATH 字面
scripts/ncmu_init.py:823-833  # mongo 写 team_subscriptions {teamId:root, free}
scripts/ncmu_init.py:839-857  # rootkey 头实调用 admin 端点
scripts/e2e_personal_kb.sh:21         # .env 注入说明
scripts/e2e_personal_kb.sh:166-170    # cleanup DELETE 同 key
scripts/e2e_personal_kb.sh:204-209    # load_env 从主 worktree .env
scripts/e2e_personal_kb.sh:305-321    # preflight 第 4 项 KEY 双源注入检
scripts/e2e_personal_kb.sh:430-541    # Step 1-4 全 NCMU backend / 0 FastGPT 调用
scripts/e2e_personal_kb.sh:544-567    # Step 5 dataset/create 全段
scripts/e2e_personal_kb.sh:570-598    # Step 5 upload + 60s embedding polling
```

## 附录 B：本调研 0-代码 grep 自检

```bash
# AC#4 验证命令（Boss / 审查员可直接跑）
cd /mnt/d/Project/AIConsProject/NCMU_Proj/NCMU-fastgpt-research
/usr/bin/git -C . diff --stat
# 期望输出 only:  docs/followup/fastgpt-403-research.md | <N> +
```

— END —
