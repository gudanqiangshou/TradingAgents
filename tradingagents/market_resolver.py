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

# Mainstream bare crypto base symbols — restricted to symbols with NO known
# US-equity ticker collision.  Symbols removed from earlier versions:
#   LTC  → LTC Properties (NYSE)
#   ADA  → ambiguous / historical equity conflicts
#   TRX  → TRX Gold (NYSE American)
#   DOT  → ambiguous / historical equity conflicts
#   BCH  → Banco de Chile (NYSE: BCH); audit-v2 trim — use BCH-USD for Bitcoin Cash
# Users who specifically want those as crypto should use the suffix form
# (e.g. BCH-USD, LTC-USD, TRX-USDT) which is always unambiguous.
BARE_CRYPTO_BASES = frozenset({
    "BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "AVAX",
})

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


def to_yfinance_symbol(ticker: str) -> str:
    """Canonicalize a ticker for the yfinance vendor.

    Bare crypto base symbols (e.g. ``'ETH'``, ``'BTC'``) are rewritten to
    suffix form (``'ETH-USD'``, ``'BTC-USD'``) so yfinance fetches the crypto
    pair, not the same-named US equity (e.g. NYSE ``ETH`` = Ethan Allen
    Interiors, NYSE ``BTC`` = Grayscale Bitcoin Trust on some dates).

    Existing suffix forms (``'ETH-USDT'``, ``'BTC-USD'``) and all non-crypto
    tickers are returned unchanged.

    Parameters
    ----------
    ticker:
        Raw ticker string as entered by the user.

    Returns
    -------
    str
        Canonical ticker for yfinance consumption.
    """
    t = ticker.strip().upper()
    if t in BARE_CRYPTO_BASES:
        return f"{t}-USD"
    return t
