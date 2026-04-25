-- Phase 1 dev seed users — 固定 UUID 供 dev/测试代码 import 引用
--
-- B-4 修订：张三 (a0000001-0000-4000-8000-000000000001) 是 dev 默认 admin，
-- 其 UUID 必须同时硬编码写到 NCMU/.env.example 的 NCMU_ADMIN_USER_IDS= 默认值（由 TASK-25 负责），
-- 使 test_admin.py 无需额外配置 env 即可 PASS，且真容器 compose up 后 admin 端点开箱可访问。
--
-- 幂等：ON CONFLICT DO NOTHING 允许多次应用（container entrypoint 重入场景）。
INSERT INTO users (id, dingtalk_userid, name, dept_path) VALUES
  ('a0000001-0000-4000-8000-000000000001', NULL, '张三', '/HR/招聘组'),
  ('a0000001-0000-4000-8000-000000000002', NULL, '李四', '/技术/后端'),
  ('a0000001-0000-4000-8000-000000000003', NULL, '王五', '/技术/前端'),
  ('a0000001-0000-4000-8000-000000000004', NULL, '赵六', '/财务/总账'),
  ('a0000001-0000-4000-8000-000000000005', NULL, '钱七', '/销售/华东')
ON CONFLICT (id) DO NOTHING;
