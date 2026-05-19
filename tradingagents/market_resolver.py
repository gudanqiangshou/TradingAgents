"""Single source of truth for ticker → market classification."""
from __future__ import annotations

import re
from enum import Enum


class Market(str, Enum):
    US = "us"
    A_SHARE = "a_share"
    HK = "hk"
    CRYPTO = "crypto"


# Crypto pair suffixes (preserves existing suffix-based detection)
_CRYPTO_SUFFIXES = ("-USD", "-USDT", "-USDC", "-BTC", "-ETH")

# Mainstream bare crypto base symbols — intentionally extensible, kept conservative
BARE_CRYPTO_BASES = {
    "BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "LTC", "BCH",
    "TRX", "DOT", "AVAX",
}

# A-share: 6-digit code, optionally suffixed with .SH / .SS / .SZ / .BJ
_A_SHARE_RE = re.compile(r"^\d{6}(\.(SH|SS|SZ|BJ))?$")

# HK: 1–5 digit code suffixed with .HK
_HK_RE = re.compile(r"^\d{1,5}\.HK$")


def resolve_market(ticker: str) -> Market:
    """Classify a ticker string into its market.

    Classification order (first match wins):
    1. CRYPTO  — suffix match or bare base symbol
    2. A_SHARE — 6-digit code with optional .SH/.SS/.SZ/.BJ
    3. HK      — 1–5 digit code with .HK suffix
    4. US      — everything else
    """
    t = ticker.strip().upper()

    # 1. Crypto: suffix-based (e.g. BTC-USD) or bare base (e.g. ETH, btc)
    if t.endswith(_CRYPTO_SUFFIXES) or t in BARE_CRYPTO_BASES:
        return Market.CRYPTO

    # 2. A-share: e.g. 600519, 000001.SZ, 430047.BJ
    if _A_SHARE_RE.match(t):
        return Market.A_SHARE

    # 3. HK: e.g. 0700.HK, 00700.HK, 9988.HK
    if _HK_RE.match(t):
        return Market.HK

    # 4. Default: US / international suffixed tickers (AAPL, 7203.T, CNC.TO)
    return Market.US
