"""Unit tests for scripts/ncmu_init.py token-sync flow (TASK-42 / B-NEW-14).

Covers AC#6 four scenarios:
  1. ``.env`` with no CHANGE_ME placeholders → sync skipped, .env untouched.
  2. ``.env`` with one placeholder + Dify login mocked-success →
     ``atomic_write_env`` called with the resolved token.
  3. Dify login mocked-failure → STDERR warn, ``.env`` untouched.
  4. ``atomic_write_env`` raises OSError (disk-full simulation) →
     ``run_token_sync_pre_init`` returns 1, ``.env`` not partially overwritten.

The script lives at ``scripts/ncmu_init.py`` (sibling of ``ncmu-backend/``);
we prepend the repo's ``scripts/`` dir to ``sys.path`` once at import time so
``import ncmu_init`` works the same way TASK-41 will set up.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

# Repo root = NCMU/ (test file lives at NCMU/ncmu-backend/tests/...).
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import ncmu_init  # noqa: E402  -- sys.path manipulation must happen first


def _write_env(tmp_path: Path, body: str) -> Path:
    env = tmp_path / ".env"
    env.write_text(body, encoding="utf-8")
    return env


def _write_bootstrap(tmp_path: Path, body: str) -> Path:
    bootstrap = tmp_path / ".env.bootstrap"
    bootstrap.write_text(body, encoding="utf-8")
    return bootstrap


# ─────────────────────────────────────────────────────────────────────
# AC#6 Scenario 1: no placeholders → skip sync
# ─────────────────────────────────────────────────────────────────────


def test_check_env_placeholders_returns_empty_when_no_change_me(tmp_path):
    env = _write_env(
        tmp_path,
        "DIFY_APP_DEFAULT_TOKEN=app-realtoken123\n"
        "DIFY_CONSOLE_API_KEY=eyJrealjwt.value.here\n"
        "FASTGPT_API_KEY=fastgpt-realkey-456\n"
        "FASTGPT_ROOT_KEY=fastgpt-root-789\n"
        "DEPLOY_PROFILE=dev\n",
    )
    assert ncmu_init.check_env_placeholders(env) == []


def test_check_env_placeholders_filters_to_token_keys_only(tmp_path):
    """Non-token CHANGE_ME placeholders (e.g. NCMU_JWT_SECRET) must NOT
    leak into the placeholder list — those are deployment-time secrets,
    not auto-syncable from any service."""
    env = _write_env(
        tmp_path,
        "DIFY_APP_DEFAULT_TOKEN=app-realtoken\n"
        "NCMU_JWT_SECRET=CHANGE_ME_FERNET_OR_OPENSSL_RAND_BASE64_32_CHARS_\n"
        "FERNET_KEY=CHANGE_ME\n"
        "DIFY_CONSOLE_API_KEY=eyJrealjwt\n",
    )
    found = ncmu_init.check_env_placeholders(env)
    assert found == []  # nothing matches token-key allowlist + CHANGE_ME


def test_run_token_sync_pre_init_returns_none_when_no_placeholders(tmp_path, monkeypatch, capsys):
    env = _write_env(tmp_path, "DIFY_APP_DEFAULT_TOKEN=app-real\n")
    monkeypatch.setattr(ncmu_init, "ENV_PATH", env)
    monkeypatch.setenv("DEPLOY_PROFILE", "dev")
    rc = ncmu_init.run_token_sync_pre_init()
    assert rc is None
    out_unchanged = env.read_text(encoding="utf-8")
    assert out_unchanged == "DIFY_APP_DEFAULT_TOKEN=app-real\n"


def test_run_token_sync_pre_init_skips_in_prod_profile(tmp_path, monkeypatch):
    """Prod mode must never auto-sync — secrets come from Vault/sops."""
    env = _write_env(tmp_path, "DIFY_APP_DEFAULT_TOKEN=CHANGE_ME\n")
    monkeypatch.setattr(ncmu_init, "ENV_PATH", env)
    monkeypatch.setenv("DEPLOY_PROFILE", "prod")
    # Even if sync_tokens_to_env existed, prod path returns before calling it.
    with mock.patch.object(ncmu_init, "sync_tokens_to_env") as sync_mock:
        rc = ncmu_init.run_token_sync_pre_init()
    assert rc is None
    sync_mock.assert_not_called()


# ─────────────────────────────────────────────────────────────────────
# AC#6 Scenario 2: one placeholder + Dify login success → sync 1 key
# ─────────────────────────────────────────────────────────────────────


def test_check_env_placeholders_detects_change_me_prefix_variants(tmp_path):
    """Bare CHANGE_ME and suffix-variants (e.g. CHANGE_ME_FERNET_...) both
    qualify for the token keys we DO track."""
    env = _write_env(
        tmp_path,
        "DIFY_APP_DEFAULT_TOKEN=CHANGE_ME\n"
        "DIFY_CONSOLE_API_KEY=CHANGE_ME_SOMETHING_LONG_SUFFIX\n"
        "FASTGPT_API_KEY=already-real\n",
    )
    found = ncmu_init.check_env_placeholders(env)
    assert sorted(found) == ["DIFY_APP_DEFAULT_TOKEN", "DIFY_CONSOLE_API_KEY"]


def test_sync_tokens_to_env_writes_dify_console_key_on_login_success(tmp_path, monkeypatch):
    bootstrap = _write_bootstrap(
        tmp_path,
        "DIFY_ADMIN_EMAIL=admin@ncmu.local\n"
        "DIFY_ADMIN_PASSWORD=Ncmu@E2E2026\n",
    )

    fake_response = mock.MagicMock()
    fake_response.cookies = {"access_token": "eyJ.fake.jwt"}
    fake_response.raise_for_status = mock.MagicMock()
    fake_response.json = mock.MagicMock(return_value={"result": "success"})

    fake_requests = mock.MagicMock()
    fake_requests.post = mock.MagicMock(return_value=fake_response)
    fake_requests.RequestException = Exception

    monkeypatch.setitem(sys.modules, "requests", fake_requests)

    updates = ncmu_init.sync_tokens_to_env(
        ["DIFY_CONSOLE_API_KEY"],
        bootstrap_path=bootstrap,
        dify_base_url="http://dify-api:5001",
    )

    assert updates == {"DIFY_CONSOLE_API_KEY": "eyJ.fake.jwt"}
    fake_requests.post.assert_called_once()
    # Verify base64 encoding was applied to the password.
    call_kwargs = fake_requests.post.call_args.kwargs
    body = call_kwargs.get("json") or {}
    assert body.get("password") != "Ncmu@E2E2026"  # must be base64-encoded
    import base64
    assert base64.b64decode(body["password"]).decode("utf-8") == "Ncmu@E2E2026"


def test_run_token_sync_pre_init_writes_env_and_returns_zero(tmp_path, monkeypatch, capsys):
    """End-to-end: placeholder detected → mocked sync returns 1 key →
    ``atomic_write_env`` runs → return 0 + STDOUT restart hint."""
    env = _write_env(
        tmp_path,
        "# Comment line preserved\n"
        "DIFY_CONSOLE_API_KEY=CHANGE_ME\n"
        "DIFY_APP_DEFAULT_TOKEN=app-real-already\n"
        "\n"
        "FASTGPT_API_KEY=fg-real-already\n",
    )
    monkeypatch.setattr(ncmu_init, "ENV_PATH", env)
    monkeypatch.setenv("DEPLOY_PROFILE", "dev")
    monkeypatch.setattr(
        ncmu_init,
        "sync_tokens_to_env",
        lambda missing, **kw: {"DIFY_CONSOLE_API_KEY": "eyJ.synced.jwt"},
    )

    rc = ncmu_init.run_token_sync_pre_init()
    out = capsys.readouterr()

    assert rc == 0
    assert "Tokens synced" in out.out
    assert "docker compose restart ncmu-backend" in out.out

    rewritten = env.read_text(encoding="utf-8")
    # Replaced line
    assert "DIFY_CONSOLE_API_KEY=eyJ.synced.jwt" in rewritten
    # Comment + other lines preserved verbatim
    assert "# Comment line preserved" in rewritten
    assert "DIFY_APP_DEFAULT_TOKEN=app-real-already" in rewritten
    assert "FASTGPT_API_KEY=fg-real-already" in rewritten
    # Old placeholder removed
    assert "DIFY_CONSOLE_API_KEY=CHANGE_ME" not in rewritten


# ─────────────────────────────────────────────────────────────────────
# AC#6 Scenario 3: Dify login fails → STDERR warn + .env unchanged
# ─────────────────────────────────────────────────────────────────────


def test_sync_tokens_to_env_login_failure_returns_empty_dict(tmp_path, monkeypatch, capsys):
    bootstrap = _write_bootstrap(
        tmp_path,
        "DIFY_ADMIN_EMAIL=admin@ncmu.local\n"
        "DIFY_ADMIN_PASSWORD=wrongpw\n",
    )

    fake_requests = mock.MagicMock()
    fake_requests.RequestException = type("ReqExc", (Exception,), {})

    def _failing_post(*args, **kwargs):
        raise fake_requests.RequestException("connection refused")

    fake_requests.post = _failing_post
    monkeypatch.setitem(sys.modules, "requests", fake_requests)

    updates = ncmu_init.sync_tokens_to_env(
        ["DIFY_CONSOLE_API_KEY", "DIFY_APP_DEFAULT_TOKEN"],
        bootstrap_path=bootstrap,
    )
    err = capsys.readouterr().err

    assert updates == {}
    assert "Dify Console login failed" in err


def test_run_token_sync_pre_init_login_fail_keeps_env_intact(tmp_path, monkeypatch, capsys):
    """sync全失败 → .env 不动 → 返回 None（继续 init 主流程）。"""
    env_text_orig = (
        "DIFY_CONSOLE_API_KEY=CHANGE_ME\n"
        "DIFY_APP_DEFAULT_TOKEN=CHANGE_ME\n"
    )
    env = _write_env(tmp_path, env_text_orig)
    monkeypatch.setattr(ncmu_init, "ENV_PATH", env)
    monkeypatch.setenv("DEPLOY_PROFILE", "dev")
    monkeypatch.setattr(ncmu_init, "sync_tokens_to_env", lambda missing, **kw: {})

    rc = ncmu_init.run_token_sync_pre_init()

    assert rc is None  # all-failed → continue init
    assert env.read_text(encoding="utf-8") == env_text_orig  # .env intact


def test_sync_tokens_to_env_missing_bootstrap_warns(tmp_path, monkeypatch, capsys):
    """No .env.bootstrap file at all → STDERR warn + empty dict."""
    nonexistent = tmp_path / ".env.bootstrap"  # never created
    updates = ncmu_init.sync_tokens_to_env(
        ["DIFY_CONSOLE_API_KEY"],
        bootstrap_path=nonexistent,
    )
    err = capsys.readouterr().err
    assert updates == {}
    assert ".env.bootstrap not found" in err


def test_sync_tokens_to_env_siliconflow_emits_guidance(tmp_path, monkeypatch, capsys):
    """SILICONFLOW key → STDERR guidance + skipped (not in updates)."""
    bootstrap = _write_bootstrap(tmp_path, "DIFY_ADMIN_EMAIL=x\nDIFY_ADMIN_PASSWORD=y\n")
    # Provide a fake requests so Dify part doesn't NPE; we only care about
    # the SILICONFLOW guidance branch firing first.
    fake_requests = mock.MagicMock()
    fake_requests.RequestException = type("ReqExc", (Exception,), {})
    fake_requests.post = mock.MagicMock(side_effect=fake_requests.RequestException("x"))
    monkeypatch.setitem(sys.modules, "requests", fake_requests)

    updates = ncmu_init.sync_tokens_to_env(
        ["SILICONFLOW_API_KEY", "DIFY_CONSOLE_API_KEY"],
        bootstrap_path=bootstrap,
    )
    err = capsys.readouterr().err
    assert "siliconflow.cn" in err
    assert "SILICONFLOW_API_KEY" not in updates


# ─────────────────────────────────────────────────────────────────────
# AC#6 Scenario 4: atomic_write fails (disk full) → exit 1 + .env intact
# ─────────────────────────────────────────────────────────────────────


def test_atomic_write_env_preserves_lines_and_replaces_target(tmp_path):
    """Sanity: on the happy path, atomic_write_env preserves comments,
    blank lines, unrelated keys verbatim — only target key is rewritten."""
    env = _write_env(
        tmp_path,
        "# Header comment\n"
        "DIFY_APP_DEFAULT_TOKEN=CHANGE_ME\n"
        "\n"
        "# Section divider\n"
        "FASTGPT_API_KEY=existing-untouched\n"
        "DIFY_CONSOLE_API_KEY=CHANGE_ME\n",
    )
    ncmu_init.atomic_write_env(
        {"DIFY_CONSOLE_API_KEY": "eyJ.new.jwt"}, env_path=env
    )
    out = env.read_text(encoding="utf-8")
    assert out == (
        "# Header comment\n"
        "DIFY_APP_DEFAULT_TOKEN=CHANGE_ME\n"
        "\n"
        "# Section divider\n"
        "FASTGPT_API_KEY=existing-untouched\n"
        "DIFY_CONSOLE_API_KEY=eyJ.new.jwt\n"
    )


def test_atomic_write_env_appends_missing_key(tmp_path):
    """Keys not in .env get appended at end (rare; defensive fallback)."""
    env = _write_env(tmp_path, "FOO=bar\n")
    ncmu_init.atomic_write_env({"NEW_KEY": "newval"}, env_path=env)
    out = env.read_text(encoding="utf-8")
    assert "FOO=bar" in out
    assert "NEW_KEY=newval" in out


def test_atomic_write_env_oserror_cleans_tmp_and_propagates(tmp_path, monkeypatch):
    """Disk-full simulation: ``os.replace`` raises → tmp cleaned + .env unchanged."""
    original_text = "DIFY_CONSOLE_API_KEY=CHANGE_ME\n"
    env = _write_env(tmp_path, original_text)

    real_replace = ncmu_init.os.replace

    def fail_replace(src, dst):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(ncmu_init.os, "replace", fail_replace)

    with pytest.raises(OSError):
        ncmu_init.atomic_write_env(
            {"DIFY_CONSOLE_API_KEY": "eyJ.new.jwt"}, env_path=env
        )

    # .env untouched
    assert env.read_text(encoding="utf-8") == original_text
    # tmp cleaned up (no orphan .env.tmp left behind)
    assert not (env.with_name(env.name + ".tmp")).exists()
    # restore (no other test relies on this; defensive)
    monkeypatch.setattr(ncmu_init.os, "replace", real_replace)


def test_run_token_sync_pre_init_atomic_write_failure_returns_one(tmp_path, monkeypatch, capsys):
    env_text_orig = "DIFY_CONSOLE_API_KEY=CHANGE_ME\n"
    env = _write_env(tmp_path, env_text_orig)
    monkeypatch.setattr(ncmu_init, "ENV_PATH", env)
    monkeypatch.setenv("DEPLOY_PROFILE", "dev")
    monkeypatch.setattr(
        ncmu_init,
        "sync_tokens_to_env",
        lambda missing, **kw: {"DIFY_CONSOLE_API_KEY": "eyJ.real.jwt"},
    )
    monkeypatch.setattr(
        ncmu_init,
        "atomic_write_env",
        mock.MagicMock(side_effect=OSError(28, "No space left on device")),
    )

    rc = ncmu_init.run_token_sync_pre_init()
    err = capsys.readouterr().err

    assert rc == 1
    assert "atomic write to .env failed" in err
    # .env unchanged
    assert env.read_text(encoding="utf-8") == env_text_orig
