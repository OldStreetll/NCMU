-- =====================================================================
-- pg-fastgpt init-db (TASK-07)
-- =====================================================================
-- pg-fastgpt now runs the pgvector image (FASTGPT_PGVECTOR_IMAGE_TAG,
-- default pgvector/pgvector:0.8.0-pg15 per FastGPT v4.14.12 upstream
-- compose). The pgvector extension ships with the image; CREATE EXTENSION
-- enables it inside the FASTGPT_DB_NAME database created by the
-- POSTGRES_DB entrypoint.
--
-- FastGPT manages its own schema via application-level migrations on
-- first boot of fastgpt-app; this script only enables the extension.
--
-- See:
--   NCMU-Wiki/sources/phase0/versions-locked-2026-04-21-errata-02.md
--     (rationale for unified pgvector vector store across pg-dify/pg-fastgpt)
--   NCMU-Wiki/sources/specs/2026-04-21-phase0-infrastructure.md TASK-07
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS vector;
