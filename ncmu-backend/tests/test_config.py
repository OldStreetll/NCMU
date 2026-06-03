"""Unit tests for `ncmu_backend.config.Settings._validate_sensitive_placeholders`.

Coverage matrix (plan §3 TASK-46 AC#3, eight subcases):
  - prod + `NCMU_JWT_SECRET="CHANGE_ME"` → raise (literal placeholder)
  - prod + `NCMU_JWT_SECRET="CHANGE_ME_DEV_ONLY_PLACEHOLDER_AT_LEAST_32_BYTES"` → raise (config.py default, prefix path)
  - prod + `NCMU_JWT_SECRET="CHANGE_ME_FERNET_OR_OPENSSL_RAND_BASE64_32_CHARS_"` → raise (.env.example default, prefix path)
  - prod + 6 real values → no raise
  - dev + literal `CHANGE_ME` → no raise + WARNING captured
  - dev + `CHANGE_ME_<variant>` → no raise + WARNING captured
  - dev + 6 real values → no warnings emitted by validator
  - prod + multi-key placeholder → raise the first key in iteration order

CI / docker-compose impact (plan §3 TASK-46 AC#6):
  prod profile + any sensitive key still placeholder → Settings instantiation
  raises during `from ncmu_backend.config import get_settings; get_settings()`
  → ncmu-backend container startup fails → docker-compose healthcheck never
  passes → restart loop (fail-fast, intended). CI / docker-compose targeting
  prod profile MUST inject real values via CI secrets or .env override before
  `docker compose up -d ncmu-backend`; otherwise the backend container will
  never be healthy.
"""
from __future__ import annotations

import logging

import pytest
from pydantic import ValidationError

from ncmu_backend.config import SENSITIVE_KEYS_PROD_REQUIRED, Settings


REAL_DEFAULTS: dict[str, str] = {
    "NCMU_JWT_SECRET": "real-32byte-secret-from-openssl-rand-base64-32",
    "DIFY_CONSOLE_API_KEY": "real-dify-console-key",
    "DIFY_APP_DEFAULT_TOKEN": "real-dify-app-token",
    # B-NEW-49: was the live prod tenant UUID; swapped for an obviously-
    # placeholder v4 UUID so this hygiene-test file never embeds a real
    # production identifier. The string is still a syntactically valid
    # UUID v4 (version nibble 4, variant nibble 8) so any downstream
    # parsing/format check stays happy.
    "DIFY_TENANT_ID": "00000000-0000-4000-8000-000000000000",
    "FASTGPT_API_KEY": "real-fastgpt-key",
    "SILICONFLOW_API_KEY": "real-siliconflow-key",
    # TASK-PRODVAL-1: the two DingTalk-login keys joined the prod-required
    # list, so _build() must seed them with real values too — otherwise every
    # prod test below would now trip on their CHANGE_ME defaults. Snake-case
    # kwargs work because Settings has populate_by_name=True. The redirect URI
    # real value MUST NOT contain the "CHANGE_ME" substring.
    "dingtalk_app_secret": "real-dingtalk-app-secret",
    "dingtalk_login_redirect_uri": (
        "https://ncmu.example.com/api/v1/ncmu/auth/dingtalk/callback"
    ),
}


def _build(**overrides: str) -> Settings:
    """Build Settings with all sensitive keys set to real values, then apply
    `overrides`. Lets each test focus on the single field it varies without
    leaking placeholder defaults from the others."""
    kwargs: dict[str, str] = dict(REAL_DEFAULTS)
    kwargs.update(overrides)
    return Settings(**kwargs)


# ---------------------------------------------------------------------------
# Module-level invariants
# ---------------------------------------------------------------------------


def test_sensitive_keys_prod_required_lists_expected_fields() -> None:
    # REWORK-A-INDEP-DEPLOY-2 (2026-05-14): DIFY_TENANT_ID added — Dify
    # ADMIN_API_KEY bypass (path B') gates on a non-empty X-WORKSPACE-ID
    # header, so a placeholder tenant_id in prod must fail-fast same as
    # a placeholder API key.
    # TASK-PRODVAL-1 (2026-06-03): the two DingTalk-login keys are appended
    # (snake attribute names). Order matters — the validator raises on the
    # FIRST placeholder in iteration order (see multi-key test below).
    assert SENSITIVE_KEYS_PROD_REQUIRED == [
        "NCMU_JWT_SECRET",
        "DIFY_CONSOLE_API_KEY",
        "DIFY_APP_DEFAULT_TOKEN",
        "DIFY_TENANT_ID",
        "FASTGPT_API_KEY",
        "SILICONFLOW_API_KEY",
        "dingtalk_app_secret",
        "dingtalk_login_redirect_uri",
    ]


def test_all_listed_keys_exist_on_settings() -> None:
    """Every entry must be a real Settings attribute — otherwise the validator's
    `getattr(self, key)` raises AttributeError at instantiation time."""
    s = _build()
    for key in SENSITIVE_KEYS_PROD_REQUIRED:
        assert hasattr(s, key), f"{key} not declared on Settings"


# ---------------------------------------------------------------------------
# prod + placeholder → raise (3 prefix variants)
# ---------------------------------------------------------------------------


def test_prod_literal_change_me_raises() -> None:
    with pytest.raises(ValidationError) as exc_info:
        _build(DEPLOY_PROFILE="prod", NCMU_JWT_SECRET="CHANGE_ME")
    assert "NCMU_JWT_SECRET" in str(exc_info.value)
    assert "placeholder in prod deployment" in str(exc_info.value)


def test_prod_config_default_padded_placeholder_raises() -> None:
    """config.py:49 NCMU_JWT_SECRET default = "CHANGE_ME_DEV_ONLY_..."
    — startswith path must catch it (literal `==` would miss)."""
    with pytest.raises(ValidationError) as exc_info:
        _build(
            DEPLOY_PROFILE="prod",
            NCMU_JWT_SECRET="CHANGE_ME_DEV_ONLY_PLACEHOLDER_AT_LEAST_32_BYTES",
        )
    assert "NCMU_JWT_SECRET" in str(exc_info.value)


def test_prod_env_example_padded_placeholder_raises() -> None:
    """.env.example padding variant (different suffix from config.py default)."""
    with pytest.raises(ValidationError) as exc_info:
        _build(
            DEPLOY_PROFILE="prod",
            NCMU_JWT_SECRET="CHANGE_ME_FERNET_OR_OPENSSL_RAND_BASE64_32_CHARS_",
        )
    assert "NCMU_JWT_SECRET" in str(exc_info.value)


# ---------------------------------------------------------------------------
# prod + real → no raise
# ---------------------------------------------------------------------------


def test_prod_all_real_values_passes() -> None:
    s = _build(DEPLOY_PROFILE="prod")
    assert s.is_prod is True
    assert s.NCMU_JWT_SECRET == REAL_DEFAULTS["NCMU_JWT_SECRET"]


# ---------------------------------------------------------------------------
# dev + placeholder → warn, no raise (literal + prefix)
# ---------------------------------------------------------------------------


def test_dev_literal_change_me_warns(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="ncmu_backend.config")
    s = _build(DEPLOY_PROFILE="dev", DIFY_CONSOLE_API_KEY="CHANGE_ME")
    assert s.is_prod is False
    assert any(
        "DIFY_CONSOLE_API_KEY" in rec.message and "placeholder" in rec.message
        for rec in caplog.records
    )


def test_dev_padded_change_me_prefix_warns(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="ncmu_backend.config")
    s = _build(
        DEPLOY_PROFILE="dev",
        NCMU_JWT_SECRET="CHANGE_ME_DEV_ONLY_PLACEHOLDER_AT_LEAST_32_BYTES",
    )
    assert s.is_prod is False
    assert any(
        "NCMU_JWT_SECRET" in rec.message and "placeholder" in rec.message
        for rec in caplog.records
    )


# ---------------------------------------------------------------------------
# dev + real → no warning emitted by validator
# ---------------------------------------------------------------------------


def test_dev_all_real_values_no_warning(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="ncmu_backend.config")
    s = _build(DEPLOY_PROFILE="dev")
    assert s.is_prod is False
    placeholder_logs = [
        rec for rec in caplog.records if "placeholder" in rec.message
    ]
    assert placeholder_logs == [], f"unexpected warnings: {placeholder_logs}"


# ---------------------------------------------------------------------------
# Multi-key prod placeholder → raise the FIRST key in iteration order
# ---------------------------------------------------------------------------


def test_prod_multi_placeholder_raises_first_key() -> None:
    """If multiple sensitive keys are placeholders in prod, the validator
    raises only on the first one in `SENSITIVE_KEYS_PROD_REQUIRED` order
    — `NCMU_JWT_SECRET` here, even though `DIFY_CONSOLE_API_KEY` is also
    placeholder."""
    with pytest.raises(ValidationError) as exc_info:
        _build(
            DEPLOY_PROFILE="prod",
            NCMU_JWT_SECRET="CHANGE_ME",
            DIFY_CONSOLE_API_KEY="CHANGE_ME",
        )
    msg = str(exc_info.value)
    assert "NCMU_JWT_SECRET" in msg
    assert "DIFY_CONSOLE_API_KEY" not in msg


# ---------------------------------------------------------------------------
# Per-field coverage — every field in the list raises in prod when it (and
# only it) is placeholder. Guards against accidental list/code drift.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("placeholder_key", SENSITIVE_KEYS_PROD_REQUIRED)
def test_prod_each_sensitive_key_raises_in_isolation(placeholder_key: str) -> None:
    overrides = {"DEPLOY_PROFILE": "prod", placeholder_key: "CHANGE_ME"}
    with pytest.raises(ValidationError) as exc_info:
        _build(**overrides)
    assert placeholder_key in str(exc_info.value)


# ---------------------------------------------------------------------------
# TASK-PRODVAL-1 — DingTalk-login keys join the prod-required gate
# ---------------------------------------------------------------------------


def test_prod_dingtalk_app_secret_placeholder_raises() -> None:
    """app_secret is the OAuth clientSecret for the login exchange — a
    placeholder in prod must fail-fast like any other secret (AC#1)."""
    with pytest.raises(ValidationError) as exc_info:
        _build(DEPLOY_PROFILE="prod", dingtalk_app_secret="CHANGE_ME")
    msg = str(exc_info.value)
    assert "dingtalk_app_secret" in msg
    assert "placeholder in prod deployment" in msg


def test_prod_redirect_uri_nested_placeholder_default_raises() -> None:
    """The redirect-URI config default embeds the placeholder mid-string
    (`https://CHANGE_ME/...`). startswith("CHANGE_ME") would MISS it — the
    substring switch is what makes this raise (AC#1, the core gap fix). This
    value is byte-identical to config.py's `dingtalk_login_redirect_uri`
    default."""
    with pytest.raises(ValidationError) as exc_info:
        _build(
            DEPLOY_PROFILE="prod",
            dingtalk_login_redirect_uri=(
                "https://CHANGE_ME/api/v1/ncmu/auth/dingtalk/callback"
            ),
        )
    assert "dingtalk_login_redirect_uri" in str(exc_info.value)


def test_prod_dingtalk_keys_real_values_pass() -> None:
    """Both DingTalk-login keys at real values → no raise in prod (AC#2).
    The redirect URI real value carries no CHANGE_ME substring, so the
    substring detector leaves it alone (no false positive)."""
    s = _build(DEPLOY_PROFILE="prod")
    assert s.is_prod is True
    assert s.dingtalk_app_secret == "real-dingtalk-app-secret"
    assert "CHANGE_ME" not in s.dingtalk_login_redirect_uri


def test_dev_dingtalk_app_secret_placeholder_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """dev + placeholder app_secret → warn, no raise (AC#2, existing dev
    warn path extends to the new key)."""
    caplog.set_level(logging.WARNING, logger="ncmu_backend.config")
    s = _build(DEPLOY_PROFILE="dev", dingtalk_app_secret="CHANGE_ME")
    assert s.is_prod is False
    assert any(
        "dingtalk_app_secret" in rec.message and "placeholder" in rec.message
        for rec in caplog.records
    )


def test_dev_redirect_uri_nested_placeholder_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """dev + nested-placeholder redirect URI → warn (substring path), no
    raise — proves the substring detector also fires the dev warn branch."""
    caplog.set_level(logging.WARNING, logger="ncmu_backend.config")
    s = _build(
        DEPLOY_PROFILE="dev",
        dingtalk_login_redirect_uri=(
            "https://CHANGE_ME/api/v1/ncmu/auth/dingtalk/callback"
        ),
    )
    assert s.is_prod is False
    assert any(
        "dingtalk_login_redirect_uri" in rec.message
        and "placeholder" in rec.message
        for rec in caplog.records
    )


# =============================================================================
# Phase 2C TASK-PC2-D — Personal KB feature/storage env coverage (AC#5)
# -----------------------------------------------------------------------------
# Three new envs landed alongside Personal KB (spec §1.5 调和 #4 / Q9-A):
#   - TAGS_ROUTING_ENABLED        永久 false 直到钉钉接入
#   - PERSONAL_KB_STORAGE_BACKEND "local_fs" only this phase
#   - PERSONAL_KB_STORAGE_ROOT    filesystem root for LocalFsBackend
# Three cases below cover (a) defaults when env unset, (b) successful env
# override, (c) invalid bool literal -> pydantic ValidationError.
# =============================================================================


PERSONAL_KB_ENV_KEYS = (
    "TAGS_ROUTING_ENABLED",
    "PERSONAL_KB_STORAGE_BACKEND",
    "PERSONAL_KB_STORAGE_ROOT",
)


@pytest.fixture
def _clear_personal_kb_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip the three Personal KB envs so defaults / explicit kwargs win.

    pydantic-settings reads os.environ at instantiation; without this
    fixture a developer's shell-exported value would silently flip the
    default-coverage test, hiding regressions.
    """
    for key in PERSONAL_KB_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_personal_kb_envs_have_expected_defaults(
    _clear_personal_kb_env: None,
) -> None:
    """AC#5 case (a): with no env set, the three fields land on the
    BaseSettings defaults declared in config.py.

    These defaults are part of the contract for downstream PC2-A
    (file_storage.LocalFsBackend root) and PC2-C (apps service feature
    flag) — changing them is a cross-task coordination event.
    """
    s = _build()
    assert s.tags_routing_enabled is False, (
        "tags_routing_enabled must default False until DingTalk lands "
        "(spec §1.5 调和 #4 — never silently flip)"
    )
    assert s.personal_kb_storage_backend == "local_fs"
    assert s.personal_kb_storage_root == "/var/lib/ncmu/personal-kb"


def test_personal_kb_envs_pick_up_legal_env_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC#5 case (b): legal env values are applied at Settings()
    instantiation (pydantic-settings reads os.environ once at startup;
    Q9-A startup-read semantics).
    """
    monkeypatch.setenv("TAGS_ROUTING_ENABLED", "true")
    monkeypatch.setenv("PERSONAL_KB_STORAGE_BACKEND", "minio")
    monkeypatch.setenv("PERSONAL_KB_STORAGE_ROOT", "/var/lib/ncmu/pkb-test")

    s = _build()
    assert s.tags_routing_enabled is True
    assert s.personal_kb_storage_backend == "minio"
    assert s.personal_kb_storage_root == "/var/lib/ncmu/pkb-test"


def test_personal_kb_envs_reject_non_bool_for_tags_routing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC#5 case (c): a string that doesn't parse as a bool raises
    pydantic ValidationError at Settings() construction.

    pydantic v2 accepts the usual truthy/falsy variants — "true",
    "false", "yes", "no", "on", "off", "1", "0" (case-insensitive) —
    so the case-(c) probe uses a clearly non-bool literal ("maybe")
    which the loose-bool coercion can't interpret. The point is
    fail-fast at startup, not the exact set of accepted spellings.
    """
    monkeypatch.setenv("TAGS_ROUTING_ENABLED", "maybe")
    with pytest.raises(ValidationError) as exc_info:
        _build()
    assert "TAGS_ROUTING_ENABLED" in str(
        exc_info.value
    ) or "tags_routing_enabled" in str(exc_info.value)
