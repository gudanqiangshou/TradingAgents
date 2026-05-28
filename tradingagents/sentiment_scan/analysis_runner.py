"""Per-ticker TradingAgents analysis runner with watchdog & failure isolation.

Public:
    run_single_analysis(ticker, date, deadline) → dict
    run_batch(intersection, date, hard_deadline) → list[dict]

Never throws. Every exception path returns a dict with status field; the
caller (`scripts/daily_sentiment_scan.py`) appends each result to the
JSON snapshot's `analyses` array.
"""
from __future__ import annotations

import gc
import re
import time
from datetime import datetime, timedelta

from tradingagents.agents.utils.rating import SIGNAL_ACTION_MAP
from tradingagents.dataflows.akshare_china import apply_china_vendor_overlay
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.signal_processing import SignalProcessor
from tradingagents.graph.trading_graph import TradingAgentsGraph

_SUMMARY_RE = re.compile(r"\*\*Executive Summary\*\*[：:\s]*([^\n]+)")
_SINGLE_TICKER_BUDGET = timedelta(minutes=30)


def run_single_analysis(ticker: str, date: str, deadline: datetime) -> dict:
    """Run TradingAgents (fundamentals + news) on one ticker.

    Returns a dict with at least `status` and (on ok/partial/incomplete) `decision`.

    Watchdog granularity: deadline is checked BETWEEN langgraph chunks. A single
    LLM call that hangs longer than the deadline is NOT interrupted — rely on the
    LLM client's per-request timeout as the inner-layer guard. Spec accepts this
    limitation; the 8:50 hard_deadline in run_batch provides the outer-layer
    cap across the whole batch.
    """
    started_at = time.time()
    try:
        config = DEFAULT_CONFIG.copy()
        config["max_debate_rounds"] = 1
        config["max_risk_discuss_rounds"] = 1
        config["output_language"] = "zh-Hans"
        config["checkpoint_enabled"] = False
        apply_china_vendor_overlay(config, ticker)

        graph = TradingAgentsGraph(
            selected_analysts=["fundamentals", "news"],
            debug=False,
            config=config,
        )
        try:
            init_state = graph.propagator.create_initial_state(
                ticker, date, asset_type="stock"
            )
            args = graph.propagator.get_graph_args()

            final_state: dict | None = None
            for chunk in graph.graph.stream(init_state, **args):
                if datetime.now() >= deadline:
                    return _result(ticker, "timeout", started_at, error="exceeded per-ticker deadline")
                final_state = chunk if isinstance(chunk, dict) else final_state

            final_decision_md = (final_state or {}).get("final_trade_decision") or ""
            if not final_decision_md:
                return _result(ticker, "incomplete", started_at, error="no final_trade_decision produced")

            rating = SignalProcessor().process_signal(final_decision_md)
            action = SIGNAL_ACTION_MAP.get(rating, "HOLD")
            summary_1line = _extract_summary_1line(final_decision_md)
            return _result(
                ticker, "ok", started_at,
                decision={"rating": rating, "action": action, "summary_1line": summary_1line},
            )
        finally:
            # Release graph heap + LLM clients before next ticker.
            del graph
            gc.collect()
    except Exception as exc:  # noqa: BLE001 — never-throws contract
        return _result(ticker, "error", started_at, error=f"{type(exc).__name__}: {str(exc)[:200]}")


def _result(ticker: str, status: str, started_at: float, *, decision: dict | None = None, error: str | None = None) -> dict:
    return {
        "ticker": ticker,
        "status": status,
        "decision": decision,
        "error": error,
        "elapsed_seconds": round(time.time() - started_at, 2),
    }


def _extract_summary_1line(md: str) -> str:
    """First sentence of Executive Summary, or first non-empty rating line as fallback."""
    m = _SUMMARY_RE.search(md)
    if m:
        return m.group(1).strip()[:200]
    for line in md.splitlines():
        line = line.strip()
        if line and not line.lower().startswith("rating"):
            return line[:200]
    return ""


def run_batch(intersection: dict, date: str, hard_deadline: datetime) -> list[dict]:
    """Run analyses across all intersection tickers in tier priority order.

    Tickers not reached before hard_deadline get status=budget_exhausted.
    """
    ordered: list[str] = []
    for tier in ("triple", "ab_only", "ac_only", "bc_only"):
        for code in intersection.get(tier, []):
            ordered.append(code)

    results: list[dict] = []
    for ticker in ordered:
        if datetime.now() >= hard_deadline:
            results.append({
                "ticker": ticker,
                "status": "budget_exhausted",
                "decision": None,
                "error": "global deadline reached before this ticker",
                "elapsed_seconds": 0,
            })
            continue
        per_ticker_deadline = min(datetime.now() + _SINGLE_TICKER_BUDGET, hard_deadline)
        result = run_single_analysis(ticker, date, per_ticker_deadline)
        results.append(result)
    return results
