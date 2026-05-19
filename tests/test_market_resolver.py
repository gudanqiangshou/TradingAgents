import pytest
from tradingagents.market_resolver import resolve_market, Market

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
