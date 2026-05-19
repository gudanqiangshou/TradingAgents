"""Self-contained AkShare A-share data vendor; akshare loaded on demand."""

from datetime import datetime

import pandas as pd

from tradingagents.dataflows import _dep_bootstrap
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
    datetime.strptime(start_date, "%Y-%m-%d")
    datetime.strptime(end_date, "%Y-%m-%d")
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
    # Rename Chinese columns → standard names, keep only OHLCV
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # Build output string  (same contract as yfinance vendor)
    # ------------------------------------------------------------------
    retrieved_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header = (
        f"# Stock data for {symbol.strip().split('.')[0].upper()} from {start_date} to {end_date}\n"
        f"# Total records: {len(df)}\n"
        f"# Data retrieved on: {retrieved_at}\n"
        "\n"
    )

    return header + df.to_csv()
