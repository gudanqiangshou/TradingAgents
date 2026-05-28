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

import pandas as pd
import yfinance as yf

from tradingagents.dataflows import _dep_bootstrap
from tradingagents.dataflows.akshare_china import (
    _eastmoney_http_retry,
    _eastmoney_session,
)
from tradingagents.dataflows.stockstats_utils import yf_retry
from tradingagents.market_resolver import Market, resolve_market

_EASTMONEY_QUOTE_URL = "http://push2.eastmoney.com/api/qt/stock/get"
_EASTMONEY_UT = "fa5fd1943c7b386f172d6893dbfba10b"  # eastmoney public ut token
_EASTMONEY_FIELDS = "f43,f57,f58,f116,f117,f162,f163,f167"

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


def _a_share_secid(code: str) -> str:
    """A 股 eastmoney secid prefix.

    SH (上交所) = 1: code starts with 60 / 68 / 90
    SZ (深交所) = 0: code starts with 00 / 30 / 20
    BJ (北交所) = 0: code starts with 4 / 8
    """
    if code.startswith(("60", "68", "90")):
        return f"1.{code}"
    return f"0.{code}"   # 涵盖 SZ + BJ


def _eastmoney_quote(secid: str) -> dict:
    """Fetch eastmoney quote dict (bypass akshare wrapper, hit push2 directly).

    Raises ValueError if vendor returns rc != 0 or no data (wrong secid /
    delisted / server err) — avoid silently degrading to partial-with-Nones.
    """
    session = _eastmoney_session()
    params = {"secid": secid, "fields": _EASTMONEY_FIELDS, "ut": _EASTMONEY_UT}
    r = _eastmoney_http_retry(
        lambda: session.get(_EASTMONEY_QUOTE_URL, params=params, timeout=10)
    )
    payload = r.json()
    if payload.get("rc") != 0:
        raise ValueError(f"eastmoney returned rc={payload.get('rc')} for secid={secid}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError(f"eastmoney returned no data for secid={secid}")
    return data


def _a_share_roe(code: str) -> float | None:
    """Extract A-share ROE from akshare.stock_financial_abstract.

    Strategy: EXACT-MATCH row 指标 == "净资产收益率(ROE)" (NOT substring;
    avoid colliding with "摊薄净资产收益率" / "净资产收益率_平均_扣除非经常损益"
    / "净资产收益率_平均" / "摊薄净资产收益率_扣除非经常损益").
    Take the value from the latest 8-digit-period column.
    """
    ak = _dep_bootstrap.ensure("akshare")
    df = ak.stock_financial_abstract(symbol=code)
    if not isinstance(df, pd.DataFrame) or df.empty or "指标" not in df.columns:
        return None
    period_cols = [c for c in df.columns if str(c).isdigit() and len(str(c)) == 8]
    if not period_cols:
        return None
    latest = max(period_cols)
    for _, row in df.iterrows():
        if str(row["指标"]).strip() == "净资产收益率(ROE)":
            val = row[latest]
            if pd.isna(val):
                continue
            try:
                return float(val)
            except (TypeError, ValueError):
                return None
    return None


def _fetch_a_share(ticker: str) -> dict:
    code = ticker.strip().upper().split(".")[0]
    secid = _a_share_secid(code)

    quote = _eastmoney_quote(secid)
    f163 = quote.get("f163")
    pe_ttm = f163 / 100.0 if f163 and f163 > 0 else None   # ×100 编码; 0 视为 None
    market_cap = quote.get("f116") or None

    roe = _a_share_roe(code)

    values: dict[str, float | None] = {
        "pe_ttm": pe_ttm,
        "pe_forward": None,    # akshare 不暴露 forward consensus
        "fcf": None,           # aggregate FCF 在公开端点不可得
        "roe": roe,
        "market_cap": market_cap,
    }
    missing, status = _fields_status(values)
    return {
        "ticker": code,
        "market": "A_SHARE",
        **values,
        "currency": "CNY",
        "as_of": datetime.now().strftime("%Y-%m-%d"),
        "source": "akshare+eastmoney",
        "missing_fields": missing,
        "status": status,
        "error": None,
    }


def _hk_secid(ticker: str) -> str:
    """HK secid: '116.' + 5-digit zero-padded code (4-digit must be zfilled)."""
    raw = ticker.strip().upper()
    if raw.endswith(".HK"):
        raw = raw[:-3]
    return f"116.{raw.zfill(5)}"


def _hk_roe(code5: str) -> tuple[float | None, str]:
    """Extract HK ROE (as ratio) + currency from akshare HK indicator endpoint.

    ROE_AVG 单位是百分点 (e.g. 21.13) — divide by 100 to match ratio form
    (US/A_SHARE both use ratio).
    """
    ak = _dep_bootstrap.ensure("akshare")
    df = ak.stock_financial_hk_analysis_indicator_em(symbol=code5)
    roe: float | None = None
    currency = "HKD"
    if isinstance(df, pd.DataFrame) and not df.empty:
        row = df.iloc[0]
        for col in ("ROE_AVG", "ROE"):
            if col in df.columns:
                v = row.get(col)
                if not pd.isna(v):
                    try:
                        roe = float(v) / 100.0      # 百分点 → ratio
                    except (TypeError, ValueError):
                        pass
                    break
        cur = row.get("CURRENCY")
        if isinstance(cur, str) and cur.strip():
            currency = cur
    return roe, currency


def _fetch_hk(ticker: str) -> dict:
    raw = ticker.strip().upper()
    if raw.endswith(".HK"):
        raw = raw[:-3]
    code5 = raw.zfill(5)
    secid = _hk_secid(ticker)

    quote = _eastmoney_quote(secid)
    f163 = quote.get("f163")
    pe_ttm = f163 / 100.0 if f163 and f163 > 0 else None
    market_cap = quote.get("f116") or None

    roe, currency = _hk_roe(code5)

    values: dict[str, float | None] = {
        "pe_ttm": pe_ttm,
        "pe_forward": None,
        "fcf": None,
        "roe": roe,
        "market_cap": market_cap,
    }
    missing, status = _fields_status(values)
    return {
        "ticker": f"{code5}.HK",
        "market": "HK",
        **values,
        "currency": currency,
        "as_of": datetime.now().strftime("%Y-%m-%d"),
        "source": "akshare+eastmoney",
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
        if m == Market.A_SHARE:
            return _fetch_a_share(ticker)
        if m == Market.HK:
            return _fetch_hk(ticker)
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
