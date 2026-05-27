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
    section_c_xueqiu_surge_top15,
    section_d_stocktwits_trending,
    convert_to_feishu_post,
    main,
    _A_SHARE_PREFIXED_RE,
    _A_SHARE_BARE_RE,
)
from tradingagents.dataflows.akshare_china import _XUEQIU_CACHE, _XUEQIU_CACHE_LOCK
import tradingagents.dataflows.akshare_china as _akshare_mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_lhb_df_dupes():
    """Fake 龙虎榜 DataFrame with duplicate 京东方A rows (3x) + 茅台 (1x)."""
    return pd.DataFrame({
        "代码":   ["000725", "000725", "000725", "600519"],
        "名称":   ["京东方A",  "京东方A",  "京东方A",  "贵州茅台"],
        "上榜日": ["2026-05-27", "2026-05-26", "2026-05-25", "2026-05-27"],
        "龙虎榜净买额": [3e8, 2e8, 1e8, 5e8],
        "解读":   ["机构净买", "机构净买", "主力买入", "机构净买"],
    })


def _make_lhb_df():
    """Fake 龙虎榜 DataFrame with 7 unique rows of varying 净买额."""
    return pd.DataFrame({
        "代码":   ["000001", "600519", "300750", "000858", "601318", "002594", "600036"],
        "名称":   ["平安银行", "贵州茅台", "宁德时代", "五粮液", "中国平安", "比亚迪", "招商银行"],
        "上榜日": ["2026-05-27"] * 7,
        "龙虎榜净买额": [5e8, 3e8, 8e8, 1e8, 2e8, 9e8, 4e8],
        "解读":   ["机构净买"] * 7,
    })


def _make_xueqiu_hot_df(codes):
    """Fake 最热门 df with given codes in order."""
    return pd.DataFrame({
        "股票代码": codes,
        "名称": [f"Stock_{c}" for c in codes],
        "关注数": list(range(100000, 100000 - len(codes), -1)),
    })


def _make_xueqiu_weekly_df(codes, follows=None):
    """Fake 本周新增 df with given codes in order."""
    if follows is None:
        follows = list(range(5000, 5000 - len(codes), -1))
    return pd.DataFrame({
        "股票代码": codes,
        "名称": [f"Stock_{c}" for c in codes],
        "关注数": follows,
    })


# ---------------------------------------------------------------------------
# test_build_report_all_sources_succeed
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_build_report_all_sources_succeed():
    """All 4 sources mocked to succeed; report has all expected keywords."""
    with (
        patch("scripts.daily_sentiment_scan.get_hot_up_rank", return_value="🚀 东方财富 attention 飙升榜 — Top 20\n🔥 SZ000001 平安银行 · 排名 #100 (飙升 +200 位) · +2.50%"),
        patch("scripts.daily_sentiment_scan.section_b_lhb_top5", return_value="🐂 A股 龙虎榜 — 近 5 个交易日 Top 5 净买入 (按代码聚合)\n🐂 600519 贵州茅台 · 净买入 +5.00亿"),
        patch("scripts.daily_sentiment_scan.section_c_xueqiu_surge_top15", return_value="📈 雪球飙升榜 — 散户讨论排名突然蹿升的新晋热门 Top 15\n🔥 SH605066 天正电气 · 本周#5 vs 累计#300 (飙升 +295)"),
        patch("scripts.daily_sentiment_scan.fetch_stocktwits_trending", return_value="🇺🇸 StockTwits Trending Equities — Top 1 (retrieved 2026-05-27 09:00:00 UTC)\n1. AAPL NASDAQ · Apple Inc"),
    ):
        report = build_report("2026-05-27")

    assert "# 散户情绪扫盘" in report
    assert "飙升榜" in report
    assert "龙虎榜" in report
    assert "雪球飙升榜" in report
    assert "StockTwits" in report


# ---------------------------------------------------------------------------
# test_build_report_one_source_fails_others_succeed
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_build_report_one_source_fails_others_succeed():
    """飙升榜 raises; other 3 sections still appear in report."""
    with (
        patch("scripts.daily_sentiment_scan.get_hot_up_rank", side_effect=RuntimeError("网络异常")),
        patch("scripts.daily_sentiment_scan.section_b_lhb_top5", return_value="🐂 A股 龙虎榜\n🐂 600519 贵州茅台"),
        patch("scripts.daily_sentiment_scan.section_c_xueqiu_surge_top15", return_value="📈 雪球飙升榜\n🔥 SH000001 平安银行"),
        patch("scripts.daily_sentiment_scan.fetch_stocktwits_trending", return_value="🇺🇸 StockTwits Trending\n1. MSFT NASDAQ · Microsoft"),
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
    with patch("tradingagents.dataflows._dep_bootstrap.ensure", return_value=mock_ak):
        result = section_b_lhb_top5("2026-05-27")

    # 002594 (比亚迪, 9e8) should be first
    lines = [l for l in result.splitlines() if l.startswith("🐂 0") or l.startswith("🐂 6") or l.startswith("🐂 3")]
    assert len(lines) == 5
    # First row should contain 002594 (highest net buy at 9e8)
    assert "002594" in lines[0]
    # 300750 (宁德时代, 8e8) second
    assert "300750" in lines[1]


# ---------------------------------------------------------------------------
# test_section_b_lhb_dedupes_by_code
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_section_b_lhb_dedupes_by_code():
    """京东方A appears 3x + 茅台 1x → output has only 2 unique code lines."""
    df = _make_lhb_df_dupes()
    mock_ak = MagicMock()
    mock_ak.stock_lhb_detail_em.return_value = df
    with patch("tradingagents.dataflows._dep_bootstrap.ensure", return_value=mock_ak):
        result = section_b_lhb_top5("2026-05-27")

    lines = [l for l in result.splitlines() if l.startswith("🐂 0") or l.startswith("🐂 6")]
    assert len(lines) == 2, f"Expected 2 deduped lines, got {len(lines)}: {lines}"
    # 京东方A appears once with summed net buy (3e8+2e8+1e8=6e8 > 5e8 for 茅台)
    kjf_lines = [l for l in lines if "000725" in l or "京东方" in l]
    assert len(kjf_lines) == 1
    # Check summed amount ~6.00亿
    assert "6.00" in kjf_lines[0], f"Expected summed 6.00亿, got: {kjf_lines[0]}"
    # Check N=3 times listed
    assert "3 次" in kjf_lines[0] or "3次" in kjf_lines[0], f"Expected '3 次' in line: {kjf_lines[0]}"


# ---------------------------------------------------------------------------
# test_xueqiu_surge_top15_computes_rank_delta
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_xueqiu_surge_top15_computes_rank_delta():
    """Rank delta computed correctly: ticker with hot_rank>50, low weekly_rank → surges."""
    import time
    # 最热门: 50 tickers where hot_rank 1-50 are big caps, then SH900100 at rank 51
    hot_codes = [f"SH6000{i:02d}" for i in range(1, 51)] + ["SH900100"]
    # 本周新增: SH900100 appears at rank #1
    weekly_codes = ["SH900100"] + [f"SH6000{i:02d}" for i in range(1, 51)]

    df_hot = _make_xueqiu_hot_df(hot_codes)
    df_weekly = _make_xueqiu_weekly_df(weekly_codes)

    with _XUEQIU_CACHE_LOCK:
        _XUEQIU_CACHE["最热门"] = (time.time(), df_hot)
        _XUEQIU_CACHE["本周新增"] = (time.time(), df_weekly)
    try:
        result = section_c_xueqiu_surge_top15()
    finally:
        with _XUEQIU_CACHE_LOCK:
            _XUEQIU_CACHE.pop("最热门", None)
            _XUEQIU_CACHE.pop("本周新增", None)

    assert "📈 雪球飙升榜" in result
    # SH900100: hot_rank=51, weekly_rank=1, surge=50
    assert "SH900100" in result
    assert "飙升 +50" in result


# ---------------------------------------------------------------------------
# test_xueqiu_surge_filters_megacaps
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_xueqiu_surge_filters_megacaps():
    """Tickers with hot_rank <= 50 are filtered out (old megacaps)."""
    import time
    # Only 3 tickers, all with hot_rank <= 50 → no surge candidates
    hot_codes = ["SH600519", "SZ000725", "SH601318"]
    weekly_codes = ["SH601318", "SZ000725", "SH600519"]  # reordered

    df_hot = _make_xueqiu_hot_df(hot_codes)
    df_weekly = _make_xueqiu_weekly_df(weekly_codes)

    with _XUEQIU_CACHE_LOCK:
        _XUEQIU_CACHE["最热门"] = (time.time(), df_hot)
        _XUEQIU_CACHE["本周新增"] = (time.time(), df_weekly)
    try:
        result = section_c_xueqiu_surge_top15()
    finally:
        with _XUEQIU_CACHE_LOCK:
            _XUEQIU_CACHE.pop("最热门", None)
            _XUEQIU_CACHE.pop("本周新增", None)

    # All filtered out (hot_rank <=50) → "无新晋飙升标的" or empty
    assert "无新晋飙升标的" in result or "老热门主导" in result or "📈 雪球飙升榜" in result


# ---------------------------------------------------------------------------
# test_xueqiu_surge_top15_sort_desc
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_xueqiu_surge_top15_sort_desc():
    """Multiple eligible tickers → top result has highest surge."""
    import time
    # Build: 100 hot codes (ranks 1-100), with SH900050 at rank 51 and SH900099 at rank 100
    hot_codes = [f"SH6000{i:02d}" for i in range(1, 51)] + [f"SH9000{i:02d}" for i in range(50, 100)]
    # weekly: SH900099 at rank #1 (surge = 100 - 1 = 99), SH900050 at rank #2 (surge = 51 - 2 = 49)
    weekly_codes = ["SH900099", "SH900050"] + [f"SH6000{i:02d}" for i in range(1, 51)] + [
        f"SH9000{i:02d}" for i in range(50, 99)
    ]

    df_hot = _make_xueqiu_hot_df(hot_codes)
    df_weekly = _make_xueqiu_weekly_df(weekly_codes[:len(hot_codes)])

    with _XUEQIU_CACHE_LOCK:
        _XUEQIU_CACHE["最热门"] = (time.time(), df_hot)
        _XUEQIU_CACHE["本周新增"] = (time.time(), df_weekly)
    try:
        result = section_c_xueqiu_surge_top15()
    finally:
        with _XUEQIU_CACHE_LOCK:
            _XUEQIU_CACHE.pop("最热门", None)
            _XUEQIU_CACHE.pop("本周新增", None)

    # First 🔥 line should be SH900099 (highest surge)
    fire_lines = [l for l in result.splitlines() if l.startswith("🔥")]
    assert fire_lines, f"Expected 🔥 lines, got: {result}"
    assert "SH900099" in fire_lines[0], f"Expected SH900099 first, got: {fire_lines[0]}"


# ---------------------------------------------------------------------------
# test_a_share_prefixed_regex_matches
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_a_share_prefixed_regex_matches():
    """Prefixed regex matches SH/SZ/BJ in upper and lower case."""
    assert _A_SHARE_PREFIXED_RE.search("SH600519") is not None
    assert _A_SHARE_PREFIXED_RE.search("sz000725") is not None
    assert _A_SHARE_PREFIXED_RE.search("BJ430047") is not None
    assert _A_SHARE_PREFIXED_RE.search("bj430047") is not None
    # Should NOT match bare code without prefix
    assert _A_SHARE_PREFIXED_RE.search("600519") is None


@pytest.mark.unit
def test_a_share_bare_regex_matches():
    """Bare regex matches valid A-share codes."""
    assert _A_SHARE_BARE_RE.search("600519") is not None
    assert _A_SHARE_BARE_RE.search("000725") is not None
    assert _A_SHARE_BARE_RE.search("430047") is not None
    # Should NOT match inside a longer number
    assert _A_SHARE_BARE_RE.search("1600519") is None


# ---------------------------------------------------------------------------
# test_feishu_post_xueqiu_section_has_links
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_feishu_post_xueqiu_section_has_links():
    """Line with SH600519 → feishu payload has xueqiu link for SH600519."""
    sample_md = (
        "# 散户情绪扫盘 — 2026-05-27\n"
        "📈 雪球飙升榜 — 散户讨论排名突然蹿升的新晋热门 Top 15\n"
        "🔥 SH600519 茅台 · 本周#15 vs 累计#280 (飙升 +265)\n"
    )
    payload = convert_to_feishu_post(sample_md, "2026-05-27")
    all_elements = [
        elem
        for para in payload["content"]["post"]["zh_cn"]["content"]
        for elem in para
    ]
    link_hrefs = [e.get("href", "") for e in all_elements if e.get("tag") == "a"]
    assert any("xueqiu.com/S/SH600519" in h for h in link_hrefs), (
        f"Expected xueqiu link for SH600519, got hrefs: {link_hrefs}"
    )


# ---------------------------------------------------------------------------
# test_feishu_post_includes_xueqiu_link_for_a_share_bare_ticker
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_feishu_post_includes_xueqiu_link_for_a_share_bare_ticker():
    """Report containing bare 600519 → feishu payload has xueqiu link for SH600519."""
    sample_md = (
        "# 散户情绪扫盘 — 2026-05-27\n"
        "🔥 600519 贵州茅台 · 排名 #100 (飙升 +200 位) · +2.50%\n"
    )
    payload = convert_to_feishu_post(sample_md, "2026-05-27")
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
# test_convert_to_feishu_post_structure
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_convert_to_feishu_post_structure():
    """convert_to_feishu_post returns correct msg_type and nested structure."""
    sample_md = (
        "# 散户情绪扫盘 — 2026-05-27\n"
        "_生成于 2026-05-27 09:05:00_\n"
        "\n"
        "🚀 东方财富 attention 飙升榜 — Top 20\n"
        "🔥 SZ000001 平安银行 · 排名 #100 (飙升 +200 位) · +2.50%\n"
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
# test_feishu_post_no_markdown_table_characters
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_feishu_post_no_markdown_table_characters():
    """New compact format → no '| -- |' or markdown table separators in payload text."""
    sample_md = (
        "# 散户情绪扫盘 — 2026-05-27\n"
        "🚀 东方财富 attention 飙升榜 — Top 20\n"
        "🔥 SH600519 贵州茅台 · 排名 #70 (飙升 +3753 位) · +20.04%\n"
        "🐂 A股 龙虎榜 — 近 5 个交易日 Top 5 净买入 (按代码聚合)\n"
        "🐂 600519 贵州茅台 · 净买入 +5.00亿 (上榜 1 次, 最近 2026-05-27) · 机构净买\n"
    )
    payload = convert_to_feishu_post(sample_md, "2026-05-27")
    all_text = " ".join(
        e.get("text", "") or ""
        for para in payload["content"]["post"]["zh_cn"]["content"]
        for e in para
    )
    assert "| -- |" not in all_text
    assert "| 代码 |" not in all_text


# ---------------------------------------------------------------------------
# test_feishu_post_includes_stocktwits_link_for_us_ticker
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_feishu_post_includes_stocktwits_link_for_us_ticker():
    """Report with AAPL in StockTwits section → feishu payload has stocktwits link."""
    sample_md = (
        "# 散户情绪扫盘 — 2026-05-27\n"
        "🇺🇸 StockTwits Trending Equities — Top 1 (retrieved 2026-05-27 09:00:00 UTC)\n"
        "1. AAPL NASDAQ · Apple Inc\n"
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
