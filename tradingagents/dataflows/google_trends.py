"""Google Trends interest-over-time fetcher for retail attention signal.

Uses pytrends library; works for any ticker text query. Best for US tickers
where the alpha character is unambiguous (AAPL, TSLA, etc.). For ambiguous
short tickers (single-letter), the trend signal may be noisy.

Fail-safe: never raises; returns a formatted plaintext string for prompt
injection.
"""
from __future__ import annotations
import logging
from datetime import datetime
import pandas as pd

logger = logging.getLogger(__name__)


def get_google_trends(ticker: str, lookback_days: int = 30, geo: str = "US") -> str:
    """Fetch Google search interest over time for a ticker.

    Args:
        ticker: search term (e.g., "AAPL"). Used as-is, no normalization.
        lookback_days: window. Maps to pytrends timeframe — 7→"now 7-d",
                       30→"today 1-m", 90→"today 3-m", 365→"today 12-m";
                       default 30.
        geo: country code for pytrends, default "US".
    """
    # Validate inputs
    if not isinstance(ticker, str) or not ticker.strip():
        return f"Error: invalid ticker for Google Trends: {ticker!r}"
    if not isinstance(lookback_days, int) or isinstance(lookback_days, bool) or lookback_days <= 0:
        return f"Error: lookback_days must be positive int, got {lookback_days!r}"

    # Map lookback_days to pytrends timeframe string
    if lookback_days <= 7:
        timeframe = "now 7-d"
    elif lookback_days <= 30:
        timeframe = "today 1-m"
    elif lookback_days <= 90:
        timeframe = "today 3-m"
    elif lookback_days <= 365:
        timeframe = "today 12-m"
    else:
        timeframe = "today 5-y"

    try:
        from pytrends.request import TrendReq
        pt = TrendReq(hl="en-US", tz=360, timeout=(10, 25))
        pt.build_payload(kw_list=[ticker.upper().strip()], timeframe=timeframe, geo=geo)
        df = pt.interest_over_time()
    except ImportError as e:
        return f"Error: pytrends library not available: {e}"
    except Exception as e:
        logger.warning("Google Trends fetch failed for %s: %s", ticker, e)
        return f"Error: Google Trends unavailable for {ticker}: {type(e).__name__}: {str(e)[:120]}"

    if not isinstance(df, pd.DataFrame) or df.empty:
        return f"No Google Trends data found for {ticker} (geo={geo}, lookback={lookback_days}d)"

    try:
        # Drop isPartial column for analysis, keep last few rows for context
        col = ticker.upper().strip()
        if col not in df.columns:
            return f"No Google Trends data column for {ticker}"
        series = df[col].astype(float)
        latest = series.iloc[-1]
        is_partial = bool(df["isPartial"].iloc[-1]) if "isPartial" in df.columns else False
        avg = float(series.mean())
        peak = float(series.max())
        peak_date = str(series.idxmax().date())
        recent_avg = float(series.tail(7).mean())
        prior_avg = float(series.head(max(1, len(series) - 7)).mean())
        trend = "rising" if recent_avg > prior_avg * 1.1 else ("falling" if recent_avg < prior_avg * 0.9 else "stable")

        last_n_lines = []
        for ts, val in series.tail(10).items():
            last_n_lines.append(f"  {ts.date()}: {int(val)}")

        out = f"""# Google Trends interest-over-time for {ticker.upper()} (geo={geo}, lookback={lookback_days}d)
# Retrieved at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
# (Google Trends normalizes interest to 0-100 over the requested window; 100 = peak)

## Summary
- Latest value: {int(latest)}{' (partial — current period not complete)' if is_partial else ''}
- Window average: {avg:.1f}
- Window peak: {int(peak)} on {peak_date}
- Recent 7-period avg vs prior avg: {recent_avg:.1f} vs {prior_avg:.1f} → trend = {trend}

## Last 10 data points
{chr(10).join(last_n_lines)}

Interpretation: Google search interest serves as a leading retail-attention indicator (academic studies show predictive power for short-term moves). Sustained rising trend may precede price moves in either direction; sharp peaks often coincide with news events. Compare with news flow to disambiguate positive vs negative narrative.
"""
        return out
    except Exception as e:
        logger.warning("Google Trends format failed for %s: %s", ticker, e)
        return f"Error: unexpected Google Trends response for {ticker}: {type(e).__name__}: {str(e)[:120]}"
