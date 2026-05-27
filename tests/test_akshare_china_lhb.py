"""Tests for get_lhb_summary in AkShare vendor.

All tests are marked @pytest.mark.unit. No network calls.
"""
from __future__ import annotations

import pytest
import pandas as pd
from unittest.mock import MagicMock, patch

import tradingagents.dataflows.akshare_china as _vendor_mod

DependencyUnavailable = _vendor_mod._dep_bootstrap.DependencyUnavailable


def _make_lhb_df(include_600519: bool = True) -> pd.DataFrame:
    rows = []
    if include_600519:
        rows.append({
            "序号": 1,
            "代码": "600519",
            "名称": "贵州茅台",
            "上榜日": "2026-05-26",
            "解读": "1家机构买入，成功率47.25%",
            "收盘价": 1800.0,
            "涨跌幅": "5.20%",
            "龙虎榜净买额": 23000000,
        })
    for i in range(5):
        rows.append({
            "序号": i + 2,
            "代码": f"00000{i+1}",
            "名称": f"其他股{i+1}",
            "上榜日": "2026-05-26",
            "解读": f"游资买入{i+1}",
            "收盘价": 10.0 + i,
            "涨跌幅": f"{i+1}.0%",
            "龙虎榜净买额": (i + 1) * 1000000,
        })
    return pd.DataFrame(rows)


@pytest.mark.unit
def test_lhb_ticker_listed():
    """Mock returns 5 rows including target 600519; output has 'Ticker-specific 上榜' with the row."""
    df = _make_lhb_df(include_600519=True)
    ak = MagicMock()
    ak.stock_lhb_detail_em.return_value = df

    with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
        result = _vendor_mod.get_lhb_summary("600519", "2026-05-27", days_back=5)

    assert "龙虎榜" in result
    assert "Ticker-specific 上榜" in result
    assert "贵州茅台" in result or "600519" in result
    assert "Market-wide context" in result


@pytest.mark.unit
def test_lhb_ticker_not_listed():
    """Mock returns rows for other tickers only; output has '未上榜'."""
    df = _make_lhb_df(include_600519=False)
    ak = MagicMock()
    ak.stock_lhb_detail_em.return_value = df

    with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
        result = _vendor_mod.get_lhb_summary("600519", "2026-05-27", days_back=5)

    assert isinstance(result, str)
    assert "未上榜" in result or "not on 龙虎榜" in result


@pytest.mark.unit
def test_lhb_market_wide_top5():
    """Both Top 5 净买入 and Top 5 净卖出 sections must exist."""
    df = _make_lhb_df(include_600519=True)
    ak = MagicMock()
    ak.stock_lhb_detail_em.return_value = df

    with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
        result = _vendor_mod.get_lhb_summary("600519", "2026-05-27", days_back=5)

    assert "Top 5 净买入" in result
    assert "Top 5 净卖出" in result


@pytest.mark.unit
def test_lhb_invalid_date():
    """Bad date → Error string."""
    result = _vendor_mod.get_lhb_summary("600519", "bad-date!")
    assert isinstance(result, str)
    assert result.startswith("Error")


@pytest.mark.unit
def test_lhb_empty_df():
    """Empty df → 'No 龙虎榜 data ...' string."""
    ak = MagicMock()
    ak.stock_lhb_detail_em.return_value = pd.DataFrame()

    with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
        result = _vendor_mod.get_lhb_summary("600519", "2026-05-27", days_back=5)

    assert isinstance(result, str)
    assert "No 龙虎榜 data" in result


@pytest.mark.unit
def test_lhb_endpoint_raises():
    """Endpoint raises → Error string, no raise."""
    ak = MagicMock()
    ak.stock_lhb_detail_em.side_effect = ConnectionError("timeout")

    with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
        result = _vendor_mod.get_lhb_summary("600519", "2026-05-27", days_back=5)

    assert isinstance(result, str)
    assert result.startswith("Error")


@pytest.mark.unit
def test_lhb_list_return():
    """Endpoint returns list → Error string, no raise."""
    ak = MagicMock()
    ak.stock_lhb_detail_em.return_value = []

    with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
        result = _vendor_mod.get_lhb_summary("600519", "2026-05-27", days_back=5)

    assert isinstance(result, str)
    assert "No 龙虎榜 data" in result or result.startswith("Error")
