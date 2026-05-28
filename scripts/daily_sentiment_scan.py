#!/usr/bin/env python3
"""Daily retail-attention scan across A股 (飙升榜/龙虎榜/雪球飙升榜) and 美股
(StockTwits Trending Equities). Outputs compact emoji-prefixed lines to stdout
for human review; optionally posts a 飞书 post-format message if
TRADINGAGENTS_FEISHU_WEBHOOK env var is set.

Usage:
    python scripts/daily_sentiment_scan.py [--date YYYY-MM-DD] [--no-feishu]

Designed for daily cron / LaunchAgent invocation. Every section is
fail-isolated: a failure in one source produces an inline "(暂不可用： ...)"
placeholder but does NOT block the other sections.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timedelta
from typing import NamedTuple

import pandas as pd

from tradingagents.dataflows.akshare_china import (
    fetch_hot_up_rank_data,
    _get_xueqiu_cached,
    _XUEQIU_CACHE,
    _df_is_empty,
)
from tradingagents.dataflows.stocktwits import fetch_stocktwits_trending


# ---------------------------------------------------------------------------
# SectionResult type
# ---------------------------------------------------------------------------

class SectionResult(NamedTuple):
    display: str
    top20_codes: list  # bare 6-digit codes for A-share sources; ticker symbols for US
    rank_by_code: dict  # bare code → rank within this section (1-20)
    summary_by_code: dict  # bare code → concise per-source info string


# ---------------------------------------------------------------------------
# Section A — 飙升榜 (内部 Top 20, 显示 Top 5)
# ---------------------------------------------------------------------------

def section_a_hot_up_rank() -> SectionResult:
    """飙升榜: 内部 Top 20, 显示 Top 5."""
    try:
        data = fetch_hot_up_rank_data()
    except Exception as exc:
        return SectionResult(
            display=f"🚀 A 股飙升榜\n(暂不可用：{type(exc).__name__}: {str(exc)[:120]})",
            top20_codes=[], rank_by_code={}, summary_by_code={},
        )
    if not data:
        return SectionResult(
            display="🚀 A 股飙升榜\n(暂不可用：无数据)",
            top20_codes=[], rank_by_code={}, summary_by_code={},
        )
    # data is list of dicts sorted desc by hrc, up to 20 entries
    top5 = data[:5]
    lines = [
        "🚀 A 股关注度飙升榜 — Top 5（来自东方财富，按昨日排名变动降序）",
    ]
    for d in top5:
        chg = d.get("chg_pct")
        chg_str = f"{chg:+.2f}%" if chg is not None else "—"
        rank_now = d.get("rank")
        rank_now_str = f"{rank_now}" if rank_now is not None else "—"
        lines.append(
            f"🔥 {d['code_prefixed']} {d['name']} · 排名 #{rank_now_str} "
            f"(飙升 +{d['hrc']} 位) · {chg_str}"
        )
    top20_codes = [d["code_bare"] for d in data[:20]]
    rank_by_code = {d["code_bare"]: i + 1 for i, d in enumerate(data[:20])}
    summary_by_code = {
        d["code_bare"]: f"{d['name']} 飙升 +{d['hrc']} 位"
        for d in data[:20]
    }
    return SectionResult(
        display="\n".join(lines),
        top20_codes=top20_codes,
        rank_by_code=rank_by_code,
        summary_by_code=summary_by_code,
    )


# ---------------------------------------------------------------------------
# Section B — 龙虎榜 (内部 Top 20, 显示 Top 5)
# ---------------------------------------------------------------------------

def section_b_lhb(curr_date: str) -> SectionResult:
    """龙虎榜: 内部 Top 20 by 净买额 (dedupe by code), 显示 Top 5."""
    try:
        from tradingagents.dataflows import _dep_bootstrap
        ak = _dep_bootstrap.ensure("akshare")
        end_dt = datetime.strptime(curr_date, "%Y-%m-%d")
        start_dt = end_dt - timedelta(days=5)
        df = ak.stock_lhb_detail_em(
            start_date=start_dt.strftime("%Y%m%d"),
            end_date=end_dt.strftime("%Y%m%d"),
        )
        if not isinstance(df, pd.DataFrame) or df.empty:
            return SectionResult(
                display="🐂 A股 龙虎榜 — 近 5 个交易日 Top 5 净买入 (按代码聚合)\n(无数据)",
                top20_codes=[], rank_by_code={}, summary_by_code={},
            )
        # Find columns
        net_col = next(
            (c for c in df.columns if "净买额" in c or "净买入" in c),
            None,
        )
        if net_col is None:
            return SectionResult(
                display="🐂 A股 龙虎榜 — 近 5 个交易日 Top 5 净买入 (按代码聚合)\n(column 龙虎榜净买额 not found)",
                top20_codes=[], rank_by_code={}, summary_by_code={},
            )
        col_code = next((c for c in df.columns if c in ("代码", "股票代码")), None)
        col_name = next((c for c in df.columns if c in ("名称", "股票名称")), None)
        col_date = next((c for c in df.columns if "上榜日" in c or "日期" in c), None)
        col_reason = next((c for c in df.columns if "解读" in c or "上榜原因" in c), None)

        df = df.copy()
        df["净买额_num"] = pd.to_numeric(df[net_col], errors="coerce")
        df = df.dropna(subset=["净买额_num"])

        # Dedupe by code: aggregate top 20
        if col_code:
            def _agg_group(g):
                agg = {
                    "净买额_sum": g["净买额_num"].sum(),
                    "count": len(g),
                }
                if col_name:
                    agg["名称"] = g[col_name].iloc[0]
                if col_date:
                    try:
                        agg["最近上榜日"] = str(g[col_date].max())
                    except Exception:
                        agg["最近上榜日"] = str(g[col_date].iloc[-1])
                if col_reason:
                    agg["解读"] = str(g[col_reason].iloc[-1])
                return pd.Series(agg)

            grp = df.groupby(col_code).apply(_agg_group).reset_index()
            grp = grp.sort_values("净买额_sum", ascending=False).head(20)

            all_rows = []
            for _, r in grp.iterrows():
                code = str(r[col_code])
                name = str(r.get("名称", "N/A"))
                net_yi = r["净买额_sum"] / 1e8
                count = int(r.get("count", 1))
                last_date = str(r.get("最近上榜日", "N/A"))
                reason = str(r.get("解读", ""))[:30]
                all_rows.append({
                    "code": code,
                    "name": name,
                    "net_yi": net_yi,
                    "count": count,
                    "last_date": last_date,
                    "reason": reason,
                })
        else:
            # No code column fallback: sort and take top 20
            top_df = df.sort_values("净买额_num", ascending=False).head(20)
            all_rows = []
            for _, r in top_df.iterrows():
                name = str(r[col_name]) if col_name else "N/A"
                date_val = str(r[col_date]) if col_date else "N/A"
                net_yi = r["净买额_num"] / 1e8
                reason = str(r[col_reason])[:30] if col_reason else "N/A"
                all_rows.append({
                    "code": None,
                    "name": name,
                    "net_yi": net_yi,
                    "count": 1,
                    "last_date": date_val,
                    "reason": reason,
                })

        # Build display (top 5)
        display_rows = []
        for r in all_rows[:5]:
            if r["code"]:
                display_rows.append(
                    f"🐂 {r['code']} {r['name']} · 净买入 +{r['net_yi']:.2f}亿 "
                    f"(上榜 {r['count']} 次, 最近 {r['last_date']}) · {r['reason']}"
                )
            else:
                display_rows.append(
                    f"🐂 {r['name']} · 净买入 +{r['net_yi']:.2f}亿 (最近 {r['last_date']}) · {r['reason']}"
                )

        # Build intersection data from top 20 (bare 6-digit codes only when col_code exists)
        top20_codes = []
        rank_by_code: dict[str, int] = {}
        summary_by_code: dict[str, str] = {}
        if col_code:
            for i, r in enumerate(all_rows[:20]):
                code = r["code"]
                if code:
                    top20_codes.append(code)
                    rank_by_code[code] = i + 1
                    summary_by_code[code] = f"{r['name']} · 净买入+{r['net_yi']:.2f}亿"

        return SectionResult(
            display=(
                "🐂 A股 龙虎榜 — 近 5 个交易日 Top 5 净买入 (按代码聚合)\n"
                + "\n".join(display_rows)
            ),
            top20_codes=top20_codes,
            rank_by_code=rank_by_code,
            summary_by_code=summary_by_code,
        )
    except Exception as exc:
        return SectionResult(
            display=f"🐂 A股 龙虎榜\n\n(暂不可用： {type(exc).__name__}: {str(exc)[:100]})",
            top20_codes=[], rank_by_code={}, summary_by_code={},
        )


# ---------------------------------------------------------------------------
# Backward-compat alias (tests still call section_b_lhb_top5)
# ---------------------------------------------------------------------------

def section_b_lhb_top5(curr_date: str) -> str:
    """Backward-compat alias: returns display string only."""
    return section_b_lhb(curr_date).display


# ---------------------------------------------------------------------------
# Section C — 雪球飙升榜 (内部 Top 20, 显示 Top 5)
# ---------------------------------------------------------------------------

def section_c_xueqiu_surge() -> SectionResult:
    """雪球飙升: 内部 Top 20, 显示 Top 5.

    Computes "本周新增" rank vs "最热门" rank delta — stocks that suddenly
    surged in 本周新增 relative to their cumulative hot rank.
    """
    try:
        df_hot = _get_xueqiu_cached("最热门")
        df_weekly = _get_xueqiu_cached("本周新增")
        if _df_is_empty(df_hot) or _df_is_empty(df_weekly):
            return SectionResult(
                display="📈 雪球飙升榜\n(暂不可用： xueqiu data not loaded)",
                top20_codes=[], rank_by_code={}, summary_by_code={},
            )

        col_sym_hot = next((c for c in df_hot.columns if "代码" in c), None)
        col_sym_weekly = next((c for c in df_weekly.columns if "代码" in c), None)
        col_name = next((c for c in df_weekly.columns if "名称" in c or "简称" in c), None)
        col_follow = next((c for c in df_weekly.columns if "关注" in c), None)
        if not col_sym_hot or not col_sym_weekly:
            return SectionResult(
                display="📈 雪球飙升榜\n(暂不可用： 代码 column not found)",
                top20_codes=[], rank_by_code={}, summary_by_code={},
            )

        # Build rank maps
        hot_rank = {row[col_sym_hot]: i + 1 for i, (_, row) in enumerate(df_hot.iterrows())}
        weekly_rank = {row[col_sym_weekly]: i + 1 for i, (_, row) in enumerate(df_weekly.iterrows())}

        # Compute surge for each ticker in weekly Top 200, filter hot_rank > 50
        surges = []
        weekly_top = df_weekly.head(200)
        for _, r in weekly_top.iterrows():
            code = r[col_sym_weekly]
            w_rank = weekly_rank.get(code)
            h_rank = hot_rank.get(code)
            if not w_rank or not h_rank:
                continue
            if h_rank <= 50:
                continue
            surge = h_rank - w_rank
            if surge <= 0:
                continue
            surges.append({
                "code": code,
                "name": str(r[col_name]) if col_name else "",
                "hot_rank": h_rank,
                "weekly_rank": w_rank,
                "surge": surge,
                "follow": r[col_follow] if col_follow else None,
            })

        surges.sort(key=lambda x: x["surge"], reverse=True)
        top20 = surges[:20]

        if not top20:
            return SectionResult(
                display="📈 雪球飙升榜 (无新晋飙升标的，老热门主导)",
                top20_codes=[], rank_by_code={}, summary_by_code={},
            )

        # Strip SH/SZ/BJ prefix to get bare 6-digit code for intersection
        def _bare(code_str: str) -> str:
            if isinstance(code_str, str) and len(code_str) >= 8 and code_str[:2].upper() in ("SH", "SZ", "BJ"):
                return code_str[2:]
            return code_str

        # Build display (top 5)
        lines = ["📈 雪球飙升榜 — 散户讨论排名突然蹿升的新晋热门 Top 5"]
        for s in top20[:5]:
            follow_str = ""
            if s["follow"] is not None:
                try:
                    follow_str = f" · 关注 {int(float(s['follow'])):,}"
                except (TypeError, ValueError):
                    pass
            lines.append(
                f"🔥 {s['code']} {s['name']} · 本周#{s['weekly_rank']} vs 累计#{s['hot_rank']} "
                f"(飙升 +{s['surge']}){follow_str}"
            )

        # Build intersection data from top 20
        top20_codes = [_bare(s["code"]) for s in top20]
        rank_by_code = {_bare(s["code"]): i + 1 for i, s in enumerate(top20)}
        summary_by_code = {
            _bare(s["code"]): f"{s['name']} 本周#{s['weekly_rank']} 累计#{s['hot_rank']} 飙升+{s['surge']}"
            for s in top20
        }

        return SectionResult(
            display="\n".join(lines),
            top20_codes=top20_codes,
            rank_by_code=rank_by_code,
            summary_by_code=summary_by_code,
        )
    except Exception as exc:
        return SectionResult(
            display=f"📈 雪球飙升榜\n(暂不可用： {type(exc).__name__}: {str(exc)[:120]})",
            top20_codes=[], rank_by_code={}, summary_by_code={},
        )


# ---------------------------------------------------------------------------
# Section D — StockTwits Top 5 (内部 Top 20 parsed, 显示 Top 5)
# ---------------------------------------------------------------------------

_ST_NUMBERED_RE = re.compile(r"^(\d+)\.\s+([A-Z]{1,5})\s+")


def section_d_stocktwits() -> SectionResult:
    """StockTwits: 内部 Top 20 美股 ticker, 显示 Top 5."""
    try:
        raw_md = fetch_stocktwits_trending(limit=20)
    except Exception as exc:
        return SectionResult(
            display=f"(暂不可用： {type(exc).__name__}: {str(exc)[:120]})",
            top20_codes=[], rank_by_code={}, summary_by_code={},
        )

    # Parse tickers from numbered lines e.g. "1. AAPL NASDAQ · Apple Inc"
    parsed: list[tuple[int, str, str]] = []  # (line_num, ticker, rest)
    for line in raw_md.splitlines():
        m = _ST_NUMBERED_RE.match(line)
        if m:
            line_num = int(m.group(1))
            ticker = m.group(2)
            rest = line[m.end():].strip()
            parsed.append((line_num, ticker, rest))

    if not parsed:
        # raw_md is an error/empty placeholder — return it as display
        return SectionResult(
            display=raw_md,
            top20_codes=[], rank_by_code={}, summary_by_code={},
        )

    # Build display: Top 5 with our own header (not the Top 20 header from raw_md)
    top5_numbered = sorted(parsed, key=lambda x: x[0])[:5]
    from datetime import datetime as _dt, timezone as _tz
    now_utc = _dt.now(_tz.utc).strftime("%Y-%m-%d %H:%M:%S")
    display_lines = [f"🇺🇸 StockTwits 美股热议榜 — Top 5（获取于 {now_utc} UTC）"]
    for i, (_, ticker, rest) in enumerate(top5_numbered, 1):
        display_lines.append(f"{i}. {ticker} {rest}")

    top20 = sorted(parsed, key=lambda x: x[0])[:20]
    top20_codes = [ticker for _, ticker, _ in top20]
    rank_by_code = {ticker: line_num for line_num, ticker, _ in top20}
    summary_by_code = {ticker: rest for _, ticker, rest in top20}

    return SectionResult(
        display="\n".join(display_lines),
        top20_codes=top20_codes,
        rank_by_code=rank_by_code,
        summary_by_code=summary_by_code,
    )


# ---------------------------------------------------------------------------
# Section D — backward-compat function name
# ---------------------------------------------------------------------------

def section_d_stocktwits_trending() -> str:
    """StockTwits Trending Top 5 — 美股 attention discovery (backward compat)."""
    return section_d_stocktwits().display


# ---------------------------------------------------------------------------
# Section E — Multi-source intersection (A股 Top 20 cross-source)
# ---------------------------------------------------------------------------

def section_e_intersection(
    sec_a: SectionResult, sec_b: SectionResult, sec_c: SectionResult
) -> str:
    """多源交集 (Top 20 cross-source): 三源命中 / 双源命中 分类显示.
    StockTwits 是美股，不参与 A股交集.
    """
    set_a = set(sec_a.top20_codes)
    set_b = set(sec_b.top20_codes)
    set_c = set(sec_c.top20_codes)

    triple = set_a & set_b & set_c          # 三源命中
    only_ab = (set_a & set_b) - set_c       # 飙升榜 ∩ 龙虎榜 only
    only_ac = (set_a & set_c) - set_b       # 飙升榜 ∩ 雪球飙升 only
    only_bc = (set_b & set_c) - set_a       # 龙虎榜 ∩ 雪球飙升 only

    if not (triple or only_ab or only_ac or only_bc):
        return "🌟 多源交集（A 股 Top 20 加权）\n(本日无多源命中标的)"

    lines = ["🌟 多源交集（A 股 Top 20 加权）"]
    if triple:
        lines.append("")
        lines.append("⭐ 三源命中（最强信号 — 散户+机构+雪球散户同向）:")
        for code in sorted(triple, key=lambda c: sec_a.rank_by_code.get(c, 99)):
            a_summary = sec_a.summary_by_code.get(code, "")
            b_summary = sec_b.summary_by_code.get(code, "")
            c_summary = sec_c.summary_by_code.get(code, "")
            lines.append(
                f"  ⭐ {code} — 飙升榜#{sec_a.rank_by_code.get(code, '?')} · "
                f"龙虎榜#{sec_b.rank_by_code.get(code, '?')} · "
                f"雪球飙升#{sec_c.rank_by_code.get(code, '?')}"
            )
            lines.append(
                (f"     {a_summary} / {b_summary} / {c_summary}")[:150]
            )
    if only_ab:
        lines.append("")
        lines.append("🔥 双源命中 飙升榜 ∩ 龙虎榜 (散户 + 机构同向):")
        for code in sorted(only_ab, key=lambda c: sec_a.rank_by_code.get(c, 99)):
            lines.append(
                f"  🔥 {code} — 飙升榜#{sec_a.rank_by_code.get(code, '?')} · "
                f"龙虎榜#{sec_b.rank_by_code.get(code, '?')}"
            )
            lines.append(
                (f"     {sec_a.summary_by_code.get(code, '')} / "
                 f"{sec_b.summary_by_code.get(code, '')}")[:150]
            )
    if only_ac:
        lines.append("")
        lines.append("🔥 双源命中 飙升榜 ∩ 雪球飙升 (双源散户关注度):")
        for code in sorted(only_ac, key=lambda c: sec_a.rank_by_code.get(c, 99)):
            lines.append(
                f"  🔥 {code} — 飙升榜#{sec_a.rank_by_code.get(code, '?')} · "
                f"雪球飙升#{sec_c.rank_by_code.get(code, '?')}"
            )
            lines.append(
                (f"     {sec_a.summary_by_code.get(code, '')} / "
                 f"{sec_c.summary_by_code.get(code, '')}")[:150]
            )
    if only_bc:
        lines.append("")
        lines.append("🔥 双源命中 龙虎榜 ∩ 雪球飙升 (大资金 + 散户同向):")
        for code in sorted(only_bc, key=lambda c: sec_b.rank_by_code.get(c, 99)):
            lines.append(
                f"  🔥 {code} — 龙虎榜#{sec_b.rank_by_code.get(code, '?')} · "
                f"雪球飙升#{sec_c.rank_by_code.get(code, '?')}"
            )
            lines.append(
                (f"     {sec_b.summary_by_code.get(code, '')} / "
                 f"{sec_c.summary_by_code.get(code, '')}")[:150]
            )
    return "\n".join(lines)


def compute_intersection(
    sec_a: SectionResult, sec_b: SectionResult, sec_c: SectionResult,
) -> dict:
    """Structured intersection result for snapshot JSON / analysis dispatch.

    Returns dict with 4 tier keys: triple, ab_only, ac_only, bc_only.
    Each value is a sorted list of bare 6-digit A-share codes.
    """
    set_a = set(sec_a.top20_codes)
    set_b = set(sec_b.top20_codes)
    set_c = set(sec_c.top20_codes)
    triple = set_a & set_b & set_c
    return {
        "triple": sorted(triple),
        "ab_only": sorted((set_a & set_b) - set_c),
        "ac_only": sorted((set_a & set_c) - set_b),
        "bc_only": sorted((set_b & set_c) - set_a),
    }


# ---------------------------------------------------------------------------
# Report builder
# ---------------------------------------------------------------------------

def build_report(curr_date: str) -> str:
    """Aggregate all sections into a report string."""
    sec_a = section_a_hot_up_rank()
    sec_b = section_b_lhb(curr_date)
    sec_c = section_c_xueqiu_surge()
    sec_d = section_d_stocktwits()
    intersection_block = section_e_intersection(sec_a, sec_b, sec_c)

    sections = [
        f"# 散户情绪扫盘 — {curr_date}",
        f"_生成于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_",
        "",
        sec_a.display,
        "",
        sec_b.display,
        "",
        sec_c.display,
        "",
        sec_d.display,
        "",
        intersection_block,
        "",
        "━━━ 📋 多源加权（30 秒决策参考）━━━",
        "• A 股飙升榜 ∩ 龙虎榜机构买入 = 散户+机构同向 = 最强信号",
        "• A 股飙升榜 ∩ 雪球飙升榜 = 双源散户关注度验证 = 强信号",
        "• 三源命中 = 飙升榜+龙虎榜+雪球同向 = 最高置信",
        "• 美股 StockTwits 热议榜 → 配 Google Trends + StockTwits 个股看多/看空",
    ]
    return "\n".join(sections)


# ---------------------------------------------------------------------------
# 飞书 post 富文本 converter
# ---------------------------------------------------------------------------

# Prefixed form: SH600519, sz000725, BJ430047 etc.
_A_SHARE_PREFIXED_RE = re.compile(r"\b([SsBb][HhZzJj])(\d{6})\b")
# Bare form: 600519, 000725, 430047 (must not be preceded/followed by digit)
_A_SHARE_BARE_RE = re.compile(r"(?<![\d.])([036489]\d{5})(?!\d)")

# US ticker: numbered list line  e.g. "1. RDW NYSE · Redwire Corp"
_US_TICKER_NUMBERED_RE = re.compile(r"^\d+\.\s+([A-Z]{1,5})\s+")


def _detect_a_share_prefix(code: str) -> str:
    """Return SH or SZ prefix for a 6-digit A-share code."""
    if code[:2] in ("60", "68", "90"):
        return "SH"
    if code[:2] in ("00", "30", "20"):
        return "SZ"
    if code[:1] in ("8", "4"):
        return "BJ"
    return "SH"


def _parse_line_to_feishu_elements(line: str, in_stocktwits_section: bool) -> list:
    """Convert a text line to a list of 飞书 rich-text element dicts.

    Links:
    - A股 prefixed codes (SH600519) → xueqiu.com/S/SH600519
    - A股 bare 6-digit codes (600519) → xueqiu.com/S/SH600519 (prefix auto-detected)
    - US tickers from StockTwits numbered lines → stocktwits.com/symbol/{ticker}
    """
    if not line.strip():
        return []

    elements = []
    remaining = line

    # Detect US ticker in numbered list line  e.g. "1. RDW NYSE · Redwire Corp"
    if in_stocktwits_section:
        us_match = _US_TICKER_NUMBERED_RE.match(line)
        if us_match:
            ticker = us_match.group(1)
            pre = remaining[: us_match.start(1)]
            post = remaining[us_match.end(1):]
            if pre:
                elements.append({"tag": "text", "text": pre})
            elements.append({
                "tag": "a",
                "text": ticker,
                "href": f"https://stocktwits.com/symbol/{ticker}",
            })
            remaining = post

    # Scan remaining text for A-share codes.
    # Strategy: find all prefixed matches and all bare matches, merge them
    # by position, preferring the longer prefixed match when they overlap.
    matches = []

    for m in _A_SHARE_PREFIXED_RE.finditer(remaining):
        prefix_str = m.group(1).upper()  # SH/SZ/BJ
        code = m.group(2)
        # Normalise prefix: map SH→SH, SZ→SZ, BJ→BJ (already uppercase first letter)
        prefix_norm = prefix_str[:1].upper() + prefix_str[1:].upper()
        matches.append((m.start(), m.end(), m.group(0), f"https://xueqiu.com/S/{prefix_norm}{code}"))

    # Build set of positions already covered by prefixed matches
    covered = set()
    for start, end, _, _ in matches:
        for pos in range(start, end):
            covered.add(pos)

    for m in _A_SHARE_BARE_RE.finditer(remaining):
        # Skip if overlaps with a prefixed match
        if any(pos in covered for pos in range(m.start(), m.end())):
            continue
        code = m.group(1)
        prefix = _detect_a_share_prefix(code)
        matches.append((m.start(), m.end(), m.group(0), f"https://xueqiu.com/S/{prefix}{code}"))

    # Sort by start position
    matches.sort(key=lambda x: x[0])

    last = 0
    for start, end, text, href in matches:
        if start > last:
            elements.append({"tag": "text", "text": remaining[last:start]})
        elements.append({"tag": "a", "text": text, "href": href})
        last = end
    if last < len(remaining):
        tail = remaining[last:]
        if tail:
            elements.append({"tag": "text", "text": tail})

    if not elements:
        elements.append({"tag": "text", "text": line})

    return elements


def convert_to_feishu_post(markdown_report: str, curr_date: str) -> dict:
    """Convert the report into a 飞书 post 富文本 payload dict.

    Structure:
      {"msg_type": "post", "content": {"post": {"zh_cn": {"title": ..., "content": [...]}}}}

    Each non-empty line becomes a paragraph (list of elements).
    A-share tickers (prefixed SH/SZ/BJ or bare 6-digit) get xueqiu links.
    StockTwits US tickers in numbered lines get stocktwits.com links.
    """
    lines = markdown_report.splitlines()
    paragraphs: list[list] = []

    in_stocktwits = False

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        # Track which section we're in for US ticker linking
        if "StockTwits" in stripped:
            in_stocktwits = True
        elif stripped.startswith("🚀") or stripped.startswith("🐂") or stripped.startswith("📈") or stripped.startswith("🌟") or stripped.startswith("━━"):
            in_stocktwits = False

        # Top-level # header (skip — used as title only)
        if stripped.startswith("# ") and not stripped.startswith("## "):
            continue

        # Italic timestamp line (skip — not useful in feishu)
        if stripped.startswith("_生成于"):
            continue

        elements = _parse_line_to_feishu_elements(line, in_stocktwits)
        if elements:
            paragraphs.append(elements)

    title = f"散户情绪扫盘 {curr_date}"
    return {
        "msg_type": "post",
        "content": {
            "post": {
                "zh_cn": {
                    "title": title,
                    "content": paragraphs,
                }
            }
        },
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Daily retail-attention scan: A股 飙升榜/龙虎榜/雪球 + 美股 StockTwits Trending"
    )
    parser.add_argument(
        "--date",
        default=datetime.now().strftime("%Y-%m-%d"),
        help="Reference date in YYYY-MM-DD format (default: today)",
    )
    parser.add_argument(
        "--no-feishu",
        action="store_true",
        help="Skip 飞书 webhook push even if TRADINGAGENTS_FEISHU_WEBHOOK is set",
    )
    parser.add_argument(
        "--feishu-only",
        action="store_true",
        help="Skip stdout output, only push to 飞书 webhook",
    )
    args = parser.parse_args()

    report_md = build_report(args.date)

    if not args.feishu_only:
        print(report_md)

    webhook = os.environ.get("TRADINGAGENTS_FEISHU_WEBHOOK")
    if webhook and not args.no_feishu:
        try:
            payload = convert_to_feishu_post(report_md, args.date)
            import requests
            r = requests.post(webhook, json=payload, timeout=10)
            resp_json = {}
            try:
                resp_json = r.json()
            except Exception:
                pass
            if r.status_code != 200 or resp_json.get("code") != 0:
                print(
                    f"[warning] 飞书 webhook returned {r.status_code}: {r.text[:200]}",
                    file=sys.stderr,
                )
        except Exception as exc:
            print(f"[warning] 飞书 push failed: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
