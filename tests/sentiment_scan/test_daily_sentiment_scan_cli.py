"""Tests for daily_sentiment_scan.py CLI subcommands + new helpers."""
import json
from unittest.mock import MagicMock, patch

import pytest


def test_compute_intersection_returns_4_tier_dict():
    from scripts.daily_sentiment_scan import compute_intersection, SectionResult

    sec_a = SectionResult(display="", top20_codes=["600519", "300866", "002230"],
                         rank_by_code={"600519": 3, "300866": 5, "002230": 7},
                         summary_by_code={})
    sec_b = SectionResult(display="", top20_codes=["600519", "002230"],
                         rank_by_code={"600519": 1, "002230": 3},
                         summary_by_code={})
    sec_c = SectionResult(display="", top20_codes=["600519", "300866"],
                         rank_by_code={"600519": 8, "300866": 2},
                         summary_by_code={})

    result = compute_intersection(sec_a, sec_b, sec_c)
    assert result["triple"] == ["600519"]      # in all three
    assert result["ab_only"] == ["002230"]     # in a,b but not c
    assert result["ac_only"] == ["300866"]     # in a,c but not b
    assert result["bc_only"] == []             # none in only b,c


def test_analyze_subcommand_writes_json(tmp_path, monkeypatch):
    """--analyze runs scan + analysis + atomic write JSON, does not push."""
    from scripts.daily_sentiment_scan import _cmd_analyze

    output = tmp_path / "snapshot.json"

    fake_sec_a = MagicMock(display="A display", top20_codes=["600519"],
                          rank_by_code={"600519": 3}, summary_by_code={"600519": "茅台"})
    fake_sec_b = MagicMock(display="B display", top20_codes=["600519"],
                          rank_by_code={"600519": 1}, summary_by_code={})
    fake_sec_c = MagicMock(display="C display", top20_codes=["600519"],
                          rank_by_code={"600519": 8}, summary_by_code={})
    fake_sec_d = MagicMock(display="D display", top20_codes=[],
                          rank_by_code={}, summary_by_code={})

    fake_fund = {
        "pe_ttm": 25.3, "pe_forward": 22.1, "fcf": 5.6e10, "roe": 0.31,
        "market_cap": 3.2e12, "currency": "CNY", "as_of": "2026-05-27",
        "source": "akshare", "missing_fields": [], "status": "ok",
    }
    fake_batch_result = [{
        "ticker": "600519", "status": "ok",
        "decision": {"rating": "Overweight", "action": "BUY", "summary_1line": "..."},
        "error": None, "elapsed_seconds": 612,
    }]

    with patch("scripts.daily_sentiment_scan.section_a_hot_up_rank", return_value=fake_sec_a), \
         patch("scripts.daily_sentiment_scan.section_b_lhb", return_value=fake_sec_b), \
         patch("scripts.daily_sentiment_scan.section_c_xueqiu_surge", return_value=fake_sec_c), \
         patch("scripts.daily_sentiment_scan.section_d_stocktwits", return_value=fake_sec_d), \
         patch("scripts.daily_sentiment_scan.fetch_structured_fundamentals", return_value=fake_fund), \
         patch("scripts.daily_sentiment_scan.run_batch", return_value=fake_batch_result):
        _cmd_analyze(date="2026-05-27", output_path=str(output))

    assert output.exists()
    data = json.loads(output.read_text())
    assert data["schema_version"] == 1
    assert data["date"] == "2026-05-27"
    assert data["sections"]["intersection"]["triple"] == ["600519"]
    assert len(data["analyses"]) == 1
    assert data["analyses"][0]["code"] == "600519"
    assert data["analyses"][0]["fundamentals"]["pe_ttm"] == 25.3
    assert data["analyses"][0]["status"] == "ok"


def test_push_subcommand_reads_json_and_calls_webhook(tmp_path, monkeypatch):
    from scripts.daily_sentiment_scan import _cmd_push

    snap = {
        "schema_version": 1,
        "date": "2026-05-27",
        "scan_completed_at": "06:31",
        "analysis_completed_at": "08:42",
        "sections": {
            "section_a": {"display": "🚀 A股飙升榜\n🔥 SH600519 茅台", "top20_codes": [], "rank_by_code": {}, "summary_by_code": {}},
            "section_b": {"display": "🐂 龙虎榜\n", "top20_codes": [], "rank_by_code": {}, "summary_by_code": {}},
            "section_c": {"display": "📈 雪球\n", "top20_codes": [], "rank_by_code": {}, "summary_by_code": {}},
            "section_d": {"display": "🇺🇸 ST\n", "top20_codes": [], "rank_by_code": {}, "summary_by_code": {}},
            "intersection": {"triple": [], "ab_only": [], "ac_only": [], "bc_only": []},
        },
        "analyses": [],
    }
    snap_path = tmp_path / "2026-05-27.json"
    snap_path.write_text(json.dumps(snap))

    monkeypatch.setenv("TRADINGAGENTS_FEISHU_WEBHOOK", "https://example.test/hook")

    mock_response = MagicMock(status_code=200, text='{"code":0}')
    mock_response.json.return_value = {"code": 0}
    with patch("requests.post", return_value=mock_response) as mock_post:
        rc = _cmd_push(date="2026-05-27", input_path=str(snap_path), no_feishu=False)
    assert rc == 0
    assert mock_post.called
    payload = mock_post.call_args.kwargs["json"]
    assert payload["msg_type"] == "post"


def test_push_with_no_feishu_skips_webhook(tmp_path, monkeypatch):
    from scripts.daily_sentiment_scan import _cmd_push
    snap = {"schema_version": 1, "date": "2026-05-27", "sections": {"section_a": {"display": "", "top20_codes": [], "rank_by_code": {}, "summary_by_code": {}}, "section_b": {"display": "", "top20_codes": [], "rank_by_code": {}, "summary_by_code": {}}, "section_c": {"display": "", "top20_codes": [], "rank_by_code": {}, "summary_by_code": {}}, "section_d": {"display": "", "top20_codes": [], "rank_by_code": {}, "summary_by_code": {}}, "intersection": {"triple": [], "ab_only": [], "ac_only": [], "bc_only": []}}, "analyses": []}
    snap_path = tmp_path / "2026-05-27.json"
    snap_path.write_text(json.dumps(snap))
    monkeypatch.setenv("TRADINGAGENTS_FEISHU_WEBHOOK", "https://example.test/hook")
    with patch("requests.post") as mock_post:
        rc = _cmd_push(date="2026-05-27", input_path=str(snap_path), no_feishu=True)
    assert rc == 0
    assert not mock_post.called


def test_push_with_missing_snapshot_sends_degraded_alert(tmp_path, monkeypatch):
    from scripts.daily_sentiment_scan import _cmd_push
    monkeypatch.setenv("TRADINGAGENTS_FEISHU_WEBHOOK", "https://example.test/hook")
    nonexistent = str(tmp_path / "not-there.json")
    mock_response = MagicMock(status_code=200, text='{"code":0}')
    mock_response.json.return_value = {"code": 0}
    with patch("requests.post", return_value=mock_response) as mock_post:
        rc = _cmd_push(date="2026-05-27", input_path=nonexistent, no_feishu=False)
    # Exit 0 but webhook called with a degraded-alert payload
    assert rc == 0
    assert mock_post.called
    payload = mock_post.call_args.kwargs["json"]
    text_blob = json.dumps(payload, ensure_ascii=False)
    assert "未拿到分析快照" in text_blob or "snapshot" in text_blob.lower()


def test_no_flags_default_path_unchanged(tmp_path, monkeypatch, capsys):
    """No --analyze and no --push → existing behavior: print + push directly."""
    from scripts import daily_sentiment_scan as mod
    # Mock build_report + everything that touches the network/disk.
    monkeypatch.setattr(mod, "build_report", lambda d: "REPORT_PLACEHOLDER")
    monkeypatch.setattr(mod, "convert_to_feishu_post", lambda md, d: {"msg_type": "post"})
    monkeypatch.delenv("TRADINGAGENTS_FEISHU_WEBHOOK", raising=False)
    # main() reads sys.argv — set it minimally.
    monkeypatch.setattr(mod.sys, "argv", ["daily_sentiment_scan.py", "--date", "2026-05-27"])
    mod.main()
    out = capsys.readouterr().out
    assert "REPORT_PLACEHOLDER" in out  # current default writes to stdout


def test_analyze_subcommand_name_fallback_for_bc_only_tier(tmp_path, monkeypatch):
    """bc_only tier (龙虎 ∩ 雪球, NOT in 飙升榜) ticker name should come from
    sec_b or sec_c summary_by_code, not be empty."""
    from scripts.daily_sentiment_scan import _cmd_analyze

    # Build sections such that 600000 is only in sec_b and sec_c (bc_only)
    fake_sec_a = MagicMock(
        display="A", top20_codes=["600519"],
        rank_by_code={"600519": 1}, summary_by_code={"600519": "贵州茅台"},
    )
    fake_sec_b = MagicMock(
        display="B", top20_codes=["600000"],
        rank_by_code={"600000": 2}, summary_by_code={"600000": "浦发银行 · 净买入+1.2亿"},
    )
    fake_sec_c = MagicMock(
        display="C", top20_codes=["600000"],
        rank_by_code={"600000": 3}, summary_by_code={"600000": "浦发银行 本周#3"},
    )
    fake_sec_d = MagicMock(
        display="D", top20_codes=[],
        rank_by_code={}, summary_by_code={},
    )

    fake_fund = {
        "pe_ttm": 5.0, "pe_forward": None, "fcf": None, "roe": 0.10,
        "market_cap": 5e11, "currency": "CNY", "as_of": "2026-05-28",
        "source": "akshare+eastmoney", "missing_fields": ["pe_forward", "fcf"],
        "status": "partial",
    }
    fake_batch_result = [{
        "ticker": "600000", "status": "ok",
        "decision": {"rating": "Hold", "action": "HOLD", "summary_1line": "..."},
        "error": None, "elapsed_seconds": 100,
    }]

    output = tmp_path / "snapshot.json"
    with patch("scripts.daily_sentiment_scan.section_a_hot_up_rank", return_value=fake_sec_a), \
         patch("scripts.daily_sentiment_scan.section_b_lhb", return_value=fake_sec_b), \
         patch("scripts.daily_sentiment_scan.section_c_xueqiu_surge", return_value=fake_sec_c), \
         patch("scripts.daily_sentiment_scan.section_d_stocktwits", return_value=fake_sec_d), \
         patch("scripts.daily_sentiment_scan.fetch_structured_fundamentals", return_value=fake_fund), \
         patch("scripts.daily_sentiment_scan.run_batch", return_value=fake_batch_result):
        _cmd_analyze(date="2026-05-28", output_path=str(output))

    data = json.loads(output.read_text())
    assert data["sections"]["intersection"]["bc_only"] == ["600000"]
    assert len(data["analyses"]) == 1
    assert data["analyses"][0]["code"] == "600000"
    assert data["analyses"][0]["name"] == "浦发银行"   # MUST not be empty


def test_analyze_and_push_are_mutually_exclusive(monkeypatch):
    from scripts import daily_sentiment_scan as mod
    monkeypatch.setattr(mod.sys, "argv", ["s.py", "--analyze", "--push"])
    with pytest.raises(SystemExit):
        mod.main()


def test_push_with_feishu_only_rejected(monkeypatch):
    from scripts import daily_sentiment_scan as mod
    monkeypatch.setattr(mod.sys, "argv", ["s.py", "--push", "--feishu-only"])
    with pytest.raises(SystemExit):
        mod.main()


def test_analyze_with_no_feishu_rejected(monkeypatch):
    from scripts import daily_sentiment_scan as mod
    monkeypatch.setattr(mod.sys, "argv", ["s.py", "--analyze", "--no-feishu"])
    with pytest.raises(SystemExit):
        mod.main()


# ---------------------------------------------------------------------------
# Phase 10: report_dir wiring + snapshot.report_paths
# ---------------------------------------------------------------------------

def test_default_reports_dir_uses_env_var_with_reports_subdir(monkeypatch, tmp_path):
    """_default_reports_dir respects TRADINGAGENTS_SENTIMENT_SCAN_DIR and
    appends a /reports/<DATE>/ subpath."""
    from scripts.daily_sentiment_scan import _default_reports_dir

    monkeypatch.setenv("TRADINGAGENTS_SENTIMENT_SCAN_DIR", str(tmp_path))
    result = _default_reports_dir("2026-05-28")
    assert result == str(tmp_path / "reports" / "2026-05-28")


def test_analyze_subcommand_passes_report_dir_to_run_batch(tmp_path, monkeypatch):
    """_cmd_analyze MUST pass report_dir=<scan_dir>/reports/<DATE> to run_batch."""
    from scripts.daily_sentiment_scan import _cmd_analyze

    monkeypatch.setenv("TRADINGAGENTS_SENTIMENT_SCAN_DIR", str(tmp_path))
    expected_reports_dir = str(tmp_path / "reports" / "2026-05-28")

    fake_sec_a = MagicMock(display="A", top20_codes=["600519"],
                          rank_by_code={"600519": 1}, summary_by_code={"600519": "茅台"})
    fake_sec_b = MagicMock(display="B", top20_codes=["600519"],
                          rank_by_code={"600519": 1}, summary_by_code={})
    fake_sec_c = MagicMock(display="C", top20_codes=["600519"],
                          rank_by_code={"600519": 1}, summary_by_code={})
    fake_sec_d = MagicMock(display="D", top20_codes=[],
                          rank_by_code={}, summary_by_code={})

    fake_fund = {
        "pe_ttm": None, "pe_forward": None, "fcf": None, "roe": None,
        "market_cap": None, "currency": "CNY", "as_of": "2026-05-28",
        "source": "akshare", "missing_fields": [], "status": "ok",
    }
    fake_batch_result = [{
        "ticker": "600519", "status": "ok",
        "decision": {"rating": "Hold", "action": "HOLD", "summary_1line": "..."},
        "error": None, "elapsed_seconds": 1,
        "report_paths": {},
    }]

    output = tmp_path / "snapshot.json"
    with patch("scripts.daily_sentiment_scan.section_a_hot_up_rank", return_value=fake_sec_a), \
         patch("scripts.daily_sentiment_scan.section_b_lhb", return_value=fake_sec_b), \
         patch("scripts.daily_sentiment_scan.section_c_xueqiu_surge", return_value=fake_sec_c), \
         patch("scripts.daily_sentiment_scan.section_d_stocktwits", return_value=fake_sec_d), \
         patch("scripts.daily_sentiment_scan.fetch_structured_fundamentals", return_value=fake_fund), \
         patch("scripts.daily_sentiment_scan.run_batch", return_value=fake_batch_result) as mock_run_batch:
        _cmd_analyze(date="2026-05-28", output_path=str(output))

    # Verify run_batch was called with our expected report_dir
    assert mock_run_batch.called
    _, kwargs = mock_run_batch.call_args
    assert kwargs.get("report_dir") == expected_reports_dir
    # And the dir was created
    import os as _os
    assert _os.path.isdir(expected_reports_dir)


def test_analyze_subcommand_includes_report_paths_in_snapshot(tmp_path, monkeypatch):
    """Snapshot analyses[i].report_paths MUST be propagated from run_batch result."""
    from scripts.daily_sentiment_scan import _cmd_analyze

    monkeypatch.setenv("TRADINGAGENTS_SENTIMENT_SCAN_DIR", str(tmp_path))

    fake_sec_a = MagicMock(display="A", top20_codes=["600519"],
                          rank_by_code={"600519": 1}, summary_by_code={"600519": "茅台"})
    fake_sec_b = MagicMock(display="B", top20_codes=["600519"],
                          rank_by_code={"600519": 1}, summary_by_code={})
    fake_sec_c = MagicMock(display="C", top20_codes=["600519"],
                          rank_by_code={"600519": 1}, summary_by_code={})
    fake_sec_d = MagicMock(display="D", top20_codes=[],
                          rank_by_code={}, summary_by_code={})

    fake_fund = {
        "pe_ttm": None, "pe_forward": None, "fcf": None, "roe": None,
        "market_cap": None, "currency": "CNY", "as_of": "2026-05-28",
        "source": "akshare", "missing_fields": [], "status": "ok",
    }
    expected_paths = {
        "fundamentals_report": "/abs/path/fundamentals_report.md",
        "news_report": "/abs/path/news_report.md",
        "investment_plan": "/abs/path/investment_plan.md",
        "trader_investment_plan": "/abs/path/trader_investment_plan.md",
        "final_trade_decision": "/abs/path/final_trade_decision.md",
    }
    fake_batch_result = [{
        "ticker": "600519", "status": "ok",
        "decision": {"rating": "Hold", "action": "HOLD", "summary_1line": "..."},
        "error": None, "elapsed_seconds": 1,
        "report_paths": expected_paths,
    }]

    output = tmp_path / "snapshot.json"
    with patch("scripts.daily_sentiment_scan.section_a_hot_up_rank", return_value=fake_sec_a), \
         patch("scripts.daily_sentiment_scan.section_b_lhb", return_value=fake_sec_b), \
         patch("scripts.daily_sentiment_scan.section_c_xueqiu_surge", return_value=fake_sec_c), \
         patch("scripts.daily_sentiment_scan.section_d_stocktwits", return_value=fake_sec_d), \
         patch("scripts.daily_sentiment_scan.fetch_structured_fundamentals", return_value=fake_fund), \
         patch("scripts.daily_sentiment_scan.run_batch", return_value=fake_batch_result):
        _cmd_analyze(date="2026-05-28", output_path=str(output))

    data = json.loads(output.read_text())
    assert len(data["analyses"]) == 1
    assert data["analyses"][0]["code"] == "600519"
    assert data["analyses"][0]["report_paths"] == expected_paths
