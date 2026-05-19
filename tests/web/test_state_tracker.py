import pytest
from web.state_tracker import AgentTracker, process_chunk, SIGNAL_ACTION_MAP


def make_tracker(analysts=None):
    return AgentTracker(analysts or ["market", "social", "news", "fundamentals"])


def test_initial_all_pending():
    tracker = make_tracker()
    assert all(s == "pending" for s in tracker.agent_status.values())


def test_analyst_completed_when_report_present():
    tracker = make_tracker(["market"])
    events = process_chunk(tracker, {"market_report": "## Market Analysis\nContent here"})
    status_events = [e for e in events if e["type"] == "agent_status" and e["data"]["agent"] == "Market Analyst"]
    completed = [e for e in status_events if e["data"]["status"] == "completed"]
    assert len(completed) >= 1


def test_report_section_event_emitted():
    tracker = make_tracker(["market"])
    events = process_chunk(tracker, {"market_report": "## Market Analysis\nContent here"})
    section_events = [e for e in events if e["type"] == "report_section"]
    assert any(e["data"]["section"] == "market_report" for e in section_events)


def test_no_duplicate_events_same_content():
    tracker = make_tracker(["market"])
    process_chunk(tracker, {"market_report": "Content"})
    events2 = process_chunk(tracker, {"market_report": "Content"})  # same content
    section_events = [e for e in events2 if e["type"] == "report_section" and e["data"]["section"] == "market_report"]
    assert len(section_events) == 0  # no duplicate


def test_research_team_in_progress_after_analysts_complete():
    tracker = make_tracker(["market"])
    process_chunk(tracker, {"market_report": "Content"})
    events = process_chunk(tracker, {"investment_debate_state": {
        "bull_history": "Bull analysis here",
        "bear_history": "",
        "judge_decision": "",
    }})
    status_map = {e["data"]["agent"]: e["data"]["status"] for e in events if e["type"] == "agent_status"}
    assert status_map.get("Bull Researcher") in ("in_progress", None) or \
           tracker.agent_status.get("Bull Researcher") == "in_progress"


def test_final_decision_events_on_portfolio_judge():
    tracker = make_tracker(["market"])
    events = process_chunk(tracker, {
        "risk_debate_state": {
            "aggressive_history": "Aggressive view",
            "conservative_history": "Conservative view",
            "neutral_history": "Neutral view",
            "judge_decision": "**Rating**: Buy\nFinal recommendation...",
        }
    })
    pm_events = [e for e in events if e["type"] == "agent_status"
                 and e["data"]["agent"] == "Portfolio Manager"
                 and e["data"]["status"] == "completed"]
    assert len(pm_events) >= 1


def test_signal_action_map_covers_all_five():
    assert SIGNAL_ACTION_MAP["Buy"] == "BUY"
    assert SIGNAL_ACTION_MAP["Overweight"] == "BUY"
    assert SIGNAL_ACTION_MAP["Hold"] == "HOLD"
    assert SIGNAL_ACTION_MAP["Underweight"] == "SELL"
    assert SIGNAL_ACTION_MAP["Sell"] == "SELL"


def test_trader_branch_marks_trader_completed_and_aggressive_in_progress():
    tracker = make_tracker(["market"])
    events = process_chunk(tracker, {"trader_investment_plan": "Trade plan: buy 20%"})
    status_map = {}
    for e in events:
        if e["type"] == "agent_status":
            status_map[e["data"]["agent"]] = e["data"]["status"]
    section_events = [e for e in events if e["type"] == "report_section"
                      and e["data"]["section"] == "trader_investment_plan"]
    assert len(section_events) == 1
    assert status_map.get("Trader") == "completed"
    assert status_map.get("Aggressive Analyst") == "in_progress"


def test_investment_plan_emitted_as_combined_document():
    # Bull + Bear + RM must arrive in ONE section value (the live UI replaces
    # a section card per event; per-speaker emits would drop Bull/Bear).
    tracker = make_tracker(["market"])
    events = process_chunk(tracker, {"investment_debate_state": {
        "bull_history": "BULLCASE", "bear_history": "BEARCASE",
        "judge_decision": "RMVERDICT",
    }})
    secs = [e for e in events if e["type"] == "report_section"
            and e["data"]["section"] == "investment_plan"]
    assert len(secs) == 1
    c = secs[-1]["data"]["content"]
    assert "BULLCASE" in c and "BEARCASE" in c and "RMVERDICT" in c
    assert "### Bull Researcher Analysis" in c
    assert "### Research Manager Decision" in c
    # tracker holds the full combined doc, not just the last speaker
    assert tracker.report_sections["investment_plan"] == c


def test_final_trade_decision_combines_all_risk_voices():
    tracker = make_tracker(["market"])
    events = process_chunk(tracker, {"risk_debate_state": {
        "aggressive_history": "AGG", "conservative_history": "CON",
        "neutral_history": "NEU", "judge_decision": "**Rating**: Buy PMV",
    }})
    secs = [e for e in events if e["type"] == "report_section"
            and e["data"]["section"] == "final_trade_decision"]
    assert len(secs) == 1
    c = secs[-1]["data"]["content"]
    for piece in ("AGG", "CON", "NEU", "PMV"):
        assert piece in c, piece
    assert tracker.report_sections["final_trade_decision"] == c
