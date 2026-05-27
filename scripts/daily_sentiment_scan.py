#!/usr/bin/env python3
"""Daily retail-attention scan across A股 (飙升榜/龙虎榜/雪球本周新增) and 美股
(StockTwits Trending Equities). Outputs a markdown table to stdout for
human review; optionally posts a 飞书 post-format message if
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
# Section B — 龙虎榜 Top 5 净买入
# ---------------------------------------------------------------------------

def section_b_lhb_top5(curr_date: str) -> str:
    """龙虎榜 Top 5 净买入 — 大资金动向."""
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
            return "## A股 龙虎榜 — Top 5 净买入（近 5 个交易日，东方财富）\n\n(no data)"
        # Find net-buy column
        net_col = next(
            (c for c in df.columns if "净买额" in c or "净买入" in c),
            None,
        )
        if net_col is None:
            return "## A股 龙虎榜 — Top 5 净买入（近 5 个交易日，东方财富）\n\n(column 龙虎榜净买额 not found)"
        df["净买额_num"] = pd.to_numeric(df[net_col], errors="coerce")
        top = (
            df.dropna(subset=["净买额_num"])
            .sort_values("净买额_num", ascending=False)
            .head(5)
        )
        # Find helper columns
        col_code = next((c for c in df.columns if c in ("代码", "股票代码")), None)
        col_name = next((c for c in df.columns if c in ("名称", "股票名称")), None)
        col_date = next((c for c in df.columns if "上榜日" in c or "日期" in c), None)
        col_reason = next((c for c in df.columns if "解读" in c or "上榜原因" in c), None)
        rows = []
        for _, r in top.iterrows():
            code = str(r[col_code]) if col_code else "N/A"
            name = str(r[col_name]) if col_name else "N/A"
            date_val = str(r[col_date]) if col_date else "N/A"
            net_yi = r["净买额_num"] / 1e8
            reason = str(r[col_reason])[:30] if col_reason else "N/A"
            rows.append(f"| {code} | {name} | {date_val} | +{net_yi:.2f}亿 | {reason} |")
        return (
            "## A股 龙虎榜 — Top 5 净买入（近 5 个交易日，东方财富）\n\n"
            "| 代码 | 名称 | 上榜日 | 净买额 | 解读 |\n"
            "| -- | -- | -- | -- | -- |\n"
            + "\n".join(rows)
        )
    except Exception as exc:
        return f"## A股 龙虎榜\n\n(unavailable: {type(exc).__name__}: {str(exc)[:100]})"


# ---------------------------------------------------------------------------
# Section C — 雪球本周新增 Top 20
# ---------------------------------------------------------------------------

def section_c_xueqiu_weekly_top20() -> str:
    """雪球本周新增 Top 20 — A股 narrative 持续性 filter."""
    try:
        df = _get_xueqiu_cached("本周新增")
        if _df_is_empty(df):
            return "## 雪球本周新增 Top 20\n\n(unavailable: no data returned)"
        col_sym = next((c for c in df.columns if "代码" in c), None)
        col_name = next((c for c in df.columns if "名称" in c), None)
        col_follow = next((c for c in df.columns if "关注" in c), None)
        if col_sym is None:
            return "## 雪球本周新增 Top 20\n\n(unavailable: 代码 column not found)"
        df_sorted = df.copy()
        if col_follow:
            df_sorted["_follow_num"] = pd.to_numeric(df_sorted[col_follow], errors="coerce").fillna(0)
            df_sorted = df_sorted.sort_values("_follow_num", ascending=False)
        top20 = df_sorted.head(20)
        header_parts = ["代码"]
        if col_name:
            header_parts.append("名称")
        if col_follow:
            header_parts.append("本周新增关注")
        header = "| " + " | ".join(header_parts) + " |"
        sep = "| " + " | ".join(["--"] * len(header_parts)) + " |"
        rows = []
        for _, r in top20.iterrows():
            parts = [str(r[col_sym])]
            if col_name:
                parts.append(str(r[col_name]))
            if col_follow:
                parts.append(str(r[col_follow]))
            rows.append("| " + " | ".join(parts) + " |")
        return (
            "## 雪球本周新增 Top 20（A股散户关注度持续性）\n\n"
            + header + "\n" + sep + "\n"
            + "\n".join(rows)
        )
    except Exception as exc:
        return f"## 雪球本周新增 Top 20\n\n(unavailable: {type(exc).__name__}: {str(exc)[:120]})"


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
    """Aggregate all 4 sections into a markdown report string."""
    report = [
        f"# 散户情绪扫盘 — {curr_date}",
        f"_生成于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_",
        "",
        "---",
        "",
        "## A股 飙升榜 — 散户 attention 突变（东方财富）",
        "",
        section_a_hot_up_rank(),
        "",
        section_b_lhb_top5(curr_date),
        "",
        section_c_xueqiu_weekly_top20(),
        "",
        section_d_stocktwits_trending(),
        "",
        "---",
        "**Cross-source 加权（30 秒决策）**：",
        "- A股飙升榜 ∩ 龙虎榜机构买入 = 散户+机构同向 = 最强信号",
        "- A股飙升榜 ∩ 雪球本周新增 = retail momentum 持续",
        "- A股飙升榜 ∩ 涨停板同板块 = 主题主升浪",
        "- 美股 StockTwits trending → 配 Google Trends + StockTwits 个股 bull/bear",
    ]
    return "\n".join(report)


# ---------------------------------------------------------------------------
# 飞书 post 富文本 converter
# ---------------------------------------------------------------------------

_A_SHARE_RE = re.compile(r"\b([036]\d{5})\b")
_US_TICKER_RE = re.compile(r"\|\s*\d+\s*\|\s*([A-Z]{1,5})\s*\|")
_SECTION_HEADER_RE = re.compile(r"^##\s+(.+)$")


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
    - A股 6-digit codes → xueqiu.com/S/SH|SZ{code}
    - US tickers (from StockTwits section table rows) → stocktwits.com/symbol/{ticker}
    """
    if not line.strip():
        return []

    # Detect US ticker table row  e.g. "| 1 | RDW | NYSE | Redwire Corp |"
    us_match = _US_TICKER_RE.search(line) if in_stocktwits_section else None

    elements = []
    remaining = line

    if us_match:
        ticker = us_match.group(1)
        pre = remaining[: us_match.start(1)]
        post = remaining[us_match.end(1) :]
        if pre:
            elements.append({"tag": "text", "text": pre})
        elements.append({
            "tag": "a",
            "text": ticker,
            "href": f"https://stocktwits.com/symbol/{ticker}",
        })
        remaining = post

    # Scan remaining text for A-share 6-digit codes
    last = 0
    for m in _A_SHARE_RE.finditer(remaining):
        code = m.group(1)
        prefix = _detect_a_share_prefix(code)
        if m.start() > last:
            elements.append({"tag": "text", "text": remaining[last : m.start()]})
        elements.append({
            "tag": "a",
            "text": code,
            "href": f"https://xueqiu.com/S/{prefix}{code}",
        })
        last = m.end()
    if last < len(remaining):
        tail = remaining[last:]
        if tail:
            elements.append({"tag": "text", "text": tail})

    if not elements:
        elements.append({"tag": "text", "text": line})

    return elements


def convert_to_feishu_post(markdown_report: str, curr_date: str) -> dict:
    """Convert the markdown report into a 飞书 post 富文本 payload dict.

    Structure:
      {"msg_type": "post", "content": {"post": {"zh_cn": {"title": ..., "content": [...]}}}}

    Each non-empty line becomes a paragraph (list of elements).
    Section headers (##) are bolded. A-share tickers get xueqiu links;
    StockTwits US tickers get stocktwits.com links.
    """
    lines = markdown_report.splitlines()
    paragraphs: list[list] = []

    in_stocktwits = False

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        # Track which section we're in for US ticker linking
        if "StockTwits Trending" in stripped or "# StockTwits Trending" in stripped:
            in_stocktwits = True
        elif stripped.startswith("## ") or stripped.startswith("# 散"):
            if "StockTwits" not in stripped:
                in_stocktwits = False

        # Section header → bold text element
        hdr_m = _SECTION_HEADER_RE.match(line)
        if hdr_m:
            paragraphs.append([{"tag": "text", "text": f"【{hdr_m.group(1)}】"}])
            continue

        # Top-level # header (skip — used as title)
        if stripped.startswith("# ") and not stripped.startswith("## "):
            continue

        # Separator lines
        if stripped == "---":
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
