"""Tests for fundamentals_snapshot.fetch_structured_fundamentals."""
from unittest.mock import MagicMock, patch
import pytest

from tradingagents.sentiment_scan.fundamentals_snapshot import (
    fetch_structured_fundamentals,
)


def test_us_ticker_returns_full_fields(monkeypatch):
    """yf.Ticker(t).info dict → structured dict with PE/forwardPE/FCF/ROE."""
    fake_info = {
        "longName": "Apple Inc",
        "trailingPE": 28.5,
        "forwardPE": 25.1,
        "freeCashflow": 9.5e10,
        "returnOnEquity": 1.4523,
        "marketCap": 3.5e12,
        "currency": "USD",
    }
    fake_ticker = MagicMock()
    fake_ticker.info = fake_info

    with patch("tradingagents.sentiment_scan.fundamentals_snapshot.yf.Ticker", return_value=fake_ticker):
        with patch("tradingagents.sentiment_scan.fundamentals_snapshot.yf_retry", side_effect=lambda fn: fn()):
            result = fetch_structured_fundamentals("AAPL")

    assert result["ticker"] == "AAPL"
    assert result["market"] == "US"
    assert result["pe_ttm"] == 28.5
    assert result["pe_forward"] == 25.1
    assert result["fcf"] == 9.5e10
    assert result["roe"] == 1.4523
    assert result["market_cap"] == 3.5e12
    assert result["currency"] == "USD"
    assert result["source"] == "yfinance"
    assert result["status"] == "ok"
    assert result["missing_fields"] == []
