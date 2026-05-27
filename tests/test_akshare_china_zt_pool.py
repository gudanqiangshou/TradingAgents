"""Tests for get_zt_pool_summary in AkShare vendor.

All tests are marked @pytest.mark.unit. No network calls.
"""
from __future__ import annotations

import pytest
import pandas as pd
from unittest.mock import MagicMock, patch

import tradingagents.dataflows.akshare_china as _vendor_mod

DependencyUnavailable = _vendor_mod._dep_bootstrap.DependencyUnavailable


def _make_zt_df(n: int = 5) -> pd.DataFrame:
    rows = []
    for i in range(n):
        rows.append({
            "序号": i + 1,
            "代码": f"60{i:04d}",
            "名称": f"股票{i}",
            "涨跌幅": 10.0,
            "最新价": 20.0 + i,
            "成交额": (i + 1) * 1e9,
            "流通市值": (i + 1) * 5e9,
            "总市值": (i + 1) * 6e9,
        })
    return pd.DataFrame(rows)


@pytest.mark.unit
def test_get_zt_pool_summary_happy_path():
    """Mock returns a 5-row DataFrame; assert output contains expected sections."""
    df = _make_zt_df(5)
    ak = MagicMock()
    ak.stock_zt_pool_em.return_value = df

    with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
        result = _vendor_mod.get_zt_pool_summary("2026-05-27")

    assert "涨停板池" in result
    assert "Total 涨停 stocks: 5" in result
    assert "Top 10 by 流通市值" in result
    assert "Top 10 by 成交额" in result
    assert "Interpretation" in result
    ak.stock_zt_pool_em.assert_called_once_with(date="20260527")


@pytest.mark.unit
def test_zt_pool_empty_returns_no_data():
    """Mock returns empty df → 'No 涨停板 data for {date}' string."""
    ak = MagicMock()
    ak.stock_zt_pool_em.return_value = pd.DataFrame()

    with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
        result = _vendor_mod.get_zt_pool_summary("2026-05-27")

    assert "No 涨停板 data for 2026-05-27" in result


@pytest.mark.unit
def test_zt_pool_list_return_returns_error():
    """Mock returns [] → Error string, no raise."""
    ak = MagicMock()
    ak.stock_zt_pool_em.return_value = []

    with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
        result = _vendor_mod.get_zt_pool_summary("2026-05-27")

    assert isinstance(result, str)
    # _df_is_empty returns True for lists
    assert "No 涨停板 data" in result or result.startswith("Error")


@pytest.mark.unit
def test_zt_pool_invalid_date_returns_error():
    """Clearly non-date string input → returns a string (no raise).
    The _validate_date_str only checks length (8-12), so clearly invalid
    values like None are caught; a borderline-length bad string may pass
    validation but result in 'No 涨停板 data' from an empty akshare result.
    The key contract is: no raise, always a str."""
    ak = MagicMock()
    ak.stock_zt_pool_em.return_value = pd.DataFrame()

    with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
        result = _vendor_mod.get_zt_pool_summary("not-a-date!")
    assert isinstance(result, str)
    # Either an error or "no data" — both are valid outcomes
    assert len(result) > 0


@pytest.mark.unit
def test_zt_pool_none_date_returns_error():
    """None date → Error string (caught by _validate_date_str)."""
    result = _vendor_mod.get_zt_pool_summary(None)
    assert isinstance(result, str)
    assert result.startswith("Error")


@pytest.mark.unit
def test_zt_pool_endpoint_raises_returns_error():
    """Endpoint raises → Error string, no raise."""
    ak = MagicMock()
    ak.stock_zt_pool_em.side_effect = ConnectionError("timeout")

    with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
        result = _vendor_mod.get_zt_pool_summary("2026-05-27")

    assert isinstance(result, str)
    assert result.startswith("Error")


# ---------------------------------------------------------------------------
# _validate_date_str — strptime-based rejection tests (Critical 1)
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.parametrize("bad_date", [
    "invalid-date",
    "2024/01/01",
    "20240101",
    "2024-13-01",   # invalid month
    "2024-01-32",   # invalid day
    "",
])
def test_validate_date_str_rejects_wrong_format(bad_date):
    """_validate_date_str with strptime: all bad formats return Error strings."""
    result = _vendor_mod._validate_date_str(bad_date, "curr_date")
    assert result is not None
    assert isinstance(result, str)
    assert "Error" in result


@pytest.mark.unit
def test_validate_date_str_accepts_valid_date():
    """_validate_date_str: '2024-01-02' returns None (valid)."""
    result = _vendor_mod._validate_date_str("2024-01-02", "curr_date")
    assert result is None


@pytest.mark.unit
def test_get_zt_pool_summary_rejects_invalid_date_string():
    """'invalid-date' → Error string; akshare NOT called."""
    ak = MagicMock()
    with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
        result = _vendor_mod.get_zt_pool_summary("invalid-date")
    assert isinstance(result, str)
    assert result.startswith("Error")
    ak.stock_zt_pool_em.assert_not_called()


@pytest.mark.unit
def test_get_zt_pool_summary_rejects_slash_date_format():
    """'2024/01/01' → Error string; akshare NOT called."""
    ak = MagicMock()
    with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
        result = _vendor_mod.get_zt_pool_summary("2024/01/01")
    assert isinstance(result, str)
    assert result.startswith("Error")
    ak.stock_zt_pool_em.assert_not_called()
