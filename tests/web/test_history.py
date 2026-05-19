import json
import pytest
from web import history


@pytest.fixture
def hist(tmp_path, monkeypatch):
    d = tmp_path / "web"
    monkeypatch.setattr(history, "HISTORY_DIR", d)
    monkeypatch.setattr(history, "_INDEX", d / "history.json")
    return history


def test_list_empty_when_no_history(hist):
    assert hist.list_history() == []


def test_save_creates_report_and_index(hist):
    entry = hist.save_analysis("AAPL", "2026-05-19", "BUY", "# Report\nbody")
    assert entry["ticker"] == "AAPL"
    assert entry["date"] == "2026-05-19"
    assert entry["action"] == "BUY"
    assert entry["id"].startswith("AAPL_")
    assert "created_at" in entry
    report_file = hist.HISTORY_DIR / entry["id"] / "report.md"
    assert report_file.read_text(encoding="utf-8") == "# Report\nbody"


def test_list_returns_newest_first(hist):
    a = hist.save_analysis("AAPL", "2026-05-19", "BUY", "a")
    b = hist.save_analysis("TSLA", "2026-05-19", "SELL", "b")
    items = hist.list_history()
    assert [i["id"] for i in items] == [b["id"], a["id"]]


def test_get_report_roundtrip(hist):
    e = hist.save_analysis("NVDA", "2026-05-19", "HOLD", "## NVDA\ncontent here")
    assert hist.get_report(e["id"]) == "## NVDA\ncontent here"


def test_get_report_unknown_returns_none(hist):
    assert hist.get_report("NOPE_20260101-000000") is None


def test_get_report_rejects_path_traversal(hist):
    assert hist.get_report("../../etc/passwd") is None
    assert hist.get_report("..%2f..%2fetc") is None


def test_index_survives_corruption(hist):
    hist.save_analysis("AAPL", "2026-05-19", "BUY", "a")
    hist._INDEX.write_text("{ not valid json", encoding="utf-8")
    # Corrupt index degrades to empty list, then a new save rebuilds cleanly.
    assert hist.list_history() == []
    e = hist.save_analysis("TSLA", "2026-05-19", "SELL", "b")
    items = hist.list_history()
    assert items == [{"id": e["id"], "ticker": "TSLA", "date": "2026-05-19",
                      "action": "SELL", "created_at": e["created_at"]}]


def test_history_capped_and_old_dirs_pruned(hist, monkeypatch):
    monkeypatch.setattr(hist, "MAX_HISTORY", 3)
    ids = [hist.save_analysis("AAA", "2026-05-19", "BUY", f"r{i}")["id"]
           for i in range(5)]
    items = hist.list_history()
    assert len(items) == 3
    kept = {i["id"] for i in items}
    assert kept == set(ids[-3:])               # newest 3 kept
    for old in ids[:2]:                          # oldest 2 dirs deleted
        assert not (hist.HISTORY_DIR / old).exists()
    for new in ids[-3:]:
        assert (hist.HISTORY_DIR / new / "report.md").exists()


def test_list_history_limit(hist):
    for i in range(5):
        hist.save_analysis("AAA", "2026-05-19", "HOLD", str(i))
    assert len(hist.list_history(limit=2)) == 2
    assert len(hist.list_history()) == 5
