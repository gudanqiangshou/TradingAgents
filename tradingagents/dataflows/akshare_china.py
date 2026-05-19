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


def apply_china_vendor_overlay(config: dict, ticker: str) -> None:
    """Route A-share tickers to the 'akshare' vendor for THIS run only.

    HK tickers are intentionally NOT routed here yet; they keep using the
    default yfinance vendor until HK data fetching is implemented in a
    later phase.

    The function REPLACES config["data_vendors"] with a fresh dict rather
    than mutating it in place.  The call sites pass a SHALLOW
    DEFAULT_CONFIG.copy(), so config["data_vendors"] is the SAME object as
    the module-global DEFAULT_CONFIG["data_vendors"]; mutating it in place
    would corrupt the global default across all subsequent runs.
    """
    if resolve_market(ticker) != Market.A_SHARE:
        return
    vendors = dict(config.get("data_vendors") or {})
    # Only core_stock_apis is implemented today; fundamental_data / news_data
    # will be added to this overlay in later phases.
    vendors["core_stock_apis"] = "akshare"
    config["data_vendors"] = vendors
