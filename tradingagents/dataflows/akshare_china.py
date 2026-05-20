"""Self-contained AkShare A-share data vendor; akshare loaded on demand."""

import logging
from datetime import datetime, timedelta

import pandas as pd

from tradingagents.dataflows import _dep_bootstrap
from tradingagents.dataflows.config import get_config as _config_get
from tradingagents.market_resolver import resolve_market, Market

logger = logging.getLogger(__name__)

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
    if df is not None and not df.empty:
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

    if df_sina is not None and not df_sina.empty:
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
    if df is None or df.empty:
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

    if df is None or df.empty:
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
            period_cols = [c for c in df.columns if re.match(r"^\d{8}$", str(c))]
            if not period_cols:
                raise ValueError("No 8-digit period columns found in A-share fundamentals DataFrame")

            # Latest period: string comparison works because YYYYMMDD sorts like calendar order.
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
            # HK: wide-form DataFrame; take latest period (iloc[0]) and emit each column
            header = (
                f"# Company Fundamentals for {ticker.strip().upper()}\n"
                f"# Data retrieved on: {retrieved_at}\n"
                "\n"
            )
            row = df.iloc[0]
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
    if curr_date is None or df is None or df.empty:
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

    if df is None or df.empty:
        return f"No {empty_msg_kind} data found for symbol '{ticker}'"

    try:
        df = _filter_by_curr_date(df, curr_date)
        if df is None or df.empty:
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
    # Empty result
    # ------------------------------------------------------------------
    if df is None or df.empty:
        return f"No news found for {ticker}"

    # ------------------------------------------------------------------
    # Shape the DataFrame — guard against unexpected akshare column schema.
    # akshare changed column names across versions; handle both variants.
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

            title     = str(row.get(title_col, "")) if title_col else ""
            publisher = str(row.get(source_col, "")) if source_col else ""

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

    # 3. Date parse curr_date — if invalid return error string
    try:
        curr_date_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    except ValueError:
        return (
            f"Error: invalid date format for {curr_date!r}; expected yyyy-mm-dd"
        )

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


def apply_china_vendor_overlay(config: dict, ticker: str) -> None:
    """Route A-share or HK tickers to the 'akshare' vendor for THIS run only.

    Design: A_SHARE vs HK use fundamentally different overlay strategies
    because AkShare covers different methods for each market.

    **A_SHARE** (category-wide overlay):
    Replaces ``config["data_vendors"]`` with a fresh dict and sets all four
    data categories:
    - ``core_stock_apis``       → ``"akshare"``  (OHLCV price data)
    - ``fundamental_data``      → ``"akshare"``  (balance_sheet / cashflow /
                                                   income_statement / fundamentals)
    - ``news_data``             → ``"akshare"``  (per-stock news via stock_news_em)
    - ``technical_indicators``  → ``"akshare"``  (indicators via stockstats + eastmoney/Sina)

    **HK** (per-method tool_vendors overlay):
    AkShare only covers HK OHLCV (``stock_hk_daily``) and HK key indicators
    (``stock_financial_hk_analysis_indicator_em``).  Statements and news are
    NOT available from AkShare for HK, so those fall back to yfinance.
    Sets per-method overrides in ``config["tool_vendors"]``:
    - ``get_stock_data``   → ``"akshare"``
    - ``get_fundamentals`` → ``"akshare"``
    Does NOT touch ``data_vendors`` (yfinance remains the category default for
    all other HK methods, including technical_indicators).

    **US / CRYPTO**: no-op; config unchanged.

    Aliasing safety:
    Both paths create a fresh dict (``dict(...)`` copy) rather than mutating the
    existing nested dict in place.  Call sites may pass a SHALLOW
    ``DEFAULT_CONFIG.copy()``, so ``config["data_vendors"]`` and
    ``config["tool_vendors"]`` could be the SAME objects as in the module-global
    ``DEFAULT_CONFIG``; mutating them in place would corrupt that global.
    """
    market = resolve_market(ticker)

    if market == Market.A_SHARE:
        vendors = dict(config.get("data_vendors") or {})
        # All four categories overlaid for A-share:
        vendors["core_stock_apis"] = "akshare"
        vendors["fundamental_data"] = "akshare"
        vendors["news_data"] = "akshare"
        vendors["technical_indicators"] = "akshare"
        config["data_vendors"] = vendors

    elif market == Market.HK:
        # Per-method overrides only for the two HK-supported endpoints.
        # Do NOT touch data_vendors (yfinance remains default for all categories).
        tool_overrides = dict(config.get("tool_vendors") or {})
        tool_overrides["get_stock_data"] = "akshare"
        tool_overrides["get_fundamentals"] = "akshare"
        config["tool_vendors"] = tool_overrides

    # else: US, CRYPTO — no-op
