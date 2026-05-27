#!/usr/bin/env python3
"""Daily retail-attention scan across A股 (飙升榜/龙虎榜/雪球飙升榜) and 美股
(StockTwits Trending Equities). Outputs compact emoji-prefixed lines to stdout
for human review; optionally posts a 飞书 post-format message if
TRADINGAGENTS_FEISHU_WEBHOOK env var is set.

Usage:
    python scripts/daily_sentiment_scan.py [--date YYYY-MM-DD] [--no-feishu]

Designed for daily cron / LaunchAgent invocation. Every section is
fail-isolated: a failure in one source produces an inline "(unavailable: ...)"
placeholder but does NOT block the other sections.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timedelta

import pandas as pd

from tradingagents.dataflows.akshare_china import (
    get_hot_up_rank,
    _get_xueqiu_cached,
    _XUEQIU_CACHE,
    _df_is_empty,
)
from tradingagents.dataflows.stocktwits import fetch_stocktwits_trending


# ---------------------------------------------------------------------------
# Section A — 飙升榜
# ---------------------------------------------------------------------------

def section_a_hot_up_rank() -> str:
    """飙升榜 — 散户突然涌入的标的."""
    try:
        return get_hot_up_rank()
    except Exception as exc:
        return f"(unavailable: {type(exc).__name__}: {str(exc)[:120]})"


# ---------------------------------------------------------------------------
# Section B — 龙虎榜 Top 5 净买入 (deduplicated by code)
# ---------------------------------------------------------------------------

def section_b_lhb_top5(curr_date: str) -> str:
    """龙虎榜 Top 5 净买入 — 大资金动向 (dedupe by code, sum 净买额)."""
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
            return "🐂 A股 龙虎榜 — 近 5 个交易日 Top 5 净买入 (按代码聚合)\n\n(no data)"
        # Find columns
        net_col = next(
            (c for c in df.columns if "净买额" in c or "净买入" in c),
            None,
        )
        if net_col is None:
            return "🐂 A股 龙虎榜 — 近 5 个交易日 Top 5 净买入 (按代码聚合)\n\n(column 龙虎榜净买额 not found)"
        col_code = next((c for c in df.columns if c in ("代码", "股票代码")), None)
        col_name = next((c for c in df.columns if c in ("名称", "股票名称")), None)
        col_date = next((c for c in df.columns if "上榜日" in c or "日期" in c), None)
        col_reason = next((c for c in df.columns if "解读" in c or "上榜原因" in c), None)

        df = df.copy()
        df["净买额_num"] = pd.to_numeric(df[net_col], errors="coerce")
        df = df.dropna(subset=["净买额_num"])

        # Dedupe by code: aggregate
        if col_code:
            def _agg_group(g):
                agg = {
                    "净买额_sum": g["净买额_num"].sum(),
                    "count": len(g),
                }
                if col_name:
                    agg["名称"] = g[col_name].iloc[0]
                if col_date:
                    # Most recent date
                    try:
                        agg["最近上榜日"] = str(g[col_date].max())
                    except Exception:
                        agg["最近上榜日"] = str(g[col_date].iloc[-1])
                if col_reason:
                    agg["解读"] = str(g[col_reason].iloc[-1])
                return pd.Series(agg)

            grp = df.groupby(col_code).apply(_agg_group).reset_index()
            grp = grp.sort_values("净买额_sum", ascending=False).head(5)

            rows = []
            for _, r in grp.iterrows():
                code = str(r[col_code])
                name = str(r.get("名称", "N/A"))
                net_yi = r["净买额_sum"] / 1e8
                count = int(r.get("count", 1))
                last_date = str(r.get("最近上榜日", "N/A"))
                reason = str(r.get("解读", ""))[:30]
                rows.append(
                    f"🐂 {code} {name} · 净买入 +{net_yi:.2f}亿 (上榜 {count} 次, 最近 {last_date}) · {reason}"
                )
        else:
            # No code column fallback: just sort and take top 5
            top = df.sort_values("净买额_num", ascending=False).head(5)
            rows = []
            for _, r in top.iterrows():
                name = str(r[col_name]) if col_name else "N/A"
                date_val = str(r[col_date]) if col_date else "N/A"
                net_yi = r["净买额_num"] / 1e8
                reason = str(r[col_reason])[:30] if col_reason else "N/A"
                rows.append(
                    f"🐂 {name} · 净买入 +{net_yi:.2f}亿 (最近 {date_val}) · {reason}"
                )

        return (
            "🐂 A股 龙虎榜 — 近 5 个交易日 Top 5 净买入 (按代码聚合)\n"
            + "\n".join(rows)
        )
    except Exception as exc:
        return f"🐂 A股 龙虎榜\n\n(unavailable: {type(exc).__name__}: {str(exc)[:100]})"


# ---------------------------------------------------------------------------
# Section C — 雪球飙升榜 (rank delta signal)
# ---------------------------------------------------------------------------

def section_c_xueqiu_surge_top15() -> str:
    """雪球飙升榜 — 计算 "本周新增" rank vs "最热门" rank 的差值，挑出新晋飙升股。
    一只股票在 本周新增 排名靠前但在 累计最热门 排名靠后 = 散户突然涌入讨论 = 雪球版 attention spike。
    """
    try:
        df_hot = _get_xueqiu_cached("最热门")
        df_weekly = _get_xueqiu_cached("本周新增")
        if _df_is_empty(df_hot) or _df_is_empty(df_weekly):
            return "📈 雪球飙升榜\n(unavailable: xueqiu data not loaded)"

        col_sym_hot = next((c for c in df_hot.columns if "代码" in c), None)
        col_sym_weekly = next((c for c in df_weekly.columns if "代码" in c), None)
        col_name = next((c for c in df_weekly.columns if "名称" in c or "简称" in c), None)
        col_follow = next((c for c in df_weekly.columns if "关注" in c), None)
        if not col_sym_hot or not col_sym_weekly:
            return "📈 雪球飙升榜\n(unavailable: 代码 column not found)"

        # Build rank maps
        hot_rank = {row[col_sym_hot]: i + 1 for i, (_, row) in enumerate(df_hot.iterrows())}
        weekly_rank = {row[col_sym_weekly]: i + 1 for i, (_, row) in enumerate(df_weekly.iterrows())}

        # For each ticker in weekly Top 200, compute surge = hot_rank - weekly_rank.
        # Filter: only include tickers whose hot_rank > 50 (filter out old megacaps that are always hot).
        surges = []
        weekly_top = df_weekly.head(200)  # limit search space
        for _, r in weekly_top.iterrows():
            code = r[col_sym_weekly]
            w_rank = weekly_rank.get(code)
            h_rank = hot_rank.get(code)
            if not w_rank or not h_rank:
                continue
            if h_rank <= 50:  # 老热门，no surge signal
                continue
            surge = h_rank - w_rank
            if surge <= 0:
                continue  # 本周比累计还慢，反向退潮，跳过
            surges.append({
                "code": code,
                "name": str(r[col_name]) if col_name else "",
                "hot_rank": h_rank,
                "weekly_rank": w_rank,
                "surge": surge,
                "follow": r[col_follow] if col_follow else None,
            })

        surges.sort(key=lambda x: x["surge"], reverse=True)
        top = surges[:15]

        if not top:
            return "📈 雪球飙升榜 (无新晋飙升标的，老热门主导)"

        lines = []
        for s in top:
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

        return (
            "📈 雪球飙升榜 — 散户讨论排名突然蹿升的新晋热门 Top 15\n"
            + "\n".join(lines)
        )
    except Exception as exc:
        return f"📈 雪球飙升榜\n(unavailable: {type(exc).__name__}: {str(exc)[:120]})"


# ---------------------------------------------------------------------------
# Section D — StockTwits Trending Top 10
# ---------------------------------------------------------------------------

def section_d_stocktwits_trending() -> str:
    """StockTwits Trending Top 10 — 美股 attention discovery."""
    try:
        return fetch_stocktwits_trending(limit=10)
    except Exception as exc:
        return f"(unavailable: {type(exc).__name__}: {str(exc)[:120]})"


# ---------------------------------------------------------------------------
# Report builder
# ---------------------------------------------------------------------------

def build_report(curr_date: str) -> str:
    """Aggregate all 4 sections into a report string."""
    sections = [
        f"# 散户情绪扫盘 — {curr_date}",
        f"_生成于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_",
        "",
        section_a_hot_up_rank(),
        "",
        section_b_lhb_top5(curr_date),
        "",
        section_c_xueqiu_surge_top15(),
        "",
        section_d_stocktwits_trending(),
        "",
        "━━━ 📋 Cross-source 加权（30 秒决策）━━━",
        "• A股飙升榜 ∩ 龙虎榜机构买入 = 散户+机构同向 = 最强信号",
        "• A股飙升榜 ∩ 雪球飙升榜 = 双 retail attention 验证 = 强信号",
        "• A股飙升榜 ∩ 涨停板同板块 = 主题主升浪",
        "• 美股 StockTwits trending → 配 Google Trends + StockTwits 个股 bull/bear",
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
        if "StockTwits Trending" in stripped:
            in_stocktwits = True
        elif stripped.startswith("🚀") or stripped.startswith("🐂") or stripped.startswith("📈") or stripped.startswith("━━"):
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
