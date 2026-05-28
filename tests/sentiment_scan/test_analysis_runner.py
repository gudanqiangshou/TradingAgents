"""Tests for analysis_runner.run_single_analysis + run_batch."""
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest


def test_happy_path_returns_ok_with_rating_and_action():
    """Mock graph stream returns a final_state with non-empty final_trade_decision.
    Result: status=ok, rating=5-tier, action=3-tier from SIGNAL_ACTION_MAP."""
    from tradingagents.sentiment_scan.analysis_runner import run_single_analysis

    fake_final_state = {
        "final_trade_decision": "Rating: Overweight\n\nExecutive Summary: ...",
    }
    fake_graph = MagicMock()
    fake_graph.graph.stream.return_value = iter([fake_final_state])
    fake_graph.propagator.create_initial_state.return_value = {}
    fake_graph.propagator.get_graph_args.return_value = {}

    deadline = datetime.now() + timedelta(minutes=30)
    with patch(
        "tradingagents.sentiment_scan.analysis_runner.TradingAgentsGraph",
        return_value=fake_graph,
    ), patch(
        "tradingagents.sentiment_scan.analysis_runner.apply_china_vendor_overlay"
    ):
        result = run_single_analysis("600519", "2026-05-27", deadline)

    assert result["status"] == "ok"
    assert result["decision"]["rating"] == "Overweight"
    assert result["decision"]["action"] == "BUY"  # via SIGNAL_ACTION_MAP[Overweight]
    assert result["elapsed_seconds"] >= 0


def test_deadline_exceeded_during_stream_returns_timeout():
    """If now >= deadline during stream iteration, return status=timeout."""
    from tradingagents.sentiment_scan.analysis_runner import run_single_analysis

    fake_graph = MagicMock()
    # Stream yields many chunks; deadline check should fire on the second one.
    fake_graph.graph.stream.return_value = iter([{"k": 1}, {"k": 2}, {"k": 3}])
    fake_graph.propagator.create_initial_state.return_value = {}
    fake_graph.propagator.get_graph_args.return_value = {}

    past = datetime.now() - timedelta(seconds=1)  # already expired
    with patch(
        "tradingagents.sentiment_scan.analysis_runner.TradingAgentsGraph",
        return_value=fake_graph,
    ), patch(
        "tradingagents.sentiment_scan.analysis_runner.apply_china_vendor_overlay"
    ):
        result = run_single_analysis("600519", "2026-05-27", past)
    assert result["status"] == "timeout"


def test_empty_final_trade_decision_returns_incomplete():
    from tradingagents.sentiment_scan.analysis_runner import run_single_analysis

    fake_graph = MagicMock()
    fake_graph.graph.stream.return_value = iter([{"final_trade_decision": ""}])
    fake_graph.propagator.create_initial_state.return_value = {}
    fake_graph.propagator.get_graph_args.return_value = {}

    deadline = datetime.now() + timedelta(minutes=30)
    with patch(
        "tradingagents.sentiment_scan.analysis_runner.TradingAgentsGraph",
        return_value=fake_graph,
    ), patch(
        "tradingagents.sentiment_scan.analysis_runner.apply_china_vendor_overlay"
    ):
        result = run_single_analysis("600519", "2026-05-27", deadline)
    assert result["status"] == "incomplete"


def test_graph_construction_exception_returns_error():
    from tradingagents.sentiment_scan.analysis_runner import run_single_analysis

    deadline = datetime.now() + timedelta(minutes=30)
    with patch(
        "tradingagents.sentiment_scan.analysis_runner.TradingAgentsGraph",
        side_effect=RuntimeError("LLM client init failed"),
    ), patch(
        "tradingagents.sentiment_scan.analysis_runner.apply_china_vendor_overlay"
    ):
        result = run_single_analysis("600519", "2026-05-27", deadline)
    assert result["status"] == "error"
    assert "LLM client init failed" in result["error"]
    assert len(result["error"]) <= 250  # truncated


def test_apply_china_vendor_overlay_exception_returns_error():
    """The outer try MUST also wrap apply_china_vendor_overlay."""
    from tradingagents.sentiment_scan.analysis_runner import run_single_analysis

    deadline = datetime.now() + timedelta(minutes=30)
    with patch(
        "tradingagents.sentiment_scan.analysis_runner.apply_china_vendor_overlay",
        side_effect=ValueError("bad ticker shape"),
    ):
        result = run_single_analysis("600519", "2026-05-27", deadline)
    assert result["status"] == "error"
