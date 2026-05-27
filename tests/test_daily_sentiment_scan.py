"""Tests for scripts/daily_sentiment_scan.py — all offline, mocked."""

from __future__ import annotations

import sys
import os
import json
from io import StringIO
from unittest.mock import MagicMock, patch, call
import pandas as pd
import pytest

# Ensure scripts/ is importable as a module by adding project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from scripts.daily_sentiment_scan import (
    build_report,
    section_a_hot_up_rank,
    section_b_lhb_top5,
    section_c_xueqiu_weekly_top20,
    section_d_stocktwits_trending,
    convert_to_feishu_post,
    main,
)
from tradingagents.dataflows.akshare_china import _XUEQIU_CACHE, _XUEQIU_CACHE_LOCK
import tradingagents.dataflows.akshare_china as _akshare_mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_lhb_df():
    """Fake 龙虎榜 DataFrame with 7 rows of varying 净买额."""
    return pd.DataFrame({
        "代码":   ["000001", "600519", "300750", "000858", "601318", "002594", "600036"],
        "名称":   ["平安银行", "贵州茅台", "宁德时代", "五粮液", "中国平安", "比亚迪", "招商银行"],
        "上榜日": ["2026-05-27"] * 7,
        "龙虎榜净买额": [5e8, 3e8, 8e8, 1e8, 2e8, 9e8, 4e8],
        "解读":   ["机构净买"] * 7,
    })


def _make_xueqiu_df():
    """Fake 雪球 DataFrame with 25 rows."""
    codes = [f"{i:06d}" for i in range(600001, 600026)]
    names = [f"Stock{i}" for i in range(25)]
    follows = list(range(25000, 25000 + 25))
    return pd.DataFrame({
        "股票代码": codes,
        "名称":    names,
        "关注数":  follows,
    })


# ---------------------------------------------------------------------------
# test_build_report_all_sources_succeed
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_build_report_all_sources_succeed():
    """All 4 sources mocked to succeed; report has all expected section headers."""
    with (
        patch("scripts.daily_sentiment_scan.get_hot_up_rank", return_value="| 1 | 000001 | 平安银行 | 2.5% |"),
        patch("scripts.daily_sentiment_scan.section_b_lhb_top5", return_value="## A股 龙虎榜 — Top 5 净买入（近 5 个交易日，东方财富）\n| 600519 | 贵州茅台 |"),
        patch("scripts.daily_sentiment_scan.section_c_xueqiu_weekly_top20", return_value="## 雪球本周新增 Top 20\n| 600519 | 贵州茅台 |"),
        patch("scripts.daily_sentiment_scan.fetch_stocktwits_trending", return_value="# StockTwits Trending Equities\n| 1 | AAPL | NASDAQ | Apple |"),
    ):
        report = build_report("2026-05-27")

    assert "# 散户情绪扫盘" in report
    assert "飙升榜" in report
    assert "龙虎榜" in report
    assert "雪球" in report
    assert "StockTwits" in report


# ---------------------------------------------------------------------------
# test_build_report_one_source_fails_others_succeed
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_build_report_one_source_fails_others_succeed():
    """飙升榜 raises; other 3 sections still appear in report."""
    with (
        patch("scripts.daily_sentiment_scan.get_hot_up_rank", side_effect=RuntimeError("网络异常")),
        patch("scripts.daily_sentiment_scan.section_b_lhb_top5", return_value="## A股 龙虎榜 — Top 5\n| 600519 |"),
        patch("scripts.daily_sentiment_scan.section_c_xueqiu_weekly_top20", return_value="## 雪球\n| 000001 |"),
        patch("scripts.daily_sentiment_scan.fetch_stocktwits_trending", return_value="# StockTwits Trending\n| 1 | MSFT |"),
    ):
        report = build_report("2026-05-27")

    assert "unavailable" in report or "RuntimeError" in report
    assert "龙虎榜" in report
    assert "雪球" in report
    assert "StockTwits" in report


# ---------------------------------------------------------------------------
# test_lhb_section_top5_sort_by_net_buy
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_lhb_section_top5_sort_by_net_buy():
    """section_b returns top 5 sorted by 净买额 descending."""
    df = _make_lhb_df()
    mock_ak = MagicMock()
    mock_ak.stock_lhb_detail_em.return_value = df
    # section_b calls _dep_bootstrap.ensure("akshare") via the internal import
    with patch("tradingagents.dataflows._dep_bootstrap.ensure", return_value=mock_ak):
        result = section_b_lhb_top5("2026-05-27")

    # 002594 (比亚迪, 9e8) should be first
    lines = [l for l in result.splitlines() if l.startswith("| ") and "代码" not in l and "-- " not in l]
    assert len(lines) == 5
    # First row should contain 002594 (highest net buy at 9e8)
    assert "002594" in lines[0]
    # 300750 (宁德时代, 8e8) second
    assert "300750" in lines[1]


# ---------------------------------------------------------------------------
# test_xueqiu_section_uses_cached_data
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_xueqiu_section_uses_cached_data():
    """Pre-populate _XUEQIU_CACHE with fake df; section_c returns top 20 rows."""
    import time
    df = _make_xueqiu_df()
    with _XUEQIU_CACHE_LOCK:
        _XUEQIU_CACHE["本周新增"] = (time.time(), df)
    try:
        result = section_c_xueqiu_weekly_top20()
    finally:
        with _XUEQIU_CACHE_LOCK:
            _XUEQIU_CACHE.pop("本周新增", None)

    assert "雪球" in result
    # Should have 20 data rows (df has 25 rows, we take top 20)
    rows = [l for l in result.splitlines() if l.startswith("| ") and "代码" not in l and "-- " not in l]
    assert len(rows) == 20


# ---------------------------------------------------------------------------
# test_convert_to_feishu_post_structure
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_convert_to_feishu_post_structure():
    """convert_to_feishu_post returns correct msg_type and nested structure."""
    sample_md = (
        "# 散户情绪扫盘 — 2026-05-27\n"
        "_生成于 2026-05-27 09:05:00_\n"
        "\n"
        "## A股 飙升榜 — 散户 attention 突变\n"
        "| 1 | 000001 | 平安银行 | 2.5% |\n"
    )
    payload = convert_to_feishu_post(sample_md, "2026-05-27")
    assert payload["msg_type"] == "post"
    assert "content" in payload
    assert "post" in payload["content"]
    zh_cn = payload["content"]["post"]["zh_cn"]
    assert "title" in zh_cn
    assert "content" in zh_cn
    assert isinstance(zh_cn["content"], list)
    assert zh_cn["title"] == "散户情绪扫盘 2026-05-27"


# ---------------------------------------------------------------------------
# test_feishu_post_includes_xueqiu_link_for_a_share_ticker
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_feishu_post_includes_xueqiu_link_for_a_share_ticker():
    """Report containing 600519 → feishu payload has xueqiu link for SH600519."""
    sample_md = (
        "# 散户情绪扫盘 — 2026-05-27\n"
        "## A股 飙升榜\n"
        "| 代码 | 名称 |\n"
        "| 600519 | 贵州茅台 |\n"
    )
    payload = convert_to_feishu_post(sample_md, "2026-05-27")
    # Flatten all elements
    all_elements = [
        elem
        for para in payload["content"]["post"]["zh_cn"]["content"]
        for elem in para
    ]
    link_hrefs = [e.get("href", "") for e in all_elements if e.get("tag") == "a"]
    assert any("xueqiu.com/S/SH600519" in h for h in link_hrefs), (
        f"Expected xueqiu link for 600519, got hrefs: {link_hrefs}"
    )


# ---------------------------------------------------------------------------
# test_feishu_post_includes_stocktwits_link_for_us_ticker
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_feishu_post_includes_stocktwits_link_for_us_ticker():
    """Report with AAPL in StockTwits section → feishu payload has stocktwits link."""
    sample_md = (
        "# 散户情绪扫盘 — 2026-05-27\n"
        "# StockTwits Trending Equities (top 1, retrieved 2026-05-27 09:00:00 UTC)\n"
        "\n"
        "| # | Symbol | Exchange | Title |\n"
        "| -- | -- | -- | -- |\n"
        "| 1 | AAPL | NASDAQ | Apple Inc |\n"
    )
    payload = convert_to_feishu_post(sample_md, "2026-05-27")
    all_elements = [
        elem
        for para in payload["content"]["post"]["zh_cn"]["content"]
        for elem in para
    ]
    link_hrefs = [e.get("href", "") for e in all_elements if e.get("tag") == "a"]
    assert any("stocktwits.com/symbol/AAPL" in h for h in link_hrefs), (
        f"Expected stocktwits link for AAPL, got hrefs: {link_hrefs}"
    )


# ---------------------------------------------------------------------------
# test_webhook_env_missing_skips_post
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_webhook_env_missing_skips_post():
    """No TRADINGAGENTS_FEISHU_WEBHOOK env var → requests.post never called."""
    with (
        patch.dict(os.environ, {}, clear=False),
        patch("scripts.daily_sentiment_scan.build_report", return_value="# mock"),
        patch("scripts.daily_sentiment_scan.convert_to_feishu_post", return_value={}),
    ):
        os.environ.pop("TRADINGAGENTS_FEISHU_WEBHOOK", None)
        import requests as _req
        with patch.object(_req, "post") as mock_post:
            sys.argv = ["daily_sentiment_scan.py", "--no-feishu"]
            main()
            mock_post.assert_not_called()


# ---------------------------------------------------------------------------
# test_webhook_env_present_calls_post
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_webhook_env_present_calls_post():
    """TRADINGAGENTS_FEISHU_WEBHOOK set → requests.post called once with correct URL."""
    fake_url = "https://open.feishu.cn/open-apis/bot/v2/hook/fake123"
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"code": 0}
    mock_response.text = '{"code":0}'

    with (
        patch.dict(os.environ, {"TRADINGAGENTS_FEISHU_WEBHOOK": fake_url}),
        patch("scripts.daily_sentiment_scan.build_report", return_value="# mock"),
        patch("scripts.daily_sentiment_scan.convert_to_feishu_post", return_value={"msg_type": "post"}),
    ):
        import requests as _req
        with patch.object(_req, "post", return_value=mock_response) as mock_post:
            sys.argv = ["daily_sentiment_scan.py", "--feishu-only"]
            main()
            mock_post.assert_called_once()
            call_args = mock_post.call_args
            assert call_args[0][0] == fake_url


# ---------------------------------------------------------------------------
# test_no_feishu_flag_skips_post_even_if_env_set
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_no_feishu_flag_skips_post_even_if_env_set():
    """--no-feishu flag → requests.post not called even when env var is set."""
    fake_url = "https://open.feishu.cn/open-apis/bot/v2/hook/fake456"

    with (
        patch.dict(os.environ, {"TRADINGAGENTS_FEISHU_WEBHOOK": fake_url}),
        patch("scripts.daily_sentiment_scan.build_report", return_value="# mock"),
        patch("scripts.daily_sentiment_scan.convert_to_feishu_post", return_value={}),
    ):
        import requests as _req
        with patch.object(_req, "post") as mock_post:
            sys.argv = ["daily_sentiment_scan.py", "--no-feishu"]
            main()
            mock_post.assert_not_called()
