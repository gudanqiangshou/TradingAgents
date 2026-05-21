"""Tests for get_social_sentiment in AkShare vendor.

Covers: market routing, A-share/HK happy paths, symbol prefix rules,
partial-failure tolerance, empty/non-DataFrame guard, dependency unavailability.
Fully offline — mocks _dep_bootstrap.ensure.
All tests are marked @pytest.mark.unit.
"""

from __future__ import annotations

import pytest
import pandas as pd
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, call

import tradingagents.dataflows.akshare_china as _vendor_mod

DependencyUnavailable = _vendor_mod._dep_bootstrap.DependencyUnavailable


# ---------------------------------------------------------------------------
# Helpers — build realistic fake DataFrames
# ---------------------------------------------------------------------------

def _make_rank_df(n_rows: int = 35, start_days_ago: int = 40) -> pd.DataFrame:
    """Build a realistic A-share rank DataFrame with 时间/排名/新晋粉丝/铁杆粉丝."""
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    rows = []
    for i in range(n_rows):
        dt = today - timedelta(days=(n_rows - 1 - i))
        rows.append({
            "时间": dt.strftime("%Y-%m-%d"),
            "排名": 100 + i,          # rank starts at 100, improves over time
            "证券代码": "600519",
            "新晋粉丝": 500 + i * 10,
            "铁杆粉丝": 2000 + i * 5,
        })
    return pd.DataFrame(rows)


def _make_keyword_df() -> pd.DataFrame:
    """Build a realistic keyword DataFrame."""
    return pd.DataFrame({
        "时间": ["2026-05-21"] * 3,
        "股票代码": ["600519"] * 3,
        "概念名称": ["白酒", "消费升级", "A股蓝筹"],
        "概念代码": ["BJ001", "BJ002", "BJ003"],
        "热度": [980, 720, 450],
    })


def _make_hk_rank_df(n_rows: int = 35) -> pd.DataFrame:
    """Build a realistic HK rank DataFrame with 时间/排名/证券代码."""
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    rows = []
    for i in range(n_rows):
        dt = today - timedelta(days=(n_rows - 1 - i))
        rows.append({
            "时间": dt.strftime("%Y-%m-%d"),
            "排名": 50 + i,
            "证券代码": "00700",
        })
    return pd.DataFrame(rows)


def _fake_ak_a_share(rank_df, kw_df) -> MagicMock:
    ak = MagicMock()
    ak.stock_hot_rank_detail_em.return_value = rank_df
    ak.stock_hot_keyword_em.return_value = kw_df
    return ak


def _fake_ak_hk(rank_df) -> MagicMock:
    ak = MagicMock()
    ak.stock_hk_hot_rank_detail_em.return_value = rank_df
    return ak


# ---------------------------------------------------------------------------
# 1. Market routing — US and CRYPTO tickers return "not applicable" placeholder
# ---------------------------------------------------------------------------

class TestMarketRouting:

    @pytest.mark.unit
    def test_us_ticker_returns_not_applicable_placeholder(self):
        """AAPL → returns placeholder starting with '<social sentiment via this vendor...'"""
        ensure_mock = MagicMock()
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", ensure_mock):
            result = _vendor_mod.get_social_sentiment("AAPL")
        ensure_mock.assert_not_called()
        assert result.startswith("<social sentiment via this vendor is not applicable for non-CN/HK markets")

    @pytest.mark.unit
    def test_crypto_ticker_returns_not_applicable_placeholder(self):
        """BTC-USD → same placeholder."""
        ensure_mock = MagicMock()
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", ensure_mock):
            result = _vendor_mod.get_social_sentiment("BTC-USD")
        ensure_mock.assert_not_called()
        assert result.startswith("<social sentiment via this vendor is not applicable for non-CN/HK markets")


# ---------------------------------------------------------------------------
# 2. A-share happy path
# ---------------------------------------------------------------------------

class TestAShareHappyPath:

    @pytest.mark.unit
    def test_a_share_happy_path(self):
        """600519 → full output with all expected sections."""
        rank_df = _make_rank_df(35)
        kw_df = _make_keyword_df()
        ak = _fake_ak_a_share(rank_df, kw_df)
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            result = _vendor_mod.get_social_sentiment("600519")

        assert "Social sentiment for 600519" in result
        assert "Retail attention rank" in result
        assert "Current: rank #" in result
        assert "7 days ago" in result
        assert "30 days ago" in result
        assert "Follower composition" in result
        assert "新晋粉丝" in result
        assert "Associated hot concepts" in result

        ak.stock_hot_rank_detail_em.assert_called_once_with(symbol="SH600519")
        ak.stock_hot_keyword_em.assert_called_once_with(symbol="SH600519")

    @pytest.mark.unit
    def test_a_share_szm_prefix(self):
        """000001 → stock_hot_rank_detail_em called with symbol='SZ000001'."""
        rank_df = _make_rank_df(35)
        kw_df = _make_keyword_df()
        ak = _fake_ak_a_share(rank_df, kw_df)
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            _vendor_mod.get_social_sentiment("000001")
        ak.stock_hot_rank_detail_em.assert_called_once_with(symbol="SZ000001")

    @pytest.mark.unit
    def test_a_share_bj_prefix(self):
        """430047 → stock_hot_rank_detail_em called with symbol='BJ430047'."""
        rank_df = _make_rank_df(35)
        kw_df = _make_keyword_df()
        ak = _fake_ak_a_share(rank_df, kw_df)
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            _vendor_mod.get_social_sentiment("430047")
        ak.stock_hot_rank_detail_em.assert_called_once_with(symbol="BJ430047")


# ---------------------------------------------------------------------------
# 3. A-share partial failure tolerance
# ---------------------------------------------------------------------------

class TestASharePartialFailure:

    @pytest.mark.unit
    def test_a_share_keyword_failure_does_not_break_rank(self):
        """Rank succeeds, keyword raises ConnectionError → rank section present, no keywords section, no raise."""
        rank_df = _make_rank_df(35)
        ak = MagicMock()
        ak.stock_hot_rank_detail_em.return_value = rank_df
        ak.stock_hot_keyword_em.side_effect = ConnectionError("network error")
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            result = _vendor_mod.get_social_sentiment("600519")
        assert isinstance(result, str)
        assert "Retail attention rank" in result
        assert "Current: rank #" in result
        assert "Associated hot concepts" not in result

    @pytest.mark.unit
    def test_a_share_rank_endpoint_failure_returns_no_data(self):
        """Rank raises ConnectionError → returns 'No retail attention rank data' or 'Error'. No raise."""
        ak = MagicMock()
        ak.stock_hot_rank_detail_em.side_effect = ConnectionError("timeout")
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            result = _vendor_mod.get_social_sentiment("600519")
        assert isinstance(result, str)
        assert "No retail attention rank data" in result or result.startswith("Error")

    @pytest.mark.unit
    def test_a_share_rank_returns_empty_df(self):
        """Rank returns empty DataFrame → 'No retail attention rank data...' string. No raise."""
        ak = MagicMock()
        ak.stock_hot_rank_detail_em.return_value = pd.DataFrame()
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            result = _vendor_mod.get_social_sentiment("600519")
        assert isinstance(result, str)
        assert "No retail attention rank data" in result

    @pytest.mark.unit
    def test_a_share_rank_returns_list(self):
        """Rank returns [] (list, not DataFrame) → returns Error string via _df_is_empty. No raise."""
        ak = MagicMock()
        ak.stock_hot_rank_detail_em.return_value = []
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            result = _vendor_mod.get_social_sentiment("600519")
        assert isinstance(result, str)
        # _df_is_empty returns True for lists, so we get "No retail attention rank data"
        assert "No retail attention rank data" in result or result.startswith("Error")


# ---------------------------------------------------------------------------
# 4. HK happy path
# ---------------------------------------------------------------------------

class TestHKHappyPath:

    @pytest.mark.unit
    def test_hk_happy_path(self):
        """0700.HK → rank section present; no keywords or followers section; called with symbol='00700'."""
        rank_df = _make_hk_rank_df(35)
        ak = _fake_ak_hk(rank_df)
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            result = _vendor_mod.get_social_sentiment("0700.HK")
        assert isinstance(result, str)
        assert "Retail attention rank" in result
        assert "Current: rank #" in result
        # HK has no follower composition or concepts
        assert "Follower composition" not in result
        assert "Associated hot concepts" not in result
        ak.stock_hk_hot_rank_detail_em.assert_called_once_with(symbol="00700")

    @pytest.mark.unit
    def test_hk_short_code_zfilled(self):
        """700.HK → called with symbol='00700'."""
        rank_df = _make_hk_rank_df(35)
        ak = _fake_ak_hk(rank_df)
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            _vendor_mod.get_social_sentiment("700.HK")
        ak.stock_hk_hot_rank_detail_em.assert_called_once_with(symbol="00700")


# ---------------------------------------------------------------------------
# 5. Dependency unavailable
# ---------------------------------------------------------------------------

class TestDependencyUnavailable:

    @pytest.mark.unit
    def test_dependency_unavailable_returns_error_string(self):
        """DependencyUnavailable → returns 'Error: ... unavailable' string. No raise."""
        with patch(
            "tradingagents.dataflows.akshare_china._dep_bootstrap.ensure",
            side_effect=DependencyUnavailable("akshare: pip install failed"),
        ):
            result = _vendor_mod.get_social_sentiment("600519")
        assert isinstance(result, str)
        assert result.startswith("Error:")
        assert "unavailable" in result.lower() or "akshare" in result.lower()


# ---------------------------------------------------------------------------
# 6. Invalid / empty ticker
# ---------------------------------------------------------------------------

class TestInvalidTicker:

    @pytest.mark.unit
    @pytest.mark.parametrize("bad_ticker", ["", "   "])
    def test_invalid_ticker_does_not_raise(self, bad_ticker):
        """Empty or whitespace ticker → returns a clear string (no raise)."""
        ak = MagicMock()
        ak.stock_hot_rank_detail_em.return_value = pd.DataFrame()
        ak.stock_hk_hot_rank_detail_em.return_value = pd.DataFrame()
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            try:
                result = _vendor_mod.get_social_sentiment(bad_ticker)
                assert isinstance(result, str)
                assert len(result) > 0
            except Exception as exc:
                pytest.fail(f"get_social_sentiment raised for ticker={bad_ticker!r}: {exc}")
