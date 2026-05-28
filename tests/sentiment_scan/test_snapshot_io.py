"""Tests for snapshot_io: atomic write + tolerant read."""
import json
from pathlib import Path

import pytest


def test_save_and_load_round_trip(tmp_path):
    from tradingagents.sentiment_scan.snapshot_io import save_snapshot, load_snapshot

    snapshot = {
        "schema_version": 1,
        "date": "2026-05-27",
        "sections": {"section_a": {"display": "..."}},
        "analyses": [{"ticker": "600519", "status": "ok"}],
    }
    target = tmp_path / "2026-05-27.json"
    save_snapshot(str(target), snapshot)
    loaded = load_snapshot(str(target))
    assert loaded == snapshot


def test_load_missing_file_returns_none(tmp_path):
    from tradingagents.sentiment_scan.snapshot_io import load_snapshot
    result = load_snapshot(str(tmp_path / "does-not-exist.json"))
    assert result is None


def test_load_malformed_json_returns_none(tmp_path):
    from tradingagents.sentiment_scan.snapshot_io import load_snapshot
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json")
    assert load_snapshot(str(bad)) is None


def test_load_schema_mismatch_returns_none(tmp_path):
    from tradingagents.sentiment_scan.snapshot_io import load_snapshot
    bad = tmp_path / "future.json"
    bad.write_text(json.dumps({"schema_version": 99, "date": "2099-01-01"}))
    assert load_snapshot(str(bad)) is None


def test_save_is_atomic_tmp_renamed(tmp_path):
    """save_snapshot writes to .tmp then renames — no half-written file visible."""
    from tradingagents.sentiment_scan.snapshot_io import save_snapshot
    target = tmp_path / "2026-05-27.json"
    save_snapshot(str(target), {"schema_version": 1, "date": "2026-05-27"})
    assert target.exists()
    # No leftover .tmp file
    assert not (tmp_path / "2026-05-27.json.tmp").exists()


# ---------------------------------------------------------------------------
# Codex I5 — defense-in-depth + write-error handling
# ---------------------------------------------------------------------------

def test_save_snapshot_injects_schema_version_if_missing(tmp_path):
    """When the caller forgets schema_version, save_snapshot fills it in
    so load_snapshot doesn't reject our own writes."""
    from tradingagents.sentiment_scan.snapshot_io import (
        SCHEMA_VERSION, load_snapshot, save_snapshot,
    )
    target = tmp_path / "no-version.json"
    # Caller forgot the schema_version key
    save_snapshot(str(target), {"date": "2026-05-27", "analyses": []})
    loaded = load_snapshot(str(target))
    assert loaded is not None, "round-trip must succeed"
    assert loaded["schema_version"] == SCHEMA_VERSION


def test_save_snapshot_returns_true_on_success(tmp_path):
    from tradingagents.sentiment_scan.snapshot_io import save_snapshot
    target = tmp_path / "ok.json"
    assert save_snapshot(str(target), {"schema_version": 1}) is True
    assert target.exists()


def test_save_snapshot_returns_false_when_path_is_dir(tmp_path):
    """If `path` is an existing directory (IsADirectoryError on open), the
    function must return False — never raise."""
    from tradingagents.sentiment_scan.snapshot_io import save_snapshot
    a_dir = tmp_path / "a-dir"
    a_dir.mkdir()
    # Pass the directory as the target path. os.replace into a dir raises.
    result = save_snapshot(str(a_dir), {"schema_version": 1})
    assert result is False


def test_save_snapshot_returns_false_on_unserializable(tmp_path):
    """Non-JSON-serializable values must be caught and logged, not raised."""
    from tradingagents.sentiment_scan.snapshot_io import save_snapshot
    target = tmp_path / "bad.json"
    # object() is not JSON serializable → TypeError inside json.dump
    result = save_snapshot(str(target), {"schema_version": 1, "x": object()})
    assert result is False
