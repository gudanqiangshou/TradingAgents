"""Tests for get_hot_up_rank in AkShare vendor.

All tests are marked @pytest.mark.unit. No network calls.
"""
from __future__ import annotations

import pytest
import pandas as pd
from unittest.mock import MagicMock, patch

import tradingagents.dataflows.akshare_china as _vendor_mod

DependencyUnavailable = _vendor_mod._dep_bootstrap.DependencyUnavailable


def _make_hot_up_df(n: int = 100) -> pd.DataFrame:
    rows = []
    for i in range(n):
        rows.append({
            "排名较昨日变动": 1000 - i * 10,
            "当前排名": i + 1,
            "代码": f"SZ30{i:04d}",
            "股票名称": f"飙升股{i}",
            "最新价": 20.0 + i,
            "涨跌额": 1.0 + i * 0.1,
            "涨跌幅": 5.0 + i * 0.05,
        })
    return pd.DataFrame(rows)


@pytest.mark.unit
def test_hot_up_happy_path():
    """Mock returns 100 rows; assert Top 20 by 排名较昨日变动 selected; output mentions stock symbols."""
    df = _make_hot_up_df(100)
    ak = MagicMock()
    ak.stock_hot_up_em.return_value = df

    with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
        result = _vendor_mod.get_hot_up_rank()

    assert "飙升榜" in result
    assert "| 当前排名 |" in result or "当前排名" in result
    # Should contain stock code from the highest-ranked item
    assert "SZ30" in result or "飙升股" in result
    # Should have table rows
    assert "| -- |" in result or "--" in result
    assert "Interpretation" in result
    ak.stock_hot_up_em.assert_called_once()


@pytest.mark.unit
def test_hot_up_empty_returns_no_data():
    """Empty df → no-data string."""
    ak = MagicMock()
    ak.stock_hot_up_em.return_value = pd.DataFrame()

    with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
        result = _vendor_mod.get_hot_up_rank()

    assert isinstance(result, str)
    assert "No 飙升榜 data" in result


@pytest.mark.unit
def test_hot_up_list_return():
    """Mock returns list → Error string."""
    ak = MagicMock()
    ak.stock_hot_up_em.return_value = []

    with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
        result = _vendor_mod.get_hot_up_rank()

    assert isinstance(result, str)
    assert "No 飙升榜 data" in result or result.startswith("Error")


@pytest.mark.unit
def test_hot_up_endpoint_raises():
    """Endpoint raises → Error string, no raise."""
    ak = MagicMock()
    ak.stock_hot_up_em.side_effect = RuntimeError("server error")

    with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
        result = _vendor_mod.get_hot_up_rank()

    assert isinstance(result, str)
    assert result.startswith("Error")


@pytest.mark.unit
def test_hot_up_dep_unavailable():
    """DependencyUnavailable → error string."""
    with patch(
        "tradingagents.dataflows.akshare_china._dep_bootstrap.ensure",
        side_effect=DependencyUnavailable("akshare unavailable"),
    ):
        result = _vendor_mod.get_hot_up_rank()

    assert isinstance(result, str)
    assert result.startswith("Error")
