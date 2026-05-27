"""Tests for market-aware routing in sentiment_analyst.py.

Verifies that:
- US/crypto tickers route to StockTwits + Google Trends (not eastmoney, not A-share blocks)
- A-share/HK tickers route to eastmoney (not StockTwits); A-share also gets 4 extra blocks
- Both blocks are always passed to the prompt template with appropriate placeholders
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch, call


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_llm_mock():
    """Return a minimal LLM mock that returns a valid message object."""
    llm = MagicMock()
    result = MagicMock()
    result.content = "mock sentiment report"
    llm.invoke.return_value = result
    # Support the prompt | llm chain pattern
    chain = MagicMock()
    chain.invoke.return_value = result
    llm.__or__ = MagicMock(return_value=chain)
    return llm, chain


def _make_state(ticker: str, trade_date: str = "2026-05-21") -> dict:
    return {
        "company_of_interest": ticker,
        "trade_date": trade_date,
        "messages": [],
    }


# Shared patch context for all the new imports
_COMMON_PATCHES = [
    "tradingagents.agents.analysts.sentiment_analyst.fetch_reddit_posts",
    "tradingagents.agents.analysts.sentiment_analyst.get_news",
    "tradingagents.agents.analysts.sentiment_analyst.build_instrument_context",
    "tradingagents.agents.analysts.sentiment_analyst.get_language_instruction",
]


# ---------------------------------------------------------------------------
# US ticker → StockTwits called; eastmoney NOT called
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_us_ticker_routes_to_stocktwits():
    """AAPL → fetch_stocktwits_messages called; get_social_sentiment NOT called; google_trends called."""
    llm, chain = _make_llm_mock()

    with (
        patch("tradingagents.agents.analysts.sentiment_analyst.fetch_stocktwits_messages",
              return_value="stocktwits data") as mock_st,
        patch("tradingagents.agents.analysts.sentiment_analyst.get_social_sentiment",
              return_value="em data") as mock_em,
        patch("tradingagents.agents.analysts.sentiment_analyst.fetch_reddit_posts",
              return_value="reddit data"),
        patch("tradingagents.agents.analysts.sentiment_analyst.get_news",
              return_value="news data"),
        patch("tradingagents.agents.analysts.sentiment_analyst.build_instrument_context",
              return_value=""),
        patch("tradingagents.agents.analysts.sentiment_analyst.get_language_instruction",
              return_value=""),
        patch("tradingagents.agents.analysts.sentiment_analyst.get_google_trends",
              return_value="google trends data") as mock_gt,
        patch("tradingagents.agents.analysts.sentiment_analyst.get_zt_pool_summary",
              return_value="zt data") as mock_zt,
        patch("tradingagents.agents.analysts.sentiment_analyst.get_hot_up_rank",
              return_value="hot up data") as mock_hu,
        patch("tradingagents.agents.analysts.sentiment_analyst.get_lhb_summary",
              return_value="lhb data") as mock_lhb,
        patch("tradingagents.agents.analysts.sentiment_analyst.get_xueqiu_attention",
              return_value="xueqiu data") as mock_xq,
    ):
        from tradingagents.agents.analysts.sentiment_analyst import create_sentiment_analyst
        node = create_sentiment_analyst(llm)
        state = _make_state("AAPL")
        node(state)

    mock_st.assert_called_once()
    mock_em.assert_not_called()
    mock_gt.assert_called_once()  # Google Trends called for US
    mock_zt.assert_not_called()   # A-share-only blocks NOT called
    mock_hu.assert_not_called()
    mock_lhb.assert_not_called()
    mock_xq.assert_not_called()

    # The call to fetch_stocktwits_messages had AAPL as the ticker
    args = mock_st.call_args
    assert args[0][0] == "AAPL" or args.kwargs.get("ticker") == "AAPL" or "AAPL" in str(args)


@pytest.mark.unit
def test_us_ticker_eastmoney_block_is_placeholder():
    """AAPL → eastmoney_social_block passed to _build_system_message is the 'not queried' placeholder."""
    llm, chain = _make_llm_mock()
    captured = {}

    original_build = None

    def capture_build(**kwargs):
        captured.update(kwargs)
        # Call the original to get a real string back
        import tradingagents.agents.analysts.sentiment_analyst as mod
        return mod._build_system_message.__wrapped__(**kwargs) if hasattr(
            mod._build_system_message, "__wrapped__") else "<mock system message>"

    with (
        patch("tradingagents.agents.analysts.sentiment_analyst.fetch_stocktwits_messages",
              return_value="stocktwits data"),
        patch("tradingagents.agents.analysts.sentiment_analyst.get_social_sentiment",
              return_value="em data"),
        patch("tradingagents.agents.analysts.sentiment_analyst.fetch_reddit_posts",
              return_value="reddit data"),
        patch("tradingagents.agents.analysts.sentiment_analyst.get_news",
              return_value="news data"),
        patch("tradingagents.agents.analysts.sentiment_analyst.build_instrument_context",
              return_value=""),
        patch("tradingagents.agents.analysts.sentiment_analyst.get_language_instruction",
              return_value=""),
        patch("tradingagents.agents.analysts.sentiment_analyst._build_system_message",
              side_effect=lambda **kw: captured.update(kw) or "<mock>"),
    ):
        from tradingagents.agents.analysts.sentiment_analyst import create_sentiment_analyst
        node = create_sentiment_analyst(llm)
        node(_make_state("AAPL"))

    assert "eastmoney_social_block" in captured
    assert "not queried" in captured["eastmoney_social_block"] or "non-CN/HK" in captured["eastmoney_social_block"]


# ---------------------------------------------------------------------------
# A-share → eastmoney called; StockTwits NOT called
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_a_share_routes_to_eastmoney():
    """600519 → get_social_sentiment called; fetch_stocktwits_messages NOT called; all 4 A-share blocks called."""
    llm, chain = _make_llm_mock()

    with (
        patch("tradingagents.agents.analysts.sentiment_analyst.fetch_stocktwits_messages",
              return_value="st data") as mock_st,
        patch("tradingagents.agents.analysts.sentiment_analyst.get_social_sentiment",
              return_value="em data") as mock_em,
        patch("tradingagents.agents.analysts.sentiment_analyst.fetch_reddit_posts",
              return_value="reddit data"),
        patch("tradingagents.agents.analysts.sentiment_analyst.get_news",
              return_value="news data"),
        patch("tradingagents.agents.analysts.sentiment_analyst.build_instrument_context",
              return_value=""),
        patch("tradingagents.agents.analysts.sentiment_analyst.get_language_instruction",
              return_value=""),
        patch("tradingagents.agents.analysts.sentiment_analyst.get_google_trends",
              return_value="gt data") as mock_gt,
        patch("tradingagents.agents.analysts.sentiment_analyst.get_zt_pool_summary",
              return_value="zt data") as mock_zt,
        patch("tradingagents.agents.analysts.sentiment_analyst.get_hot_up_rank",
              return_value="hot up data") as mock_hu,
        patch("tradingagents.agents.analysts.sentiment_analyst.get_lhb_summary",
              return_value="lhb data") as mock_lhb,
        patch("tradingagents.agents.analysts.sentiment_analyst.get_xueqiu_attention",
              return_value="xueqiu data") as mock_xq,
    ):
        from tradingagents.agents.analysts.sentiment_analyst import create_sentiment_analyst
        node = create_sentiment_analyst(llm)
        node(_make_state("600519"))

    mock_em.assert_called_once()
    mock_st.assert_not_called()
    mock_gt.assert_not_called()  # Google Trends NOT called for A-share
    # All 4 A-share-only blocks called
    mock_zt.assert_called_once()
    mock_hu.assert_called_once()
    mock_lhb.assert_called_once()
    mock_xq.assert_called_once()


# ---------------------------------------------------------------------------
# HK → eastmoney called; StockTwits NOT called
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_hk_routes_to_eastmoney():
    """0700.HK → get_social_sentiment called; fetch_stocktwits_messages NOT called; A-share-only blocks NOT called."""
    llm, chain = _make_llm_mock()

    with (
        patch("tradingagents.agents.analysts.sentiment_analyst.fetch_stocktwits_messages",
              return_value="st data") as mock_st,
        patch("tradingagents.agents.analysts.sentiment_analyst.get_social_sentiment",
              return_value="em data") as mock_em,
        patch("tradingagents.agents.analysts.sentiment_analyst.fetch_reddit_posts",
              return_value="reddit data"),
        patch("tradingagents.agents.analysts.sentiment_analyst.get_news",
              return_value="news data"),
        patch("tradingagents.agents.analysts.sentiment_analyst.build_instrument_context",
              return_value=""),
        patch("tradingagents.agents.analysts.sentiment_analyst.get_language_instruction",
              return_value=""),
        patch("tradingagents.agents.analysts.sentiment_analyst.get_google_trends",
              return_value="gt data") as mock_gt,
        patch("tradingagents.agents.analysts.sentiment_analyst.get_zt_pool_summary",
              return_value="zt data") as mock_zt,
        patch("tradingagents.agents.analysts.sentiment_analyst.get_hot_up_rank",
              return_value="hot up data") as mock_hu,
        patch("tradingagents.agents.analysts.sentiment_analyst.get_lhb_summary",
              return_value="lhb data") as mock_lhb,
        patch("tradingagents.agents.analysts.sentiment_analyst.get_xueqiu_attention",
              return_value="xueqiu data") as mock_xq,
    ):
        from tradingagents.agents.analysts.sentiment_analyst import create_sentiment_analyst
        node = create_sentiment_analyst(llm)
        node(_make_state("0700.HK"))

    mock_em.assert_called_once()
    mock_st.assert_not_called()
    mock_gt.assert_not_called()  # Google Trends NOT called for HK
    # A-share-only blocks NOT called for HK — just placeholders
    mock_zt.assert_not_called()
    mock_hu.assert_not_called()
    mock_lhb.assert_not_called()
    mock_xq.assert_not_called()


# ---------------------------------------------------------------------------
# CRYPTO → StockTwits called; eastmoney NOT called
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_crypto_routes_to_stocktwits():
    """BTC-USD → fetch_stocktwits_messages called; get_social_sentiment NOT called; google_trends called (non-CN/HK)."""
    llm, chain = _make_llm_mock()

    with (
        patch("tradingagents.agents.analysts.sentiment_analyst.fetch_stocktwits_messages",
              return_value="stocktwits data") as mock_st,
        patch("tradingagents.agents.analysts.sentiment_analyst.get_social_sentiment",
              return_value="em data") as mock_em,
        patch("tradingagents.agents.analysts.sentiment_analyst.fetch_reddit_posts",
              return_value="reddit data"),
        patch("tradingagents.agents.analysts.sentiment_analyst.get_news",
              return_value="news data"),
        patch("tradingagents.agents.analysts.sentiment_analyst.build_instrument_context",
              return_value=""),
        patch("tradingagents.agents.analysts.sentiment_analyst.get_language_instruction",
              return_value=""),
        patch("tradingagents.agents.analysts.sentiment_analyst.get_google_trends",
              return_value="gt data") as mock_gt,
        patch("tradingagents.agents.analysts.sentiment_analyst.get_zt_pool_summary",
              return_value="zt data") as mock_zt,
        patch("tradingagents.agents.analysts.sentiment_analyst.get_hot_up_rank",
              return_value="hot up data") as mock_hu,
        patch("tradingagents.agents.analysts.sentiment_analyst.get_lhb_summary",
              return_value="lhb data") as mock_lhb,
        patch("tradingagents.agents.analysts.sentiment_analyst.get_xueqiu_attention",
              return_value="xueqiu data") as mock_xq,
    ):
        from tradingagents.agents.analysts.sentiment_analyst import create_sentiment_analyst
        node = create_sentiment_analyst(llm)
        node(_make_state("BTC-USD"))

    mock_st.assert_called_once()
    mock_em.assert_not_called()
    mock_gt.assert_called_once()   # Google Trends called for CRYPTO (non-CN/HK)
    mock_zt.assert_not_called()    # A-share-only blocks NOT called for crypto
    mock_hu.assert_not_called()
    mock_lhb.assert_not_called()
    mock_xq.assert_not_called()
