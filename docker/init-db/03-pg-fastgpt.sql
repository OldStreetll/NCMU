-- =====================================================================
-- pg-fastgpt init-db (placeholder; superseded by TASK-07)
-- =====================================================================
-- TASK-07 will:
--   1. Switch the pg-fastgpt service image from postgres:16-alpine to
--      the FastGPT-recommended pgvector image (D-G; specific tag pending
--      in versions-locked-2026-04-21.md → PGVECTOR_IMAGE_TAG).
--   2. Replace this script with `CREATE EXTENSION vector;` plus any
--      FastGPT-required role/schema bootstrap.
--
-- For now: postgres:16-alpine is used as a temporary placeholder so
-- batch 3 can finish PG/Redis stand-up without blocking on TASK-07's
-- pgvector decision. The `fastgpt` database itself is created by the
-- entrypoint via POSTGRES_DB=${FASTGPT_DB_NAME}.
-- =====================================================================

SELECT 1;  -- no-op so the entrypoint reports success
