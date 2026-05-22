"""Settings loaded from environment via pydantic-settings.

The single `Settings` instance is provided through `get_settings()` (cached
with `lru_cache`). Tests override the cached value via FastAPI dependency
overrides, not by mutating the cache.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import List

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


logger = logging.getLogger(__name__)


# Sensitive env keys whose CHANGE_ME-prefixed placeholder values are forbidden
# in prod (raise) and warned in dev. Listed only fields actually declared on
# `Settings` below — `getattr` would AttributeError otherwise. Prefix-based
# detection (`startswith`) covers `NCMU_JWT_SECRET`'s padded default
# `CHANGE_ME_DEV_ONLY_PLACEHOLDER_AT_LEAST_32_BYTES`.
SENSITIVE_KEYS_PROD_REQUIRED: List[str] = [
    "NCMU_JWT_SECRET",
    "DIFY_CONSOLE_API_KEY",
    "DIFY_APP_DEFAULT_TOKEN",
    "DIFY_TENANT_ID",
    "FASTGPT_API_KEY",
    "SILICONFLOW_API_KEY",
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=None,           # docker compose injects env via env_file:
        case_sensitive=True,
        extra="ignore",
        # Phase 2C TASK-PC2-D — three new fields below use snake_case
        # attribute names (matching the plan §3.3 consumer API) with the
        # UPPERCASE env var carried via validation_alias. populate_by_name
        # keeps the kwarg constructor (`Settings(tags_routing_enabled=...)`)
        # working alongside the alias form, so existing tests that build a
        # Settings via uppercase field-name kwargs are unaffected.
        populate_by_name=True,
    )

    # --- Database --------------------------------------------------
    # NCMU_DB_URL is shared with alembic env.py (sync psycopg) and the
    # ncmu-backend runtime (async asyncpg via db.session._build_async_url).
    NCMU_DB_URL: str = "postgresql://ncmu_app:CHANGE_ME@pg-ncmu:5432/ncmu"

    # --- Auth ------------------------------------------------------
    # NCMU_JWT_SECRET signs HS256 access tokens (Phase 1). Phase 2
    # DingTalk integration upgrades to RS256 with a managed key pair.
    NCMU_JWT_SECRET: str = "CHANGE_ME_DEV_ONLY_PLACEHOLDER_AT_LEAST_32_BYTES"
    NCMU_JWT_TTL_HOURS: int = 24

    # NCMU_ENABLE_DEV_LOGIN gates the unauthenticated /auth/dev-login
    # and /dev/users endpoints. Must be false in prod (enforced at
    # lifespan startup; raises RuntimeError otherwise).
    NCMU_ENABLE_DEV_LOGIN: bool = False

    # NCMU_ADMIN_USER_IDS — comma-separated user UUIDs that pass
    # admin-permission checks. Default value matches dev_users.sql
    # seed row 1 (张三) so test_admin.py / admin endpoints work
    # out-of-the-box on a fresh dev deploy.
    NCMU_ADMIN_USER_IDS: str = "a0000001-0000-4000-8000-000000000001"

    # --- Deploy profile -------------------------------------------
    # G-NEW-2 修订：use DEPLOY_PROFILE (Phase 0 standard), not the
    # virtual NCMU_PROFILE that earlier drafts referenced.
    DEPLOY_PROFILE: str = "dev"

    # --- Dify (Phase 0 envs reused, Phase 1 additions) -------------
    DIFY_BASE_URL: str = "http://dify-api:5001"
    # Phase 1 2026-05-14+ (TASK-A B-NEW-39): value is Dify ADMIN_API_KEY
    # (admin bypass token, never expires); must match the
    # DIFY_ADMIN_API_KEY referenced by NCMU/docker-compose.override.yaml.
    # See NCMU/scripts/README.md rotation runbook.
    DIFY_CONSOLE_API_KEY: str = "CHANGE_ME"
    # Phase 1 2026-05-14 INDEP-FIX-DEPLOY-2 (path B'): Dify's
    # ADMIN_API_KEY bypass also requires `X-WORKSPACE-ID: <tenant_id>`
    # (ext_login.py:56-72). Pull the owner-tenant UUID with:
    #   docker exec ncmu-pg-dify psql -U dify -d dify -c \
    #     "SELECT id FROM tenants t \
    #        JOIN tenant_account_joins taj ON taj.tenant_id=t.id \
    #       WHERE taj.role='owner';"
    DIFY_TENANT_ID: str = "CHANGE_ME"
    DIFY_APP_DEFAULT_TOKEN: str = "CHANGE_ME"  # [KB] App's app_api_token

    # --- FastGPT ---------------------------------------------------
    FASTGPT_BASE_URL: str = "http://fastgpt:3000"
    FASTGPT_API_KEY: str = "CHANGE_ME"

    # --- Embedding (errata-11: SiliconFlow) ------------------------
    SILICONFLOW_API_KEY: str = "CHANGE_ME"

    # --- KB-adapter (read-only reference, not used by backend) -----
    KB_ADAPTER_BASE_URL: str = "http://kb-adapter:8000"

    # --- Phase 2C Personal KB (TASK-PC2-D) -------------------------
    # tags_routing_enabled: feature flag for the shared-App tag-based
    # routing path. **永久 false 直到钉钉接入** — only after DingTalk
    # login backfills users.tags + tag_app_mappings does the
    # shared-App route consult those tables (spec §1.5 调和 #4 / Q9-A
    # startup-read semantics, no dynamic flip; rotation requires
    # restarting ncmu-backend).
    tags_routing_enabled: bool = Field(
        default=False,
        validation_alias="TAGS_ROUTING_ENABLED",
    )

    # personal_kb_storage_backend: picks which StorageBackend impl
    # personal_kb/file_storage.py loads at startup. Phase 2C ships
    # "local_fs" only; "minio" is a stub for the next phase.
    personal_kb_storage_backend: str = Field(
        default="local_fs",
        validation_alias="PERSONAL_KB_STORAGE_BACKEND",
    )

    # personal_kb_storage_root: filesystem root LocalFsBackend writes
    # under. One subdir per application_id. Override to ./tmp/personal-kb
    # in docker-compose.override.yaml for dev to avoid /var/lib writes.
    personal_kb_storage_root: str = Field(
        default="/var/lib/ncmu/personal-kb",
        validation_alias="PERSONAL_KB_STORAGE_ROOT",
    )

    @property
    def admin_user_id_set(self) -> set[str]:
        """Parsed NCMU_ADMIN_USER_IDS as a set, lower-cased."""
        return {u.strip().lower() for u in self.NCMU_ADMIN_USER_IDS.split(",") if u.strip()}

    @property
    def is_prod(self) -> bool:
        return self.DEPLOY_PROFILE == "prod"

    @model_validator(mode="after")
    def _validate_sensitive_placeholders(self) -> "Settings":
        # prod: raise on first hit (avoid noisy multi-error). dev: warn each.
        for key in SENSITIVE_KEYS_PROD_REQUIRED:
            value = getattr(self, key)
            if not value.startswith("CHANGE_ME"):
                continue
            if self.is_prod:
                raise ValueError(f"{key} is placeholder in prod deployment")
            logger.warning(f"{key} is placeholder; some features may fail")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    """Clear the cached Settings — used by tests that mutate env mid-session."""
    get_settings.cache_clear()
