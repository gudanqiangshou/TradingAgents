"""StockTwits public symbol-stream fetcher.

StockTwits exposes a per-symbol message stream at
``api.stocktwits.com/api/2/streams/symbol/{ticker}.json`` that requires no
API key, no OAuth, and no registration. Each message includes a
user-labeled sentiment field (``Bullish``/``Bearish``/null), the message
body, timestamp, and posting user.

The function is deliberately self-contained: short timeout, graceful
degradation on any HTTP or parse failure, and a string return type so
the calling agent gets a uniform interface regardless of whether the
network call succeeded.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

_API = "https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json"
_TRENDING_API = "https://api.stocktwits.com/api/2/trending/symbols/equities.json"
_UA = "tradingagents/0.2 (+https://github.com/TauricResearch/TradingAgents)"


def fetch_stocktwits_messages(ticker: str, limit: int = 30, timeout: float = 10.0) -> str:
    """Fetch recent StockTwits messages for ``ticker`` and return them as a
    formatted plaintext block ready for prompt injection.

    Returns a placeholder string when the endpoint is unreachable, the
    symbol has no messages, or the response shape is unexpected — the
    caller never has to special-case None or exceptions.

    HTTP 404 is handled distinctly: StockTwits only covers US equities, so
    A-share and HK symbols routinely return 404.  The returned placeholder
    makes this clear so the LLM agent does not treat it as an outage.
    """
    url = _API.format(ticker=ticker.upper())
    req = Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
    except HTTPError as exc:
        if exc.code == 404:
            return (
                f"<stocktwits has no data for {ticker.upper()}: symbol not in their "
                "US-equity database (StockTwits covers US equities only; "
                "A-share / HK symbols routinely 404)>"
            )
        logger.warning("StockTwits HTTP %s for %s", exc.code, ticker)
        return f"<stocktwits unavailable: HTTP {exc.code}>"
    except (URLError, json.JSONDecodeError, TimeoutError) as exc:
        logger.warning("StockTwits fetch failed for %s: %s", ticker, exc)
        return f"<stocktwits unavailable: {type(exc).__name__}>"

    messages = data.get("messages", []) if isinstance(data, dict) else []
    if not messages:
        return f"<no StockTwits messages found for ${ticker.upper()}>"

    lines = []
    bullish = bearish = unlabeled = 0
    for m in messages[:limit]:
        created = m.get("created_at", "")
        user = (m.get("user") or {}).get("username", "?")
        entities = m.get("entities") or {}
        sentiment_obj = entities.get("sentiment") or {}
        sentiment = sentiment_obj.get("basic") if isinstance(sentiment_obj, dict) else None
        body = (m.get("body") or "").replace("\n", " ").strip()
        if len(body) > 280:
            body = body[:280] + "…"

        if sentiment == "Bullish":
            bullish += 1
            tag = "Bullish"
        elif sentiment == "Bearish":
            bearish += 1
            tag = "Bearish"
        else:
            unlabeled += 1
            tag = "no-label"
        lines.append(f"[{created} · @{user} · {tag}] {body}")

    total = bullish + bearish + unlabeled
    bull_pct = round(100 * bullish / total) if total else 0
    bear_pct = round(100 * bearish / total) if total else 0
    summary = (
        f"Bullish: {bullish} ({bull_pct}%) · "
        f"Bearish: {bearish} ({bear_pct}%) · "
        f"Unlabeled: {unlabeled} · "
        f"Total: {total} most-recent messages"
    )
    return summary + "\n\n" + "\n".join(lines)


def fetch_stocktwits_trending(limit: int = 30, timeout: float = 10.0) -> str:
    """Fetch the current StockTwits trending US equity tickers.

    Public endpoint, no API key. The /equities.json variant filters to
    stocks only (excludes crypto cashtags). Returns a formatted plaintext
    table ready for prompt or human display. Fail-safe: never raises;
    returns a placeholder string on any error.
    """
    # Validate limit: reject bool, NaN, inf, non-int, <=0
    if isinstance(limit, bool):
        return f"Error: limit must be int, got bool: {limit!r}"
    if isinstance(limit, float) and (math.isnan(limit) or math.isinf(limit)):
        return f"Error: limit must be a finite number, got {limit!r}"
    try:
        limit = int(limit)
    except (TypeError, ValueError, OverflowError):
        return f"Error: limit must be coercible to int, got {type(limit).__name__}: {limit!r}"
    if limit <= 0:
        return f"Error: limit must be positive, got {limit}"

    req = Request(_TRENDING_API, headers={"User-Agent": _UA, "Accept": "application/json"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
    except HTTPError as exc:
        if exc.code == 404:
            return "<stocktwits trending: 404 from endpoint>"
        if exc.code == 403:
            return "<stocktwits trending blocked: HTTP 403 — anti-bot challenge>"
        if exc.code >= 500:
            return f"<stocktwits trending unavailable: HTTP {exc.code}>"
        logger.warning("StockTwits trending HTTP %s", exc.code)
        return f"<stocktwits trending unavailable: HTTP {exc.code}>"
    except (URLError, TimeoutError) as exc:
        return f"<stocktwits trending unavailable: {type(exc).__name__}>"
    except json.JSONDecodeError as exc:
        return f"<stocktwits trending unavailable: {type(exc).__name__}>"
    except Exception as exc:
        return f"<stocktwits trending unavailable: {type(exc).__name__}>"

    symbols = data.get("symbols") if isinstance(data, dict) else None
    if not isinstance(symbols, list) or len(symbols) == 0:
        return "No StockTwits trending symbols available"

    now_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    rows = []
    for entry in symbols:
        if not isinstance(entry, dict):
            continue
        symbol = entry.get("symbol")
        if not symbol:
            continue
        exchange = entry.get("exchange", "")
        title = entry.get("title", "")
        rows.append((symbol, exchange, title))
        if len(rows) >= limit:
            break

    if not rows:
        return "No StockTwits trending symbols available"

    n = len(rows)
    lines = [
        f"# StockTwits Trending Equities (top {n}, retrieved {now_str} UTC)",
        "",
        "| # | Symbol | Exchange | Title |",
        "| -- | -- | -- | -- |",
    ]
    for i, (symbol, exchange, title) in enumerate(rows, 1):
        lines.append(f"| {i} | {symbol} | {exchange} | {title} |")

    lines += [
        "",
        "Interpretation: These US equity tickers have the highest message/cashtag",
        "velocity on StockTwits right now. Pair with bullish/bearish ratio from",
        "fetch_stocktwits_messages(ticker) to determine narrative direction;",
        "combine with Google Trends to confirm whether attention is sustained or",
        "spiking. Cross-source confirmation with Reddit r/wallstreetbets or news",
        "flow helps separate organic interest from pump-and-dump.",
    ]
    return "\n".join(lines)
