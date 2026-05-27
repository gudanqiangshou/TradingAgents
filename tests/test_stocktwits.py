"""Tests for stocktwits.py — including the refined 404 error handling."""

from __future__ import annotations

import json
import re
import pytest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError
from io import BytesIO

from tradingagents.dataflows.stocktwits import fetch_stocktwits_messages, fetch_stocktwits_trending


def _make_http_error(code: int) -> HTTPError:
    """Build a minimal HTTPError with the given code."""
    return HTTPError(
        url="https://api.stocktwits.com/api/2/streams/symbol/TEST.json",
        code=code,
        msg=f"HTTP {code}",
        hdrs=None,
        fp=None,
    )


# ---------------------------------------------------------------------------
# 404 — distinct message
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_stocktwits_404_returns_no_data_message():
    """HTTPError 404 → message contains 'no data for' and 'symbol not in their US-equity database'. No raise."""
    with patch("tradingagents.dataflows.stocktwits.urlopen", side_effect=_make_http_error(404)):
        result = fetch_stocktwits_messages("600519")
    assert isinstance(result, str)
    assert "no data for" in result
    assert "symbol not in their US-equity database" in result


# ---------------------------------------------------------------------------
# Other HTTP errors
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_stocktwits_other_http_error_returns_unavailable():
    """HTTPError 429 → 'HTTP 429' in output. No raise."""
    with patch("tradingagents.dataflows.stocktwits.urlopen", side_effect=_make_http_error(429)):
        result = fetch_stocktwits_messages("AAPL")
    assert isinstance(result, str)
    assert "HTTP 429" in result


@pytest.mark.unit
def test_stocktwits_http_500_returns_unavailable():
    """HTTPError 500 → 'HTTP 500' in output. No raise."""
    with patch("tradingagents.dataflows.stocktwits.urlopen", side_effect=_make_http_error(500)):
        result = fetch_stocktwits_messages("AAPL")
    assert isinstance(result, str)
    assert "HTTP 500" in result


# ---------------------------------------------------------------------------
# URLError / TimeoutError — unchanged behavior
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_stocktwits_timeout_unchanged():
    """TimeoutError → '<stocktwits unavailable: TimeoutError>' shape preserved. No raise."""
    with patch("tradingagents.dataflows.stocktwits.urlopen", side_effect=TimeoutError("timed out")):
        result = fetch_stocktwits_messages("AAPL")
    assert isinstance(result, str)
    assert "<stocktwits unavailable: TimeoutError>" == result


@pytest.mark.unit
def test_stocktwits_url_error_returns_unavailable():
    """URLError → '<stocktwits unavailable: URLError>'. No raise."""
    with patch("tradingagents.dataflows.stocktwits.urlopen", side_effect=URLError("no route")):
        result = fetch_stocktwits_messages("AAPL")
    assert isinstance(result, str)
    assert "<stocktwits unavailable: URLError>" == result


# ===========================================================================
# fetch_stocktwits_trending — tests
# ===========================================================================

def _make_trending_http_error(code: int) -> HTTPError:
    return HTTPError(
        url="https://api.stocktwits.com/api/2/trending/symbols/equities.json",
        code=code,
        msg=f"HTTP {code}",
        hdrs=None,
        fp=None,
    )


def _make_trending_symbols(n: int) -> list:
    return [
        {"id": i, "symbol": f"SYM{i}", "exchange": "NYSE", "title": f"Company {i}"}
        for i in range(1, n + 1)
    ]


def _make_urlopen_mock(payload: dict):
    """Return a context-manager mock that yields a BytesIO of JSON payload."""
    body = BytesIO(json.dumps(payload).encode())
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=body)
    cm.__exit__ = MagicMock(return_value=False)
    return MagicMock(return_value=cm)


@pytest.mark.unit
def test_trending_happy_path():
    """Valid JSON with 30 symbols → emoji header and 30 numbered lines."""
    payload = {"symbols": _make_trending_symbols(30), "response": {"status": 200}}
    with patch("tradingagents.dataflows.stocktwits.urlopen", _make_urlopen_mock(payload)):
        result = fetch_stocktwits_trending(limit=30)
    assert "🇺🇸 StockTwits 美股热议榜" in result
    assert "| Symbol | Exchange | Title |" not in result
    # Count data rows: numbered lines like "1. SYM1 NYSE · Company 1"
    data_rows = [l for l in result.splitlines() if re.match(r"^\d+\. SYM", l)]
    assert len(data_rows) == 30


@pytest.mark.unit
def test_trending_limit_truncates():
    """limit=5 → only 5 numbered lines."""
    payload = {"symbols": _make_trending_symbols(30), "response": {"status": 200}}
    with patch("tradingagents.dataflows.stocktwits.urlopen", _make_urlopen_mock(payload)):
        result = fetch_stocktwits_trending(limit=5)
    data_rows = [l for l in result.splitlines() if re.match(r"^\d+\. SYM", l)]
    assert len(data_rows) == 5


@pytest.mark.unit
def test_trending_endpoint_returns_empty_list():
    """data['symbols']=[] → 'StockTwits 热议榜 暂无数据'."""
    payload = {"symbols": [], "response": {"status": 200}}
    with patch("tradingagents.dataflows.stocktwits.urlopen", _make_urlopen_mock(payload)):
        result = fetch_stocktwits_trending()
    assert result == "StockTwits 热议榜 暂无数据"


@pytest.mark.unit
def test_trending_404_returns_clear_message():
    """HTTPError 404 → message contains '404 from endpoint'."""
    with patch("tradingagents.dataflows.stocktwits.urlopen", side_effect=_make_trending_http_error(404)):
        result = fetch_stocktwits_trending()
    assert "404 from endpoint" in result


@pytest.mark.unit
def test_trending_403_cloudflare_returns_clear_message():
    """HTTPError 403 → message mentions 'anti-bot' and 'blocked'."""
    with patch("tradingagents.dataflows.stocktwits.urlopen", side_effect=_make_trending_http_error(403)):
        result = fetch_stocktwits_trending()
    assert "anti-bot" in result
    assert "blocked" in result


@pytest.mark.unit
def test_trending_timeout_returns_unavailable():
    """TimeoutError → '<StockTwits 热议榜 暂不可用: TimeoutError>'."""
    with patch("tradingagents.dataflows.stocktwits.urlopen", side_effect=TimeoutError("timed out")):
        result = fetch_stocktwits_trending()
    assert result == "<StockTwits 热议榜 暂不可用: TimeoutError>"


@pytest.mark.unit
@pytest.mark.parametrize("bad_limit", [0, -1, None, float("inf"), True])
def test_trending_invalid_limit_returns_error_string(bad_limit):
    """Invalid limit values → Error string, no raise."""
    result = fetch_stocktwits_trending(limit=bad_limit)
    assert isinstance(result, str)
    assert result.startswith("Error:")


@pytest.mark.unit
def test_trending_malformed_json_returns_unavailable():
    """urlopen returns non-JSON bytes → unavailable string."""
    body = BytesIO(b"Just a moment...")
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=body)
    cm.__exit__ = MagicMock(return_value=False)
    mock_open = MagicMock(return_value=cm)
    with patch("tradingagents.dataflows.stocktwits.urlopen", mock_open):
        result = fetch_stocktwits_trending()
    assert "暂不可用" in result


@pytest.mark.unit
def test_trending_symbols_not_list_returns_no_data():
    """JSON has data['symbols']='not a list' → 'No StockTwits trending...' string."""
    payload = {"symbols": "not a list", "response": {"status": 200}}
    with patch("tradingagents.dataflows.stocktwits.urlopen", _make_urlopen_mock(payload)):
        result = fetch_stocktwits_trending()
    assert "StockTwits 热议榜 暂无数据" in result


@pytest.mark.unit
def test_trending_symbol_missing_key_skipped():
    """Entry without 'symbol' field → skipped; valid entries still included."""
    symbols = [
        {"id": 1, "exchange": "NYSE", "title": "No Symbol Entry"},  # no 'symbol' key
        {"id": 2, "symbol": "GOOD", "exchange": "NASDAQ", "title": "Good Entry"},
    ]
    payload = {"symbols": symbols, "response": {"status": 200}}
    with patch("tradingagents.dataflows.stocktwits.urlopen", _make_urlopen_mock(payload)):
        result = fetch_stocktwits_trending(limit=30)
    assert "GOOD" in result
    assert "No Symbol Entry" not in result
    # Only 1 valid numbered row
    data_rows = [l for l in result.splitlines() if re.match(r"^\d+\.", l)]
    assert len(data_rows) == 1
