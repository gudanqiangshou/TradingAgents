"""Single source of truth for ticker -> market classification."""
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

# NOTE: BARE_CRYPTO_BASES has been completely removed (audit-v3).
# Rationale: every formerly-listed base has a real NYSE Arca / NYSE ETF or
# equity ticker collision:
#   BTC  -> Grayscale Bitcoin Mini Trust (NYSE Arca: BTC)
#   ETH  -> Grayscale Ethereum Mini Trust (NYSE Arca: ETH)
#   XRP  -> Bitwise XRP ETF (NYSE Arca: XRP)
#   SOL  -> NYSE Emeren Group (NYSE: SOL)
#   BNB, DOGE, AVAX -- active SEC filings or conflicts as of 2025/2026
# Bare symbols now resolve to US (the NYSE-listed ETF/equity is the
# correct interpretation of the ambiguous ticker).
# Crypto requires explicit suffix form: BTC-USD, ETH-USDT, SOL-USD, etc.

# A-share: 6-digit code, optionally suffixed with .SH / .SS / .SZ / .BJ
_A_SHARE_RE = re.compile(r"^\d{6}(\.(SH|SS|SZ|BJ))?$")

# HK: 1-5 digit code suffixed with .HK
_HK_RE = re.compile(r"^\d{1,5}\.HK$")


def resolve_market(ticker: str) -> Market:
    """Classify a ticker string into its market.

    Classification order (first match wins):
    1. CRYPTO  -- suffix match only (e.g. BTC-USD, ETH-USDT, SOL-USDC)
    2. A_SHARE -- 6-digit code with optional .SH/.SS/.SZ/.BJ
    3. HK      -- 1-5 digit code with .HK suffix
    4. US      -- everything else

    Crypto requires suffix form (e.g. 'ETH-USD').  Bare base symbols like
    'ETH' or 'BTC' resolve to US because they collide with real NYSE-listed
    ETFs (Grayscale Bitcoin Mini Trust BTC, Grayscale Ethereum Trust ETH,
    Bitwise XRP ETF XRP, NYSE Emeren SOL).  Suffix form is always unambiguous.
    """
    t = ticker.strip().upper()

    # 1. Crypto: suffix-based only (e.g. BTC-USD, ETH-USDT)
    if t.endswith(_CRYPTO_SUFFIXES):
        return Market.CRYPTO

    # 2. A-share: e.g. 600519, 000001.SZ, 430047.BJ
    if _A_SHARE_RE.match(t):
        return Market.A_SHARE

    # 3. HK: e.g. 0700.HK, 00700.HK, 9988.HK
    if _HK_RE.match(t):
        return Market.HK

    # 4. Default: US / international suffixed tickers (AAPL, 7203.T, CNC.TO)
    # This also covers bare crypto bases (BTC, ETH, SOL, etc.) which collide
    # with NYSE-listed ETFs and must not be silently routed to crypto data.
    return Market.US
