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
