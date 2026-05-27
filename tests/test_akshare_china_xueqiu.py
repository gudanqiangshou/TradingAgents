"""Tests for get_xueqiu_attention in AkShare vendor.

All tests are marked @pytest.mark.unit. No network calls.
"""
from __future__ import annotations

import pytest
import pandas as pd
import time as _time
from unittest.mock import MagicMock, patch, call

import tradingagents.dataflows.akshare_china as _vendor_mod

DependencyUnavailable = _vendor_mod._dep_bootstrap.DependencyUnavailable


def _make_xueqiu_df(include_code: str = "SH600519") -> pd.DataFrame:
    rows = []
    for i in range(10):
        code = f"SH60{i:04d}" if i > 0 else include_code
        rows.append({
            "股票代码": code,
            "股票简称": f"股票{i}",
            "关注": 100000 - i * 1000,
            "最新价": 20.0 + i,
        })
    return pd.DataFrame(rows)


def _clear_xueqiu_cache():
    """Clear the module-level Xueqiu cache between tests."""
    with _vendor_mod._XUEQIU_CACHE_LOCK:
        _vendor_mod._XUEQIU_CACHE.clear()


@pytest.fixture(autouse=True)
def clear_cache():
    _clear_xueqiu_cache()
    yield
    _clear_xueqiu_cache()


@pytest.mark.unit
def test_xueqiu_ticker_found():
    """Mock akshare returns fake df with target ticker; output has 关注数, 雪球排名, percentile."""
    df_hot = _make_xueqiu_df(include_code="SH600519")
    df_weekly = _make_xueqiu_df(include_code="SH600519")

    ak = MagicMock()
    ak.stock_hot_tweet_xq.side_effect = lambda symbol: (
        df_hot if symbol == "最热门" else df_weekly
    )

    with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
        result = _vendor_mod.get_xueqiu_attention("600519")

    assert isinstance(result, str)
    assert "雪球 attention for 600519" in result
    assert "关注数" in result
    assert "雪球排名" in result
    assert "top" in result.lower() and "%" in result


@pytest.mark.unit
def test_xueqiu_ticker_not_found():
    """Ticker not in df → 'No 雪球 attention data...' string."""
    df_empty = _make_xueqiu_df(include_code="SH999999")

    ak = MagicMock()
    ak.stock_hot_tweet_xq.return_value = df_empty

    with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
        result = _vendor_mod.get_xueqiu_attention("600519")

    assert isinstance(result, str)
    assert "No 雪球 attention data for 600519" in result


@pytest.mark.unit
def test_xueqiu_non_a_share():
    """AAPL → 'not applicable' placeholder; akshare NOT called."""
    ak = MagicMock()

    with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
        result = _vendor_mod.get_xueqiu_attention("AAPL")

    assert isinstance(result, str)
    assert "not applicable" in result.lower()
    # ensure should NOT have been called (market routing before dep check)
    ak.stock_hot_tweet_xq.assert_not_called()


@pytest.mark.unit
def test_xueqiu_non_a_share_does_not_call_akshare():
    """HK ticker → placeholder; ensure NOT called."""
    with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure") as mock_ensure:
        result = _vendor_mod.get_xueqiu_attention("0700.HK")

    assert "not applicable" in result.lower()
    mock_ensure.assert_not_called()


@pytest.mark.unit
def test_xueqiu_cache_hits_within_ttl():
    """Call twice within TTL → akshare called only once per symbol type."""
    df = _make_xueqiu_df(include_code="SH600519")
    ak = MagicMock()
    ak.stock_hot_tweet_xq.return_value = df

    with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
        _vendor_mod.get_xueqiu_attention("600519")
        _vendor_mod.get_xueqiu_attention("600519")

    # Should have been called once per symbol type (最热门 and 本周新增) in the first call,
    # and no new calls on the second call (served from cache).
    assert ak.stock_hot_tweet_xq.call_count == 2  # one for each symbol type, first call only


@pytest.mark.unit
def test_xueqiu_cache_expires_after_ttl():
    """Monkey-patch time; akshare called twice after TTL expires."""
    df = _make_xueqiu_df(include_code="SH600519")
    ak = MagicMock()
    ak.stock_hot_tweet_xq.return_value = df

    with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
        # First call — populates cache
        _vendor_mod.get_xueqiu_attention("600519")
        # Expire the cache by backdating
        with _vendor_mod._XUEQIU_CACHE_LOCK:
            for key in _vendor_mod._XUEQIU_CACHE:
                ts, data = _vendor_mod._XUEQIU_CACHE[key]
                _vendor_mod._XUEQIU_CACHE[key] = (ts - _vendor_mod._XUEQIU_CACHE_TTL - 1, data)
        # Second call — cache expired, should re-fetch
        _vendor_mod.get_xueqiu_attention("600519")

    # Should be called 4 times total: 2 on first call + 2 on second call
    assert ak.stock_hot_tweet_xq.call_count == 4


@pytest.mark.unit
def test_xueqiu_endpoint_failure_returns_error():
    """akshare raises → error string, no raise."""
    ak = MagicMock()
    ak.stock_hot_tweet_xq.side_effect = ConnectionError("network failure")

    with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
        result = _vendor_mod.get_xueqiu_attention("600519")

    assert isinstance(result, str)
    # Should get "No 雪球 attention data" (None returned from cache helper) or Error
    assert "No 雪球 attention data" in result or result.startswith("Error")


@pytest.mark.unit
def test_xueqiu_endpoint_returns_list():
    """akshare returns list → graceful error string."""
    ak = MagicMock()
    ak.stock_hot_tweet_xq.return_value = []

    with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
        result = _vendor_mod.get_xueqiu_attention("600519")

    assert isinstance(result, str)
    # Non-DataFrame → None from cache helper → "No 雪球 attention data" or Error
    assert "No 雪球 attention data" in result or result.startswith("Error")
