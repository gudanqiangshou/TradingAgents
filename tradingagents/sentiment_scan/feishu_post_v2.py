"""飞书 post 富文本 builder — 变体 B 排版.

Order:
  1. Header (title + subline)
  2. 🚀 飙升榜 Top 5            (snapshot.sections.section_a.display)
  3. 🐂 龙虎榜 Top 5
  4. 📈 雪球飙升榜 Top 5
  5. 🇺🇸 StockTwits Top 5
  6. 🌟 多源交集决策卡 (per-ticker)
  7. 📋 决策口诀 (static)

A 股 ticker codes get xueqiu.com links; StockTwits-section US tickers get
stocktwits.com links — reuse `_parse_line_to_feishu_elements` from
scripts/daily_sentiment_scan.py.
"""
from __future__ import annotations

from typing import Any

# Reuse the link-rich element parser from the existing script.
from scripts.daily_sentiment_scan import _parse_line_to_feishu_elements

_MANTRA_BULLETS = [
    "• A 股飙升榜 ∩ 龙虎榜 = 散户 + 机构同向 = 最强信号",
    "• A 股飙升榜 ∩ 雪球飙升榜 = 双源散户关注度验证 = 强信号",
    "• 三源命中 = 飙升榜+龙虎榜+雪球同向 = 最高置信",
    "• 美股 StockTwits 热议榜 → 配 Google Trends + StockTwits 个股看多/看空",
]


def build_feishu_post(snapshot: dict, date: str) -> dict:
    """Build the 飞书 post payload from a sentiment-scan snapshot."""
    paragraphs: list[list] = []

    # 1. Header subline
    paragraphs.extend(_header_block(snapshot, date))

    # 2-5. Four Top-5 sections
    for sec_key, emoji_title in (
        ("section_a", "🚀 A 股关注度飙升榜"),
        ("section_b", "🐂 A 股龙虎榜"),
        ("section_c", "📈 雪球飙升榜"),
        ("section_d", "🇺🇸 StockTwits 美股热议榜"),
    ):
        paragraphs.append([{"tag": "text", "text": f"━━━━━━━━ {emoji_title} ━━━━━━━━"}])
        display = (snapshot.get("sections", {}).get(sec_key) or {}).get("display", "")
        in_stocktwits = sec_key == "section_d"
        header_skipped = False
        for line in display.splitlines():
            if not line.strip():
                continue
            # Drop the first non-empty line (it's the section's original emoji header,
            # which we replace with our own ━━━ header above). Robust to whatever
            # emoji conventions the section uses — section_b uses 🐂 for BOTH header
            # AND data rows, so a "startswith emoji" filter would drop data too.
            if not header_skipped:
                header_skipped = True
                continue
            paragraphs.append(_parse_line_to_feishu_elements(line, in_stocktwits))

    # 6. Multi-source intersection decision cards
    decision_paragraphs = _decision_block(snapshot)
    if decision_paragraphs:
        paragraphs.append([{"tag": "text", "text": "━━━━━━━━ 🌟 重点关注 · 多源交集决策 ━━━━━━━━"}])
        paragraphs.extend(decision_paragraphs)

    # 7. Static mantra
    paragraphs.append([{"tag": "text", "text": "━━━━━━━━ 📋 决策口诀 ━━━━━━━━"}])
    for bullet in _MANTRA_BULLETS:
        paragraphs.append([{"tag": "text", "text": bullet}])

    return {
        "msg_type": "post",
        "content": {
            "post": {
                "zh_cn": {
                    "title": f"散户情绪扫盘 · 决策分析 — {date}",
                    "content": paragraphs,
                }
            }
        },
    }


def _header_block(snapshot: dict, date: str) -> list[list]:
    """Header subline with timing + counts."""
    analyses = snapshot.get("analyses", [])
    ok_count = sum(1 for a in analyses if a.get("status") == "ok")
    partial_count = sum(1 for a in analyses if a.get("status") in ("partial", "incomplete"))
    fail_count = sum(1 for a in analyses if a.get("status") in ("timeout", "error", "budget_exhausted"))
    subline = (
        f"扫描完成 {snapshot.get('scan_completed_at', '—')} · "
        f"分析完成 {snapshot.get('analysis_completed_at', '—')} · "
        f"{ok_count} 完整 / {partial_count} 部分 / {fail_count} 失败"
    )
    return [[{"tag": "text", "text": subline}]]


def _decision_block(snapshot: dict) -> list[list]:
    analyses = snapshot.get("analyses", [])
    if not analyses:
        return []
    paragraphs: list[list] = []
    tier_order = ("triple", "ab_only", "ac_only", "bc_only")
    seen = False
    for tier in tier_order:
        for a in analyses:
            if a.get("tier") != tier:
                continue
            if seen:
                paragraphs.append([{"tag": "text", "text": "────────────────────────────────"}])
            paragraphs.extend(_card_for_analysis(a))
            seen = True
    return paragraphs


def _card_for_analysis(a: dict) -> list[list]:
    """Build per-ticker decision card paragraphs."""
    status = a.get("status", "error")
    code = a.get("code", "?")
    name = a.get("name", "")
    tier = a.get("tier", "?")
    tier_emoji = "⭐⭐⭐" if tier == "triple" else "⭐⭐"
    tier_label = {
        "triple": "三源命中",
        "ab_only": "双源命中 飙升榜∩龙虎榜",
        "ac_only": "双源命中 飙升榜∩雪球",
        "bc_only": "双源命中 龙虎榜∩雪球",
    }.get(tier, "—")
    rank_summary = _rank_summary(a.get("ranks", {}))

    if status in ("ok", "partial"):
        decision = a.get("decision") or {}
        fundamentals = a.get("fundamentals") or {}
        rows = [
            _parse_line_to_feishu_elements(f"{tier_emoji} {code} {name}", False),
            [{"tag": "text", "text": f"     {tier_label}：{rank_summary}"}],
            [{"tag": "text", "text": f"     💰 建议：{decision.get('action', '—')} ({decision.get('rating', '—')})"}],
            [{"tag": "text", "text": f"     📊 PE {_fmt_pe(fundamentals.get('pe_ttm'))} · 远期PE {_fmt_pe(fundamentals.get('pe_forward'))} · ROE {_fmt_roe(fundamentals.get('roe'))} · FCF {_fmt_fcf(fundamentals.get('fcf'), fundamentals.get('currency'))}"}],
            [{"tag": "text", "text": f"     💡 {decision.get('summary_1line', '—')}"}],
        ]
        return rows

    # status in {incomplete, timeout, error, budget_exhausted}
    status_zh = {
        "incomplete": "分析未产出决策",
        "timeout": "分析超时",
        "error": "分析失败",
        "budget_exhausted": "未分析（时间预算用尽）",
    }.get(status, "分析异常")
    extra = ""
    if status == "error" and a.get("error"):
        extra = f" — {a['error'][:80]}"
    rows = [
        [{"tag": "text", "text": f"⚠ {code} {name} — {status_zh}{extra}"}],
        [{"tag": "text", "text": f"     {tier_label}：{rank_summary}"}],
        [{"tag": "text", "text": "     仅原始信号可参考"}],
    ]
    return rows


def _rank_summary(ranks: dict) -> str:
    parts = []
    for key, label in (("a", "飙升榜"), ("b", "龙虎榜"), ("c", "雪球飙升")):
        v = ranks.get(key)
        if v is not None:
            parts.append(f"{label}#{v}")
    return " · ".join(parts) if parts else "—"


def _fmt_pe(v: Any) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.1f}"
    except (TypeError, ValueError):
        return "—"


def _fmt_roe(v: Any) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v) * 100:.1f}%"
    except (TypeError, ValueError):
        return "—"


def _fmt_fcf(v: Any, currency: str | None) -> str:
    if v is None:
        return "—"
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "—"
    sign = "" if v >= 0 else "-"
    a = abs(v)
    if currency == "CNY":
        if a >= 1e8:
            return f"{sign}¥{a / 1e8:.1f}亿"
        if a >= 1e4:
            return f"{sign}¥{a / 1e4:.1f}万"
        return f"{sign}¥{a:.0f}"
    if currency == "HKD":
        if a >= 1e9:
            return f"{sign}HK${a / 1e9:.1f}B"
        if a >= 1e6:
            return f"{sign}HK${a / 1e6:.1f}M"
        return f"{sign}HK${a:.0f}"
    # USD / default
    if a >= 1e9:
        return f"{sign}${a / 1e9:.1f}B"
    if a >= 1e6:
        return f"{sign}${a / 1e6:.1f}M"
    return f"{sign}${a:.0f}"
