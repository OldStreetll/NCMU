-- scripts/e2e_personal_kb_fixtures/seed.sql
--
-- TASK-PC4 真容器 E2E 专用 seed（preflight #4 字面验 users 表 count ≥ 4）。
--
-- 复用既有 dev admin (a0000001-...001, 已在 NCMU_ADMIN_USER_IDS 默认值)
-- + 3 个 PC4 专属测试员工 (b0000001-...001~003 ≠ 既有 a0000001-..002~005，
-- 防 PC4 用户与现有 dev 用户混用拉杂 E2E 状态)。
--
-- 幂等：ON CONFLICT DO NOTHING — 多次 apply 安全。
-- 清理由 e2e_personal_kb.sh cleanup 段以 user_id 列表 DELETE 触发。

INSERT INTO users (id, dingtalk_userid, name, dept_path) VALUES
  -- admin 必须与 NCMU_ADMIN_USER_IDS 默认值一致；这里 ON CONFLICT 仅做防御性兜底
  ('a0000001-0000-4000-8000-000000000001', NULL, '张三',         '/HR/招聘组'),
  -- 3 个 PC4 专属测试员工
  ('b0000001-0000-4000-8000-000000000001', NULL, 'PC4-Tester-A', '/E2E/applicant'),
  ('b0000001-0000-4000-8000-000000000002', NULL, 'PC4-Tester-B', '/E2E/intruder'),
  ('b0000001-0000-4000-8000-000000000003', NULL, 'PC4-Tester-C', '/E2E/spare')
ON CONFLICT (id) DO NOTHING;
