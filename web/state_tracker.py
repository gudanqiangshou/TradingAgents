"""
Agent state tracker for the web backend.
Ported from cli/main.py (MessageBuffer + update_analyst_statuses + chunk handlers),
with all Rich/typer/display dependencies removed.
Produces structured event dicts suitable for SSE emission.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

ANALYST_ORDER = ["market", "social", "news", "fundamentals"]

ANALYST_AGENT_NAMES = {
    "market": "Market Analyst",
    "social": "Sentiment Analyst",
    "news": "News Analyst",
    "fundamentals": "Fundamentals Analyst",
}

ANALYST_REPORT_MAP = {
    "market": "market_report",
    "social": "sentiment_report",
    "news": "news_report",
    "fundamentals": "fundamentals_report",
}

FIXED_AGENTS = [
    "Bull Researcher", "Bear Researcher", "Research Manager",
    "Trader",
    "Aggressive Analyst", "Neutral Analyst", "Conservative Analyst",
    "Portfolio Manager",
]

SIGNAL_ACTION_MAP = {
    "Buy": "BUY",
    "Overweight": "BUY",
    "Hold": "HOLD",
    "Underweight": "SELL",
    "Sell": "SELL",
}


@dataclass
class AgentTracker:
    selected_analysts: list[str]
    agent_status: dict[str, str] = field(default_factory=dict)
    report_sections: dict[str, str | None] = field(default_factory=dict)

    def __post_init__(self):
        for key in self.selected_analysts:
            name = ANALYST_AGENT_NAMES.get(key)
            if name:
                self.agent_status[name] = "pending"
        for agent in FIXED_AGENTS:
            self.agent_status[agent] = "pending"
        for key in ANALYST_ORDER:
            if key in self.selected_analysts:
                self.report_sections[ANALYST_REPORT_MAP[key]] = None
        for section in ["investment_plan", "trader_investment_plan", "final_trade_decision"]:
            self.report_sections[section] = None


def _emit_status(tracker: AgentTracker, agent: str, status: str,
                 prev_status: dict, events: list) -> None:
    if tracker.agent_status.get(agent) != status:
        tracker.agent_status[agent] = status
        if prev_status.get(agent) != status:
            events.append({"type": "agent_status", "data": {"agent": agent, "status": status}})


def _emit_section(tracker: AgentTracker, section: str, content: str,
                  prev_sections: dict, events: list) -> None:
    if content and content != prev_sections.get(section):
        tracker.report_sections[section] = content
        events.append({"type": "report_section", "data": {"section": section, "content": content}})


def process_chunk(tracker: AgentTracker, chunk: dict[str, Any]) -> list[dict]:
    """Process one LangGraph stream chunk. Returns list of SSE event dicts."""
    events: list[dict] = []
    prev_status = dict(tracker.agent_status)
    prev_sections = dict(tracker.report_sections)

    # --- Analyst team ---
    found_active = False
    for key in ANALYST_ORDER:
        if key not in tracker.selected_analysts:
            continue
        agent_name = ANALYST_AGENT_NAMES[key]
        report_key = ANALYST_REPORT_MAP[key]
        if chunk.get(report_key):
            _emit_section(tracker, report_key, chunk[report_key], prev_sections, events)
        has_report = bool(tracker.report_sections.get(report_key))
        if has_report:
            _emit_status(tracker, agent_name, "completed", prev_status, events)
        elif not found_active:
            _emit_status(tracker, agent_name, "in_progress", prev_status, events)
            found_active = True

    if not found_active and tracker.selected_analysts:
        if tracker.agent_status.get("Bull Researcher") == "pending":
            _emit_status(tracker, "Bull Researcher", "in_progress", prev_status, events)

    # --- Research team ---
    if chunk.get("investment_debate_state"):
        debate = chunk["investment_debate_state"]
        bull = (debate.get("bull_history") or "").strip()
        bear = (debate.get("bear_history") or "").strip()
        judge = (debate.get("judge_decision") or "").strip()

        if bull or bear:
            for agent in ["Bull Researcher", "Bear Researcher", "Research Manager"]:
                if tracker.agent_status.get(agent) == "pending":
                    _emit_status(tracker, agent, "in_progress", prev_status, events)
        # Emit the section as ONE combined document (not per-speaker
        # overwrites): the live UI replaces a section card on each event,
        # so per-speaker emits would drop Bull/Bear and only show the last
        # speaker. Combining keeps the live view as complete as the report.
        parts = []
        if bull:
            parts.append(f"### Bull Researcher Analysis\n{bull}")
        if bear:
            parts.append(f"### Bear Researcher Analysis\n{bear}")
        if judge:
            parts.append(f"### Research Manager Decision\n{judge}")
        if parts:
            _emit_section(tracker, "investment_plan",
                          "\n\n".join(parts), prev_sections, events)
        if judge:
            for agent in ["Bull Researcher", "Bear Researcher", "Research Manager"]:
                _emit_status(tracker, agent, "completed", prev_status, events)
            _emit_status(tracker, "Trader", "in_progress", prev_status, events)

    # --- Trading team ---
    if chunk.get("trader_investment_plan"):
        _emit_section(tracker, "trader_investment_plan",
                      chunk["trader_investment_plan"], prev_sections, events)
        _emit_status(tracker, "Trader", "completed", prev_status, events)
        _emit_status(tracker, "Aggressive Analyst", "in_progress", prev_status, events)

    # --- Risk & Portfolio ---
    if chunk.get("risk_debate_state"):
        risk = chunk["risk_debate_state"]
        agg = (risk.get("aggressive_history") or "").strip()
        con = (risk.get("conservative_history") or "").strip()
        neu = (risk.get("neutral_history") or "").strip()
        judge = (risk.get("judge_decision") or "").strip()

        if agg:
            _emit_status(tracker, "Aggressive Analyst", "in_progress", prev_status, events)
        if con:
            _emit_status(tracker, "Conservative Analyst", "in_progress", prev_status, events)
        if neu:
            _emit_status(tracker, "Neutral Analyst", "in_progress", prev_status, events)
        # Combined document (same reasoning as investment_plan): one section
        # holding the full risk debate + PM decision, not per-speaker
        # overwrites that the live UI would collapse to just the last one.
        parts = []
        if agg:
            parts.append(f"### Aggressive Analyst Analysis\n{agg}")
        if con:
            parts.append(f"### Conservative Analyst Analysis\n{con}")
        if neu:
            parts.append(f"### Neutral Analyst Analysis\n{neu}")
        if judge:
            parts.append(f"### Portfolio Manager Decision\n{judge}")
        if parts:
            _emit_section(tracker, "final_trade_decision",
                          "\n\n".join(parts), prev_sections, events)
        if judge:
            for agent in ["Aggressive Analyst", "Conservative Analyst",
                          "Neutral Analyst", "Portfolio Manager"]:
                _emit_status(tracker, agent, "completed", prev_status, events)

    return events
