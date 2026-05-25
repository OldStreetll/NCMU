# e2e_personal_kb_fixtures/

TASK-PC4 真容器 E2E 用 fixture 资源。

## 静态文件（仓内）

- `seed.sql` — 测试用户 seed（idempotent / 复用 dev admin + 3 PC4 专属员工）

## 运行时生成（不入仓 / 见 .gitignore）

`e2e_personal_kb.sh` 启动时在工作目录 `${PC4_TMP_DIR:-/tmp/pc4-fixtures-$$}/` 用 `dd if=/dev/zero` + 内联 python heredoc 现造以下 fixture（守任务模板 "fixtures 用 dd / python 现造，0 外部依赖"）：

| 文件 | 用途 | 大小 |
|---|---|---|
| `golden.pdf` | 黄金路径 Step 1 上传（minimal valid PDF + 嵌入 KB 测试文本） | ~500 bytes |
| `bigfile_51mb.pdf` | 异常分支 b（>50 MB 拒绝） | 51 MiB 零填充 |
| `forbidden.exe` | 异常分支 d（非 8 类格式拒绝） | 1 KB 零填充 |
| `count_overflow_0..10.txt` | 异常分支 c（>10 文件拒绝 = 11 个） | 1 KB × 11 |
| 8 格式各 1 候选池 | 备用 / 当前 AC 未直接使用 | 1 KB × 8 |

## 服务前提（dev pane 不启停 / Boss 负责 stack）

执行前必须：
- 5 service docker stack 全 healthy（ncmu-backend / pg-ncmu / fastgpt-app / dify-api / kb-adapter）
- `NCMU_ENABLE_DEV_LOGIN=true` 在 .env
- `FASTGPT_API_KEY` / `DIFY_CONSOLE_API_KEY` / `DIFY_TENANT_ID` 已注入 .env（dev token-sync 已跑过）
- alembic upgrade head 已跑到 0008（personal_kb 3 表 + external_kb_name VARCHAR(200) 已应用）

## 测试用户

| UUID | 角色 | 备注 |
|---|---|---|
| `a0000001-0000-4000-8000-000000000001` | admin | 复用 dev 默认 admin（NCMU_ADMIN_USER_IDS 默认值） |
| `b0000001-0000-4000-8000-000000000001` | employee_a (applicant) | 提交申请 + 黄金路径主用户 |
| `b0000001-0000-4000-8000-000000000002` | employee_b (intruder) | 异常分支 e/f 越权测试 |
| `b0000001-0000-4000-8000-000000000003` | employee_c (spare) | 预留 |

## 触发

```bash
make e2e-personal-kb           # 推荐
# 或直接：
bash scripts/e2e_personal_kb.sh
```
