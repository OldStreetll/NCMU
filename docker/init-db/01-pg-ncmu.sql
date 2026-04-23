-- =====================================================================
-- pg-ncmu init-db (v3.3 Dify-integration tables)
-- =====================================================================
-- Source : NCMU-Wiki/sources/specs/ncmu-dify-design-v3.md §14.3
--          (lines 1302-1360, full DDL transcribed verbatim)
-- Owner  : ncmu_app  (POSTGRES_USER from .env)
-- Database: ncmu     (POSTGRES_DB from .env, pre-created by entrypoint)
--
-- The postgres official image runs scripts in /docker-entrypoint-initdb.d/
-- AFTER the POSTGRES_DB has been created and connected to. Therefore this
-- script does NOT issue `CREATE DATABASE ncmu` (would fail with
-- "database already exists"); it operates inside the `ncmu` database.
--
-- Idempotency: re-running the entrypoint on a populated data dir is a
-- no-op (postgres skips initdb), so this script never executes a second
-- time on the same volume. Statements use IF NOT EXISTS where supported
-- to make manual re-runs (psql -f) safe as well.
--
-- Phase 1 ncmu-backend Alembic migrations create the rest of the schema
-- (users, tags, tag_app_mappings, conversation_logs, etc.); this script
-- is intentionally limited to the three v3.3 NEW tables.
-- =====================================================================


-- =====================================================================
-- Extension: pg_trgm (trigram similarity for dify_apps name search)
-- =====================================================================
-- Required by dify_apps_name_trgm GIN index below; admin panel uses
-- ILIKE / similarity() for App search by Chinese name.
CREATE EXTENSION IF NOT EXISTS pg_trgm;


-- =====================================================================
-- Table: dify_apps  (cache of Dify Console API /apps)
-- =====================================================================
-- Spec §14.3 lines 1302-1318. Refreshed by NCMU admin panel "Sync Apps"
-- action; primary key is the Dify App UUID (authoritative, not generated).
CREATE TABLE IF NOT EXISTS dify_apps (
    id          VARCHAR(100) PRIMARY KEY,        -- Dify App ID (authoritative, no autoincrement)
    name        VARCHAR(200) NOT NULL,
    app_type    VARCHAR(30)  NOT NULL,           -- 'chatbot'/'agent'/'workflow'/'chatflow'/'text_gen'/'kb_qa'
    description TEXT,
    icon        VARCHAR(200),
    synced_at   TIMESTAMP DEFAULT NOW(),
    is_active   BOOLEAN   DEFAULT true           -- soft-marker for Dify-side deletion
);

CREATE INDEX IF NOT EXISTS dify_apps_type_idx
    ON dify_apps(app_type) WHERE is_active = true;

CREATE INDEX IF NOT EXISTS dify_apps_synced_at_idx
    ON dify_apps(synced_at DESC);

-- Trigram GIN index for fuzzy name search (admin panel filter).
CREATE INDEX IF NOT EXISTS dify_apps_name_trgm
    ON dify_apps USING gin(name gin_trgm_ops);


-- =====================================================================
-- Table: dify_external_kb_configs  (audit metadata for kb-adapter keys)
-- =====================================================================
-- Spec §14.3 lines 1326-1340. Audit table only; runtime auth on
-- kb-adapter reads env KB_ADAPTER_ALLOWED_KEYS, NOT this table. Stores
-- Fernet-encrypted key material for rotation/recovery scripts.
CREATE TABLE IF NOT EXISTS dify_external_kb_configs (
    id                  SERIAL PRIMARY KEY,
    name                VARCHAR(200) NOT NULL UNIQUE,  -- admin-friendly id, e.g. "sales-contract"
    fastgpt_endpoint    VARCHAR(500) NOT NULL,         -- audit/display only; runtime reads FASTGPT_BASE_URL env
    fastgpt_dataset_id  VARCHAR(100),                  -- becomes mandatory after Phase 0
    api_key_encrypted   TEXT         NOT NULL,         -- Fernet(JWT_SECRET-derived) ciphertext, audit-only
    description         TEXT,
    created_at          TIMESTAMP DEFAULT NOW(),
    last_rotated_at     TIMESTAMP,                     -- key rotation audit
    is_active           BOOLEAN DEFAULT true
);


-- =====================================================================
-- Table: dify_app_kb_bindings  (v3.3 NEW: App ↔ external KB mapping)
-- =====================================================================
-- Spec §14.3 lines 1346-1360. v3.3 NEW table — Dify DSL export does NOT
-- include external KB bindings (Spike 2026-04-20 finding), so NCMU has
-- to maintain this layer to support §7.3 method B "App as Code"
-- (cross-env sync, disaster recovery rebuild).
--
-- Maintenance is admin-panel only (§16); ncmu-backend runtime does NOT
-- query this table for routing — runtime path stays inside Dify's own
-- dataset binding mechanism, decoupled from this table.
CREATE TABLE IF NOT EXISTS dify_app_kb_bindings (
    dify_app_id           VARCHAR(100) NOT NULL REFERENCES dify_apps(id) ON DELETE CASCADE,
    dify_external_kb_id   VARCHAR(100) NOT NULL,        -- Dify-side external KB UUID
    external_kb_config_id INTEGER      REFERENCES dify_external_kb_configs(id) ON DELETE SET NULL,
                                                        -- pointer to NCMU connection config (optional)
    fastgpt_dataset_id    VARCHAR(100) NOT NULL,        -- redundant copy for direct FastGPT lookup
    created_at            TIMESTAMPTZ  DEFAULT NOW(),
    PRIMARY KEY (dify_app_id, dify_external_kb_id)
);

CREATE INDEX IF NOT EXISTS dify_app_kb_bindings_ekb_idx
    ON dify_app_kb_bindings(dify_external_kb_id);

CREATE INDEX IF NOT EXISTS dify_app_kb_bindings_dataset_idx
    ON dify_app_kb_bindings(fastgpt_dataset_id);

CREATE INDEX IF NOT EXISTS dify_app_kb_bindings_config_idx
    ON dify_app_kb_bindings(external_kb_config_id);
