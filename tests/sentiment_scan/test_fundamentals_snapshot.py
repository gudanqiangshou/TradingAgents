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


def test_a_share_extracts_pe_marketcap_roe_via_eastmoney(monkeypatch):
    """A 股: 东财 quote 拿 PE+市值, akshare 拿 ROE."""
    import pandas as pd

    fake_quote_response = MagicMock(status_code=200)
    fake_quote_response.json.return_value = {
        "rc": 0,
        "data": {
            "f43": 128600,                        # 1286.00 价格 (×100)
            "f57": "600519",
            "f58": "贵州茅台",
            "f116": 1_607_604_938_886.0,          # 总市值 ≈ 1.6 万亿元
            "f163": 1953,                         # PE TTM 19.53 (×100)
            "f167": 593,                          # PB 5.93
        },
    }
    fake_session = MagicMock()
    fake_session.get.return_value = fake_quote_response

    fake_df = pd.DataFrame({
        "指标": ["净利润", "净资产收益率(ROE)", "摊薄净资产收益率", "营业总收入"],
        "20260331": [8.5e9, 0.31, 0.29, 5.5e10],
        "20251231": [8.2e9, 0.30, 0.28, 5.3e10],
    })
    fake_ak = MagicMock()
    fake_ak.stock_financial_abstract.return_value = fake_df

    with patch(
        "tradingagents.sentiment_scan.fundamentals_snapshot._eastmoney_session",
        return_value=fake_session,
    ), patch(
        "tradingagents.sentiment_scan.fundamentals_snapshot._eastmoney_http_retry",
        side_effect=lambda fn: fn(),
    ), patch(
        "tradingagents.sentiment_scan.fundamentals_snapshot._dep_bootstrap.ensure",
        return_value=fake_ak,
    ):
        result = fetch_structured_fundamentals("600519")

    assert result["ticker"] == "600519"
    assert result["market"] == "A_SHARE"
    assert result["pe_ttm"] == 19.53                  # f163 / 100
    assert result["pe_forward"] is None
    assert result["fcf"] is None
    assert result["roe"] == 0.31                      # 精确匹配 "净资产收益率(ROE)"，不会误中 "摊薄净资产收益率"
    assert result["market_cap"] == 1_607_604_938_886.0
    assert result["currency"] == "CNY"
    assert result["source"] == "akshare+eastmoney"
    assert result["status"] == "partial"
    assert set(result["missing_fields"]) == {"pe_forward", "fcf"}


def test_a_share_eastmoney_zero_pe_treated_as_none(monkeypatch):
    """东财 f163: 0 视为无 PE (停牌/ST 标的的常见编码)."""
    import pandas as pd
    fake_quote_response = MagicMock(status_code=200)
    fake_quote_response.json.return_value = {
        "rc": 0,
        "data": {
            "f43": 50000,
            "f57": "600555",
            "f58": "测试",
            "f116": 5_000_000_000.0,
            "f163": 0,                              # 无 PE
            "f167": 100,
        },
    }
    fake_session = MagicMock(get=MagicMock(return_value=fake_quote_response))
    fake_df = pd.DataFrame({"指标": ["净资产收益率(ROE)"], "20260331": [-0.05]})
    fake_ak = MagicMock(stock_financial_abstract=MagicMock(return_value=fake_df))

    with patch(
        "tradingagents.sentiment_scan.fundamentals_snapshot._eastmoney_session",
        return_value=fake_session,
    ), patch(
        "tradingagents.sentiment_scan.fundamentals_snapshot._eastmoney_http_retry",
        side_effect=lambda fn: fn(),
    ), patch(
        "tradingagents.sentiment_scan.fundamentals_snapshot._dep_bootstrap.ensure",
        return_value=fake_ak,
    ):
        result = fetch_structured_fundamentals("600555")
    assert result["pe_ttm"] is None                  # f163: 0 → None
    assert result["roe"] == -0.05


def test_a_share_secid_prefix_mapping():
    """SH (60/68/90) = 1; SZ (00/30/20) + BJ (4/8) = 0."""
    from tradingagents.sentiment_scan.fundamentals_snapshot import _a_share_secid
    assert _a_share_secid("600519") == "1.600519"    # SH 主板
    assert _a_share_secid("688981") == "1.688981"    # SH 科创板
    assert _a_share_secid("900939") == "1.900939"    # SH B 股
    assert _a_share_secid("000001") == "0.000001"    # SZ 主板
    assert _a_share_secid("300866") == "0.300866"    # SZ 创业板
    assert _a_share_secid("200568") == "0.200568"    # SZ B 股
    assert _a_share_secid("430047") == "0.430047"    # BJ
    assert _a_share_secid("832000") == "0.832000"    # BJ
