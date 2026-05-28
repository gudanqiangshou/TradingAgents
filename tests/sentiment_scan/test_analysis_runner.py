"""Tests for analysis_runner.run_single_analysis + run_batch."""
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch


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


def test_summary_extracted_from_pm_markdown_format():
    """PM 真实 markdown 输出 (with **bold** markers) → summary_1line 抽对."""
    from tradingagents.sentiment_scan.analysis_runner import run_single_analysis

    fake_final_state = {
        "final_trade_decision": (
            "**Rating**: Buy\n\n"
            "**Executive Summary**: 收入增长强劲且自由现金流转正，估值仍合理\n\n"
            "**Investment Thesis**: ..."
        ),
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
        result = run_single_analysis("AAPL", "2026-05-28", deadline)

    assert result["status"] == "ok"
    assert result["decision"]["rating"] == "Buy"
    assert result["decision"]["action"] == "BUY"
    assert result["decision"]["summary_1line"] == "收入增长强劲且自由现金流转正，估值仍合理"


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


def test_apply_china_vendor_overlay_is_called_with_ticker():
    from tradingagents.sentiment_scan.analysis_runner import run_single_analysis

    fake_graph = MagicMock()
    fake_graph.graph.stream.return_value = iter([{"final_trade_decision": "Rating: Hold"}])
    fake_graph.propagator.create_initial_state.return_value = {}
    fake_graph.propagator.get_graph_args.return_value = {}

    deadline = datetime.now() + timedelta(minutes=30)
    with patch(
        "tradingagents.sentiment_scan.analysis_runner.TradingAgentsGraph",
        return_value=fake_graph,
    ), patch(
        "tradingagents.sentiment_scan.analysis_runner.apply_china_vendor_overlay"
    ) as mock_overlay:
        run_single_analysis("600519", "2026-05-27", deadline)
    # Verify the China-vendor overlay was applied with the ticker (akshare routing).
    assert mock_overlay.called
    args, kwargs = mock_overlay.call_args
    assert args[1] == "600519"  # second positional arg is ticker


def test_gc_collect_called_in_finally():
    """gc.collect must run regardless of inner success/failure."""
    from tradingagents.sentiment_scan.analysis_runner import run_single_analysis

    fake_graph = MagicMock()
    fake_graph.graph.stream.return_value = iter([{"final_trade_decision": "Rating: Hold"}])
    fake_graph.propagator.create_initial_state.return_value = {}
    fake_graph.propagator.get_graph_args.return_value = {}

    deadline = datetime.now() + timedelta(minutes=30)
    with patch(
        "tradingagents.sentiment_scan.analysis_runner.TradingAgentsGraph",
        return_value=fake_graph,
    ), patch(
        "tradingagents.sentiment_scan.analysis_runner.apply_china_vendor_overlay"
    ), patch(
        "tradingagents.sentiment_scan.analysis_runner.gc.collect"
    ) as mock_gc:
        run_single_analysis("600519", "2026-05-27", deadline)
    assert mock_gc.called


def test_run_batch_processes_triple_first_then_double():
    """Tier order: triple → ab_only → ac_only → bc_only."""
    from tradingagents.sentiment_scan.analysis_runner import run_batch

    calls: list[str] = []

    def fake_runner(ticker, date, deadline, report_dir=None):
        calls.append(ticker)
        return {"ticker": ticker, "status": "ok", "decision": None, "error": None, "elapsed_seconds": 1.0, "report_paths": {}}

    intersection = {
        "triple": ["TRP1"],
        "ab_only": ["AB1", "AB2"],
        "ac_only": ["AC1"],
        "bc_only": ["BC1"],
    }
    hard_deadline = datetime.now() + timedelta(hours=2)
    with patch(
        "tradingagents.sentiment_scan.analysis_runner.run_single_analysis",
        side_effect=fake_runner,
    ):
        results = run_batch(intersection, "2026-05-27", hard_deadline)

    assert [r["ticker"] for r in results] == ["TRP1", "AB1", "AB2", "AC1", "BC1"]
    assert calls == ["TRP1", "AB1", "AB2", "AC1", "BC1"]


def test_run_batch_budget_exhausted_skips_remaining():
    """If hard_deadline already passed, remaining tickers get status=budget_exhausted."""
    from tradingagents.sentiment_scan.analysis_runner import run_batch

    intersection = {"triple": ["T1", "T2"], "ab_only": [], "ac_only": [], "bc_only": []}
    past = datetime.now() - timedelta(minutes=1)
    with patch(
        "tradingagents.sentiment_scan.analysis_runner.run_single_analysis",
    ) as mock_run:
        results = run_batch(intersection, "2026-05-27", past)

    # Neither ticker should have been actually analyzed.
    assert mock_run.call_count == 0
    assert len(results) == 2
    assert all(r["status"] == "budget_exhausted" for r in results)
    assert {r["ticker"] for r in results} == {"T1", "T2"}


# ---------------------------------------------------------------------------
# Phase 10: full markdown persistence
# ---------------------------------------------------------------------------

def test_run_single_analysis_writes_markdown_when_report_dir_set(tmp_path):
    """When report_dir is passed, all 5 non-empty markdown reports are
    written under <report_dir>/<ticker>/ and report_paths is populated."""
    from tradingagents.sentiment_scan.analysis_runner import run_single_analysis

    fake_final_state = {
        "fundamentals_report": "## FUND\n茅台估值合理",
        "news_report": "## NEWS\n白酒板块利好",
        "investment_plan": "## PLAN\n研究团队倾向超配",
        "trader_investment_plan": "## TRADER\n小幅建仓",
        "final_trade_decision": (
            "**Rating**: Overweight\n\n"
            "**Executive Summary**: 高端白酒龙头机构净买入背书\n"
        ),
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
        result = run_single_analysis(
            "600519", "2026-05-27", deadline, report_dir=str(tmp_path),
        )

    assert result["status"] == "ok"
    paths = result["report_paths"]
    assert set(paths.keys()) == {
        "fundamentals_report",
        "news_report",
        "investment_plan",
        "trader_investment_plan",
        "final_trade_decision",
    }
    # All paths are absolute and the files exist with expected content.
    import os as _os
    for key, p in paths.items():
        assert _os.path.isabs(p), f"{key} path must be absolute"
        with open(p, "r", encoding="utf-8") as fh:
            assert fh.read() == fake_final_state[key]
    # And they live under <tmp_path>/600519/.
    assert (tmp_path / "600519" / "fundamentals_report.md").exists()
    assert (tmp_path / "600519" / "news_report.md").exists()
    assert (tmp_path / "600519" / "investment_plan.md").exists()
    assert (tmp_path / "600519" / "trader_investment_plan.md").exists()
    assert (tmp_path / "600519" / "final_trade_decision.md").exists()


def test_run_single_analysis_no_report_dir_skips_disk_write(tmp_path):
    """report_dir=None must NOT touch disk. report_paths={} in result."""
    from tradingagents.sentiment_scan.analysis_runner import run_single_analysis

    fake_final_state = {
        "fundamentals_report": "FOO",
        "news_report": "BAR",
        "final_trade_decision": "**Rating**: Hold\n**Executive Summary**: ok\n",
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
        # also try explicit None
        result2 = run_single_analysis("600519", "2026-05-27", deadline, report_dir=None)

    assert result["status"] == "ok"
    assert result["report_paths"] == {}
    assert result2["report_paths"] == {}
    # tmp_path remains empty
    assert list(tmp_path.iterdir()) == []


def test_run_single_analysis_disk_write_failure_logged_not_fatal(tmp_path, caplog):
    """Disk error during report write must NOT crash: status=ok, paths={}."""
    import logging

    from tradingagents.sentiment_scan.analysis_runner import run_single_analysis

    fake_final_state = {
        "fundamentals_report": "FOO",
        "final_trade_decision": "**Rating**: Hold\n**Executive Summary**: ok\n",
    }
    fake_graph = MagicMock()
    fake_graph.graph.stream.return_value = iter([fake_final_state])
    fake_graph.propagator.create_initial_state.return_value = {}
    fake_graph.propagator.get_graph_args.return_value = {}

    deadline = datetime.now() + timedelta(minutes=30)
    # Simulate disk full: builtin open() raises OSError on the tmp file.
    real_open = open

    def flaky_open(path, *args, **kwargs):
        if str(path).endswith(".tmp"):
            raise OSError("simulated disk full")
        return real_open(path, *args, **kwargs)

    with patch(
        "tradingagents.sentiment_scan.analysis_runner.TradingAgentsGraph",
        return_value=fake_graph,
    ), patch(
        "tradingagents.sentiment_scan.analysis_runner.apply_china_vendor_overlay"
    ), patch("builtins.open", side_effect=flaky_open):
        caplog.set_level(logging.WARNING)
        result = run_single_analysis(
            "600519", "2026-05-27", deadline, report_dir=str(tmp_path),
        )

    # Analysis still succeeded — just no report paths.
    assert result["status"] == "ok"
    assert result["report_paths"] == {}
    # At least one warning was logged.
    assert any("failed to persist report" in r.message or "could not create" in r.message
               for r in caplog.records)
