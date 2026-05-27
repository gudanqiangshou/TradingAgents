"""Self-contained AkShare A-share data vendor; akshare loaded on demand."""

import logging
import threading
import time
import time as _time
from datetime import datetime, timedelta

import pandas as pd

from tradingagents.dataflows import _dep_bootstrap
from tradingagents.dataflows.config import get_config as _config_get, replace_section
from tradingagents.market_resolver import resolve_market, Market

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level Xueqiu cache — thread-safe TTL cache for the 18s endpoints
# ---------------------------------------------------------------------------
_XUEQIU_CACHE: dict = {}  # key -> (cached_at_ts, df)
_XUEQIU_CACHE_LOCK = threading.Lock()
_XUEQIU_CACHE_TTL = 600  # 10 minutes


def _df_is_empty(df) -> bool:
    """Treat None, non-DataFrame, and empty DataFrame uniformly.

    Used at every akshare call-site empty-check to guard against endpoint
    returning a list (``[]``) instead of an empty ``pd.DataFrame``.
    """
    if df is None:
        return True
    if not isinstance(df, pd.DataFrame):
        return True
    return df.empty


# Sync with tradingagents/dataflows/y_finance.py:get_stock_stats_indicators_window
_INDICATOR_CATALOG: dict[str, str] = {
    # Moving Averages
    "close_50_sma": (
        "50 SMA: A medium-term trend indicator. "
        "Usage: Identify trend direction and serve as dynamic support/resistance. "
        "Tips: It lags price; combine with faster indicators for timely signals."
    ),
    "close_200_sma": (
        "200 SMA: A long-term trend benchmark. "
        "Usage: Confirm overall market trend and identify golden/death cross setups. "
        "Tips: It reacts slowly; best for strategic trend confirmation rather than frequent trading entries."
    ),
    "close_10_ema": (
        "10 EMA: A responsive short-term average. "
        "Usage: Capture quick shifts in momentum and potential entry points. "
        "Tips: Prone to noise in choppy markets; use alongside longer averages for filtering false signals."
    ),
    # MACD Related
    "macd": (
        "MACD: Computes momentum via differences of EMAs. "
        "Usage: Look for crossovers and divergence as signals of trend changes. "
        "Tips: Confirm with other indicators in low-volatility or sideways markets."
    ),
    "macds": (
        "MACD Signal: An EMA smoothing of the MACD line. "
        "Usage: Use crossovers with the MACD line to trigger trades. "
        "Tips: Should be part of a broader strategy to avoid false positives."
    ),
    "macdh": (
        "MACD Histogram: Shows the gap between the MACD line and its signal. "
        "Usage: Visualize momentum strength and spot divergence early. "
        "Tips: Can be volatile; complement with additional filters in fast-moving markets."
    ),
    # Momentum Indicators
    "rsi": (
        "RSI: Measures momentum to flag overbought/oversold conditions. "
        "Usage: Apply 70/30 thresholds and watch for divergence to signal reversals. "
        "Tips: In strong trends, RSI may remain extreme; always cross-check with trend analysis."
    ),
    # Volatility Indicators
    "boll": (
        "Bollinger Middle: A 20 SMA serving as the basis for Bollinger Bands. "
        "Usage: Acts as a dynamic benchmark for price movement. "
        "Tips: Combine with the upper and lower bands to effectively spot breakouts or reversals."
    ),
    "boll_ub": (
        "Bollinger Upper Band: Typically 2 standard deviations above the middle line. "
        "Usage: Signals potential overbought conditions and breakout zones. "
        "Tips: Confirm signals with other tools; prices may ride the band in strong trends."
    ),
    "boll_lb": (
        "Bollinger Lower Band: Typically 2 standard deviations below the middle line. "
        "Usage: Indicates potential oversold conditions. "
        "Tips: Use additional analysis to avoid false reversal signals."
    ),
    "atr": (
        "ATR: Averages true range to measure volatility. "
        "Usage: Set stop-loss levels and adjust position sizes based on current market volatility. "
        "Tips: It's a reactive measure, so use it as part of a broader risk management strategy."
    ),
    # Volume-Based Indicators
    "vwma": (
        "VWMA: A moving average weighted by volume. "
        "Usage: Confirm trends by integrating price action with volume data. "
        "Tips: Watch for skewed results from volume spikes; use in combination with other volume analyses."
    ),
    "mfi": (
        "MFI: The Money Flow Index is a momentum indicator that uses both price and volume to measure buying and selling pressure. "
        "Usage: Identify overbought (>80) or oversold (<20) conditions and confirm the strength of trends or reversals. "
        "Tips: Use alongside RSI or MACD to confirm signals; divergence between price and MFI can indicate potential reversals."
    ),
}


def _validate_date_str(value, name: str) -> "str | None":
    """Return None if value is a valid 'yyyy-mm-dd' string; otherwise an
    informative error message string (vendor never-raises convention).
    Uses strptime to actually parse the date, not a length heuristic."""
    if not isinstance(value, str) or not value.strip():
        return f"Error: {name} must be a 'yyyy-mm-dd' string, got {type(value).__name__}: {value!r}"
    s = value.strip()
    try:
        from datetime import datetime
        datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        return f"Error: {name}={value!r} is not a valid yyyy-mm-dd date"
    return None


def _coerce_positive_int(value, name: str) -> "tuple[int | None, str | None]":
    """Coerce to a non-negative finite int. Returns (value, None) on success or
    (None, error_string) on failure. Rejects bool, NaN, inf, negatives."""
    try:
        if isinstance(value, bool):
            return None, f"Error: {name} must be int, got bool: {value!r}"
        # Reject inf/nan before int() because int(float('inf')) raises OverflowError
        import math
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None, f"Error: {name} must be a finite number, got {value!r}"
        coerced = int(value)
    except (TypeError, ValueError, OverflowError):
        return None, f"Error: {name} must be coercible to int, got {type(value).__name__}: {value!r}"
    if coerced < 0:
        return None, f"Error: {name} must be non-negative, got {coerced}"
    return coerced, None


def _eastmoney_a_share_symbol(code: str) -> str:
    """Map a bare 6-digit A-share code to the prefixed form akshare's
    eastmoney heat/keyword endpoints expect, e.g. '600519' -> 'SH600519'."""
    code = code.strip()
    if len(code) < 1:
        return code.upper()
    if code[:2] in ("60", "68", "90"):
        return f"SH{code}"
    if code[:2] in ("00", "30", "20"):
        return f"SZ{code}"
    if code[:1] in ("8", "4"):
        return f"BJ{code}"
    return f"SH{code}"  # safe default


def _sina_prefix(code: str) -> str:
    """Return the Sina/akshare exchange prefix for a bare 6-digit A-share code.

    Prefix rules (same as akshare CN convention):
      - first 2 digits in {60, 68, 90}  → "sh"
      - first 2 digits in {00, 30, 20}  → "sz"
      - first char in {8, 4}            → "bj"
      - fallback                        → "sh"
    """
    two = code[:2]
    one = code[:1]
    if two in {"60", "68", "90"}:
        return "sh"
    if two in {"00", "30", "20"}:
        return "sz"
    if one in {"8", "4"}:
        return "bj"
    return "sh"


def _fetch_a_share_ohlcv(ak, code: str, start_yyyymmdd: str, end_yyyymmdd: str) -> "tuple[pd.DataFrame | None, Exception | None]":
    """Try eastmoney stock_zh_a_hist, fall back to Sina stock_zh_a_daily.

    Returns a tuple (df_or_None, last_exc_or_None):
    - (df, None)   — one source succeeded; df has capitalized columns
                     Date/Open/High/Low/Close/Volume (Date as a column, not index),
                     sorted ascending. Never empty when returned with None.
    - (None, exc)  — at least one source raised an exception; last_exc is the
                     most-recent exception encountered.
    - (None, None) — both sources returned empty DataFrames (no exceptions).

    Never raises.
    """
    _last_exc: Exception | None = None
    _any_exc = False

    # Source 1: eastmoney
    df: pd.DataFrame | None = None
    try:
        df = ak.stock_zh_a_hist(
            symbol=code,
            period="daily",
            start_date=start_yyyymmdd,
            end_date=end_yyyymmdd,
            adjust="qfq",
        )
    except Exception as exc:
        _last_exc = exc
        _any_exc = True
        df = None

    # If eastmoney succeeded with rows, normalize Chinese columns and return
    if not _df_is_empty(df):
        try:
            col_map = {
                "日期": "Date",
                "开盘": "Open",
                "收盘": "Close",
                "最高": "High",
                "最低": "Low",
                "成交量": "Volume",
            }
            df = df.rename(columns=col_map)
            keep = [c for c in ["Date", "Open", "High", "Low", "Close", "Volume"] if c in df.columns]
            df = df[keep].copy()
            df = df.sort_values("Date").reset_index(drop=True)
            logger.info("A-share OHLCV for %s: eastmoney source succeeded", code)
            return (df, None)
        except Exception as exc:
            logger.warning(
                "A-share OHLCV for %s: eastmoney schema normalization failed (%s); "
                "trying Sina fallback",
                code, exc,
            )
            _last_exc = exc
            _any_exc = True

    # Source 2: Sina fallback
    prefix = _sina_prefix(code)
    sina_symbol = prefix + code
    df_sina: pd.DataFrame | None = None
    try:
        df_sina = ak.stock_zh_a_daily(
            symbol=sina_symbol,
            start_date=start_yyyymmdd,
            end_date=end_yyyymmdd,
            adjust="qfq",
        )
    except Exception as sina_exc:
        _last_exc = sina_exc
        _any_exc = True
        df_sina = None

    if not _df_is_empty(df_sina):
        try:
            # Normalize Sina columns (lowercase → capitalize)
            df_sina = df_sina.rename(columns=str.capitalize)
            sina_keep = [c for c in ["Date", "Open", "High", "Low", "Close", "Volume"] if c in df_sina.columns]
            df_sina = df_sina[sina_keep].copy()
            df_sina = df_sina.sort_values("Date").reset_index(drop=True)
            logger.info(
                "A-share OHLCV for %s: eastmoney unavailable, used Sina fallback",
                code,
            )
            return (df_sina, None)
        except Exception as exc:
            logger.warning(
                "A-share OHLCV for %s: Sina schema normalization failed (%s)",
                code, exc,
            )
            _last_exc = exc
            _any_exc = True

    # Both sources failed or returned empty
    if _any_exc:
        return (None, _last_exc)
    return (None, None)


def get_stock_data(symbol: str, start_date: str, end_date: str) -> str:
    """Return OHLCV data for an A-share or HK symbol as a formatted CSV string.

    Parameters
    ----------
    symbol:
        A-share ticker — bare 6-digit code or with exchange suffix
        (.SH, .SS, .SZ, .BJ), e.g. ``"600519"`` or ``"600519.SH"``.
        HK ticker — 1-5 digit code with ``.HK`` suffix, e.g. ``"0700.HK"``
        or ``"00700.HK"`` or ``"9988.HK"``.
    start_date:
        Start of the date range in ``yyyy-mm-dd`` format.
    end_date:
        End of the date range in ``yyyy-mm-dd`` format.

    Returns
    -------
    str
        On success: a 3-line ``#`` header followed by a blank line and then
        the DataFrame serialised as CSV (Date index, columns
        Open/High/Low/Close/Volume, OHLC rounded to 2 dp, dates ascending).

        On error or empty result: a plain-text message (never raises).
    """
    # ------------------------------------------------------------------
    # Guard: this vendor only handles A-share and HK symbols
    # ------------------------------------------------------------------
    market = resolve_market(symbol)
    if market not in (Market.A_SHARE, Market.HK):
        return (
            f"This vendor handles A-share and HK data only. "
            f"'{symbol}' was classified as non-A-share/non-HK. "
            "Please use the appropriate vendor for this symbol."
        )

    kind = "A-share" if market == Market.A_SHARE else "HK"

    # ------------------------------------------------------------------
    # Validate date parameters at entry (never-raises contract)
    # ------------------------------------------------------------------
    err = _validate_date_str(start_date, "start_date")
    if err:
        return err
    err = _validate_date_str(end_date, "end_date")
    if err:
        return err

    # ------------------------------------------------------------------
    # Normalise symbol
    # ------------------------------------------------------------------
    if market == Market.A_SHARE:
        # Strip whitespace, upper-case, drop exchange suffix
        code = symbol.strip().upper().split(".")[0]
    else:
        # HK: strip whitespace, upper-case, drop .HK suffix, left-pad to 5 digits
        raw = symbol.strip().upper()
        if raw.endswith(".HK"):
            raw = raw[:-3]
        code = raw.zfill(5)

    # Validate and convert dates yyyy-mm-dd → YYYYMMDD
    # Guard: malformed dates must return an error string, never raise
    try:
        datetime.strptime(start_date, "%Y-%m-%d")
        datetime.strptime(end_date, "%Y-%m-%d")
    except ValueError:
        return (
            f"Error: invalid date format for {start_date!r} or {end_date!r};"
            " expected yyyy-mm-dd"
        )
    ak_start = start_date.replace("-", "")
    ak_end = end_date.replace("-", "")

    # ------------------------------------------------------------------
    # Load akshare on demand
    # ------------------------------------------------------------------
    try:
        ak = _dep_bootstrap.ensure("akshare")
    except _dep_bootstrap.DependencyUnavailable as exc:
        return f"Error: {kind} data source unavailable ({exc})"

    # ------------------------------------------------------------------
    # Fetch data — catch arbitrary scraping errors
    # ------------------------------------------------------------------
    if market == Market.A_SHARE:
        df_raw, last_exc = _fetch_a_share_ohlcv(ak, code, ak_start, ak_end)
        if df_raw is None:
            if last_exc is not None:
                return f"Error: failed to fetch A-share data for {symbol}: {last_exc}"
            return (
                f"No data found for symbol '{symbol}' between {start_date} and {end_date}"
            )
        # df_raw already has capitalized columns Date/Open/High/Low/Close/Volume,
        # sorted ascending, Date as a plain column (not index).
        df = df_raw
    else:
        try:
            # HK: no start/end date params — returns all history; filter client-side
            df = ak.stock_hk_daily(symbol=code, adjust="qfq")
        except Exception as exc:
            return f"Error: failed to fetch {kind} data for {symbol}: {exc}"

    # ------------------------------------------------------------------
    # Empty result
    # ------------------------------------------------------------------
    if _df_is_empty(df):
        return (
            f"No data found for symbol '{symbol}' between {start_date} and {end_date}"
        )

    # ------------------------------------------------------------------
    # Shape the DataFrame — guard against unexpected akshare column schema
    # ------------------------------------------------------------------
    try:
        if market == Market.A_SHARE:
            # _fetch_a_share_ohlcv already returned capitalized English columns
            # (Date/Open/High/Low/Close/Volume); just select to enforce column order.
            df = df[["Date", "Open", "High", "Low", "Close", "Volume"]].copy()
        else:
            # HK: English columns date, open, high, low, close, volume
            df = df.rename(columns={
                "date": "Date",
                "open": "Open",
                "high": "High",
                "low": "Low",
                "close": "Close",
                "volume": "Volume",
            })
            df = df[["Date", "Open", "High", "Low", "Close", "Volume"]].copy()
            # Client-side date filter (HK endpoint returns all history)
            df["Date"] = df["Date"].astype(str)
            df = df[(df["Date"] >= start_date) & (df["Date"] <= end_date)]

        # Coerce numerics
        for col in ["Open", "High", "Low", "Close", "Volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        # Sort ascending by date, set Date as index
        df = df.sort_values("Date").reset_index(drop=True)
        df = df.set_index("Date")
        df.index.name = "Date"

        # Round OHLC to 2 dp
        for col in ["Open", "High", "Low", "Close"]:
            df[col] = df[col].round(2)
    except Exception as exc:
        return f"Error: unexpected {kind} data response for {symbol}: {exc}"

    # ------------------------------------------------------------------
    # After shaping, check again for empty (HK client-side filter may empty it)
    # ------------------------------------------------------------------
    if df.empty:
        return (
            f"No data found for symbol '{symbol}' between {start_date} and {end_date}"
        )

    # ------------------------------------------------------------------
    # Build output string  (same contract as yfinance vendor)
    # ------------------------------------------------------------------
    # Use original ticker upper-cased as the display symbol in the header
    display_symbol = symbol.strip().upper() if market == Market.HK else code
    retrieved_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header = (
        f"# Stock data for {display_symbol} from {start_date} to {end_date}\n"
        f"# Total records: {len(df)}\n"
        f"# Data retrieved on: {retrieved_at}\n"
        "\n"
    )

    return header + df.to_csv()


def get_fundamentals(ticker: str, curr_date: str | None = None) -> str:
    """Return A-share or HK company fundamentals as a formatted label/value string.

    Parameters
    ----------
    ticker:
        A-share ticker — bare 6-digit code or with exchange suffix.
        HK ticker — 1-5 digit code with ``.HK`` suffix.
    curr_date:
        Accepted but not currently used; included to match the yfinance
        vendor's signature.

    Returns
    -------
    str
        On success: a 2-line ``#`` header followed by a blank line and then
        ``"Label: value"`` lines, one per indicator (latest period value).
        On empty/error: a plain-text message (never raises).
    """
    market = resolve_market(ticker)
    if market not in (Market.A_SHARE, Market.HK):
        return (
            f"This vendor handles A-share and HK data only. "
            f"'{ticker}' was classified as non-A-share/non-HK. "
            "Please use the appropriate vendor for this symbol."
        )

    kind = "A-share" if market == Market.A_SHARE else "HK"

    # Validate curr_date at entry if provided
    if curr_date is not None:
        date_err = _validate_date_str(curr_date, "curr_date")
        if date_err:
            return date_err

    if market == Market.A_SHARE:
        code = ticker.strip().upper().split(".")[0]
    else:
        # HK: strip .HK suffix, left-pad to 5 digits
        raw = ticker.strip().upper()
        if raw.endswith(".HK"):
            raw = raw[:-3]
        code = raw.zfill(5)

    try:
        ak = _dep_bootstrap.ensure("akshare")
    except _dep_bootstrap.DependencyUnavailable as exc:
        return f"Error: {kind} data source unavailable ({exc})"

    try:
        if market == Market.A_SHARE:
            df = ak.stock_financial_abstract(symbol=code)
        else:
            df = ak.stock_financial_hk_analysis_indicator_em(symbol=code)
    except Exception as exc:
        return f"Error: failed to fetch {kind} fundamentals for {ticker}: {exc}"

    if _df_is_empty(df):
        return f"No fundamentals data found for symbol '{ticker}'"

    try:
        retrieved_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if market == Market.A_SHARE:
            import re
            # Locate the indicator-name column: prefer "指标", fall back to "项目".
            if "指标" in df.columns:
                item_col = "指标"
            elif "项目" in df.columns:
                item_col = "项目"
            else:
                raise KeyError("No indicator column found (expected 指标 or 项目)")

            # Find all 8-digit period columns (YYYYMMDD).
            period_cols = [c for c in df.columns if re.fullmatch(r"\d{8}", str(c))]
            if not period_cols:
                raise ValueError("No 8-digit period columns found in A-share fundamentals DataFrame")

            # History-aware: filter out period columns that are AFTER curr_date
            # so a backtest with curr_date="2024-01-01" doesn't see 2026 data.
            if curr_date:
                cutoff_yyyymmdd = curr_date.replace("-", "")
                period_cols = [c for c in period_cols if str(c) <= cutoff_yyyymmdd]
                if not period_cols:
                    return (
                        f"No fundamentals data found for symbol '{ticker}' "
                        f"on or before {curr_date}"
                    )

            # Latest period (after curr_date filter): string comparison works
            # because YYYYMMDD sorts like calendar order.
            latest_period_col = max(period_cols)

            header = (
                f"# Company Fundamentals for {ticker.strip().upper()}\n"
                f"# Data retrieved on: {retrieved_at}\n"
                f"# Latest period: {latest_period_col}\n"
                "\n"
            )

            lines = []
            for _, row in df.iterrows():
                indicator = row[item_col]
                if pd.isna(indicator) or (isinstance(indicator, str) and not indicator.strip()):
                    continue
                value = row[latest_period_col]
                if pd.isna(value) or (isinstance(value, str) and not value.strip()):
                    continue
                if isinstance(value, float):
                    formatted_value = f"{value:,.2f}"
                else:
                    formatted_value = str(value)
                lines.append(f"{indicator}: {formatted_value}")
        else:
            # HK: wide-form DataFrame with a REPORT_DATE column.
            # History-aware: take the row whose REPORT_DATE is the latest ≤ curr_date.
            if curr_date and "REPORT_DATE" in df.columns:
                df_hk = df.copy()
                df_hk["_rd"] = pd.to_datetime(df_hk["REPORT_DATE"], errors="coerce")
                cutoff = pd.Timestamp(curr_date)
                df_hk = df_hk[df_hk["_rd"] <= cutoff].sort_values("_rd", ascending=False)
                df_hk = df_hk.drop(columns=["_rd"])
                if df_hk.empty:
                    return (
                        f"No fundamentals data found for symbol '{ticker}' "
                        f"on or before {curr_date}"
                    )
                row = df_hk.iloc[0]
            else:
                row = df.iloc[0]

            header = (
                f"# Company Fundamentals for {ticker.strip().upper()}\n"
                f"# Data retrieved on: {retrieved_at}\n"
                "\n"
            )
            lines = []
            for col_name in df.columns:
                value = row[col_name]
                if pd.isna(value) or (isinstance(value, str) and not value.strip()):
                    continue
                lines.append(f"{col_name}: {value}")

        return header + "\n".join(lines)
    except Exception as exc:
        return f"Error: unexpected {kind} fundamentals response for {ticker}: {exc}"


def _filter_by_curr_date(df: "pd.DataFrame", curr_date: str | None) -> "pd.DataFrame":
    """Best-effort filter: drop rows whose date-like column value > curr_date.

    Looks for a column whose name contains any of REPORT_DATE / 报告期 / 报告日期
    (case-insensitive).  If no such column is found, returns the DataFrame unchanged.
    """
    if curr_date is None or _df_is_empty(df):
        return df

    date_col = None
    target_substrings = ("report_date", "报告期", "报告日期")
    for col in df.columns:
        col_lower = col.lower()
        if any(sub in col_lower for sub in target_substrings):
            date_col = col
            break

    if date_col is None:
        return df

    try:
        mask = pd.to_datetime(df[date_col], errors="coerce") <= pd.Timestamp(curr_date)
        return df[mask]
    except Exception:
        # If comparison fails for any reason, skip filter silently
        return df


def _financial_statement(
    ticker: str,
    freq: str,
    curr_date: str | None,
    ak_method_name: str,
    title: str,
    empty_msg_kind: str,
    error_kind: str,
) -> str:
    """Shared implementation for balance_sheet / cashflow / income_statement."""
    if resolve_market(ticker) != Market.A_SHARE:
        return (
            f"This vendor handles A-share data only. "
            f"'{ticker}' was classified as non-A-share. "
            "Please use the appropriate vendor for this symbol."
        )

    # Validate curr_date at entry if provided
    if curr_date is not None:
        date_err = _validate_date_str(curr_date, "curr_date")
        if date_err:
            return date_err

    code = ticker.strip().upper().split(".")[0]

    try:
        ak = _dep_bootstrap.ensure("akshare")
    except _dep_bootstrap.DependencyUnavailable as exc:
        return f"Error: A-share data source unavailable ({exc})"

    try:
        method = getattr(ak, ak_method_name)
        df = method(symbol=code)
    except Exception as exc:
        return f"Error: failed to fetch {error_kind} for {ticker}: {exc}"

    if _df_is_empty(df):
        return f"No {empty_msg_kind} data found for symbol '{ticker}'"

    try:
        df = _filter_by_curr_date(df, curr_date)
        if _df_is_empty(df):
            return f"No {empty_msg_kind} data found for symbol '{ticker}'"

        retrieved_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        header = (
            f"# {title} for {ticker.strip().upper()} ({freq})\n"
            f"# Data retrieved on: {retrieved_at}\n"
            "\n"
        )
        return header + df.to_csv()
    except Exception as exc:
        return f"Error: unexpected A-share {error_kind} response for {ticker}: {exc}"


def get_balance_sheet(
    ticker: str, freq: str = "quarterly", curr_date: str | None = None
) -> str:
    """Return A-share balance sheet data as a header + CSV string.

    Parameters
    ----------
    ticker:
        A-share ticker — bare 6-digit code or with exchange suffix.
    freq:
        Accepted to match the yfinance vendor's signature; not used to select
        akshare endpoint (the EM endpoint returns all periods).
    curr_date:
        If provided and a date-like column (REPORT_DATE / 报告期 / 报告日期) is
        present, rows with date > curr_date are dropped.  If no date column is
        found the filter is silently skipped.

    Returns
    -------
    str
        Header + ``df.to_csv()`` or a plain-text error message (never raises).
    """
    return _financial_statement(
        ticker=ticker,
        freq=freq,
        curr_date=curr_date,
        ak_method_name="stock_balance_sheet_by_report_em",
        title="Balance Sheet data",
        empty_msg_kind="balance sheet",
        error_kind="A-share balance sheet",
    )


def get_cashflow(
    ticker: str, freq: str = "quarterly", curr_date: str | None = None
) -> str:
    """Return A-share cash flow data as a header + CSV string.

    Parameters
    ----------
    ticker:
        A-share ticker — bare 6-digit code or with exchange suffix.
    freq:
        Accepted to match the yfinance vendor's signature; not used to select
        akshare endpoint.
    curr_date:
        Best-effort date filter; see ``get_balance_sheet`` for details.

    Returns
    -------
    str
        Header + ``df.to_csv()`` or a plain-text error message (never raises).
    """
    return _financial_statement(
        ticker=ticker,
        freq=freq,
        curr_date=curr_date,
        ak_method_name="stock_cash_flow_sheet_by_report_em",
        title="Cash Flow data",
        empty_msg_kind="cash flow",
        error_kind="A-share cash flow",
    )


def get_income_statement(
    ticker: str, freq: str = "quarterly", curr_date: str | None = None
) -> str:
    """Return A-share income statement (profit sheet) data as a header + CSV string.

    Parameters
    ----------
    ticker:
        A-share ticker — bare 6-digit code or with exchange suffix.
    freq:
        Accepted to match the yfinance vendor's signature; not used to select
        akshare endpoint.
    curr_date:
        Best-effort date filter; see ``get_balance_sheet`` for details.

    Returns
    -------
    str
        Header + ``df.to_csv()`` or a plain-text error message (never raises).
    """
    return _financial_statement(
        ticker=ticker,
        freq=freq,
        curr_date=curr_date,
        ak_method_name="stock_profit_sheet_by_report_em",
        title="Income Statement data",
        empty_msg_kind="income statement",
        error_kind="A-share income statement",
    )


def get_news(ticker: str, start_date: str, end_date: str) -> str:
    """Return A-share per-stock news as a formatted markdown string.

    Signature matches the yfinance vendor's ``get_news_yfinance`` exactly so
    that ``route_to_vendor`` can dispatch transparently.

    Parameters
    ----------
    ticker:
        A-share ticker — bare 6-digit code or with exchange suffix
        (.SH, .SS, .SZ, .BJ), e.g. ``"600519"`` or ``"600519.SH"``.
    start_date:
        Start of the date range in ``yyyy-mm-dd`` format.
    end_date:
        End of the date range in ``yyyy-mm-dd`` format.

    Returns
    -------
    str
        On success: yfinance-compatible markdown with header
        ``## {ticker} News, from {start_date} to {end_date}:`` followed by
        ``### {title} (source: {publisher})`` sections.

        On empty/filtered/error result: a plain-text message (never raises).
    """
    # ------------------------------------------------------------------
    # Guard: this vendor only handles A-share symbols
    # ------------------------------------------------------------------
    if resolve_market(ticker) != Market.A_SHARE:
        return (
            f"This vendor handles A-share data only. "
            f"'{ticker}' was classified as non-A-share. "
            "Please use the appropriate vendor for this symbol."
        )

    # ------------------------------------------------------------------
    # Validate date parameters at entry (never-raises contract)
    # ------------------------------------------------------------------
    err = _validate_date_str(start_date, "start_date")
    if err:
        return err
    err = _validate_date_str(end_date, "end_date")
    if err:
        return err

    # ------------------------------------------------------------------
    # Normalise symbol: strip whitespace, upper-case, drop exchange suffix,
    # then zero-pad to 6 digits (A-share codes are always 6 digits).
    # ------------------------------------------------------------------
    code = ticker.strip().upper().split(".")[0].zfill(6)

    # ------------------------------------------------------------------
    # Load akshare on demand
    # ------------------------------------------------------------------
    try:
        ak = _dep_bootstrap.ensure("akshare")
    except _dep_bootstrap.DependencyUnavailable as exc:
        return f"Error: A-share data source unavailable ({exc})"

    # ------------------------------------------------------------------
    # Fetch news — catch arbitrary scraping / network errors
    # ------------------------------------------------------------------
    try:
        df = ak.stock_news_em(symbol=code)
    except Exception as exc:
        return f"Error: failed to fetch A-share news for {ticker}: {exc}"

    # ------------------------------------------------------------------
    # Empty result — also guard against endpoint returning a non-DataFrame
    # (scalar, list, etc.) via _df_is_empty which covers None/non-DataFrame.
    # ------------------------------------------------------------------
    if df is None:
        return f"No news found for {ticker}"
    # Some akshare versions return [] (a list) instead of an empty DataFrame;
    # scalars (int, str) must also be caught without calling len() which raises
    # on ints.  Route ALL non-DataFrame through _df_is_empty first.
    if not isinstance(df, pd.DataFrame):
        # Try length check only for sized types (list, tuple, etc.)
        try:
            is_empty_sized = len(df) == 0
        except TypeError:
            # Scalar types (int, float, etc.) have no len() — treat as error
            return f"Error: unexpected A-share news response for {ticker}: got {type(df).__name__}, expected DataFrame"
        if is_empty_sized:
            return f"No news found for {ticker}"
        return f"Error: unexpected A-share news response for {ticker}: got {type(df).__name__}, expected DataFrame"
    # Use helper (consistent with other empty-check sites)
    if _df_is_empty(df):
        return f"No news found for {ticker}"

    # ------------------------------------------------------------------
    # Shape the DataFrame — guard against unexpected akshare column schema.
    # akshare changed column names across versions; handle both variants.
    # Outer try/except covers the ENTIRE shape+loop so any unexpected
    # schema change (KeyError, AttributeError, etc.) returns an error string
    # rather than propagating to the caller.
    # ------------------------------------------------------------------
    try:
        # Resolve column names (variant A preferred; variant B as fallback)
        def _col(preferred: str, fallback: str) -> str | None:
            if preferred in df.columns:
                return preferred
            if fallback in df.columns:
                return fallback
            return None

        def _safe_str(value) -> str:
            if value is None or pd.isna(value):
                return ""
            return str(value).strip()

        title_col   = _col("新闻标题", "标题")
        content_col = _col("新闻内容", "内容")
        summary_col = _col("新闻摘要", "摘要")
        link_col    = _col("新闻链接", "链接")
        source_col  = _col("文章来源", "来源")
        time_col    = _col("发布时间", "时间")

        if title_col is None:
            raise KeyError("No title column found (expected 新闻标题 or 标题)")

        # Parse publish times (tz-naive); NaT treated as "include without filter"
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt   = datetime.strptime(end_date, "%Y-%m-%d")
        end_dt_inclusive = end_dt + pd.Timedelta(days=1)

        article_limit = int(_config_get()["news_article_limit"])
        news_str = ""
        count = 0

        for _, row in df.iterrows():
            if count >= article_limit:
                break

            # Date filter
            if time_col is not None:
                raw_time = row.get(time_col, "")
                pub_ts = pd.to_datetime(raw_time, errors="coerce")
                if pub_ts is not pd.NaT and not pd.isna(pub_ts):
                    # Drop tz if present
                    if hasattr(pub_ts, "tzinfo") and pub_ts.tzinfo is not None:
                        pub_ts = pub_ts.tz_localize(None)
                    pub_naive = pub_ts.to_pydatetime().replace(tzinfo=None)
                    if not (start_dt <= pub_naive < end_dt_inclusive):
                        continue

            title     = _safe_str(row.get(title_col, "")) if title_col else ""
            publisher = _safe_str(row.get(source_col, "")) if source_col else ""

            # Skip articles with no title — they are useless news entries
            if not title:
                continue

            # Summary: prefer explicit summary column; fall back to truncated content
            summary = ""
            if summary_col is not None:
                summary = _safe_str(row.get(summary_col, ""))
            if not summary and content_col is not None:
                content = _safe_str(row.get(content_col, ""))
                summary = content[:200] if content else ""

            link = _safe_str(row.get(link_col, "")) if link_col else ""

            news_str += f"### {title} (source: {publisher})\n"
            if summary:
                news_str += f"{summary}\n"
            if link:
                news_str += f"Link: {link}\n"
            news_str += "\n"
            count += 1

        if count == 0:
            return f"No news found for {ticker} between {start_date} and {end_date}"

        return f"## {ticker} News, from {start_date} to {end_date}:\n\n{news_str}"

    except Exception as exc:
        return f"Error: unexpected A-share news response for {ticker}: {exc}"


def get_indicators(symbol: str, indicator: str, curr_date: str, look_back_days: int) -> str:
    """Return A-share technical indicator values as a formatted string.

    Mirrors the contract of ``y_finance.get_stock_stats_indicators_window``:
    - Same output format: ``## {indicator} values from {before} to {curr_date}``
      header, one line per calendar day walking BACKWARD, description appended.
    - Same fail-safe contract: never raises; all error paths return strings.

    Parameters
    ----------
    symbol:
        A-share ticker — bare 6-digit code or with exchange suffix.
    indicator:
        One of the 13 supported indicators in ``_INDICATOR_CATALOG``.
    curr_date:
        Reference date in ``yyyy-mm-dd`` format.
    look_back_days:
        Number of calendar days to walk back from curr_date.

    Returns
    -------
    str
        On success: ``## {indicator} values from {before} to {curr_date}:``
        followed by one ``{date}: {value}`` line per calendar day, then the
        indicator description from ``_INDICATOR_CATALOG``.

        On error: a plain-text message starting with ``"Error: ..."`` or
        ``"No price data found ..."`` (never raises).
    """
    # 1. Indicator validation — return error string (no raise) if not in catalog
    if indicator not in _INDICATOR_CATALOG:
        return (
            f"Error: indicator '{indicator}' is not supported. "
            f"Choose from: {list(_INDICATOR_CATALOG.keys())}"
        )

    # 2. Non-A-share guard — return message (do NOT touch ensure)
    if resolve_market(symbol) != Market.A_SHARE:
        return (
            f"This vendor handles A-share data only. "
            f"'{symbol}' was classified as non-A-share. "
            "Please use the appropriate vendor for this symbol."
        )

    # 3. Validate curr_date — if invalid return error string
    date_err = _validate_date_str(curr_date, "curr_date")
    if date_err:
        return date_err
    try:
        curr_date_dt = datetime.strptime(curr_date.strip(), "%Y-%m-%d")
    except ValueError:
        return (
            f"Error: invalid date format for {curr_date!r}; expected yyyy-mm-dd"
        )

    # 3b. Coerce look_back_days to int — LLM agents may pass a string (e.g. "30")
    # Also rejects bool, NaN, inf, overflow via _coerce_positive_int.
    look_back_days, coerce_err = _coerce_positive_int(look_back_days, "look_back_days")
    if coerce_err:
        return coerce_err

    # 4. Compute fetch window (generous: 400 extra days for 200-SMA + buffer)
    fetch_start_dt = curr_date_dt - timedelta(days=look_back_days + 400)
    fetch_start = fetch_start_dt.strftime("%Y%m%d")
    fetch_end = curr_date_dt.strftime("%Y%m%d")

    code = symbol.strip().upper().split(".")[0]

    # 5. Ensure akshare via _dep_bootstrap
    try:
        ak = _dep_bootstrap.ensure("akshare")
    except _dep_bootstrap.DependencyUnavailable as exc:
        return f"Error: A-share data source unavailable ({exc})"

    # 6. Fetch OHLCV through the shared multi-source helper
    df_raw, last_exc = _fetch_a_share_ohlcv(ak, code, fetch_start, fetch_end)
    if df_raw is None:
        if last_exc is not None:
            return f"Error: failed to fetch A-share indicators for {symbol}: {last_exc}"
        return f"No price data found for symbol '{symbol}'; cannot compute {indicator}"

    # 7. Shape block — all stockstats work; guard unexpected schema errors
    try:
        from stockstats import wrap  # stockstats is a project dependency

        df = df_raw.copy()
        # Ensure Date column is datetime for stockstats wrap
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
        df = df.dropna(subset=["Date"])

        # Coerce OHLCV to numeric (in case helper returned object columns)
        for col in ["Open", "High", "Low", "Close", "Volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["Close"])

        # stockstats wrap — must have Date as a column, not index
        df = wrap(df)
        # Format Date strings after wrap (mirrors yfinance _get_stock_stats_bulk)
        df["Date"] = df["Date"].dt.strftime("%Y-%m-%d")

        # Trigger stockstats indicator calculation
        df[indicator]

        # Build {date_str: value_str} lookup dict
        date_value: dict[str, str] = {}
        for _, row in df.iterrows():
            date_str = row["Date"]
            val = row[indicator]
            if pd.isna(val):
                date_value[date_str] = "N/A"
            else:
                date_value[date_str] = str(val)

    except Exception as exc:
        return f"Error: unexpected A-share indicator response for {symbol}: {exc}"

    # 8. Walk dates BACK from curr_date through look_back_days
    before_dt = curr_date_dt - timedelta(days=look_back_days)
    before_date = before_dt.strftime("%Y-%m-%d")

    ind_string = ""
    current_dt = curr_date_dt
    while current_dt >= before_dt:
        date_str = current_dt.strftime("%Y-%m-%d")
        value = date_value.get(date_str, "N/A: Not a trading day (weekend or holiday)")
        ind_string += f"{date_str}: {value}\n"
        current_dt = current_dt - timedelta(days=1)

    # 9. Compose result (mirrors yfinance result_str format)
    result_str = (
        f"## {indicator} values from {before_date} to {curr_date}:\n\n"
        + ind_string
        + "\n\n"
        + _INDICATOR_CATALOG[indicator]
    )
    return result_str


def get_social_sentiment(ticker: str) -> str:
    """Retrieve retail social/attention signal for CN/HK markets via
    eastmoney 个股热度. Returns a formatted multi-line string ready for
    prompt injection. Fail-safe: never raises.

    For A-share: per-stock historical rank from stock_hot_rank_detail_em,
    plus associated hot concepts from stock_hot_keyword_em.
    For HK: per-stock historical rank (no keywords; HK endpoint lacks).
    For US/CRYPTO: returns a clear "not applicable" placeholder so the
    caller can route to StockTwits instead.
    """
    # Input type-guard (vendor never-raises contract):
    if not isinstance(ticker, str) or not ticker.strip():
        return f"Error: ticker must be a non-empty str, got {type(ticker).__name__}: {ticker!r}"

    market = resolve_market(ticker)

    if market in (Market.US, Market.CRYPTO):
        return (
            "<social sentiment via this vendor is not applicable for non-CN/HK markets; "
            "use StockTwits (US) instead>"
        )

    try:
        ak = _dep_bootstrap.ensure("akshare")
    except _dep_bootstrap.DependencyUnavailable as exc:
        return f"Error: social sentiment data source unavailable ({exc})"

    retrieved_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        if market == Market.A_SHARE:
            code = ticker.strip().upper().split(".")[0]
            prefixed = _eastmoney_a_share_symbol(code)

            # --- Rank data ---
            df_rank = None
            try:
                df_rank = ak.stock_hot_rank_detail_em(symbol=prefixed)
            except Exception as exc:
                logger.warning("stock_hot_rank_detail_em failed for %s: %s", prefixed, exc)

            if _df_is_empty(df_rank):
                return f"No retail attention rank data for {ticker}"

            market_label = "A-share"
            rank_col = "排名"
            time_col = "时间"

            # Sort ascending by time
            try:
                df_rank = df_rank.copy()
                df_rank[time_col] = pd.to_datetime(df_rank[time_col], errors="coerce")
                df_rank = df_rank.sort_values(time_col).reset_index(drop=True)
            except Exception as exc:
                logger.warning("rank df sort failed for %s: %s", ticker, exc)

            latest_row = df_rank.iloc[-1]
            latest_time = latest_row[time_col]
            try:
                current_rank = int(latest_row[rank_col])
            except (ValueError, TypeError):
                current_rank = latest_row[rank_col]

            # New/loyal followers
            new_followers = "N/A"
            loyal_followers = "N/A"
            ratio_str = "N/A"
            try:
                if "新晋粉丝" in df_rank.columns and "铁杆粉丝" in df_rank.columns:
                    nf = latest_row.get("新晋粉丝", None)
                    lf = latest_row.get("铁杆粉丝", None)
                    new_followers = str(int(nf)) if nf is not None and not pd.isna(nf) else "N/A"
                    loyal_followers = str(int(lf)) if lf is not None and not pd.isna(lf) else "N/A"
                    if nf is not None and lf is not None and not pd.isna(nf) and not pd.isna(lf):
                        lf_float = float(lf)
                        ratio_str = f"{float(nf) / lf_float:.2f}" if lf_float != 0 else "inf"
            except Exception as exc:
                logger.warning("follower extraction failed for %s: %s", ticker, exc)

            def _find_row_near(df, latest_dt, days_back):
                target = latest_dt - pd.Timedelta(days=days_back)
                try:
                    idx = (df[time_col] - target).abs().idxmin()
                    return df.loc[idx]
                except Exception:
                    return df.iloc[0]

            def _trend_label(old_rank, cur_rank):
                try:
                    delta = int(old_rank) - int(cur_rank)
                except (ValueError, TypeError):
                    return "无法计算"
                if abs(delta) <= 2:
                    return f"无明显变化 (Δ~0)"
                direction = "注意力上升" if delta > 0 else "注意力下降"
                qualifier = "显著" if abs(delta) > 20 else ""
                sign = "+" if delta > 0 else ""
                return f"注意力{qualifier + ('上升' if delta > 0 else '下降')} (Δ{sign}{delta})"

            latest_dt = latest_row[time_col]
            enough_history_7 = len(df_rank) >= 2
            enough_history_30 = len(df_rank) >= 2

            row_7d = _find_row_near(df_rank, latest_dt, 7)
            try:
                rank_7d = int(row_7d[rank_col])
                time_7d = row_7d[time_col]
            except Exception:
                rank_7d = row_7d[rank_col]
                time_7d = row_7d[time_col]

            row_30d = _find_row_near(df_rank, latest_dt, 30)
            try:
                rank_30d = int(row_30d[rank_col])
                time_30d = row_30d[time_col]
            except Exception:
                rank_30d = row_30d[rank_col]
                time_30d = row_30d[time_col]

            trend_7d = _trend_label(rank_7d, current_rank)
            trend_30d = _trend_label(rank_30d, current_rank)

            # --- Keywords ---
            keywords_block = ""
            try:
                df_kw = ak.stock_hot_keyword_em(symbol=prefixed)
                if not _df_is_empty(df_kw) and "概念名称" in df_kw.columns and "热度" in df_kw.columns:
                    try:
                        df_kw = df_kw.copy()
                        df_kw["热度"] = pd.to_numeric(df_kw["热度"], errors="coerce")
                        df_kw = df_kw.sort_values("热度", ascending=False).head(5)
                        kw_lines = []
                        for i, (_, krow) in enumerate(df_kw.iterrows(), 1):
                            name = str(krow["概念名称"]).strip()
                            heat = krow["热度"]
                            heat_str = f"{int(heat)}" if not pd.isna(heat) else "N/A"
                            kw_lines.append(f"{i}. {name} (heat: {heat_str})")
                        if kw_lines:
                            keywords_block = (
                                f"\n## Associated hot concepts (top {len(kw_lines)} by heat)\n"
                                + "\n".join(kw_lines)
                                + "\n"
                            )
                    except Exception as exc:
                        logger.warning("keyword formatting failed for %s: %s", ticker, exc)
            except Exception as exc:
                logger.warning("stock_hot_keyword_em failed for %s: %s", prefixed, exc)

            output = (
                f"# Social sentiment for {ticker} ({market_label}, via Eastmoney 股吧)\n"
                f"# Retrieved at: {retrieved_at}\n"
                f"\n"
                f"## Retail attention rank (lower number = more retail attention)\n"
                f"- Current: rank #{current_rank} (as of {latest_time})\n"
                f"- ~7 days ago: rank #{rank_7d} (as of {time_7d}) → {trend_7d}\n"
                f"- ~30 days ago: rank #{rank_30d} (as of {time_30d}) → {trend_30d}\n"
                f"\n"
                f"## Follower composition (most recent snapshot)\n"
                f"- 新晋粉丝 (new followers in latest window): {new_followers}\n"
                f"- 铁杆粉丝 (loyal/long-term followers): {loyal_followers}\n"
                f"- new/loyal ratio: {ratio_str} (low = stable interest; high = speculative influx)\n"
                + keywords_block
                + "\n"
                "Interpretation guide for the agent:\n"
                "- Rank rising (smaller number over time) = more retail attention; falling = less.\n"
                "- High new/loyal ratio = recent speculative buying interest; low ratio = stable institutional or long-term retail.\n"
                "- Hot concepts reveal which themes are driving the attention.\n"
            )
            return output

        else:  # HK
            raw = ticker.strip().upper()
            if raw.endswith(".HK"):
                raw = raw[:-3]
            code5 = raw.zfill(5)

            df_rank = None
            try:
                df_rank = ak.stock_hk_hot_rank_detail_em(symbol=code5)
            except Exception as exc:
                logger.warning("stock_hk_hot_rank_detail_em failed for %s: %s", code5, exc)

            if _df_is_empty(df_rank):
                return f"No retail attention rank data for {ticker}"

            rank_col = "排名"
            time_col = "时间"

            try:
                df_rank = df_rank.copy()
                df_rank[time_col] = pd.to_datetime(df_rank[time_col], errors="coerce")
                df_rank = df_rank.sort_values(time_col).reset_index(drop=True)
            except Exception as exc:
                logger.warning("HK rank df sort failed for %s: %s", ticker, exc)

            latest_row = df_rank.iloc[-1]
            latest_time = latest_row[time_col]
            try:
                current_rank = int(latest_row[rank_col])
            except (ValueError, TypeError):
                current_rank = latest_row[rank_col]

            def _find_row_near_hk(df, latest_dt, days_back):
                target = latest_dt - pd.Timedelta(days=days_back)
                try:
                    idx = (df[time_col] - target).abs().idxmin()
                    return df.loc[idx]
                except Exception:
                    return df.iloc[0]

            def _trend_label_hk(old_rank, cur_rank):
                try:
                    delta = int(old_rank) - int(cur_rank)
                except (ValueError, TypeError):
                    return "无法计算"
                if abs(delta) <= 2:
                    return f"无明显变化 (Δ~0)"
                sign = "+" if delta > 0 else ""
                qualifier = "显著" if abs(delta) > 20 else ""
                return f"注意力{qualifier + ('上升' if delta > 0 else '下降')} (Δ{sign}{delta})"

            latest_dt = latest_row[time_col]
            row_7d = _find_row_near_hk(df_rank, latest_dt, 7)
            try:
                rank_7d = int(row_7d[rank_col])
                time_7d = row_7d[time_col]
            except Exception:
                rank_7d = row_7d[rank_col]
                time_7d = row_7d[time_col]

            row_30d = _find_row_near_hk(df_rank, latest_dt, 30)
            try:
                rank_30d = int(row_30d[rank_col])
                time_30d = row_30d[time_col]
            except Exception:
                rank_30d = row_30d[rank_col]
                time_30d = row_30d[time_col]

            trend_7d = _trend_label_hk(rank_7d, current_rank)
            trend_30d = _trend_label_hk(rank_30d, current_rank)

            # TOP100 HK attention enrichment
            top100_section = ""
            try:
                df_top100 = ak.stock_hk_hot_rank_em()
                if not _df_is_empty(df_top100):
                    col_top100_code = next(
                        (c for c in df_top100.columns if c in ("代码", "股票代码")), None
                    )
                    col_top100_rank = next(
                        (c for c in df_top100.columns if "排名" in c), None
                    )
                    if col_top100_code:
                        mask100 = df_top100[col_top100_code].astype(str).str.strip() == code5
                        matched100 = df_top100[mask100]
                        if not matched100.empty:
                            top100_rank_val = ""
                            if col_top100_rank:
                                top100_rank_val = _safe_str(matched100.iloc[0].get(col_top100_rank, ""))
                            top100_section = (
                                f"\n## Position in TOP100 HK attention: "
                                f"#{top100_rank_val} (out of 100 most-watched HK stocks today)"
                            )
                        else:
                            top100_section = (
                                "\n## Not in TOP100 HK attention today (less mainstream interest)"
                            )
            except Exception as exc:
                logger.warning("stock_hk_hot_rank_em failed for %s: %s", code5, exc)
                top100_section = ""

            output = (
                f"# Social sentiment for {ticker} (HK, via Eastmoney 股吧)\n"
                f"# Retrieved at: {retrieved_at}\n"
                f"\n"
                f"## Retail attention rank (lower = more attention)\n"
                f"- Current: rank #{current_rank} (as of {latest_time})\n"
                f"- ~7 days ago: rank #{rank_7d}  → {trend_7d}\n"
                f"- ~30 days ago: rank #{rank_30d} → {trend_30d}\n"
                f"\n"
                "(HK eastmoney endpoint provides rank only; no follower composition or concept keywords.)\n"
                + top100_section + "\n"
            )
            return output

    except Exception as exc:
        return f"Error: unexpected social sentiment response for {ticker}: {exc}"


def _safe_str(value) -> str:
    """Convert value to string safely, returning '' for None/NaN."""
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _eastmoney_session():
    """A requests.Session configured to bypass macOS system-level proxy
    and look like a real Chrome browser making XHR requests from the
    Eastmoney 股吧 page. Useful for eastmoney API sub-domains that have
    light anti-bot heuristics (UA + Referer/Origin checks)."""
    import requests
    s = requests.Session()
    s.trust_env = False  # bypass system proxy (push2.eastmoney.com is blocked through proxy)
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://guba.eastmoney.com/rank/",
        "Origin": "https://guba.eastmoney.com",
    })
    return s


def _eastmoney_http_retry(call_fn, max_attempts=3, backoffs=(0.5, 1.5, 3.0)):
    """Light retry wrapper for eastmoney HTTP calls. Retries on ConnectionError
    / Timeout (eastmoney burst-rate-limits with TCP resets, not HTTP codes).
    Other exceptions propagate immediately. Returns the response on success."""
    import requests as _r
    last_exc = None
    for attempt in range(max_attempts):
        try:
            return call_fn()
        except (_r.exceptions.ConnectionError, _r.exceptions.Timeout, ConnectionError) as e:
            last_exc = e
            if attempt < max_attempts - 1:
                time.sleep(backoffs[attempt])
    # Exhausted retries; re-raise the last exception so caller's outer try wraps it
    raise last_exc


# ---------------------------------------------------------------------------
# A. 涨停板 pool summary
# ---------------------------------------------------------------------------

def get_zt_pool_summary(curr_date: str) -> str:
    """Return 涨停板 pool summary for the given trading date.

    Parameters
    ----------
    curr_date:
        Date in ``yyyy-mm-dd`` format.

    Returns
    -------
    str
        Formatted plaintext summary of 涨停板 stocks. Fail-safe: never raises.
    """
    err = _validate_date_str(curr_date, "curr_date")
    if err:
        return err

    code_date = curr_date.replace("-", "")

    try:
        ak = _dep_bootstrap.ensure("akshare")
    except _dep_bootstrap.DependencyUnavailable as exc:
        return f"Error: A-share data source unavailable ({exc})"

    try:
        df = ak.stock_zt_pool_em(date=code_date)
    except Exception as exc:
        return f"Error: failed to fetch 涨停板 data for {curr_date}: {type(exc).__name__}: {str(exc)[:120]}"

    if _df_is_empty(df):
        return f"No 涨停板 data for {curr_date} (may be non-trading day or weekend)"

    try:
        total = len(df)

        def _parse_market_cap(val):
            try:
                return float(val) / 1e8
            except (TypeError, ValueError):
                return 0.0

        def _parse_amount(val):
            try:
                return float(val) / 1e8
            except (TypeError, ValueError):
                return 0.0

        # Determine column names (defensive: 流通市值 / 成交额 / 涨跌幅 / 代码 / 名称)
        col_market = next((c for c in df.columns if "流通市值" in c), None)
        col_amount = next((c for c in df.columns if "成交额" in c), None)
        col_pct = next((c for c in df.columns if "涨跌幅" in c), None)
        col_code = next((c for c in df.columns if c in ("代码", "股票代码")), None)
        col_name = next((c for c in df.columns if c in ("名称", "股票名称")), None)

        lines = [
            f"# 涨停板池 for {curr_date} (Eastmoney)",
            f"# Total 涨停 stocks: {total}",
        ]

        if col_market and col_code and col_name:
            df_cap = df.copy()
            df_cap["_cap"] = pd.to_numeric(df_cap[col_market], errors="coerce").fillna(0)
            top_cap = df_cap.nlargest(10, "_cap")
            lines.append("# Top 10 by 流通市值:")
            for i, (_, row) in enumerate(top_cap.iterrows(), 1):
                code = _safe_str(row.get(col_code, ""))
                name = _safe_str(row.get(col_name, ""))
                pct = _safe_str(row.get(col_pct, "")) if col_pct else "N/A"
                cap_val = _parse_market_cap(row.get(col_market, 0))
                amt_val = _parse_amount(row.get(col_amount, 0)) if col_amount else 0.0
                lines.append(
                    f"{i}. {code} {name} 涨幅+{pct}% 流通市值{cap_val:.1f}亿 成交额{amt_val:.1f}亿"
                )

        if col_amount and col_code and col_name:
            df_amt = df.copy()
            df_amt["_amt"] = pd.to_numeric(df_amt[col_amount], errors="coerce").fillna(0)
            top_amt = df_amt.nlargest(10, "_amt")
            lines.append("# Top 10 by 成交额:")
            for i, (_, row) in enumerate(top_amt.iterrows(), 1):
                code = _safe_str(row.get(col_code, ""))
                name = _safe_str(row.get(col_name, ""))
                pct = _safe_str(row.get(col_pct, "")) if col_pct else "N/A"
                cap_val = _parse_market_cap(row.get(col_market, 0)) if col_market else 0.0
                amt_val = _parse_amount(row.get(col_amount, 0))
                lines.append(
                    f"{i}. {code} {name} 涨幅+{pct}% 流通市值{cap_val:.1f}亿 成交额{amt_val:.1f}亿"
                )

        lines.append("")
        lines.append(
            "Interpretation: 涨停板 represents the strongest retail-FOMO signal in A-share. "
            "High 成交额 涨停 = institutional + retail conviction; "
            "small-cap 涨停 with low 成交额 = speculative."
        )
        return "\n".join(lines)
    except Exception as exc:
        return f"Error: unexpected 涨停板 response for {curr_date}: {type(exc).__name__}: {str(exc)[:120]}"


# ---------------------------------------------------------------------------
# B. 飙升榜 — stocks with biggest day-over-day rank improvement
# ---------------------------------------------------------------------------

def get_hot_up_rank() -> str:
    """A-share retail-attention 飙升榜: top 20 stocks with largest day-over-day
    rank improvement on Eastmoney 股吧 (个股人气榜). Directly calls eastmoney
    APIs (bypassing akshare) so it can use trust_env=False to avoid the macOS
    system-proxy issue that breaks push2.eastmoney.com.

    Returns formatted markdown table. Fail-safe: never raises; any failure
    returns an informative placeholder string.
    """
    try:
        sess = _eastmoney_session()
        # Step 1: GET ranking changes (POST endpoint)
        rank_url = "https://emappdata.eastmoney.com/stockrank/getAllHisRcList"
        rank_payload = {
            "appId": "appId01",
            "globalId": "786e4c21-70dc-435a-93bb-38",
            "marketType": "",
            "pageNo": 1,
            "pageSize": 100,
        }
        rank_resp = _eastmoney_http_retry(
            lambda: sess.post(rank_url, json=rank_payload, timeout=10)
        )
        if rank_resp.status_code != 200:
            return f"<飙升榜 unavailable: rank endpoint HTTP {rank_resp.status_code}>"
        rank_json = rank_resp.json()
        rank_data = rank_json.get("data") if isinstance(rank_json, dict) else None
        if not isinstance(rank_data, list) or not rank_data:
            return "<飙升榜 unavailable: empty rank list from eastmoney>"
        # rank_data items have keys: sc (e.g. "SH600519"), rk (current rank), hrc (rank change vs yesterday)

        # Step 2: GET prices for those tickers via push2.eastmoney.com
        # Build secids: SH→"1.<code>", SZ→"0.<code>"
        marks = []
        for item in rank_data:
            sc = item.get("sc", "")
            if isinstance(sc, str) and len(sc) >= 8:
                marks.append(("0." if "SZ" in sc else "1.") + sc[2:])
        if not marks:
            return "<飙升榜 unavailable: no valid sc codes from rank list>"
        secids = ",".join(marks) + ",?v=08926209912590994"
        price_url = "https://push2.eastmoney.com/api/qt/ulist.np/get"
        price_params = {
            "ut": "f057cbcbce2a86e2866ab8877db1d059",
            "fltt": "2",
            "invt": "2",
            "fields": "f14,f3,f12,f2",
            "secids": secids,
        }
        price_resp = _eastmoney_http_retry(
            lambda: sess.get(price_url, params=price_params, timeout=10)
        )
        if price_resp.status_code != 200:
            return f"<飙升榜 unavailable: price endpoint HTTP {price_resp.status_code}>"
        price_json = price_resp.json()
        price_diff = (
            (price_json.get("data") or {}).get("diff", [])
            if isinstance(price_json, dict) else []
        )
        if not isinstance(price_diff, list) or not price_diff:
            return "<飙升榜 unavailable: empty price list from push2>"

        # Step 3: merge rank_data + price_diff into a single DataFrame
        # rank_data: sc, rk, hrc (and we use sc as the key)
        # price_diff: f12=代码, f14=股票名称, f2=最新价, f3=涨跌幅
        price_by_code = {
            row.get("f12"): row for row in price_diff if isinstance(row, dict) and row.get("f12")
        }
        rows_out = []
        for item in rank_data:
            sc = item.get("sc", "")
            if not isinstance(sc, str) or len(sc) < 8:
                continue
            code_only = sc[2:]  # strip "SH"/"SZ" prefix
            price_row = price_by_code.get(code_only, {})
            rows_out.append({
                "代码": sc,
                "股票名称": price_row.get("f14", ""),
                "最新价": price_row.get("f2"),
                "涨跌幅": price_row.get("f3"),
                "当前排名": item.get("rk"),
                "排名较昨日变动": item.get("hrc"),
            })

        # Sort by 排名较昨日变动 desc, take top 20
        df = pd.DataFrame(rows_out)
        df["排名较昨日变动"] = pd.to_numeric(df["排名较昨日变动"], errors="coerce")
        df["当前排名"] = pd.to_numeric(df["当前排名"], errors="coerce")
        df["涨跌幅"] = pd.to_numeric(df["涨跌幅"], errors="coerce")
        df = df.dropna(subset=["排名较昨日变动"]).sort_values("排名较昨日变动", ascending=False).head(20)

        if df.empty:
            return "<飙升榜 unavailable: no rows after merge>"

        from datetime import datetime as _dt
        header = (
            "🚀 东方财富 attention 飙升榜 — Top 20 (排名较昨日变动 desc)"
        )
        rows = []
        for _, r in df.iterrows():
            chg = r["涨跌幅"]
            chg_str = f"{chg:+.2f}%" if pd.notna(chg) else "—"
            rank_now = r["当前排名"]
            rank_now_str = f"{int(rank_now)}" if pd.notna(rank_now) else "—"
            rank_chg = r["排名较昨日变动"]
            rank_chg_str = f"{int(rank_chg)}"
            rows.append(
                f"🔥 {r['代码']} {r['股票名称']} · 排名 #{rank_now_str} (飙升 +{rank_chg_str} 位) · {chg_str}"
            )
        return (
            header + "\n" + "\n".join(rows) + "\n\n"
            "Interpretation: Stocks here have suddenly become topics of retail attention. "
            "Combined with 涨跌幅 direction, indicates breakout/breakdown narratives forming. "
            "Cross-reference with the same-board peers in 涨停板 or the 龙虎榜 to confirm "
            "if a sector-wide narrative is in motion."
        )
    except Exception as e:
        logger.warning("飙升榜 fetch failed: %s", e)
        return f"<飙升榜 unavailable: {type(e).__name__}: {str(e)[:120]}>"


# ---------------------------------------------------------------------------
# C. 龙虎榜 summary (market-wide + ticker-specific)
# ---------------------------------------------------------------------------

def get_lhb_summary(ticker: str, curr_date: str, days_back: int = 5) -> str:
    """Return 龙虎榜 summary for recent trading days.

    Parameters
    ----------
    ticker:
        A-share ticker (bare code or with exchange suffix).
    curr_date:
        Reference date in ``yyyy-mm-dd`` format (used as end_date).
    days_back:
        Number of days to look back from curr_date (default 5).

    Returns
    -------
    str
        Formatted plaintext. Fail-safe: never raises.
    """
    # Input type-guards (vendor never-raises contract):
    if not isinstance(ticker, str) or not ticker.strip():
        return f"Error: ticker must be a non-empty str, got {type(ticker).__name__}: {ticker!r}"
    err = _validate_date_str(curr_date, "curr_date")
    if err:
        return err
    coerced, coerce_err = _coerce_positive_int(days_back, "days_back")
    if coerce_err:
        return coerce_err
    days_back = coerced

    try:
        curr_date_dt = datetime.strptime(curr_date.strip(), "%Y-%m-%d")
    except ValueError:
        return f"Error: invalid date format for {curr_date!r}; expected yyyy-mm-dd"

    code = ticker.strip().upper().split(".")[0]
    end_yyyymmdd = curr_date.replace("-", "")
    start_dt = curr_date_dt - timedelta(days=days_back)
    start_yyyymmdd = start_dt.strftime("%Y%m%d")

    try:
        ak = _dep_bootstrap.ensure("akshare")
    except _dep_bootstrap.DependencyUnavailable as exc:
        return f"Error: A-share data source unavailable ({exc})"

    try:
        df = ak.stock_lhb_detail_em(start_date=start_yyyymmdd, end_date=end_yyyymmdd)
    except Exception as exc:
        return f"Error: failed to fetch 龙虎榜 data for {ticker}: {type(exc).__name__}: {str(exc)[:120]}"

    if _df_is_empty(df):
        return f"No 龙虎榜 data for the window {start_yyyymmdd}–{end_yyyymmdd}"

    try:
        col_code = next((c for c in df.columns if c in ("代码", "股票代码")), None)
        col_name = next((c for c in df.columns if c in ("名称", "股票名称")), None)
        col_date = next((c for c in df.columns if "上榜日" in c or "日期" in c), None)
        col_pct = next((c for c in df.columns if "涨跌幅" in c), None)
        col_net = next((c for c in df.columns if "净买" in c), None)
        col_interp = next((c for c in df.columns if "解读" in c), None)

        lines = [
            f"# 龙虎榜 for {ticker} (recent {days_back} trading days, Eastmoney)",
            "",
            "## Ticker-specific 上榜 (if any):",
        ]

        if col_code:
            df_ticker = df[df[col_code].astype(str).str.strip() == code]
        else:
            df_ticker = pd.DataFrame()

        if _df_is_empty(df_ticker):
            lines.append("未上榜 (not on 龙虎榜 in this window)")
        else:
            header_cols = [c for c in ["上榜日", col_date, "涨跌幅", col_pct, "净买额", col_net, "解读", col_interp] if c]
            # Build table header
            th_parts = []
            for col in [col_date, col_pct, col_net, col_interp]:
                if col:
                    th_parts.append(col)
            lines.append("| " + " | ".join(th_parts) + " |")
            lines.append("| " + " | ".join(["--"] * len(th_parts)) + " |")
            for _, row in df_ticker.iterrows():
                row_parts = [_safe_str(row.get(c, "")) for c in th_parts]
                lines.append("| " + " | ".join(row_parts) + " |")

        lines.append("")
        lines.append(f"## Market-wide context — Top 5 净买入 in window:")

        # Try to sort by net buy amount
        df_sorted = None
        sort_note = ""
        if col_net and not _df_is_empty(df):
            try:
                df_copy = df.copy()
                df_copy["_net_numeric"] = pd.to_numeric(
                    df_copy[col_net].astype(str).str.replace(",", "").str.replace("亿", "e8").str.replace("万", "e4"),
                    errors="coerce"
                )
                df_sorted_buy = df_copy.sort_values("_net_numeric", ascending=False).head(5)
                df_sorted_sell = df_copy.sort_values("_net_numeric", ascending=True).head(5)
            except Exception:
                sort_note = " (could not sort by 净买额 due to format)"
                df_sorted_buy = df.head(5)
                df_sorted_sell = df.tail(5)
        else:
            sort_note = " (could not sort by 净买额 due to format)"
            df_sorted_buy = df.head(5)
            df_sorted_sell = df.tail(5)

        th_parts_mw = [c for c in [col_code, col_name, col_date, col_net, col_interp] if c]
        lines.append("| " + " | ".join(th_parts_mw) + f" |{sort_note}")
        lines.append("| " + " | ".join(["--"] * len(th_parts_mw)) + " |")
        for _, row in df_sorted_buy.iterrows():
            row_parts = [_safe_str(row.get(c, "")) for c in th_parts_mw]
            lines.append("| " + " | ".join(row_parts) + " |")

        lines.append("")
        lines.append(f"## Market-wide context — Top 5 净卖出 in window:")
        lines.append("| " + " | ".join(th_parts_mw) + " |")
        lines.append("| " + " | ".join(["--"] * len(th_parts_mw)) + " |")
        for _, row in df_sorted_sell.iterrows():
            row_parts = [_safe_str(row.get(c, "")) for c in th_parts_mw]
            lines.append("| " + " | ".join(row_parts) + " |")

        lines.append("")
        lines.append(
            "Interpretation: 龙虎榜 captures large-fund / hot-money desk transactions and is a "
            "\"hard signal\" (real capital flow), not just retail attention. The 解读 field reveals "
            "institutional vs hot-money desk patterns."
        )
        return "\n".join(lines)
    except Exception as exc:
        return f"Error: unexpected 龙虎榜 response for {ticker}: {type(exc).__name__}: {str(exc)[:120]}"


# ---------------------------------------------------------------------------
# D. 雪球 attention (with thread-safe TTL cache)
# ---------------------------------------------------------------------------

def _get_xueqiu_cached(symbol: str) -> "pd.DataFrame | None":
    """Return cached or freshly-fetched xueqiu df for symbol ('最热门'|'本周新增').
    Returns DataFrame on success, None on failure. Failures are NOT cached so
    a transient network error doesn't poison the 10-min TTL window.
    Single-flight: with the lock held during fetch, concurrent callers wait
    rather than triggering a stampede.

    Trade-off: holding the lock during the ~18s fetch means concurrent callers
    for the same symbol block. For 最热门 + 本周新增 called in sequence, worst
    case is ~36s wait for concurrent callers. This is acceptable in a
    1-task-concurrency production web context.
    """
    import time as _t
    with _XUEQIU_CACHE_LOCK:
        # First check inside lock
        if symbol in _XUEQIU_CACHE:
            cached_at, df = _XUEQIU_CACHE[symbol]
            if _t.time() - cached_at < _XUEQIU_CACHE_TTL:
                return df
        # Cache miss or expired — fetch INSIDE lock so concurrent callers wait
        try:
            ak = _dep_bootstrap.ensure("akshare")
            df = ak.stock_hot_tweet_xq(symbol=symbol)
        except Exception as exc:
            logger.warning("xueqiu fetch %s failed: %s", symbol, exc)
            return None  # do NOT cache failures
        # Only cache successful fetches
        if isinstance(df, pd.DataFrame):
            _XUEQIU_CACHE[symbol] = (_t.time(), df)
            return df
        return None


def get_xueqiu_attention(ticker: str) -> str:
    """Return 雪球 social attention rank for an A-share ticker.

    Parameters
    ----------
    ticker:
        A-share ticker (bare 6-digit code or with exchange suffix).

    Returns
    -------
    str
        Formatted plaintext. Fail-safe: never raises.
        Non-A-share tickers return a "not applicable" placeholder.
    """
    # Input type-guard (vendor never-raises contract):
    if not isinstance(ticker, str) or not ticker.strip():
        return f"Error: ticker must be a non-empty str, got {type(ticker).__name__}: {ticker!r}"

    market = resolve_market(ticker)

    if market not in (Market.A_SHARE,):
        return "<xueqiu attention not applicable; only A-share supported via this endpoint>"

    code = ticker.strip().upper().split(".")[0]
    prefixed = _eastmoney_a_share_symbol(code)  # SH600519 / SZ000001 etc.

    try:
        df_hot = _get_xueqiu_cached("最热门")
        df_weekly = _get_xueqiu_cached("本周新增")
    except Exception as exc:
        return f"Error: failed to fetch 雪球 attention: {type(exc).__name__}: {str(exc)[:120]}"

    try:
        lines = [f"# 雪球 attention for {ticker} (CN retail social platform, snapshot from past ≤10min)", ""]

        def _lookup(df, prefixed_code):
            if _df_is_empty(df):
                return None, None, None
            # Try column 股票代码
            col_sym = next((c for c in df.columns if "代码" in c), None)
            col_follow = next((c for c in df.columns if "关注" in c), None)
            if col_sym is None:
                return None, None, None
            mask = df[col_sym].astype(str).str.upper() == prefixed_code.upper()
            matched = df[mask]
            if matched.empty:
                return None, None, None
            idx = matched.index[0]
            rank = df.index.get_loc(idx) + 1  # 1-based
            total = len(df)
            follow_val = matched.iloc[0].get(col_follow, None) if col_follow else None
            return rank, total, follow_val

        cum_rank, cum_total, cum_follow = _lookup(df_hot, prefixed)
        wk_rank, wk_total, _ = _lookup(df_weekly, prefixed)

        if cum_rank is None and wk_rank is None:
            return f"No 雪球 attention data for {ticker} (not in A-share universe, or 雪球 data unavailable)"

        lines.append("## Cumulative attention (累计关注度排行):")
        if cum_rank is not None:
            cum_pct = round((cum_rank / cum_total) * 100, 1)
            follow_str = str(int(cum_follow)) if cum_follow is not None and not pd.isna(cum_follow) else "N/A"
            lines.append(f"- 关注数: {follow_str}")
            lines.append(f"- 雪球排名: #{cum_rank} of {cum_total} (top {cum_pct}% of A-share universe)")
        else:
            lines.append("- Not found in 最热门 list")

        lines.append("")
        lines.append("## Weekly new attention (本周新增):")
        if wk_rank is not None:
            wk_pct = round((wk_rank / wk_total) * 100, 1)
            lines.append(f"- 周新增排名: #{wk_rank} of {wk_total} (top {wk_pct}%)")
        else:
            lines.append("- Not found in 本周新增 list")

        lines.append("")
        lines.append(
            "Interpretation: 雪球 is the dominant CN-mainland equity-investor social platform "
            "(most similar to StockTwits). Cumulative rank reflects long-term investor interest; "
            "weekly rank reveals current narrative momentum. A stock with low cumulative rank "
            "(most-watched) AND high weekly rank delta = recent attention spike."
        )
        return "\n".join(lines)
    except Exception as exc:
        return f"Error: unexpected 雪球 attention response for {ticker}: {type(exc).__name__}: {str(exc)[:120]}"


def _strip_akshare_from_chain(value: str, default: str) -> str:
    """Remove 'akshare' tokens from a comma-separated vendor chain.

    Returns the cleaned chain or `default` if nothing remains after stripping.
    Handles exact-match (``"akshare"``), prefix (``"akshare,yfinance"``),
    suffix (``"yfinance,akshare"``), and middle forms
    (``"alpha_vantage,akshare,yfinance"``).
    """
    if not isinstance(value, str):
        return value
    parts = [p.strip() for p in value.split(",") if p.strip()]
    cleaned = [p for p in parts if p != "akshare"]
    return ",".join(cleaned) if cleaned else default


def apply_china_vendor_overlay(config: dict, ticker: str) -> None:
    """Route A-share or HK tickers to the 'akshare' vendor for THIS run only.

    **Differential overlay design (critical for correctness):**
    This function preserves any caller-set non-akshare entries (Important 6),
    clears any stale akshare entries left from a prior run in the owned key
    sets (Critical 1), then applies the akshare keys appropriate for the
    current ticker's market.  All writes are propagated to the global
    ``_config`` via :func:`tradingagents.dataflows.config.replace_section`,
    which uses REPLACE semantics — defeating
    ``tradingagents/dataflows/config.py:set_config``'s merge-only behaviour.

    Owned key sets (keys this function may add or remove):
      ``_AKSHARE_DATA_VENDOR_KEYS`` — four ``data_vendors`` category keys.
      ``_AKSHARE_TOOL_VENDOR_KEYS`` — two ``tool_vendors`` method keys.

    Contract:
    (a) Caller's custom non-akshare entries (e.g. ``{"get_news":"alpha_vantage"}``)
        are preserved — only the owned akshare keys are touched.
    (b) Stale akshare entries from prior runs in the owned keys are cleared,
        then the appropriate set is written for the current ticker.
    (c) Both local *config* and the global ``_config`` are updated, so any
        subsequent ``set_config(config)`` merge cannot leave stale keys.

    Design: A_SHARE vs HK use fundamentally different overlay strategies
    because AkShare covers different methods for each market.

    **A_SHARE** (category-wide overlay):
    Sets the four ``data_vendors`` category keys to ``"akshare"``.
    Does NOT set any ``tool_vendors`` keys.

    **HK** (per-method tool_vendors overlay):
    AkShare only covers HK OHLCV and key indicators; statements/news fall
    back to yfinance.  Sets the two ``tool_vendors`` method keys to
    ``"akshare"``.  Does NOT modify ``data_vendors``.

    **US / CRYPTO**: clearing-only — stale akshare entries removed from both
    owned key sets; no new akshare entries added.

    Aliasing safety:
    All paths work on local ``dict(...)`` copies of the caller's nested dicts
    before assigning back, so in-place mutation of shared dict objects
    (e.g. from a shallow ``DEFAULT_CONFIG.copy()``) never occurs.
    """
    from tradingagents.default_config import DEFAULT_CONFIG  # local import avoids circularity

    # Keys this function "owns" — it may add or remove these only
    _AKSHARE_DATA_VENDOR_KEYS = (
        "core_stock_apis", "fundamental_data", "news_data", "technical_indicators"
    )
    _AKSHARE_TOOL_VENDOR_KEYS = ("get_stock_data", "get_fundamentals")

    market = resolve_market(ticker)

    # Work on shallow copies of the caller's nested dicts (aliasing-safe)
    data_vendors = dict(config.get("data_vendors") or {})
    tool_vendors = dict(config.get("tool_vendors") or {})

    # Step 1: clear any stale akshare entries in the owned key sets.
    # Handles both exact form ("akshare") and comma-chain forms
    # ("akshare,yfinance", "yfinance,akshare", "alpha_vantage,akshare,yfinance").
    for k in _AKSHARE_DATA_VENDOR_KEYS:
        if k in data_vendors:
            default_val = DEFAULT_CONFIG["data_vendors"].get(k, "yfinance")
            cleaned = _strip_akshare_from_chain(data_vendors[k], default_val)
            if cleaned != data_vendors[k]:
                data_vendors[k] = cleaned

    for k in _AKSHARE_TOOL_VENDOR_KEYS:
        val = tool_vendors.get(k)
        if isinstance(val, str):
            cleaned = _strip_akshare_from_chain(val, "")
            if cleaned == "":
                tool_vendors.pop(k, None)
            elif cleaned != val:
                tool_vendors[k] = cleaned

    # Step 2: apply the current-ticker overlay
    if market == Market.A_SHARE:
        # All four data_vendor categories → akshare
        for key in _AKSHARE_DATA_VENDOR_KEYS:
            data_vendors[key] = "akshare"
        # tool_vendors: do NOT add any keys (akshare handles A-share via data_vendors)

    elif market == Market.HK:
        # data_vendors: unchanged (clearing in step 1 already reset stale A-share entries)
        # tool_vendors: set the two HK-supported per-method overrides
        for key in _AKSHARE_TOOL_VENDOR_KEYS:
            tool_vendors[key] = "akshare"

    # else: US, CRYPTO — clearing-only (step 1 already removed stale keys)

    # Write back to local config
    config["data_vendors"] = data_vendors
    config["tool_vendors"] = tool_vendors

    # Propagate REPLACE to global _config so set_config's merge semantics can't
    # leave stale akshare keys in _config after a subsequent set_config(config) call.
    replace_section("data_vendors", data_vendors)
    replace_section("tool_vendors", tool_vendors)
