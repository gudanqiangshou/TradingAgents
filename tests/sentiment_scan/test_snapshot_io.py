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
