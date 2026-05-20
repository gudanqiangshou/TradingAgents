"""Self-contained AkShare A-share data vendor; akshare loaded on demand."""

from datetime import datetime

import pandas as pd

from tradingagents.dataflows import _dep_bootstrap
from tradingagents.dataflows.config import get_config as _config_get
from tradingagents.market_resolver import resolve_market, Market


def get_stock_data(symbol: str, start_date: str, end_date: str) -> str:
    """Return OHLCV data for an A-share symbol as a formatted CSV string.

    Parameters
    ----------
    symbol:
        A-share ticker — bare 6-digit code or with exchange suffix
        (.SH, .SS, .SZ, .BJ), e.g. ``"600519"`` or ``"600519.SH"``.
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
    # Guard: this vendor only handles A-share symbols
    # ------------------------------------------------------------------
    if resolve_market(symbol) != Market.A_SHARE:
        return (
            f"This vendor handles A-share data only. "
            f"'{symbol}' was classified as non-A-share. "
            "Please use the appropriate vendor for this symbol."
        )

    # ------------------------------------------------------------------
    # Normalise symbol: strip whitespace, upper-case, drop exchange suffix
    # ------------------------------------------------------------------
    code = symbol.strip().upper().split(".")[0]

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
        return f"Error: A-share data source unavailable ({exc})"

    # ------------------------------------------------------------------
    # Fetch data — catch arbitrary scraping errors
    # ------------------------------------------------------------------
    try:
        df = ak.stock_zh_a_hist(
            symbol=code,
            period="daily",
            start_date=ak_start,
            end_date=ak_end,
            adjust="qfq",
        )
    except Exception as exc:
        return f"Error: failed to fetch A-share data for {symbol}: {exc}"

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
        col_map = {
            "日期": "Date",
            "开盘": "Open",
            "收盘": "Close",
            "最高": "High",
            "最低": "Low",
            "成交量": "Volume",
        }
        df = df.rename(columns=col_map)
        df = df[["Date", "Open", "High", "Low", "Close", "Volume"]].copy()

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
        return f"Error: unexpected A-share data schema for {symbol}: {exc}"

    # ------------------------------------------------------------------
    # Build output string  (same contract as yfinance vendor)
    # ------------------------------------------------------------------
    retrieved_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header = (
        f"# Stock data for {code} from {start_date} to {end_date}\n"
        f"# Total records: {len(df)}\n"
        f"# Data retrieved on: {retrieved_at}\n"
        "\n"
    )

    return header + df.to_csv()


def get_fundamentals(ticker: str, curr_date: str | None = None) -> str:
    """Return A-share company fundamentals as a formatted label/value string.

    Parameters
    ----------
    ticker:
        A-share ticker — bare 6-digit code or with exchange suffix.
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
        df = ak.stock_financial_abstract(symbol=code)
    except Exception as exc:
        return f"Error: failed to fetch A-share fundamentals for {ticker}: {exc}"

    if df is None or df.empty:
        return f"No fundamentals data found for symbol '{ticker}'"

    try:
        # The DataFrame is long-form: first column = item name, remaining = period values.
        # Emit label: latest-period-value pairs.
        item_col = df.columns[0]
        # Use the second column (most recent period) as the value column
        value_col = df.columns[1]
        lines = []
        for _, row in df.iterrows():
            value = row[value_col]
            if pd.isna(value) or (isinstance(value, str) and not value.strip()):
                continue
            lines.append(f"{row[item_col]}: {value}")

        retrieved_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        header = (
            f"# Company Fundamentals for {ticker.strip().upper()}\n"
            f"# Data retrieved on: {retrieved_at}\n"
            "\n"
        )
        return header + "\n".join(lines)
    except Exception as exc:
        return f"Error: unexpected A-share fundamentals response for {ticker}: {exc}"


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
                summary = str(row.get(summary_col, "") or "").strip()
            if not summary and content_col is not None:
                content = str(row.get(content_col, "") or "").strip()
                summary = content[:200] if content else ""

            link = str(row.get(link_col, "") or "").strip() if link_col else ""

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


def apply_china_vendor_overlay(config: dict, ticker: str) -> None:
    """Route A-share tickers to the 'akshare' vendor for THIS run only.

    Phase 4 overlay sets all three data categories for A_SHARE:
    - ``core_stock_apis``   → ``"akshare"``  (OHLCV price data)
    - ``fundamental_data``  → ``"akshare"``  (balance_sheet / cashflow /
                                               income_statement / fundamentals)
    - ``news_data``         → ``"akshare"``  (per-stock news via stock_news_em)

    HK tickers are intentionally NOT routed here; they keep using the default
    yfinance vendor until HK data fetching is implemented in a later phase.

    The function REPLACES config["data_vendors"] with a fresh dict rather
    than mutating it in place.  The call sites pass a SHALLOW
    DEFAULT_CONFIG.copy(), so config["data_vendors"] is the SAME object as
    the module-global DEFAULT_CONFIG["data_vendors"]; mutating it in place
    would corrupt the global default across all subsequent runs.
    """
    if resolve_market(ticker) != Market.A_SHARE:
        return
    vendors = dict(config.get("data_vendors") or {})
    # All three categories overlaid for A-share:
    vendors["core_stock_apis"] = "akshare"
    vendors["fundamental_data"] = "akshare"
    vendors["news_data"] = "akshare"
    config["data_vendors"] = vendors
