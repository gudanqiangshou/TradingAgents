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
