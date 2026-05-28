"""Codex M3: the daily-feishu-push.plist header comment used to suggest
running `daily_sentiment_scan.py --no-feishu` for manual testing — but
that command runs the legacy default path, not `--push`. This test guards
against the documentation drifting back.
"""
from __future__ import annotations

from pathlib import Path


PLIST_PATH = (
    Path(__file__).resolve().parents[2]
    / "web" / "launchd" / "com.tradingagents.daily-feishu-push.plist"
)


def test_plist_exists():
    assert PLIST_PATH.exists()


def test_plist_doc_uses_push_no_feishu_for_manual_test():
    """The header comment must recommend `--push --no-feishu`, not bare
    `--no-feishu` (which hits the legacy default path)."""
    text = PLIST_PATH.read_text(encoding="utf-8")
    assert "--push --no-feishu" in text
    # And the wrong command must not still be in there
    lines_with_no_feishu_only = [
        l for l in text.splitlines()
        if "--no-feishu" in l and "--push" not in l and "scripts/daily_sentiment_scan.py" in l
    ]
    assert lines_with_no_feishu_only == [], (
        f"plist still recommends bare `--no-feishu`: {lines_with_no_feishu_only}"
    )
