"""Tests for feishu_post_v2.build_feishu_post."""
import pytest


def _make_snapshot() -> dict:
    """Snapshot with 1 triple + 1 ab_only + 1 timeout + 4 section displays."""
    return {
        "schema_version": 1,
        "date": "2026-05-27",
        "scan_completed_at": "06:31:08",
        "analysis_completed_at": "08:42:13",
        "analysis_budget_exhausted": False,
        "sections": {
            "section_a": {"display": "🚀 A 股关注度飙升榜 — Top 5\n🔥 SH600519 贵州茅台 #3", "top20_codes": [], "rank_by_code": {}, "summary_by_code": {}},
            "section_b": {"display": "🐂 龙虎榜 Top 5\n🐂 600519 贵州茅台 净买入 +12.5亿", "top20_codes": [], "rank_by_code": {}, "summary_by_code": {}},
            "section_c": {"display": "📈 雪球飙升榜 Top 5\n🔥 SH600519 贵州茅台 本周#1", "top20_codes": [], "rank_by_code": {}, "summary_by_code": {}},
            "section_d": {"display": "🇺🇸 StockTwits Top 5\n1. AAPL NASDAQ · Apple Inc", "top20_codes": [], "rank_by_code": {}, "summary_by_code": {}},
            "intersection": {"triple": ["600519"], "ab_only": ["300866"], "ac_only": [], "bc_only": []},
        },
        "analyses": [
            {
                "code": "600519", "name": "贵州茅台", "market": "A_SHARE",
                "tier": "triple", "ranks": {"a": 3, "b": 1, "c": 8},
                "fundamentals": {"pe_ttm": 25.3, "pe_forward": 22.1, "fcf": 5.6e10, "roe": 0.308, "market_cap": 3.2e12, "currency": "CNY", "as_of": "2026-05-27", "source": "akshare", "missing_fields": [], "status": "ok"},
                "decision": {"rating": "Overweight", "action": "BUY", "summary_1line": "高端白酒龙头机构净买入背书"},
                "elapsed_seconds": 612, "status": "ok",
            },
            {
                "code": "300866", "name": "安克创新", "market": "A_SHARE",
                "tier": "ab_only", "ranks": {"a": 5, "b": 12},
                "fundamentals": {"pe_ttm": 38.2, "pe_forward": None, "fcf": 1.2e9, "roe": 0.184, "market_cap": 2.1e11, "currency": "CNY", "as_of": "2026-05-27", "source": "akshare", "missing_fields": ["pe_forward"], "status": "partial"},
                "decision": {"rating": "Hold", "action": "HOLD", "summary_1line": "跨境电商景气延续但估值偏高"},
                "elapsed_seconds": 580, "status": "partial",
            },
        ],
    }


def test_build_feishu_post_returns_post_msg_type():
    from tradingagents.sentiment_scan.feishu_post_v2 import build_feishu_post
    payload = build_feishu_post(_make_snapshot(), "2026-05-27")
    assert payload["msg_type"] == "post"
    assert "zh_cn" in payload["content"]["post"]
    assert "散户情绪扫盘" in payload["content"]["post"]["zh_cn"]["title"]


def test_section_order_is_4_top5_then_intersection():
    """变体 B: 4 section first, then 决策卡 block, then 决策口诀."""
    from tradingagents.sentiment_scan.feishu_post_v2 import build_feishu_post
    payload = build_feishu_post(_make_snapshot(), "2026-05-27")
    paragraphs = payload["content"]["post"]["zh_cn"]["content"]
    # Flatten to text-only for ordering check.
    text_per_para = ["".join(e.get("text", "") for e in p) for p in paragraphs]
    full = "\n".join(text_per_para)
    pos_a = full.find("🚀")
    pos_b = full.find("🐂")
    pos_c = full.find("📈")
    pos_d = full.find("🇺🇸")
    pos_intersection = full.find("🌟")
    pos_mantra = full.find("📋")
    assert pos_a < pos_b < pos_c < pos_d < pos_intersection < pos_mantra
