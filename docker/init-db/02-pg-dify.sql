-- =====================================================================
-- pg-dify init-db (placeholder)
-- =====================================================================
-- Dify ships its own Alembic migrations and runs them on api startup
-- (langgenius/dify-api:v1.13.3 entrypoint). The pg-dify postgres
-- container only needs to provide the database; schema is owned by
-- the Dify image, not by NCMU.
--
-- The `dify` database is created by the postgres entrypoint via
-- POSTGRES_DB=${DIFY_DB_NAME}, so this script intentionally runs no
-- DDL. Kept as a placeholder so /docker-entrypoint-initdb.d/ ordering
-- (01 / 02 / 03) reflects the three pg instances symmetrically.
--
-- TASK-06 (Dify compose) may add further bootstrap here if needed
-- (e.g. extension prerequisites Dify expects); leave empty for now.
-- =====================================================================

SELECT 1;  -- no-op so the entrypoint reports success
