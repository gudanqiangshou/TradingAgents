import pytest
from tradingagents.market_resolver import resolve_market, Market

@pytest.mark.unit
@pytest.mark.parametrize("tk,exp", [
    ("AAPL", Market.US), ("SPY", Market.US), ("7203.T", Market.US), ("CNC.TO", Market.US),
    ("BTC-USD", Market.CRYPTO), ("ETH-USDT", Market.CRYPTO), ("sol-usd", Market.CRYPTO),
    # Bare crypto bases now resolve to US (NYSE ETF/equity collision — audit-v3)
    ("ETH", Market.US), ("eth", Market.US), ("btc", Market.US),
    ("600519", Market.A_SHARE), ("000001", Market.A_SHARE), ("430047", Market.A_SHARE),
    ("600519.SH", Market.A_SHARE), ("000001.SZ", Market.A_SHARE),
    ("600519.SS", Market.A_SHARE), ("430047.BJ", Market.A_SHARE),
    ("0700.HK", Market.HK), ("00700.HK", Market.HK), ("9988.hk", Market.HK),
])
def test_resolve_market(tk, exp):
    assert resolve_market(tk) == exp


# ---------------------------------------------------------------------------
# No-collision equity tickers must resolve to US, not CRYPTO
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.parametrize("tk", ["LTC", "ADA", "TRX", "DOT", "BAT", "XLM", "BCH"])
def test_no_collision_with_equity_tickers(tk):
    """Tickers removed from BARE_CRYPTO_BASES must now resolve to US (not CRYPTO).
    Users wanting these as crypto should use the suffix form (LTC-USD, BCH-USD etc.).
    BCH = Banco de Chile (NYSE), added in audit-v2 trim.
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


# ---------------------------------------------------------------------------
# audit-v3: BARE_CRYPTO_BASES completely removed; bare crypto -> US
# ---------------------------------------------------------------------------

class TestBareCryptoNoLongerSpecial:
    """Documents the new behavior after audit-v3: bare crypto bases resolve to US.

    All 7 formerly-whitelisted symbols collide with NYSE Arca or NYSE ETFs:
    - BTC  -> Grayscale Bitcoin Mini Trust (NYSE Arca: BTC)
    - ETH  -> Grayscale Ethereum Mini Trust (NYSE Arca: ETH)
    - XRP  -> Bitwise XRP ETF (NYSE Arca: XRP)
    - SOL  -> NYSE Emeren Group (NYSE: SOL)
    - BNB, DOGE, AVAX -- active SEC filings / conflicts
    """

    @pytest.mark.unit
    def test_bare_btc_resolves_to_us_due_to_grayscale_etf_collision(self):
        """BTC resolves to US; Grayscale Bitcoin Mini Trust trades on NYSE Arca as BTC."""
        assert resolve_market("BTC") == Market.US
        assert resolve_market("btc") == Market.US

    @pytest.mark.unit
    def test_bare_eth_resolves_to_us_due_to_grayscale_eth_collision(self):
        """ETH resolves to US; Grayscale Ethereum Mini Trust trades on NYSE Arca as ETH."""
        assert resolve_market("ETH") == Market.US
        assert resolve_market("eth") == Market.US

    @pytest.mark.unit
    @pytest.mark.parametrize("tk", ["XRP", "SOL", "DOGE", "AVAX", "BNB"])
    def test_bare_xrp_sol_doge_avax_bnb_resolve_to_us(self, tk):
        """Formerly whitelisted crypto bases all resolve to US (NYSE ETF collision)."""
        assert resolve_market(tk) == Market.US, (
            f"Expected {tk!r} to resolve to Market.US after audit-v3 BARE_CRYPTO removal"
        )

    @pytest.mark.unit
    def test_explicit_crypto_suffix_still_works(self):
        """Suffix form is always unambiguous and resolves correctly to CRYPTO."""
        assert resolve_market("BTC-USD") == Market.CRYPTO
        assert resolve_market("ETH-USDT") == Market.CRYPTO
        assert resolve_market("SOL-USD") == Market.CRYPTO
        assert resolve_market("XRP-USDC") == Market.CRYPTO
        assert resolve_market("DOGE-USD") == Market.CRYPTO
        assert resolve_market("AVAX-USD") == Market.CRYPTO
        assert resolve_market("BNB-BTC") == Market.CRYPTO
