"""Tests for feishu_elements — extracted from scripts/daily_sentiment_scan.py
to break circular import; this test ensures the extracted module works
standalone and the re-export from scripts/daily_sentiment_scan still works."""


def test_parse_line_with_a_share_prefixed_code():
    from tradingagents.sentiment_scan.feishu_elements import _parse_line_to_feishu_elements
    result = _parse_line_to_feishu_elements("🔥 SH600519 茅台 排名 #3", False)
    # Should contain an 'a' tag linking SH600519 to xueqiu
    links = [e for e in result if e.get("tag") == "a"]
    assert len(links) == 1
    assert links[0]["text"] == "SH600519"
    assert links[0]["href"] == "https://xueqiu.com/S/SH600519"


def test_parse_line_with_bare_a_share_code_auto_prefix():
    from tradingagents.sentiment_scan.feishu_elements import _parse_line_to_feishu_elements
    result = _parse_line_to_feishu_elements("🐂 600519 茅台 净买入", False)
    links = [e for e in result if e.get("tag") == "a"]
    assert len(links) == 1
    assert links[0]["text"] == "600519"
    assert links[0]["href"] == "https://xueqiu.com/S/SH600519"


def test_parse_line_with_us_ticker_in_stocktwits_section():
    from tradingagents.sentiment_scan.feishu_elements import _parse_line_to_feishu_elements
    result = _parse_line_to_feishu_elements("1. AAPL NASDAQ · Apple Inc", True)
    links = [e for e in result if e.get("tag") == "a"]
    assert len(links) == 1
    assert links[0]["text"] == "AAPL"
    assert links[0]["href"] == "https://stocktwits.com/symbol/AAPL"


def test_reexport_from_scripts_still_works():
    """Existing code that did `from scripts.daily_sentiment_scan import
    _parse_line_to_feishu_elements` must keep working — verify the re-export."""
    from scripts.daily_sentiment_scan import _parse_line_to_feishu_elements as via_scripts
    from tradingagents.sentiment_scan.feishu_elements import _parse_line_to_feishu_elements as via_module
    assert via_scripts is via_module
