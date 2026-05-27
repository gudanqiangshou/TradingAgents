"""Tests for get_google_trends in google_trends.py.

All tests are marked @pytest.mark.unit. No network calls.

TrendReq is imported inside the function body (``from pytrends.request import TrendReq``),
so tests must patch ``pytrends.request.TrendReq`` — not a module-level attribute.
"""
from __future__ import annotations

import sys
import types
import pytest
import pandas as pd
from datetime import datetime
from unittest.mock import MagicMock, patch

from tradingagents.dataflows.google_trends import get_google_trends


def _make_trends_df(ticker: str = "AAPL", n: int = 31) -> pd.DataFrame:
    """Build a fake interest_over_time DataFrame mimicking pytrends output."""
    dates = pd.date_range(start="2026-04-27", periods=n, freq="D", tz="UTC")
    data = {ticker: [50 + i for i in range(n)], "isPartial": [False] * (n - 1) + [True]}
    df = pd.DataFrame(data, index=dates)
    df.index.name = "date"
    return df


def _patch_trendreq(mock_instance):
    """Return a context manager that patches pytrends.request.TrendReq.

    TrendReq is imported inside the function via `from pytrends.request import TrendReq`.
    Patching the module attribute directly is the most reliable approach.
    """
    # Ensure pytrends.request exists as a module in sys.modules
    if "pytrends" not in sys.modules:
        pytrends_mod = types.ModuleType("pytrends")
        pytrends_req = types.ModuleType("pytrends.request")
        pytrends_req.TrendReq = MagicMock(return_value=mock_instance)
        pytrends_mod.request = pytrends_req
        sys.modules["pytrends"] = pytrends_mod
        sys.modules["pytrends.request"] = pytrends_req

    return patch("pytrends.request.TrendReq", return_value=mock_instance)


@pytest.mark.unit
def test_happy_path():
    """Mock TrendReq; assert output has expected sections."""
    df = _make_trends_df("AAPL", 31)

    mock_pt = MagicMock()
    mock_pt.interest_over_time.return_value = df

    with _patch_trendreq(mock_pt):
        result = get_google_trends("AAPL", lookback_days=30, geo="US")

    assert "Google Trends" in result
    assert "Latest value" in result
    assert "trend =" in result
    assert "Last 10 data points" in result
    assert "Interpretation" in result


@pytest.mark.unit
def test_pytrends_unavailable():
    """ImportError during import → Error string.

    Since TrendReq is imported inside the function body, we simulate
    ImportError by patching pytrends.request to raise on attribute access.
    """
    mock_pt = MagicMock()
    mock_pt.interest_over_time.side_effect = ImportError("No module named 'pytrends'")

    # Make TrendReq(...) raise ImportError by patching the constructor
    with patch("pytrends.request.TrendReq", side_effect=ImportError("No module named 'pytrends'")):
        result = get_google_trends("AAPL")

    assert isinstance(result, str)
    assert result.startswith("Error")


@pytest.mark.unit
def test_pytrends_returns_empty_df():
    """Empty df → 'No Google Trends data found' string."""
    mock_pt = MagicMock()
    mock_pt.interest_over_time.return_value = pd.DataFrame()

    with _patch_trendreq(mock_pt):
        result = get_google_trends("AAPL")

    assert isinstance(result, str)
    assert "No Google Trends data found" in result


@pytest.mark.unit
def test_pytrends_raises_exception():
    """interest_over_time raises → Error string, no raise."""
    mock_pt = MagicMock()
    mock_pt.interest_over_time.side_effect = RuntimeError("rate limited")

    with _patch_trendreq(mock_pt):
        result = get_google_trends("AAPL")

    assert isinstance(result, str)
    assert result.startswith("Error")


@pytest.mark.unit
def test_invalid_ticker_returns_error():
    """Empty string ticker → Error string."""
    result = get_google_trends("")
    assert isinstance(result, str)
    assert result.startswith("Error")
    assert "invalid ticker" in result.lower()


@pytest.mark.unit
def test_invalid_ticker_whitespace_returns_error():
    """Whitespace-only ticker → Error string."""
    result = get_google_trends("   ")
    assert isinstance(result, str)
    assert result.startswith("Error")


@pytest.mark.unit
@pytest.mark.parametrize("lookback,expected_tf", [
    (7, "now 7-d"),
    (30, "today 1-m"),
    (90, "today 3-m"),
    (365, "today 12-m"),
    (1000, "today 5-y"),
])
def test_lookback_days_mapping(lookback, expected_tf):
    """Correct timeframe string passed to build_payload for each lookback window."""
    df = _make_trends_df("AAPL", 31)
    mock_pt = MagicMock()
    mock_pt.interest_over_time.return_value = df

    with _patch_trendreq(mock_pt):
        get_google_trends("AAPL", lookback_days=lookback, geo="US")

    mock_pt.build_payload.assert_called_once()
    # Check via any possible call form
    call_str = str(mock_pt.build_payload.call_args)
    assert expected_tf in call_str


@pytest.mark.unit
def test_pytrends_returns_none():
    """None from interest_over_time → 'No Google Trends data found' string."""
    mock_pt = MagicMock()
    mock_pt.interest_over_time.return_value = None

    with _patch_trendreq(mock_pt):
        result = get_google_trends("AAPL")

    assert isinstance(result, str)
    assert "No Google Trends data found" in result


@pytest.mark.unit
def test_pytrends_returns_df_without_ticker_column():
    """interest_over_time returns df without AAPL column → error string mentioning no column; no raise."""
    dates = pd.date_range(start="2026-04-27", periods=31, freq="D", tz="UTC")
    df = pd.DataFrame(
        {"other_ticker": [50] * 31, "isPartial": [False] * 31},
        index=dates,
    )
    mock_pt = MagicMock()
    mock_pt.interest_over_time.return_value = df

    with _patch_trendreq(mock_pt):
        result = get_google_trends("AAPL")

    assert isinstance(result, str)
    # Should mention no column found for AAPL
    assert "no Google Trends data column" in result.lower() or "No Google Trends data column" in result


@pytest.mark.unit
def test_pytrends_all_zero_series_returns_stable_trend():
    """interest_over_time returns df where ticker column is all zeros →
    trend = stable; no crash on zero-division."""
    dates = pd.date_range(start="2026-04-27", periods=31, freq="D", tz="UTC")
    df = pd.DataFrame(
        {"AAPL": [0] * 31, "isPartial": [False] * 31},
        index=dates,
    )
    mock_pt = MagicMock()
    mock_pt.interest_over_time.return_value = df

    with _patch_trendreq(mock_pt):
        result = get_google_trends("AAPL")

    assert isinstance(result, str)
    assert "trend = stable" in result
