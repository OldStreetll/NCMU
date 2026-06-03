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


# Sensitive env keys whose CHANGE_ME placeholder values are forbidden in prod
# (raise) and warned in dev. The validator reads each via `getattr(self, key)`,
# so entries are the Settings *attribute* names: the original six are declared
# in UPPERCASE (field name == attribute name); the two DingTalk-login entries
# (TASK-PRODVAL-1) are declared snake_case with an UPPERCASE validation_alias,
# so they are listed by their snake attribute name. Listed only fields actually
# declared on `Settings` below — `getattr` would AttributeError otherwise.
#
# TASK-PRODVAL-1: `dingtalk_app_secret` is a real login dependency
# (exchange_user_token passes it as the OAuth clientSecret), and
# `dingtalk_login_redirect_uri` is prod-required config (no real redirect URI
# → the OAuth round-trip can't complete). Detection uses substring (`in`), not
# `startswith`, so it also catches the nested placeholder in the redirect URI
# default `https://CHANGE_ME/api/v1/ncmu/auth/dingtalk/callback`.
SENSITIVE_KEYS_PROD_REQUIRED: List[str] = [
    "NCMU_JWT_SECRET",
    "DIFY_CONSOLE_API_KEY",
    "DIFY_APP_DEFAULT_TOKEN",
    "DIFY_TENANT_ID",
    "FASTGPT_API_KEY",
    "SILICONFLOW_API_KEY",
    # TASK-PRODVAL-1 — DingTalk login真依赖（snake 属性名，getattr 取值）。
    "dingtalk_app_secret",          # OAuth clientSecret (login exchange)
    "dingtalk_login_redirect_uri",  # prod-required config (not a secret)
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
    # login backfills user_tags + app_tags does the
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

    # --- Phase 2D-A2 DingTalk 通讯录同步 (TASK-2DA2S-01) -------------
    # 钉钉企业内部应用凭据 + oapi 基址。app_key/secret/corp_id 默认
    # CHANGE_ME（对齐 DIFY_CONSOLE_API_KEY 占位范式）——真值由 .env 注入；
    # 同步半 client 仅 respx mock 测，不依赖真凭据。oapi_base 可指向测试 stub。
    # TASK-PRODVAL-1：dingtalk_app_secret 已纳入 SENSITIVE_KEYS_PROD_REQUIRED
    # （登录半 exchange_user_token 拿它当 OAuth clientSecret，是真依赖）。
    dingtalk_app_key: str = Field(
        default="CHANGE_ME",
        validation_alias="DINGTALK_APP_KEY",
    )
    dingtalk_app_secret: str = Field(
        default="CHANGE_ME",
        validation_alias="DINGTALK_APP_SECRET",
    )
    dingtalk_corp_id: str = Field(
        default="CHANGE_ME",
        validation_alias="DINGTALK_CORP_ID",
    )
    dingtalk_oapi_base: str = Field(
        default="https://oapi.dingtalk.com",
        validation_alias="DINGTALK_OAPI_BASE",
    )
    # TASK-2DA2S-03: 同步子树根部门 id（Boss 拍：只同步「软件开发部」子树）。
    # 默认 None —— GET /admin/dingtalk/departments 发现 dept_id 后填 .env；
    # POST /sync 的 body 未给 root_dept_id 时回落此值，两者皆空 → 400。
    dingtalk_sync_root_dept_id: int | None = Field(
        default=None,
        validation_alias="DINGTALK_SYNC_ROOT_DEPT_ID",
    )

    # --- Phase 2D-A2 DingTalk 扫码登录 (TASK-LOGIN-1) ----------------
    # 登录走钉钉 OAuth2：前端跳 auth_base 授权页 → 钉钉带 code 回调
    # redirect_uri → 后端 code→userAccessToken（oauth_api_base）→ /me 取
    # unionId → getbyunionid（oapi，复用同步半 dingtalk_oapi_base）→ 按
    # dingtalk_userid 匹配 NCMU 账号签发 JWT。
    #
    # dingtalk_login_redirect_uri 默认嵌套占位 https://CHANGE_ME/...（回调域名
    # 待 Boss 审批后定 / spec §7）—— 骨架期 respx mock 测不依赖真值；live e2e
    # 前须填真回调地址并在钉钉后台「安全设置」配白名单。TASK-PRODVAL-1：已纳入
    # SENSITIVE_KEYS_PROD_REQUIRED（prod-required config，子串检测拦嵌套占位）。
    # 两个 *_base 默认官方域（spec §2），测试用 init kwargs 覆盖指向 stub。
    dingtalk_login_redirect_uri: str = Field(
        default="https://CHANGE_ME/api/v1/ncmu/auth/dingtalk/callback",
        validation_alias="DINGTALK_LOGIN_REDIRECT_URI",
    )
    dingtalk_auth_base: str = Field(
        default="https://login.dingtalk.com",
        validation_alias="DINGTALK_AUTH_BASE",
    )
    dingtalk_oauth_api_base: str = Field(
        default="https://api.dingtalk.com",
        validation_alias="DINGTALK_OAUTH_API_BASE",
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
            # Substring (not startswith): the redirect-URI default embeds the
            # placeholder mid-string (`https://CHANGE_ME/...`), which a prefix
            # check would miss. All six original placeholders contain CHANGE_ME
            # too, so this is strictly more inclusive — no regression. A real
            # prod value containing the literal "CHANGE_ME" substring is
            # vanishingly unlikely (and would be a self-inflicted false alarm).
            if "CHANGE_ME" not in value:
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
