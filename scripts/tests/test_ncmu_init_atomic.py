"""Unit tests for atomic_write_env EBUSY/EXDEV fallback (TASK-65 / B-NEW-33).

Covers AC#5 two scenarios:
  (a) os.replace raises OSError(EBUSY=16) → in-place fallback writes new
      content to .env + STDERR carries the [WARN] line.
  (b) Default path (os.replace succeeds) → no fallback warning emitted.

Plus EXDEV (18) coverage to match the AC text ("EBUSY (16) 或 EXDEV (18)").

Layout note: this test file lives at scripts/tests/ (per TASK-65 file scope).
We prepend the parent scripts/ dir to sys.path so ``import ncmu_init`` works
the same way the existing ncmu-backend/tests/test_ncmu_init_*.py files do
(those use ``parents[2]/scripts``; we use ``parents[1]`` since we are one
level deeper into scripts/ already).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import ncmu_init  # noqa: E402  -- sys.path manipulation must happen first


_ORIG_ENV = (
    "# Header comment kept verbatim\n"
    "DIFY_CONSOLE_API_KEY=CHANGE_ME\n"
    "DIFY_APP_DEFAULT_TOKEN=app-real-untouched\n"
    "\n"
)


def _write_env(tmp_path: Path, body: str = _ORIG_ENV) -> Path:
    env = tmp_path / ".env"
    env.write_text(body, encoding="utf-8")
    return env


# ─────────────────────────────────────────────────────────────────────
# AC#5 (a): EBUSY → in-place fallback + WARN
# ─────────────────────────────────────────────────────────────────────


def test_atomic_write_env_falls_back_on_ebusy(tmp_path, monkeypatch, capsys):
    """single-file bind-mount path: os.replace raises EBUSY (errno 16) →
    atomic_write_env reads tmp content + truncate+writes env_path,
    leaves .env carrying the new content, emits the [WARN] line.
    """
    env = _write_env(tmp_path)

    real_replace = ncmu_init.os.replace

    def fake_replace(src, dst):
        # Only intercept the single-file bind-mount case (.env target).
        if str(dst).endswith(".env"):
            raise OSError(16, "Device or resource busy")
        return real_replace(src, dst)

    monkeypatch.setattr(ncmu_init.os, "replace", fake_replace)

    ncmu_init.atomic_write_env(
        {"DIFY_CONSOLE_API_KEY": "eyJ.synced.jwt"},
        env_path=env,
    )

    rewritten = env.read_text(encoding="utf-8")
    # New value written
    assert "DIFY_CONSOLE_API_KEY=eyJ.synced.jwt" in rewritten
    # Old placeholder gone
    assert "DIFY_CONSOLE_API_KEY=CHANGE_ME" not in rewritten
    # Comment + sibling key preserved verbatim
    assert "# Header comment kept verbatim" in rewritten
    assert "DIFY_APP_DEFAULT_TOKEN=app-real-untouched" in rewritten

    err = capsys.readouterr().err
    assert "[WARN] atomic-write fallback: in-place write to" in err
    assert "single-file bind-mount detected" in err
    assert str(env) in err

    # tmp file cleaned up after successful in-place write
    tmp = env.with_name(env.name + ".tmp")
    assert not tmp.exists(), f"tmp leftover after fallback: {tmp}"


# ─────────────────────────────────────────────────────────────────────
# AC#5 (a) extension: EXDEV (18) — same fallback semantics
# ─────────────────────────────────────────────────────────────────────


def test_atomic_write_env_falls_back_on_exdev(tmp_path, monkeypatch, capsys):
    """Cross-device link errno (EXDEV=18) triggers the same fallback path
    as EBUSY (per task AC#1 — both errnos qualify)."""
    env = _write_env(tmp_path)

    real_replace = ncmu_init.os.replace

    def fake_replace(src, dst):
        if str(dst).endswith(".env"):
            raise OSError(18, "Invalid cross-device link")
        return real_replace(src, dst)

    monkeypatch.setattr(ncmu_init.os, "replace", fake_replace)

    ncmu_init.atomic_write_env(
        {"DIFY_CONSOLE_API_KEY": "eyJ.exdev.token"},
        env_path=env,
    )

    rewritten = env.read_text(encoding="utf-8")
    assert "DIFY_CONSOLE_API_KEY=eyJ.exdev.token" in rewritten
    assert "[WARN] atomic-write fallback" in capsys.readouterr().err


# ─────────────────────────────────────────────────────────────────────
# AC#5 (b): default (host fs / non-bind-mount) path → no WARN
# ─────────────────────────────────────────────────────────────────────


def test_atomic_write_env_default_path_emits_no_warn(tmp_path, capsys):
    """Happy path: tmpfs / host fs supports rename → os.replace succeeds →
    no fallback WARN emitted on STDERR."""
    env = _write_env(tmp_path)

    ncmu_init.atomic_write_env(
        {"DIFY_CONSOLE_API_KEY": "eyJ.host.fs.token"},
        env_path=env,
    )

    captured = capsys.readouterr()
    assert "[WARN] atomic-write fallback" not in captured.err
    assert "single-file bind-mount" not in captured.err

    rewritten = env.read_text(encoding="utf-8")
    assert "DIFY_CONSOLE_API_KEY=eyJ.host.fs.token" in rewritten


# ─────────────────────────────────────────────────────────────────────
# AC#4 backward-compat: non-EBUSY/EXDEV OSError still propagates + tmp
# cleaned (mirrors existing test_atomic_write_env_oserror_cleans_tmp at
# ncmu-backend/tests/test_ncmu_init_token_sync.py to guard against
# accidental over-broad fallback during the change).
# ─────────────────────────────────────────────────────────────────────


def test_atomic_write_env_other_oserror_still_propagates(tmp_path, monkeypatch):
    """ENOSPC (errno 28) must NOT trigger fallback — it's a real disk
    error, not a single-file-mount artifact. Behavior matches existing
    test_atomic_write_env_oserror_cleans_tmp_and_propagates."""
    env = _write_env(tmp_path)
    orig_text = env.read_text(encoding="utf-8")

    def raise_nospc(src, dst):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(ncmu_init.os, "replace", raise_nospc)

    with pytest.raises(OSError) as excinfo:
        ncmu_init.atomic_write_env(
            {"DIFY_CONSOLE_API_KEY": "eyJ.never.written"},
            env_path=env,
        )
    assert excinfo.value.errno == 28

    # .env must be unchanged (no half-written state)
    assert env.read_text(encoding="utf-8") == orig_text
    # tmp must be cleaned up
    tmp = env.with_name(env.name + ".tmp")
    assert not tmp.exists()
