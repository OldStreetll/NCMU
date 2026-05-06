"""Unit tests for scripts/ncmu_init.py core-data check (TASK-41 / B-NEW-13).

Covers AC#1-4 of TASK-41:
  1. ``check_core_data_present`` returns True iff Dify apps > 0 AND FastGPT
     datasets > 0; any zero or query failure → False.
  2. ``list_recent_snapshots`` returns up to N most-recent backup dirs with
     total byte size, sorted recent-first.
  3. ``run_core_data_check`` flow:
       - data present → return None (continue init)
       - data missing + stdin "yes" → return None (continue, no restore)
       - data missing + stdin "no"/anything-else → return 0 (exit clean) +
         restore.sh hint on STDERR
       - non-interactive auto_yes → skip prompt, return None
  4. Strict ``==`` comparison: trailing space "yes " is NOT accepted
     (parallel to REWORK-38 IFS lesson at the python layer).

Tests follow the test_ncmu_init_token_sync.py pattern: prepend ``scripts/``
to ``sys.path``, import ``ncmu_init``, monkeypatch the unit under test.
"""
from __future__ import annotations

import io
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


# ─────────────────────────────────────────────────────────────────────
# list_recent_snapshots
# ─────────────────────────────────────────────────────────────────────


def test_list_recent_snapshots_returns_recent_first_with_sizes(tmp_path):
    """3 snapshot dirs with files → sorted recent-first; total size = sum of files."""
    backups = tmp_path / "data" / "backups"
    backups.mkdir(parents=True)
    # Create 3 dirs with timestamps in a known order; rely on dirname sort
    # (timestamp-prefixed dirs sort lexicographically = chronologically).
    older = backups / "20260501-120000"
    middle = backups / "20260502-120000"
    newest = backups / "20260503-120000"
    for d in (older, middle, newest):
        d.mkdir()
    (older / "x.gz").write_bytes(b"a" * 100)
    (middle / "x.gz").write_bytes(b"b" * 200)
    (newest / "x.gz").write_bytes(b"c" * 300)
    (newest / "manifest.json").write_bytes(b"d" * 50)

    result = ncmu_init.list_recent_snapshots(backups_dir=backups)

    assert [r[0] for r in result] == ["20260503-120000", "20260502-120000", "20260501-120000"]
    assert [r[1] for r in result] == [350, 200, 100]


def test_list_recent_snapshots_handles_missing_dir(tmp_path):
    """No backups dir → empty list (don't raise)."""
    nonexistent = tmp_path / "data" / "backups"  # never created
    assert ncmu_init.list_recent_snapshots(backups_dir=nonexistent) == []


def test_list_recent_snapshots_limit_caps_result_count(tmp_path):
    """7 dirs + limit=5 → only 5 most recent returned."""
    backups = tmp_path / "data" / "backups"
    backups.mkdir(parents=True)
    for i in range(1, 8):
        d = backups / f"2026050{i}-120000"
        d.mkdir()
        (d / "x.gz").write_bytes(b"x" * i)
    result = ncmu_init.list_recent_snapshots(backups_dir=backups, limit=5)
    assert len(result) == 5
    # Most recent first: -7, -6, -5, -4, -3
    assert [r[0] for r in result] == [
        "20260507-120000", "20260506-120000", "20260505-120000",
        "20260504-120000", "20260503-120000",
    ]


def test_list_recent_snapshots_skips_non_dir_entries(tmp_path):
    """destructive.log file in backups/ must not appear in snapshot list."""
    backups = tmp_path / "data" / "backups"
    backups.mkdir(parents=True)
    (backups / "destructive.log").write_text("audit\n")
    snap = backups / "20260505-181040"
    snap.mkdir()
    (snap / "x.gz").write_bytes(b"data")
    result = ncmu_init.list_recent_snapshots(backups_dir=backups)
    assert [r[0] for r in result] == ["20260505-181040"]


# ─────────────────────────────────────────────────────────────────────
# check_core_data_present
# ─────────────────────────────────────────────────────────────────────


def test_check_core_data_present_true_when_both_populated(monkeypatch):
    """Both apps > 0 and datasets > 0 → True."""
    monkeypatch.setattr(ncmu_init, "_count_dify_apps", lambda: 1)
    monkeypatch.setattr(ncmu_init, "_count_fastgpt_datasets", lambda: 3)
    assert ncmu_init.check_core_data_present() is True


def test_check_core_data_present_false_when_dify_apps_zero(monkeypatch):
    """Dify apps=0 → False (regardless of datasets)."""
    monkeypatch.setattr(ncmu_init, "_count_dify_apps", lambda: 0)
    monkeypatch.setattr(ncmu_init, "_count_fastgpt_datasets", lambda: 5)
    assert ncmu_init.check_core_data_present() is False


def test_check_core_data_present_false_when_fastgpt_datasets_zero(monkeypatch):
    """FastGPT datasets=0 → False (regardless of apps)."""
    monkeypatch.setattr(ncmu_init, "_count_dify_apps", lambda: 7)
    monkeypatch.setattr(ncmu_init, "_count_fastgpt_datasets", lambda: 0)
    assert ncmu_init.check_core_data_present() is False


def test_check_core_data_present_false_on_dify_query_error(monkeypatch, capsys):
    """psycopg2 raises (e.g. connection refused) → STDERR warn + False
    (fail-safe: treat unknown state as "missing" so operator gets prompted)."""
    def _raises():
        raise RuntimeError("connection refused")
    monkeypatch.setattr(ncmu_init, "_count_dify_apps", _raises)
    monkeypatch.setattr(ncmu_init, "_count_fastgpt_datasets", lambda: 5)
    result = ncmu_init.check_core_data_present()
    err = capsys.readouterr().err
    assert result is False
    assert "Dify apps query failed" in err


def test_check_core_data_present_false_on_fastgpt_query_error(monkeypatch, capsys):
    """pymongo raises → STDERR warn + False (Dify side may have run if
    queried first, but logic still returns False since both must succeed)."""
    monkeypatch.setattr(ncmu_init, "_count_dify_apps", lambda: 1)
    def _raises():
        raise RuntimeError("mongo timeout")
    monkeypatch.setattr(ncmu_init, "_count_fastgpt_datasets", _raises)
    result = ncmu_init.check_core_data_present()
    err = capsys.readouterr().err
    assert result is False
    assert "FastGPT datasets query failed" in err


# ─────────────────────────────────────────────────────────────────────
# run_core_data_check (main flow)
# ─────────────────────────────────────────────────────────────────────


def test_run_core_data_check_continues_when_data_present(monkeypatch, capsys):
    """Data check passes → return None (no prompt, no exit)."""
    monkeypatch.setattr(ncmu_init, "check_core_data_present", lambda: True)
    # Guard: input() must NOT be called
    monkeypatch.setattr("builtins.input", mock.MagicMock(side_effect=AssertionError("input() should not be called")))
    rc = ncmu_init.run_core_data_check()
    assert rc is None


def test_run_core_data_check_yes_input_continues(monkeypatch, capsys, tmp_path):
    """Data missing + stdin 'yes' → return None (continue, no restore)."""
    monkeypatch.setattr(ncmu_init, "check_core_data_present", lambda: False)
    monkeypatch.setattr(ncmu_init, "list_recent_snapshots", lambda **kw: [("20260505-181040", 12345)])
    monkeypatch.setattr("builtins.input", lambda *a, **kw: "yes")
    rc = ncmu_init.run_core_data_check()
    err = capsys.readouterr().err
    assert rc is None
    # Still warns — operator made an informed choice
    assert "WARN" in err or "warn" in err.lower()


def test_run_core_data_check_no_input_exits_with_hint(monkeypatch, capsys):
    """Data missing + stdin 'no' → return 0 + restore.sh hint on STDERR."""
    monkeypatch.setattr(ncmu_init, "check_core_data_present", lambda: False)
    monkeypatch.setattr(ncmu_init, "list_recent_snapshots", lambda **kw: [("20260505-181040", 12345)])
    monkeypatch.setattr("builtins.input", lambda *a, **kw: "no")
    rc = ncmu_init.run_core_data_check()
    err = capsys.readouterr().err
    assert rc == 0
    assert "./scripts/restore.sh" in err


def test_run_core_data_check_empty_input_exits(monkeypatch, capsys):
    """Anything other than literal 'yes' → exit (fail-closed)."""
    monkeypatch.setattr(ncmu_init, "check_core_data_present", lambda: False)
    monkeypatch.setattr(ncmu_init, "list_recent_snapshots", lambda **kw: [])
    monkeypatch.setattr("builtins.input", lambda *a, **kw: "")
    rc = ncmu_init.run_core_data_check()
    assert rc == 0


def test_run_core_data_check_yes_with_trailing_space_is_rejected(monkeypatch, capsys):
    """Strict ==: 'yes ' (trailing space) is NOT accepted (REWORK-38 lesson
    at the python layer — input() doesn't strip but we still must compare strictly)."""
    monkeypatch.setattr(ncmu_init, "check_core_data_present", lambda: False)
    monkeypatch.setattr(ncmu_init, "list_recent_snapshots", lambda **kw: [])
    monkeypatch.setattr("builtins.input", lambda *a, **kw: "yes ")
    rc = ncmu_init.run_core_data_check()
    assert rc == 0


def test_run_core_data_check_yes_uppercase_rejected(monkeypatch, capsys):
    """Strict ==: 'YES' is NOT accepted."""
    monkeypatch.setattr(ncmu_init, "check_core_data_present", lambda: False)
    monkeypatch.setattr(ncmu_init, "list_recent_snapshots", lambda **kw: [])
    monkeypatch.setattr("builtins.input", lambda *a, **kw: "YES")
    rc = ncmu_init.run_core_data_check()
    assert rc == 0


def test_run_core_data_check_auto_yes_skips_prompt(monkeypatch):
    """auto_yes=True → no input() call, treated as yes."""
    monkeypatch.setattr(ncmu_init, "check_core_data_present", lambda: False)
    monkeypatch.setattr(ncmu_init, "list_recent_snapshots", lambda **kw: [])
    # Guard: input() must NOT be called
    input_mock = mock.MagicMock(side_effect=AssertionError("input() should not be called"))
    monkeypatch.setattr("builtins.input", input_mock)
    rc = ncmu_init.run_core_data_check(auto_yes=True)
    assert rc is None
    input_mock.assert_not_called()


def test_run_core_data_check_env_auto_yes_skips_prompt(monkeypatch):
    """NCMU_INIT_AUTO_YES=1 env → equivalent to auto_yes=True."""
    monkeypatch.setattr(ncmu_init, "check_core_data_present", lambda: False)
    monkeypatch.setattr(ncmu_init, "list_recent_snapshots", lambda **kw: [])
    monkeypatch.setenv("NCMU_INIT_AUTO_YES", "1")
    input_mock = mock.MagicMock(side_effect=AssertionError("input() should not be called"))
    monkeypatch.setattr("builtins.input", input_mock)
    rc = ncmu_init.run_core_data_check()
    assert rc is None


def test_run_core_data_check_lists_snapshots_in_warn(monkeypatch, capsys, tmp_path):
    """STDERR warn must include snapshot list (timestamp + size) — operator
    needs the info to pick a target before running restore.sh."""
    monkeypatch.setattr(ncmu_init, "check_core_data_present", lambda: False)
    monkeypatch.setattr(
        ncmu_init,
        "list_recent_snapshots",
        lambda **kw: [
            ("20260506-120000", 9876543),
            ("20260505-181040", 1234567),
        ],
    )
    monkeypatch.setattr("builtins.input", lambda *a, **kw: "no")
    ncmu_init.run_core_data_check()
    err = capsys.readouterr().err
    assert "20260506-120000" in err
    assert "20260505-181040" in err


def test_run_core_data_check_handles_eof_as_reject(monkeypatch, capsys):
    """input() raises EOFError (closed stdin / non-tty) → treat as reject."""
    monkeypatch.setattr(ncmu_init, "check_core_data_present", lambda: False)
    monkeypatch.setattr(ncmu_init, "list_recent_snapshots", lambda **kw: [])
    def _eof(*a, **kw):
        raise EOFError
    monkeypatch.setattr("builtins.input", _eof)
    rc = ncmu_init.run_core_data_check()
    assert rc == 0


# ─────────────────────────────────────────────────────────────────────
# _format_bytes (REWORK-41 LOW#3)
# ─────────────────────────────────────────────────────────────────────


def test_format_bytes_unit_boundaries():
    """Binary (1024) progression across B/KB/MB/GB/TB unit boundaries.

    Implementation uses 1024 as the radix and ``f"{n:.1f} <unit>"`` for
    KB/MB/GB/TB; raw integer ``f"{n} B"`` for sub-1024 values.
    """
    fb = ncmu_init._format_bytes
    # B unit (no decimal, raw int)
    assert fb(0) == "0 B"
    assert fb(1023) == "1023 B"
    # KB starts at 1024 (1.0 KB) — boundary is `< 1024` so 1024 → next unit
    assert fb(1024) == "1.0 KB"
    # MB / GB / TB starts at 1024^k
    assert fb(1024 * 1024) == "1.0 MB"
    assert fb(1024 ** 3) == "1.0 GB"
    assert fb(1024 ** 4) == "1.0 TB"
