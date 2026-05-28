"""Shared 飞书 post element builders.

Extracted from scripts/daily_sentiment_scan.py to break the circular import
between scripts/daily_sentiment_scan.py and feishu_post_v2.py (they both
need _parse_line_to_feishu_elements; this neutral module is the single
source of truth they both depend on).
"""
from __future__ import annotations

import re

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
