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
