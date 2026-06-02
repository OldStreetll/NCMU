-- ====================================================================
-- seed_dept_tag_mappings.sql — TASK-2DA2S-03  ★ DEV ONLY ★
-- ====================================================================
-- 手动 seed「软件开发部子树」dept_id → NCMU tag 的映射，供 dingtalk 同步
-- (POST /api/v1/ncmu/admin/dingtalk/sync) 给员工打 user_tags 用。
--
-- ⚠️ DEV ONLY — 不进 prod，不入容器 entrypoint。dept_id 是占位（待用
--    `GET /api/v1/ncmu/admin/dingtalk/departments` 发现真实软件开发部子树
--    dept_id 后替换）；tag 名须先经 admin tags CRUD (PE-05) 建好。
--
-- 跑法（dev，psql 进 pg-ncmu）：
--   docker exec -i ncmu-pg-ncmu psql -U ncmu_app -d ncmu < \
--     ncmu-backend/scripts/seed_dept_tag_mappings.sql
--
-- 幂等：ON CONFLICT DO NOTHING（复合 PK (dept_id, tag_id) 防重）。
-- 子查询按 tag.name 取 tag_id —— 改成你真实建好的 tag 名。
-- ====================================================================

-- 示例：软件开发部根部门（dept_id 占位 1001）→ tag「软件开发部」
INSERT INTO department_tag_mappings (dept_id, tag_id)
SELECT 1001, id FROM tags WHERE name = '软件开发部'
ON CONFLICT (dept_id, tag_id) DO NOTHING;

-- 示例：子部门「后端组」（dept_id 占位 1002）→ tag「后端」
INSERT INTO department_tag_mappings (dept_id, tag_id)
SELECT 1002, id FROM tags WHERE name = '后端'
ON CONFLICT (dept_id, tag_id) DO NOTHING;

-- 示例：一部门可映射多 tag —— 后端组同时打「软件开发部」（部门继承）
INSERT INTO department_tag_mappings (dept_id, tag_id)
SELECT 1002, id FROM tags WHERE name = '软件开发部'
ON CONFLICT (dept_id, tag_id) DO NOTHING;
