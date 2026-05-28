"""Vendor-direct structured fundamentals extraction.

Returns plain dicts (not vendor strings) so JSON snapshot writers can
serialize without parsing free-form LLM-facing text. Never throws —
any vendor failure becomes status="error".

Schema notes (real-vendor verified 2026-05-28):
- US: yfinance .info dict
- A_SHARE PE+市值: eastmoney quote API (push2.eastmoney.com/api/qt/stock/get)
- A_SHARE ROE: akshare.stock_financial_abstract row "净资产收益率(ROE)"
- HK PE+市值: same eastmoney quote API (secid=116.{zfill5})
- HK ROE: akshare.stock_financial_hk_analysis_indicator_em col ROE_AVG ÷100
- A_SHARE+HK FCF: not available in public endpoints — always None
- A_SHARE+HK pe_forward: vendors don't expose consensus — always None
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

import yfinance as yf

from tradingagents.dataflows.stockstats_utils import yf_retry
from tradingagents.market_resolver import Market, resolve_market

_EMPTY_FIELDS = {
    "pe_ttm": None,
    "pe_forward": None,
    "fcf": None,
    "roe": None,
    "market_cap": None,
}

# At least one of these keys MUST be in yfinance .info for the response to
# be a real listing (and not the {"trailingPegRatio": None} stub returned
# for unknown tickers).
_YF_SENTINEL_KEYS = {
    "trailingPE", "forwardPE", "freeCashflow", "returnOnEquity",
    "marketCap", "longName", "shortName", "regularMarketPrice",
}


def _fields_status(values: dict) -> tuple[list[str], str]:
    """Return (missing_field_names, status) — status ok/partial."""
    missing = [k for k in ("pe_ttm", "pe_forward", "fcf", "roe", "market_cap") if values.get(k) is None]
    if not missing:
        return [], "ok"
    return missing, "partial"


def _fetch_us(ticker: str) -> dict:
    ticker_obj = yf.Ticker(ticker.upper())
    info = yf_retry(lambda: ticker_obj.info)
    if not info or not isinstance(info, dict):
        raise ValueError(f"yfinance returned empty/non-dict info for {ticker}")
    if not (set(info.keys()) & _YF_SENTINEL_KEYS):
        raise ValueError(f"yfinance returned stub dict (no recognized fields) for {ticker}")

    values = {
        "pe_ttm": info.get("trailingPE"),
        "pe_forward": info.get("forwardPE"),
        "fcf": info.get("freeCashflow"),
        "roe": info.get("returnOnEquity"),
        "market_cap": info.get("marketCap"),
    }
    missing, status = _fields_status(values)
    return {
        "ticker": ticker.upper(),
        "market": "US",
        **values,
        "currency": info.get("currency", "USD"),
        "as_of": datetime.now().strftime("%Y-%m-%d"),
        "source": "yfinance",
        "missing_fields": missing,
        "status": status,
        "error": None,
    }


def fetch_structured_fundamentals(ticker: str) -> dict:
    """Public entry — never throws. status=error on any vendor failure.

    Args:
        ticker: US uses bare/cased symbol (AAPL/NVDA); A-share uses 6-digit
            code (600519); HK uses code with .HK suffix (0700.HK).

    Returns:
        Dict with keys: ticker, market (US/A_SHARE/HK/error-market),
        pe_ttm, pe_forward, fcf, roe, market_cap (floats|None),
        currency (str|None), as_of (YYYY-MM-DD), source (yfinance|akshare+eastmoney|None),
        missing_fields (list[str]), status (ok|partial|error), error (str|None).
    """
    # Resolve market BEFORE try so error path preserves market info.
    market_name = "unknown"
    try:
        if not isinstance(ticker, str) or not ticker.strip():
            return _error_result(ticker, "unknown", "invalid ticker input")
        m = resolve_market(ticker)
        market_name = m.name  # "US" / "A_SHARE" / "HK" / "CRYPTO"
        if m == Market.US:
            return _fetch_us(ticker)
        # A_SHARE + HK branches added in subsequent tasks
        return _error_result(ticker, market_name, f"market {market_name} not yet supported")
    except Exception as exc:
        return _error_result(ticker, market_name, f"{type(exc).__name__}: {str(exc)[:200]}")


def _error_result(ticker: Any, market: str, error: str) -> dict:
    """Construct a failure result with all financial fields None."""
    return {
        "ticker": str(ticker) if ticker is not None else "",
        "market": market,
        **_EMPTY_FIELDS,
        "currency": None,
        "as_of": datetime.now().strftime("%Y-%m-%d"),
        "source": None,
        "missing_fields": list(_EMPTY_FIELDS.keys()),
        "status": "error",
        "error": error,
    }
