import pytest
from tradingagents.market_resolver import resolve_market, Market, to_yfinance_symbol, BARE_CRYPTO_BASES

@pytest.mark.unit
@pytest.mark.parametrize("tk,exp", [
    ("AAPL", Market.US), ("SPY", Market.US), ("7203.T", Market.US), ("CNC.TO", Market.US),
    ("BTC-USD", Market.CRYPTO), ("ETH-USDT", Market.CRYPTO), ("sol-usd", Market.CRYPTO),
    ("ETH", Market.CRYPTO), ("eth", Market.CRYPTO), ("btc", Market.CRYPTO),
    ("600519", Market.A_SHARE), ("000001", Market.A_SHARE), ("430047", Market.A_SHARE),
    ("600519.SH", Market.A_SHARE), ("000001.SZ", Market.A_SHARE),
    ("600519.SS", Market.A_SHARE), ("430047.BJ", Market.A_SHARE),
    ("0700.HK", Market.HK), ("00700.HK", Market.HK), ("9988.hk", Market.HK),
])
def test_resolve_market(tk, exp):
    assert resolve_market(tk) == exp


# ---------------------------------------------------------------------------
# Fix 5: No-collision equity tickers must resolve to US, not CRYPTO
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.parametrize("tk", ["LTC", "ADA", "TRX", "DOT", "BAT", "XLM"])
def test_no_collision_with_equity_tickers(tk):
    """Tickers removed from BARE_CRYPTO_BASES must now resolve to US (not CRYPTO).
    Users wanting these as crypto should use the suffix form (LTC-USD etc.).
    """
    assert resolve_market(tk) == Market.US, (
        f"Expected {tk!r} to resolve to Market.US (removed from BARE_CRYPTO_BASES), "
        f"got {resolve_market(tk)!r}"
    )


@pytest.mark.unit
@pytest.mark.parametrize("tk", ["LTC-USD", "ADA-USDT", "TRX-USDC", "DOT-BTC"])
def test_suffix_form_still_resolves_to_crypto(tk):
    """Suffix form (LTC-USD, ADA-USDT etc.) must still resolve to CRYPTO."""
    assert resolve_market(tk) == Market.CRYPTO, (
        f"Expected {tk!r} to resolve to Market.CRYPTO via suffix, got {resolve_market(tk)!r}"
    )


@pytest.mark.unit
def test_bare_crypto_bases_is_unambiguous_subset():
    """BARE_CRYPTO_BASES must be exactly the 8 no-collision symbols."""
    expected = frozenset({"BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "BCH", "AVAX"})
    assert BARE_CRYPTO_BASES == expected, (
        f"BARE_CRYPTO_BASES mismatch.\n"
        f"Expected: {sorted(expected)}\n"
        f"Got:      {sorted(BARE_CRYPTO_BASES)}"
    )


# ---------------------------------------------------------------------------
# Fix 8: to_yfinance_symbol rewrites bare crypto bases to -USD suffix
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.parametrize("raw, expected", [
    ("ETH",       "ETH-USD"),
    ("eth",       "ETH-USD"),
    ("BTC",       "BTC-USD"),
    ("btc",       "BTC-USD"),
    ("SOL",       "SOL-USD"),
    ("AVAX",      "AVAX-USD"),
    ("ETH-USD",   "ETH-USD"),    # already has suffix — pass through
    ("ETH-USDT",  "ETH-USDT"),   # already has suffix — pass through
    ("BTC-USD",   "BTC-USD"),    # already has suffix — pass through
    ("AAPL",      "AAPL"),       # US equity — unchanged
    ("600519",    "600519"),     # A-share — unchanged
    ("0700.HK",   "0700.HK"),    # HK — unchanged
    ("LTC",       "LTC"),        # removed from BARE_CRYPTO_BASES — unchanged
    ("ADA",       "ADA"),        # removed from BARE_CRYPTO_BASES — unchanged
    ("TRX",       "TRX"),        # removed from BARE_CRYPTO_BASES — unchanged
])
def test_to_yfinance_symbol_rewrites_bare_crypto(raw, expected):
    assert to_yfinance_symbol(raw) == expected, (
        f"to_yfinance_symbol({raw!r}) → {to_yfinance_symbol(raw)!r}, expected {expected!r}"
    )
