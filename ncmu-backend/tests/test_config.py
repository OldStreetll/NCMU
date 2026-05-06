"""Unit tests for `ncmu_backend.config.Settings._validate_sensitive_placeholders`.

Coverage matrix (plan §3 TASK-46 AC#3, eight subcases):
  - prod + `NCMU_JWT_SECRET="CHANGE_ME"` → raise (literal placeholder)
  - prod + `NCMU_JWT_SECRET="CHANGE_ME_DEV_ONLY_PLACEHOLDER_AT_LEAST_32_BYTES"` → raise (config.py default, prefix path)
  - prod + `NCMU_JWT_SECRET="CHANGE_ME_FERNET_OR_OPENSSL_RAND_BASE64_32_CHARS_"` → raise (.env.example default, prefix path)
  - prod + 5 real values → no raise
  - dev + literal `CHANGE_ME` → no raise + WARNING captured
  - dev + `CHANGE_ME_<variant>` → no raise + WARNING captured
  - dev + 5 real values → no warnings emitted by validator
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
    "FASTGPT_API_KEY": "real-fastgpt-key",
    "SILICONFLOW_API_KEY": "real-siliconflow-key",
}


def _build(**overrides: str) -> Settings:
    """Build Settings with all 5 sensitive keys set to real values, then apply
    `overrides`. Lets each test focus on the single field it varies without
    leaking placeholder defaults from the other four."""
    kwargs: dict[str, str] = dict(REAL_DEFAULTS)
    kwargs.update(overrides)
    return Settings(**kwargs)


# ---------------------------------------------------------------------------
# Module-level invariants
# ---------------------------------------------------------------------------


def test_sensitive_keys_prod_required_lists_five_fields() -> None:
    assert SENSITIVE_KEYS_PROD_REQUIRED == [
        "NCMU_JWT_SECRET",
        "DIFY_CONSOLE_API_KEY",
        "DIFY_APP_DEFAULT_TOKEN",
        "FASTGPT_API_KEY",
        "SILICONFLOW_API_KEY",
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
