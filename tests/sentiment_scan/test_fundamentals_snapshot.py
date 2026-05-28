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


def test_hk_extracts_pe_marketcap_roe_via_eastmoney(monkeypatch):
    """HK: secid=116.{zfill5}, ROE_AVG ÷100 转 ratio."""
    import pandas as pd

    fake_quote_response = MagicMock(status_code=200)
    fake_quote_response.json.return_value = {
        "rc": 0,
        "data": {
            "f43": 421800,                        # 4218.00 HKD (×100)
            "f57": "00700",
            "f58": "腾讯控股",
            "f116": 3_845_998_292_193.0,          # 3.85 万亿 HKD
            "f163": 1711,                         # PE TTM 17.11
            "f167": 301,
        },
    }
    fake_session = MagicMock(get=MagicMock(return_value=fake_quote_response))

    fake_df = pd.DataFrame([{
        "REPORT_DATE": "2026-03-31",
        "ROE_AVG": 21.13,                          # 百分点 - 需 ÷100
        "CURRENCY": "HKD",
    }])
    fake_ak = MagicMock(
        stock_financial_hk_analysis_indicator_em=MagicMock(return_value=fake_df),
    )

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
        result = fetch_structured_fundamentals("0700.HK")

    assert result["ticker"] == "00700.HK"
    assert result["market"] == "HK"
    assert result["pe_ttm"] == 17.11
    assert result["pe_forward"] is None
    assert result["fcf"] is None
    assert result["roe"] == pytest.approx(0.2113, abs=1e-4)   # 21.13 / 100
    assert result["market_cap"] == 3_845_998_292_193.0
    assert result["currency"] == "HKD"
    assert result["source"] == "akshare+eastmoney"


def test_hk_secid_format():
    from tradingagents.sentiment_scan.fundamentals_snapshot import _hk_secid
    assert _hk_secid("0700") == "116.00700"        # 4-digit zero-pad
    assert _hk_secid("00700") == "116.00700"
    assert _hk_secid("0700.HK") == "116.00700"
    assert _hk_secid("9988.HK") == "116.09988"     # 阿里
    assert _hk_secid("01024") == "116.01024"


def test_yfinance_stub_dict_returns_error_deterministic():
    """yfinance 对无效 ticker 返 {trailingPegRatio: None} truthy stub.
    Sentinel-key 应拒识别 → outer try catches → status=error.
    完全 mock 不打网络 — deterministic 测试 (replaces v1 plan 中真发 yahoo
    HTTP 的 INVALID_NOT_A_TICKER parametrize case)."""
    fake_ticker = MagicMock()
    fake_ticker.info = {"trailingPegRatio": None}   # 真实 stub shape
    with patch(
        "tradingagents.sentiment_scan.fundamentals_snapshot.yf.Ticker",
        return_value=fake_ticker,
    ), patch(
        "tradingagents.sentiment_scan.fundamentals_snapshot.yf_retry",
        side_effect=lambda fn: fn(),
    ):
        result = fetch_structured_fundamentals("FAKEXYZ")
    assert result["status"] == "error"
    assert ("stub" in result["error"]) or ("no recognized fields" in result["error"])
    assert result["market"] == "US"   # market 在 error path 仍保留


def test_yfinance_exception_returns_error_status():
    with patch(
        "tradingagents.sentiment_scan.fundamentals_snapshot.yf.Ticker",
        side_effect=RuntimeError("network down"),
    ):
        result = fetch_structured_fundamentals("AAPL")
    assert result["status"] == "error"
    assert "RuntimeError" in result["error"]
    assert result["pe_ttm"] is None
    assert result["market"] == "US"   # ground-truth: market preserved on error


def test_a_share_eastmoney_failure_then_akshare_failure_returns_error_status():
    """两个 vendor 都挂时 status=error 不抛."""
    fake_session = MagicMock()
    fake_session.get.side_effect = RuntimeError("eastmoney down")
    fake_ak = MagicMock()
    fake_ak.stock_financial_abstract.side_effect = RuntimeError("akshare down")
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
    assert result["status"] == "error"
    assert result["market"] == "A_SHARE"


@pytest.mark.parametrize("bad", [None, "", [], 12345])
def test_bad_inputs_never_throw(bad):
    """非字符串/空串/list/int → status=error，不抛."""
    result = fetch_structured_fundamentals(bad)  # type: ignore[arg-type]
    assert isinstance(result, dict)
    assert result["status"] == "error"
    assert result["pe_ttm"] is None
