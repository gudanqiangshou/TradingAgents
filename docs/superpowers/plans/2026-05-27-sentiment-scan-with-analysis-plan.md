# Sentiment Scan with Per-Ticker TradingAgents Analysis — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Auto-analyze every multi-source-intersection-hit A-share ticker (triple + double hits) with TradingAgents (fundamentals + news analysts), inline BUY/HOLD/SELL + PE/远期PE/ROE/FCF into a freshly-formatted 飞书 post message. Cron-split into 06:30 `--analyze` + 09:05 `--push` LaunchAgents.

**Architecture:** Two-LaunchAgent split with a JSON snapshot at `~/.tradingagents/sentiment-scan/<DATE>.json` as the only cross-process state. Analysis process imports TradingAgents directly (does NOT go through the web service — no SSE/job_capacity coupling). Per-ticker hard watchdog (30 min) + global hard deadline (08:50) for failure isolation.

**Tech Stack:** Python 3.11+, langgraph (existing), yfinance (US), akshare (A 股/HK), pytest + pytest-mock.

**Spec:** [docs/superpowers/specs/2026-05-27-sentiment-scan-with-analysis-design.md](../specs/2026-05-27-sentiment-scan-with-analysis-design.md)

**Baseline (must remain exactly green throughout):**
```bash
.venv/bin/pytest tests/web/ tests/test_cli_backend_url_override.py -q
# expected: 78 passed
```

**Verification skill reference:** Use @superpowers:verification-before-completion when claiming the build is done.

---

## File Structure (locked in)

### Created
```
tradingagents/sentiment_scan/__init__.py
tradingagents/sentiment_scan/fundamentals_snapshot.py
tradingagents/sentiment_scan/analysis_runner.py
tradingagents/sentiment_scan/snapshot_io.py
tradingagents/sentiment_scan/feishu_post_v2.py

tests/sentiment_scan/__init__.py
tests/sentiment_scan/test_fundamentals_snapshot.py
tests/sentiment_scan/test_analysis_runner.py
tests/sentiment_scan/test_snapshot_io.py
tests/sentiment_scan/test_feishu_post_v2.py
tests/sentiment_scan/test_daily_sentiment_scan_cli.py

web/launchd/com.tradingagents.daily-analysis.plist
web/launchd/com.tradingagents.daily-feishu-push.plist
```

### Modified
```
tradingagents/agents/utils/rating.py                   # +SIGNAL_ACTION_MAP constant
web/state_tracker.py                                   # remove inline dict, re-export from rating.py
scripts/daily_sentiment_scan.py                        # add --analyze / --push subcommands; keep no-flag path unchanged
web/launchd/com.tradingagents.daily-sentiment-scan.plist  # RENAMED to daily-feishu-push.plist (cosmetic, with arg change)
```

### Deleted (after successful deploy verification only)
```
~/Library/LaunchAgents/com.tradingagents.daily-sentiment-scan.plist  # replaced via bootout + bootstrap (Phase 7)
```

---

## Phase 0 — Prerequisite refactor: move SIGNAL_ACTION_MAP

This MUST land first. The analysis runner imports `SIGNAL_ACTION_MAP` from `tradingagents.agents.utils.rating`. Right now that constant lives in `web.state_tracker`. Moving it preserves the "analysis process does not depend on web" invariant.

### Task 0.1: Confirm baseline 78 tests green

- [ ] **Step 1: Run baseline tests**

```bash
cd /Users/a1/TradingAgents
.venv/bin/pytest tests/web/ tests/test_cli_backend_url_override.py -q
```

Expected: `78 passed`. If anything is red, stop and report. Do NOT proceed.

### Task 0.2: Add SIGNAL_ACTION_MAP to rating.py (additive, no behavior change yet)

**Files:**
- Modify: `tradingagents/agents/utils/rating.py:21` (append after `RATINGS_5_TIER` tuple)

- [ ] **Step 1: Write the failing test** — verify rating.py exports SIGNAL_ACTION_MAP with exact 5 keys

Create `tests/test_rating_signal_action_map.py`:
```python
"""Regression test for SIGNAL_ACTION_MAP moving to rating.py (single source of truth)."""
from tradingagents.agents.utils.rating import SIGNAL_ACTION_MAP


def test_signal_action_map_keys_and_values():
    assert SIGNAL_ACTION_MAP == {
        "Buy": "BUY",
        "Overweight": "BUY",
        "Hold": "HOLD",
        "Underweight": "SELL",
        "Sell": "SELL",
    }


def test_signal_action_map_keys_match_5_tier_vocabulary():
    from tradingagents.agents.utils.rating import RATINGS_5_TIER
    assert set(SIGNAL_ACTION_MAP.keys()) == set(RATINGS_5_TIER)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/test_rating_signal_action_map.py -q
```

Expected: FAIL with `ImportError: cannot import name 'SIGNAL_ACTION_MAP' from 'tradingagents.agents.utils.rating'`

- [ ] **Step 3: Implement** — add constant to `tradingagents/agents/utils/rating.py` after line 21

```python
# Insert after RATINGS_5_TIER tuple definition (line 21):

# 3-tier collapse for downstream consumers needing BUY/HOLD/SELL semantics.
# Lives next to the 5-tier vocabulary so the two stay in lockstep.
SIGNAL_ACTION_MAP = {
    "Buy": "BUY",
    "Overweight": "BUY",
    "Hold": "HOLD",
    "Underweight": "SELL",
    "Sell": "SELL",
}
```

- [ ] **Step 4: Run test to verify pass + baseline still green**

```bash
.venv/bin/pytest tests/test_rating_signal_action_map.py -q
.venv/bin/pytest tests/web/ tests/test_cli_backend_url_override.py -q
```

Expected: rating test 2 passed; baseline still 78 passed.

- [ ] **Step 5: Commit**

```bash
git add tradingagents/agents/utils/rating.py tests/test_rating_signal_action_map.py
git commit -m "refactor(rating): add SIGNAL_ACTION_MAP next to RATINGS_5_TIER

把 5→3 tier collapse 字典与 5-tier vocabulary 同居作为单一真相。
web.state_tracker 后续会改为 re-export 这个常量（保 baseline 测试）。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 0.3: Switch web/state_tracker.py to re-export

**Files:**
- Modify: `web/state_tracker.py:34-40` (replace inline dict with import + assignment)

- [ ] **Step 1: Inspect the current site**

```bash
sed -n '30,45p' /Users/a1/TradingAgents/web/state_tracker.py
```

Confirm lines 34-40 contain the inline `SIGNAL_ACTION_MAP = {...}` dict.

- [ ] **Step 2: Replace inline dict with re-export**

Replace lines 34-40 with:

```python
# Re-export so existing `from web.state_tracker import SIGNAL_ACTION_MAP` keeps
# working (web/app.py + tests/web/test_state_tracker.py + tests/web/test_app.py).
# Real definition lives in tradingagents/agents/utils/rating.py — single source
# of truth with the 5-tier vocabulary.
from tradingagents.agents.utils.rating import SIGNAL_ACTION_MAP  # noqa: F401
```

- [ ] **Step 3: Verify baseline 78 still green**

```bash
.venv/bin/pytest tests/web/ tests/test_cli_backend_url_override.py -q
```

Expected: 78 passed (no change). If anything red, the re-export likely missed the F401 noqa or the import path is wrong — fix before continuing.

- [ ] **Step 4: Verify web/app.py:48 + web/app.py:429 still resolve**

```bash
.venv/bin/python -c "from web.state_tracker import SIGNAL_ACTION_MAP; print(SIGNAL_ACTION_MAP)"
.venv/bin/python -c "from web import app; print(app.SIGNAL_ACTION_MAP if hasattr(app, 'SIGNAL_ACTION_MAP') else 'via state_tracker only'); print('app import ok')"
```

Both should succeed.

- [ ] **Step 5: Commit**

```bash
git add web/state_tracker.py
git commit -m "refactor(web): re-export SIGNAL_ACTION_MAP from rating.py

删除 web/state_tracker.py 的内联定义，改为 from rating.py re-export。
web/app.py / tests/web/test_state_tracker.py / tests/web/test_app.py 的
\`from web.state_tracker import SIGNAL_ACTION_MAP\` 仍生效。baseline 78 测试不动。

后续 sentiment_scan/analysis_runner.py 直接 import rating.py，不依赖 web 包。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase 1 — Package skeleton

### Task 1.1: Create empty package + test directory

**Files:**
- Create: `tradingagents/sentiment_scan/__init__.py`
- Create: `tests/sentiment_scan/__init__.py`

- [ ] **Step 1: Create directories + empty `__init__.py` files**

```bash
mkdir -p /Users/a1/TradingAgents/tradingagents/sentiment_scan
mkdir -p /Users/a1/TradingAgents/tests/sentiment_scan
```

Write `tradingagents/sentiment_scan/__init__.py`:
```python
"""Sentiment scan with per-ticker TradingAgents analysis.

See docs/superpowers/specs/2026-05-27-sentiment-scan-with-analysis-design.md
"""
```

Write `tests/sentiment_scan/__init__.py`:
```python
```
(empty file)

- [ ] **Step 2: Verify import works**

```bash
.venv/bin/python -c "import tradingagents.sentiment_scan; print('ok')"
```

Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add tradingagents/sentiment_scan/__init__.py tests/sentiment_scan/__init__.py
git commit -m "feat(sentiment_scan): package skeleton

新建 tradingagents/sentiment_scan/ 包占位。后续 phase 加 5 个模块（
fundamentals_snapshot/analysis_runner/snapshot_io/feishu_post_v2 +
被 scripts/daily_sentiment_scan.py 调用）。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase 2 — `fundamentals_snapshot.py`: vendor-direct PE/远期PE/FCF/ROE抽取

**⚠ Plan rewritten 2026-05-28 after Phase 2 v1 code-quality reviewer caught plan ground-truth errors:** 原 plan v1 假设 `stock_financial_abstract` 含「市盈率/总市值」行 + `stock_financial_hk_analysis_indicator_em` 含 `PE_TTM`/`MARKET_CAP` 列——**实测都不存在**。新版改用**东财 quote API 底层 HTTP**（绕过 akshare 函数走 push2.eastmoney.com 被代理拦的子域），复用已有 `tradingagents/dataflows/akshare_china.py:1399` 的 `_eastmoney_session()` + `_eastmoney_http_retry()` helper。实测验证：600519 茅台 PE TTM=19.53 / 总市值=1.6 万亿元；00700 腾讯 PE TTM=17.11 / 总市值=3.85 万亿 HKD。详见 spec section "fundamentals_snapshot.py"。

### Task 2.1: Write failing test for US ticker happy path

**Files:**
- Create: `tests/sentiment_scan/test_fundamentals_snapshot.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for fundamentals_snapshot.fetch_structured_fundamentals."""
from unittest.mock import MagicMock, patch
import pytest

from tradingagents.sentiment_scan.fundamentals_snapshot import (
    fetch_structured_fundamentals,
)


def test_us_ticker_returns_full_fields(monkeypatch):
    """yf.Ticker(t).info dict → structured dict with PE/forwardPE/FCF/ROE."""
    fake_info = {
        "longName": "Apple Inc",
        "trailingPE": 28.5,
        "forwardPE": 25.1,
        "freeCashflow": 9.5e10,
        "returnOnEquity": 1.4523,
        "marketCap": 3.5e12,
        "currency": "USD",
    }
    fake_ticker = MagicMock()
    fake_ticker.info = fake_info

    with patch("tradingagents.sentiment_scan.fundamentals_snapshot.yf.Ticker", return_value=fake_ticker):
        with patch("tradingagents.sentiment_scan.fundamentals_snapshot.yf_retry", side_effect=lambda fn: fn()):
            result = fetch_structured_fundamentals("AAPL")

    assert result["ticker"] == "AAPL"
    assert result["market"] == "US"
    assert result["pe_ttm"] == 28.5
    assert result["pe_forward"] == 25.1
    assert result["fcf"] == 9.5e10
    assert result["roe"] == 1.4523
    assert result["market_cap"] == 3.5e12
    assert result["currency"] == "USD"
    assert result["source"] == "yfinance"
    assert result["status"] == "ok"
    assert result["missing_fields"] == []
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/sentiment_scan/test_fundamentals_snapshot.py::test_us_ticker_returns_full_fields -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'tradingagents.sentiment_scan.fundamentals_snapshot'`.

### Task 2.2: Implement US branch with sentinel-key guard

**Files:**
- Create: `tradingagents/sentiment_scan/fundamentals_snapshot.py`

**Key design point**: yfinance returns `{"trailingPegRatio": None}` truthy stub dict for unknown tickers (NOT empty, NOT raise). To honor the spec's never-throws + bad-input → status=error contract, require at least one of `trailingPE/forwardPE/freeCashflow/returnOnEquity/marketCap/longName/shortName/regularMarketPrice` keys to be present. If none → raise → outer try catches → status=error. Without this guard, `_fetch_us("INVALID...")` would return `status="partial"` with all None values — violating spec.

- [ ] **Step 1: Implement**

```python
"""Vendor-direct structured fundamentals extraction.

Returns plain dicts (not vendor strings) so JSON snapshot writers can
serialize without parsing free-form LLM-facing text. Never throws —
any vendor failure becomes status="error".

Schema notes (real-vendor verified 2026-05-28):
- US: yfinance .info dict
- A_SHARE PE+市值: eastmoney quote API (push2.eastmoney.com/api/qt/stock/get)
- A_SHARE ROE: akshare.stock_financial_abstract row "净资产收益率(ROE)"
- HK PE+市值: same eastmoney quote API (secid=116.{zfill5})
- HK ROE: akshare.stock_financial_hk_analysis_indicator_em col ROE_AVG ÷100
- A_SHARE+HK FCF: not available in public endpoints — always None
- A_SHARE+HK pe_forward: vendors don't expose consensus — always None
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

import yfinance as yf

from tradingagents.dataflows.stockstats_utils import yf_retry
from tradingagents.market_resolver import Market, resolve_market

_EMPTY_FIELDS = {
    "pe_ttm": None,
    "pe_forward": None,
    "fcf": None,
    "roe": None,
    "market_cap": None,
}

# At least one of these keys MUST be in yfinance .info for the response to
# be a real listing (and not the {"trailingPegRatio": None} stub returned
# for unknown tickers).
_YF_SENTINEL_KEYS = {
    "trailingPE", "forwardPE", "freeCashflow", "returnOnEquity",
    "marketCap", "longName", "shortName", "regularMarketPrice",
}


def _fields_status(values: dict) -> tuple[list[str], str]:
    """Return (missing_field_names, status) — status ok/partial."""
    missing = [k for k in ("pe_ttm", "pe_forward", "fcf", "roe", "market_cap") if values.get(k) is None]
    if not missing:
        return [], "ok"
    return missing, "partial"


def _fetch_us(ticker: str) -> dict:
    ticker_obj = yf.Ticker(ticker.upper())
    info = yf_retry(lambda: ticker_obj.info)
    if not info or not isinstance(info, dict):
        raise ValueError(f"yfinance returned empty/non-dict info for {ticker}")
    if not (set(info.keys()) & _YF_SENTINEL_KEYS):
        raise ValueError(f"yfinance returned stub dict (no recognized fields) for {ticker}")

    values = {
        "pe_ttm": info.get("trailingPE"),
        "pe_forward": info.get("forwardPE"),
        "fcf": info.get("freeCashflow"),
        "roe": info.get("returnOnEquity"),
        "market_cap": info.get("marketCap"),
    }
    missing, status = _fields_status(values)
    return {
        "ticker": ticker.upper(),
        "market": "US",
        **values,
        "currency": info.get("currency", "USD"),
        "as_of": datetime.now().strftime("%Y-%m-%d"),
        "source": "yfinance",
        "missing_fields": missing,
        "status": status,
        "error": None,
    }


def fetch_structured_fundamentals(ticker: str) -> dict:
    """Public entry — never throws. status=error on any vendor failure.

    Args:
        ticker: US uses bare/cased symbol (AAPL/NVDA); A-share uses 6-digit
            code (600519); HK uses code with .HK suffix (0700.HK).

    Returns:
        Dict with keys: ticker, market (US/A_SHARE/HK/error-market),
        pe_ttm, pe_forward, fcf, roe, market_cap (floats|None),
        currency (str|None), as_of (YYYY-MM-DD), source (yfinance|akshare+eastmoney|None),
        missing_fields (list[str]), status (ok|partial|error), error (str|None).
    """
    # Resolve market BEFORE try so error path preserves market info.
    market_name = "unknown"
    try:
        if not isinstance(ticker, str) or not ticker.strip():
            return _error_result(ticker, "unknown", "invalid ticker input")
        m = resolve_market(ticker)
        market_name = m.name  # "US" / "A_SHARE" / "HK" / "CRYPTO"
        if m == Market.US:
            return _fetch_us(ticker)
        # A_SHARE + HK branches added in subsequent tasks
        return _error_result(ticker, market_name, f"market {market_name} not yet supported")
    except Exception as exc:
        return _error_result(ticker, market_name, f"{type(exc).__name__}: {str(exc)[:200]}")


def _error_result(ticker: Any, market: str, error: str) -> dict:
    """Construct a failure result with all financial fields None."""
    return {
        "ticker": str(ticker) if ticker is not None else "",
        "market": market,
        **_EMPTY_FIELDS,
        "currency": None,
        "as_of": datetime.now().strftime("%Y-%m-%d"),
        "source": None,
        "missing_fields": list(_EMPTY_FIELDS.keys()),
        "status": "error",
        "error": error,
    }
```

- [ ] **Step 2: Run test to verify pass**

```bash
.venv/bin/pytest tests/sentiment_scan/test_fundamentals_snapshot.py::test_us_ticker_returns_full_fields -v
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tradingagents/sentiment_scan/fundamentals_snapshot.py tests/sentiment_scan/test_fundamentals_snapshot.py
git commit -m "feat(sentiment_scan): fundamentals_snapshot US branch (with sentinel-key guard)

vendor-direct 抽取 PE/远期PE/FCF/ROE — yfinance .info dict 直接拿原生数值。
Sentinel-key 检查防 yfinance 对无效 ticker 返 {trailingPegRatio:None} 真值
stub 通过 (8 个关键字段至少一个出现才接受为有效响应)。
A_SHARE/HK 分支在后续 task。永不抛——异常路径返 status=error 保留 market 信息。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 2.3: Add A-share branch — 东财 quote API for PE+市值 + akshare for ROE

**Real schema (实测 2026-05-28):**
- `_eastmoney_session()` + `push2.eastmoney.com/api/qt/stock/get`，secid=`{prefix}.{code}`，fields=`f43,f57,f58,f116,f117,f162,f163,f167`
- Eastmoney market prefix:
  - SH (上交所) = `1`: code starts with 60 / 68 / 90
  - SZ (深交所) = `0`: code starts with 00 / 30 / 20
  - BJ (北交所) = `0`: code starts with 4 / 8
- 响应字段（×100 编码注意）:
  - `f43` ÷100 → 最新价 CNY
  - `f57` → code; `f58` → name
  - `f116` (原值，单位元) → 总市值
  - `f117` (原值) → 流通市值
  - `f163` ÷100 → **PE TTM** (使用)
  - `f167` ÷100 → PB (本期不取)
  - **`f163: 0` 视为 None** (停牌/ST 股的常见编码)
- ROE: `akshare.stock_financial_abstract(symbol=code)` 第一行**精确匹配** `指标 == "净资产收益率(ROE)"` (避免子串误中 5 个 ROE 变体如 "摊薄净资产收益率"/"净资产收益率_平均_扣除非经常损益"等)。值已是 ratio (0.31)。

- [ ] **Step 1: Append failing tests**

Append to `tests/sentiment_scan/test_fundamentals_snapshot.py`:

```python
def test_a_share_extracts_pe_marketcap_roe_via_eastmoney(monkeypatch):
    """A 股: 东财 quote 拿 PE+市值, akshare 拿 ROE."""
    import pandas as pd

    fake_quote_response = MagicMock(status_code=200)
    fake_quote_response.json.return_value = {
        "rc": 0,
        "data": {
            "f43": 128600,                        # 1286.00 价格 (×100)
            "f57": "600519",
            "f58": "贵州茅台",
            "f116": 1_607_604_938_886.0,          # 总市值 ≈ 1.6 万亿元
            "f163": 1953,                         # PE TTM 19.53 (×100)
            "f167": 593,                          # PB 5.93
        },
    }
    fake_session = MagicMock()
    fake_session.get.return_value = fake_quote_response

    fake_df = pd.DataFrame({
        "指标": ["净利润", "净资产收益率(ROE)", "摊薄净资产收益率", "营业总收入"],
        "20260331": [8.5e9, 0.31, 0.29, 5.5e10],
        "20251231": [8.2e9, 0.30, 0.28, 5.3e10],
    })
    fake_ak = MagicMock()
    fake_ak.stock_financial_abstract.return_value = fake_df

    with patch(
        "tradingagents.sentiment_scan.fundamentals_snapshot._eastmoney_session",
        return_value=fake_session,
    ), patch(
        "tradingagents.sentiment_scan.fundamentals_snapshot._eastmoney_http_retry",
        side_effect=lambda fn: fn(),
    ), patch(
        "tradingagents.sentiment_scan.fundamentals_snapshot._dep_bootstrap.ensure",
        return_value=fake_ak,
    ):
        result = fetch_structured_fundamentals("600519")

    assert result["ticker"] == "600519"
    assert result["market"] == "A_SHARE"
    assert result["pe_ttm"] == 19.53                  # f163 / 100
    assert result["pe_forward"] is None
    assert result["fcf"] is None
    assert result["roe"] == 0.31                      # 精确匹配 "净资产收益率(ROE)"，不会误中 "摊薄净资产收益率"
    assert result["market_cap"] == 1_607_604_938_886.0
    assert result["currency"] == "CNY"
    assert result["source"] == "akshare+eastmoney"
    assert result["status"] == "partial"
    assert set(result["missing_fields"]) == {"pe_forward", "fcf"}


def test_a_share_eastmoney_zero_pe_treated_as_none(monkeypatch):
    """东财 f163: 0 视为无 PE (停牌/ST 标的的常见编码)."""
    import pandas as pd
    fake_quote_response = MagicMock(status_code=200)
    fake_quote_response.json.return_value = {
        "rc": 0,
        "data": {
            "f43": 50000,
            "f57": "600555",
            "f58": "测试",
            "f116": 5_000_000_000.0,
            "f163": 0,                              # 无 PE
            "f167": 100,
        },
    }
    fake_session = MagicMock(get=MagicMock(return_value=fake_quote_response))
    fake_df = pd.DataFrame({"指标": ["净资产收益率(ROE)"], "20260331": [-0.05]})
    fake_ak = MagicMock(stock_financial_abstract=MagicMock(return_value=fake_df))

    with patch(
        "tradingagents.sentiment_scan.fundamentals_snapshot._eastmoney_session",
        return_value=fake_session,
    ), patch(
        "tradingagents.sentiment_scan.fundamentals_snapshot._eastmoney_http_retry",
        side_effect=lambda fn: fn(),
    ), patch(
        "tradingagents.sentiment_scan.fundamentals_snapshot._dep_bootstrap.ensure",
        return_value=fake_ak,
    ):
        result = fetch_structured_fundamentals("600555")
    assert result["pe_ttm"] is None                  # f163: 0 → None
    assert result["roe"] == -0.05


def test_a_share_secid_prefix_mapping():
    """SH (60/68/90) = 1; SZ (00/30/20) + BJ (4/8) = 0."""
    from tradingagents.sentiment_scan.fundamentals_snapshot import _a_share_secid
    assert _a_share_secid("600519") == "1.600519"    # SH 主板
    assert _a_share_secid("688981") == "1.688981"    # SH 科创板
    assert _a_share_secid("900939") == "1.900939"    # SH B 股
    assert _a_share_secid("000001") == "0.000001"    # SZ 主板
    assert _a_share_secid("300866") == "0.300866"    # SZ 创业板
    assert _a_share_secid("200568") == "0.200568"    # SZ B 股
    assert _a_share_secid("430047") == "0.430047"    # BJ
    assert _a_share_secid("832000") == "0.832000"    # BJ
```

- [ ] **Step 2: Run** — Expected FAIL (`_fetch_a_share` / `_a_share_secid` not defined).

- [ ] **Step 3: Implement A-share branch**

Add to `fundamentals_snapshot.py` (after `_fetch_us`, before `fetch_structured_fundamentals`):

```python
import pandas as pd

from tradingagents.dataflows import _dep_bootstrap
from tradingagents.dataflows.akshare_china import (
    _eastmoney_session,
    _eastmoney_http_retry,
)

_EASTMONEY_QUOTE_URL = "http://push2.eastmoney.com/api/qt/stock/get"
_EASTMONEY_UT = "fa5fd1943c7b386f172d6893dbfba10b"  # eastmoney public ut token
_EASTMONEY_FIELDS = "f43,f57,f58,f116,f117,f162,f163,f167"


def _a_share_secid(code: str) -> str:
    """A 股 eastmoney secid prefix.

    SH (上交所) = 1: code starts with 60 / 68 / 90
    SZ (深交所) = 0: code starts with 00 / 30 / 20
    BJ (北交所) = 0: code starts with 4 / 8
    """
    if code.startswith(("60", "68", "90")):
        return f"1.{code}"
    return f"0.{code}"   # 涵盖 SZ + BJ


def _eastmoney_quote(secid: str) -> dict:
    """Fetch eastmoney quote dict (bypass akshare wrapper, hit push2 directly)."""
    session = _eastmoney_session()
    params = {"secid": secid, "fields": _EASTMONEY_FIELDS, "ut": _EASTMONEY_UT}
    r = _eastmoney_http_retry(
        lambda: session.get(_EASTMONEY_QUOTE_URL, params=params, timeout=10)
    )
    payload = r.json()
    return payload.get("data") or {}


def _a_share_roe(code: str) -> float | None:
    """Extract A-share ROE from akshare.stock_financial_abstract.

    Strategy: EXACT-MATCH row 指标 == "净资产收益率(ROE)" (NOT substring;
    avoid colliding with "摊薄净资产收益率" / "净资产收益率_平均_扣除非经常损益"
    / "净资产收益率_平均" / "摊薄净资产收益率_扣除非经常损益").
    Take the value from the latest 8-digit-period column.
    """
    ak = _dep_bootstrap.ensure("akshare")
    df = ak.stock_financial_abstract(symbol=code)
    if not isinstance(df, pd.DataFrame) or df.empty or "指标" not in df.columns:
        return None
    period_cols = [c for c in df.columns if str(c).isdigit() and len(str(c)) == 8]
    if not period_cols:
        return None
    latest = max(period_cols)
    for _, row in df.iterrows():
        if str(row["指标"]).strip() == "净资产收益率(ROE)":
            val = row[latest]
            if pd.isna(val):
                continue
            try:
                return float(val)
            except (TypeError, ValueError):
                return None
    return None


def _fetch_a_share(ticker: str) -> dict:
    code = ticker.strip().upper().split(".")[0]
    secid = _a_share_secid(code)

    quote = _eastmoney_quote(secid)
    f163 = quote.get("f163")
    pe_ttm = f163 / 100.0 if f163 and f163 > 0 else None   # ×100 编码; 0 视为 None
    market_cap = quote.get("f116") or None

    roe = _a_share_roe(code)

    values: dict[str, float | None] = {
        "pe_ttm": pe_ttm,
        "pe_forward": None,    # akshare 不暴露 forward consensus
        "fcf": None,           # aggregate FCF 在公开端点不可得
        "roe": roe,
        "market_cap": market_cap,
    }
    missing, status = _fields_status(values)
    return {
        "ticker": code,
        "market": "A_SHARE",
        **values,
        "currency": "CNY",
        "as_of": datetime.now().strftime("%Y-%m-%d"),
        "source": "akshare+eastmoney",
        "missing_fields": missing,
        "status": status,
        "error": None,
    }
```

Update `fetch_structured_fundamentals` dispatcher to call `_fetch_a_share` when `m == Market.A_SHARE`.

- [ ] **Step 4: Run all tests** — Expected: 4 passed (US + 3 A-share).

- [ ] **Step 5: Commit**

```bash
git add tradingagents/sentiment_scan/fundamentals_snapshot.py tests/sentiment_scan/test_fundamentals_snapshot.py
git commit -m "feat(sentiment_scan): fundamentals_snapshot A 股分支 (东财 quote + akshare ROE)

A 股 PE TTM + 总市值: 走东财 push2.eastmoney.com/api/qt/stock/get
(绕过 akshare 包装函数, 复用 akshare_china.py:_eastmoney_session +
_eastmoney_http_retry; trust_env=False 绕代理拦截)。secid prefix
1/0 自动判 SH/SZ/BJ。f163/f167 ×100 编码自动除 100; f163=0 视为 None。
ROE: akshare.stock_financial_abstract 精确匹配 \"净资产收益率(ROE)\" 行
(避免子串误中 \"摊薄净资产收益率\" 等 5 个 ROE 变体)。
FCF + pe_forward 留 None (端点不暴露 aggregate / forward consensus)。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 2.4: Add HK branch — 东财 quote API + akshare ROE 转 ratio

**Real schema (实测 2026-05-28):**
- secid = `116.{zfill5}` — 实测 `116.00700` → 腾讯; `116.0700` (4-digit) → API rc=100 失败
- 同样字段 f43/f163/f167/f116
- ROE: `akshare.stock_financial_hk_analysis_indicator_em(symbol=code5)` 列 `ROE_AVG` 单位**百分点 (21.13)**，**必须 ÷100 转 ratio (0.2113)** 才与 US/A股 returnOnEquity ratio 形式一致

- [ ] **Step 1: Append failing tests**

```python
def test_hk_extracts_pe_marketcap_roe_via_eastmoney(monkeypatch):
    """HK: secid=116.{zfill5}, ROE_AVG ÷100 转 ratio."""
    import pandas as pd

    fake_quote_response = MagicMock(status_code=200)
    fake_quote_response.json.return_value = {
        "rc": 0,
        "data": {
            "f43": 421800,                        # 4218.00 HKD (×100)
            "f57": "00700",
            "f58": "腾讯控股",
            "f116": 3_845_998_292_193.0,          # 3.85 万亿 HKD
            "f163": 1711,                         # PE TTM 17.11
            "f167": 301,
        },
    }
    fake_session = MagicMock(get=MagicMock(return_value=fake_quote_response))

    fake_df = pd.DataFrame([{
        "REPORT_DATE": "2026-03-31",
        "ROE_AVG": 21.13,                          # 百分点 - 需 ÷100
        "CURRENCY": "HKD",
    }])
    fake_ak = MagicMock(
        stock_financial_hk_analysis_indicator_em=MagicMock(return_value=fake_df),
    )

    with patch(
        "tradingagents.sentiment_scan.fundamentals_snapshot._eastmoney_session",
        return_value=fake_session,
    ), patch(
        "tradingagents.sentiment_scan.fundamentals_snapshot._eastmoney_http_retry",
        side_effect=lambda fn: fn(),
    ), patch(
        "tradingagents.sentiment_scan.fundamentals_snapshot._dep_bootstrap.ensure",
        return_value=fake_ak,
    ):
        result = fetch_structured_fundamentals("0700.HK")

    assert result["ticker"] == "00700.HK"
    assert result["market"] == "HK"
    assert result["pe_ttm"] == 17.11
    assert result["pe_forward"] is None
    assert result["fcf"] is None
    assert result["roe"] == pytest.approx(0.2113, abs=1e-4)   # 21.13 / 100
    assert result["market_cap"] == 3_845_998_292_193.0
    assert result["currency"] == "HKD"
    assert result["source"] == "akshare+eastmoney"


def test_hk_secid_format():
    from tradingagents.sentiment_scan.fundamentals_snapshot import _hk_secid
    assert _hk_secid("0700") == "116.00700"        # 4-digit zero-pad
    assert _hk_secid("00700") == "116.00700"
    assert _hk_secid("0700.HK") == "116.00700"
    assert _hk_secid("9988.HK") == "116.09988"     # 阿里
    assert _hk_secid("01024") == "116.01024"
```

- [ ] **Step 2: Run** — Expected FAIL.

- [ ] **Step 3: Implement HK branch**

```python
def _hk_secid(ticker: str) -> str:
    """HK secid: '116.' + 5-digit zero-padded code (4-digit must be zfilled)."""
    raw = ticker.strip().upper()
    if raw.endswith(".HK"):
        raw = raw[:-3]
    return f"116.{raw.zfill(5)}"


def _hk_roe(code5: str) -> tuple[float | None, str]:
    """Extract HK ROE (as ratio) + currency from akshare HK indicator endpoint.

    ROE_AVG 单位是百分点 (e.g. 21.13) — divide by 100 to match ratio form
    (US/A_SHARE both use ratio).
    """
    ak = _dep_bootstrap.ensure("akshare")
    df = ak.stock_financial_hk_analysis_indicator_em(symbol=code5)
    roe: float | None = None
    currency = "HKD"
    if isinstance(df, pd.DataFrame) and not df.empty:
        row = df.iloc[0]
        for col in ("ROE_AVG", "ROE"):
            if col in df.columns:
                v = row.get(col)
                if not pd.isna(v):
                    try:
                        roe = float(v) / 100.0      # 百分点 → ratio
                    except (TypeError, ValueError):
                        pass
                    break
        cur = row.get("CURRENCY")
        if isinstance(cur, str) and cur.strip():
            currency = cur
    return roe, currency


def _fetch_hk(ticker: str) -> dict:
    raw = ticker.strip().upper()
    if raw.endswith(".HK"):
        raw = raw[:-3]
    code5 = raw.zfill(5)
    secid = _hk_secid(ticker)

    quote = _eastmoney_quote(secid)
    f163 = quote.get("f163")
    pe_ttm = f163 / 100.0 if f163 and f163 > 0 else None
    market_cap = quote.get("f116") or None

    roe, currency = _hk_roe(code5)

    values: dict[str, float | None] = {
        "pe_ttm": pe_ttm,
        "pe_forward": None,
        "fcf": None,
        "roe": roe,
        "market_cap": market_cap,
    }
    missing, status = _fields_status(values)
    return {
        "ticker": f"{code5}.HK",
        "market": "HK",
        **values,
        "currency": currency,
        "as_of": datetime.now().strftime("%Y-%m-%d"),
        "source": "akshare+eastmoney",
        "missing_fields": missing,
        "status": status,
        "error": None,
    }
```

Wire into dispatcher (`elif m == Market.HK: return _fetch_hk(ticker)`).

- [ ] **Step 4: Run** — Expected 6 passed (US + 3 A-share + 2 HK).

- [ ] **Step 5: Commit**

```bash
git add tradingagents/sentiment_scan/fundamentals_snapshot.py tests/sentiment_scan/test_fundamentals_snapshot.py
git commit -m "feat(sentiment_scan): fundamentals_snapshot HK 分支 (东财 quote + akshare ROE)

HK PE TTM + 总市值: 同 A 股套路走东财 quote API, secid=116.{zfill5}
(4 位代码必须 zfill 到 5 位, 否则 push2 返 rc=100)。
ROE: akshare.stock_financial_hk_analysis_indicator_em 列 ROE_AVG,
     单位百分点 ÷100 转 ratio 与 US/A 股一致。
FCF + pe_forward 留 None (端点不暴露)。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 2.5: Add never-throws matrix + deterministic stub-dict test

- [ ] **Step 1: Append tests**

```python
def test_yfinance_stub_dict_returns_error_deterministic():
    """yfinance 对无效 ticker 返 {trailingPegRatio: None} truthy stub.
    Sentinel-key 应拒识别 → outer try catches → status=error.
    完全 mock 不打网络 — deterministic 测试 (replaces v1 plan 中真发 yahoo
    HTTP 的 INVALID_NOT_A_TICKER parametrize case)."""
    fake_ticker = MagicMock()
    fake_ticker.info = {"trailingPegRatio": None}   # 真实 stub shape
    with patch(
        "tradingagents.sentiment_scan.fundamentals_snapshot.yf.Ticker",
        return_value=fake_ticker,
    ), patch(
        "tradingagents.sentiment_scan.fundamentals_snapshot.yf_retry",
        side_effect=lambda fn: fn(),
    ):
        result = fetch_structured_fundamentals("FAKEXYZ")
    assert result["status"] == "error"
    assert ("stub" in result["error"]) or ("no recognized fields" in result["error"])
    assert result["market"] == "US"   # market 在 error path 仍保留


def test_yfinance_exception_returns_error_status():
    with patch(
        "tradingagents.sentiment_scan.fundamentals_snapshot.yf.Ticker",
        side_effect=RuntimeError("network down"),
    ):
        result = fetch_structured_fundamentals("AAPL")
    assert result["status"] == "error"
    assert "RuntimeError" in result["error"]
    assert result["pe_ttm"] is None
    assert result["market"] == "US"   # ground-truth: market preserved on error


def test_a_share_eastmoney_failure_then_akshare_failure_returns_error_status():
    """两个 vendor 都挂时 status=error 不抛."""
    fake_session = MagicMock()
    fake_session.get.side_effect = RuntimeError("eastmoney down")
    fake_ak = MagicMock()
    fake_ak.stock_financial_abstract.side_effect = RuntimeError("akshare down")
    with patch(
        "tradingagents.sentiment_scan.fundamentals_snapshot._eastmoney_session",
        return_value=fake_session,
    ), patch(
        "tradingagents.sentiment_scan.fundamentals_snapshot._eastmoney_http_retry",
        side_effect=lambda fn: fn(),
    ), patch(
        "tradingagents.sentiment_scan.fundamentals_snapshot._dep_bootstrap.ensure",
        return_value=fake_ak,
    ):
        result = fetch_structured_fundamentals("600519")
    assert result["status"] == "error"
    assert result["market"] == "A_SHARE"


@pytest.mark.parametrize("bad", [None, "", [], 12345])
def test_bad_inputs_never_throw(bad):
    """非字符串/空串/list/int → status=error，不抛."""
    result = fetch_structured_fundamentals(bad)  # type: ignore[arg-type]
    assert isinstance(result, dict)
    assert result["status"] == "error"
    assert result["pe_ttm"] is None
```

Note: 不放 `"INVALID_NOT_A_TICKER_AT_ALL_!!!"` 到 parametrize（v1 plan 那个真发 yahoo HTTP；deterministic stub-dict 测试已覆盖等价行为，更快更稳）。

- [ ] **Step 2: Run all tests**

```bash
.venv/bin/pytest tests/sentiment_scan/test_fundamentals_snapshot.py -v
```

Expected: 10 passed (US 1 + A-share 3 + HK 2 + never-throws 3 + bad-inputs 1 parametrize × 4 cases = 1 test row collapsing into 4 results... pytest reports as 10 total).

- [ ] **Step 3: Mutation test (sanity check)** — temporarily change `_fetch_us` to `raise RuntimeError("forced")` inside the function, re-run tests. `test_us_ticker_returns_full_fields` should FAIL; `test_yfinance_stub_dict_returns_error_deterministic` should still pass (outer try catches). Revert.

- [ ] **Step 4: Final baseline check**

```bash
.venv/bin/pytest tests/web/ tests/test_cli_backend_url_override.py tests/test_rating_signal_action_map.py tests/sentiment_scan/ -q
```

Expected: 80 + 10 = 90 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/sentiment_scan/test_fundamentals_snapshot.py
git commit -m "test(sentiment_scan): never-throws matrix + deterministic stub-dict 守护

deterministic test: yfinance stub-dict {trailingPegRatio:None} → status=error
(完全不打网络 mock; replaces v1 plan 真发 yahoo HTTP 的 INVALID_TICKER
parametrize case)。Error path 也覆盖 market preservation (status=error 时
market 仍 = US/A_SHARE 不变成 unknown)。
四个 vendor 失败 path: yf exception / eastmoney+akshare 双 down / 4 种坏输入。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase 3 — `analysis_runner.py`: TradingAgents graph + watchdog

### Task 3.1: Write failing test for happy path

**Files:**
- Create: `tests/sentiment_scan/test_analysis_runner.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for analysis_runner.run_single_analysis + run_batch."""
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest


def test_happy_path_returns_ok_with_rating_and_action():
    """Mock graph stream returns a final_state with non-empty final_trade_decision.
    Result: status=ok, rating=5-tier, action=3-tier from SIGNAL_ACTION_MAP."""
    from tradingagents.sentiment_scan.analysis_runner import run_single_analysis

    fake_final_state = {
        "final_trade_decision": "Rating: Overweight\n\nExecutive Summary: ...",
    }
    fake_graph = MagicMock()
    fake_graph.graph.stream.return_value = iter([fake_final_state])
    fake_graph.propagator.create_initial_state.return_value = {}
    fake_graph.propagator.get_graph_args.return_value = {}

    deadline = datetime.now() + timedelta(minutes=30)
    with patch(
        "tradingagents.sentiment_scan.analysis_runner.TradingAgentsGraph",
        return_value=fake_graph,
    ), patch(
        "tradingagents.sentiment_scan.analysis_runner.apply_china_vendor_overlay"
    ):
        result = run_single_analysis("600519", "2026-05-27", deadline)

    assert result["status"] == "ok"
    assert result["decision"]["rating"] == "Overweight"
    assert result["decision"]["action"] == "BUY"  # via SIGNAL_ACTION_MAP[Overweight]
    assert result["elapsed_seconds"] >= 0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/sentiment_scan/test_analysis_runner.py::test_happy_path_returns_ok_with_rating_and_action -v
```

Expected: `ModuleNotFoundError`.

### Task 3.2: Implement run_single_analysis happy path

**Files:**
- Create: `tradingagents/sentiment_scan/analysis_runner.py`

- [ ] **Step 1: Implement**

```python
"""Per-ticker TradingAgents analysis runner with watchdog & failure isolation.

Public:
    run_single_analysis(ticker, date, deadline) → dict
    run_batch(intersection, date, hard_deadline) → list[dict]

Never throws. Every exception path returns a dict with status field; the
caller (`scripts/daily_sentiment_scan.py`) appends each result to the
JSON snapshot's `analyses` array.
"""
from __future__ import annotations

import gc
import re
import time
from datetime import datetime
from typing import Any

from tradingagents.agents.utils.rating import SIGNAL_ACTION_MAP
from tradingagents.dataflows.akshare_china import apply_china_vendor_overlay
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.signal_processing import SignalProcessor
from tradingagents.graph.trading_graph import TradingAgentsGraph


def run_single_analysis(ticker: str, date: str, deadline: datetime) -> dict:
    """Run TradingAgents (fundamentals + news) on one ticker.

    Returns a dict with at least `status` and (on ok/partial/incomplete) `decision`.
    """
    started_at = time.time()
    try:
        config = DEFAULT_CONFIG.copy()
        config["max_debate_rounds"] = 1
        config["max_risk_discuss_rounds"] = 1
        config["output_language"] = "zh-Hans"
        config["checkpoint_enabled"] = False
        apply_china_vendor_overlay(config, ticker)

        graph = TradingAgentsGraph(
            selected_analysts=["fundamentals", "news"],
            debug=False,
            config=config,
        )
        try:
            init_state = graph.propagator.create_initial_state(
                ticker, date, asset_type="stock"
            )
            args = graph.propagator.get_graph_args()

            final_state: dict | None = None
            for chunk in graph.graph.stream(init_state, **args):
                if datetime.now() >= deadline:
                    return _result(ticker, "timeout", started_at, error="exceeded per-ticker deadline")
                final_state = chunk if isinstance(chunk, dict) else final_state

            final_decision_md = (final_state or {}).get("final_trade_decision") or ""
            if not final_decision_md:
                return _result(ticker, "incomplete", started_at, error="no final_trade_decision produced")

            rating = SignalProcessor().process_signal(final_decision_md)
            action = SIGNAL_ACTION_MAP.get(rating, "HOLD")
            summary_1line = _extract_summary_1line(final_decision_md)
            return _result(
                ticker, "ok", started_at,
                decision={"rating": rating, "action": action, "summary_1line": summary_1line},
            )
        finally:
            # Release graph heap + LLM clients before next ticker.
            del graph
            gc.collect()
    except Exception as exc:  # noqa: BLE001 — never-throws contract
        return _result(ticker, "error", started_at, error=f"{type(exc).__name__}: {str(exc)[:200]}")


def _result(ticker: str, status: str, started_at: float, *, decision: dict | None = None, error: str | None = None) -> dict:
    return {
        "ticker": ticker,
        "status": status,
        "decision": decision,
        "error": error,
        "elapsed_seconds": round(time.time() - started_at, 2),
    }


_SUMMARY_RE = re.compile(r"\*\*Executive Summary\*\*[：:\s]*([^\n]+)")


def _extract_summary_1line(md: str) -> str:
    """First sentence of Executive Summary, or first non-empty rating line as fallback."""
    m = _SUMMARY_RE.search(md)
    if m:
        return m.group(1).strip()[:200]
    for line in md.splitlines():
        line = line.strip()
        if line and not line.lower().startswith("rating"):
            return line[:200]
    return ""
```

- [ ] **Step 2: Run test to verify pass**

```bash
.venv/bin/pytest tests/sentiment_scan/test_analysis_runner.py::test_happy_path_returns_ok_with_rating_and_action -v
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tradingagents/sentiment_scan/analysis_runner.py tests/sentiment_scan/test_analysis_runner.py
git commit -m "feat(sentiment_scan): analysis_runner happy path

run_single_analysis: TradingAgentsGraph fundamentals+news + SignalProcessor →
rating 5-tier + action 3-tier (via SIGNAL_ACTION_MAP)。
单 ticker watchdog 通过 deadline 参数；外层 try/except 包死整个函数。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 3.3: Add timeout / incomplete / error path tests

- [ ] **Step 1: Append 3 tests**

```python
def test_deadline_exceeded_during_stream_returns_timeout():
    """If now >= deadline during stream iteration, return status=timeout."""
    from tradingagents.sentiment_scan.analysis_runner import run_single_analysis

    fake_graph = MagicMock()
    # Stream yields many chunks; deadline check should fire on the second one.
    fake_graph.graph.stream.return_value = iter([{"k": 1}, {"k": 2}, {"k": 3}])
    fake_graph.propagator.create_initial_state.return_value = {}
    fake_graph.propagator.get_graph_args.return_value = {}

    past = datetime.now() - timedelta(seconds=1)  # already expired
    with patch(
        "tradingagents.sentiment_scan.analysis_runner.TradingAgentsGraph",
        return_value=fake_graph,
    ), patch(
        "tradingagents.sentiment_scan.analysis_runner.apply_china_vendor_overlay"
    ):
        result = run_single_analysis("600519", "2026-05-27", past)
    assert result["status"] == "timeout"


def test_empty_final_trade_decision_returns_incomplete():
    from tradingagents.sentiment_scan.analysis_runner import run_single_analysis

    fake_graph = MagicMock()
    fake_graph.graph.stream.return_value = iter([{"final_trade_decision": ""}])
    fake_graph.propagator.create_initial_state.return_value = {}
    fake_graph.propagator.get_graph_args.return_value = {}

    deadline = datetime.now() + timedelta(minutes=30)
    with patch(
        "tradingagents.sentiment_scan.analysis_runner.TradingAgentsGraph",
        return_value=fake_graph,
    ), patch(
        "tradingagents.sentiment_scan.analysis_runner.apply_china_vendor_overlay"
    ):
        result = run_single_analysis("600519", "2026-05-27", deadline)
    assert result["status"] == "incomplete"


def test_graph_construction_exception_returns_error():
    from tradingagents.sentiment_scan.analysis_runner import run_single_analysis

    deadline = datetime.now() + timedelta(minutes=30)
    with patch(
        "tradingagents.sentiment_scan.analysis_runner.TradingAgentsGraph",
        side_effect=RuntimeError("LLM client init failed"),
    ), patch(
        "tradingagents.sentiment_scan.analysis_runner.apply_china_vendor_overlay"
    ):
        result = run_single_analysis("600519", "2026-05-27", deadline)
    assert result["status"] == "error"
    assert "LLM client init failed" in result["error"]
    assert len(result["error"]) <= 250  # truncated


def test_apply_china_vendor_overlay_exception_returns_error():
    """The outer try MUST also wrap apply_china_vendor_overlay."""
    from tradingagents.sentiment_scan.analysis_runner import run_single_analysis

    deadline = datetime.now() + timedelta(minutes=30)
    with patch(
        "tradingagents.sentiment_scan.analysis_runner.apply_china_vendor_overlay",
        side_effect=ValueError("bad ticker shape"),
    ):
        result = run_single_analysis("600519", "2026-05-27", deadline)
    assert result["status"] == "error"
```

- [ ] **Step 2: Run tests** — all 4 should pass already (existing implementation covers them). If any FAIL, fix the implementation.

- [ ] **Step 3: Commit**

```bash
git add tests/sentiment_scan/test_analysis_runner.py
git commit -m "test(sentiment_scan): analysis_runner timeout/incomplete/error paths

4 个新测试: 中途 deadline → timeout, 空 final_trade_decision → incomplete,
graph 构造失败 → error, apply_china_vendor_overlay 失败 → error。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 3.4: Test gc.collect + apply_china_vendor_overlay called

- [ ] **Step 1: Append tests**

```python
def test_apply_china_vendor_overlay_is_called_with_ticker():
    from tradingagents.sentiment_scan.analysis_runner import run_single_analysis

    fake_graph = MagicMock()
    fake_graph.graph.stream.return_value = iter([{"final_trade_decision": "Rating: Hold"}])
    fake_graph.propagator.create_initial_state.return_value = {}
    fake_graph.propagator.get_graph_args.return_value = {}

    deadline = datetime.now() + timedelta(minutes=30)
    with patch(
        "tradingagents.sentiment_scan.analysis_runner.TradingAgentsGraph",
        return_value=fake_graph,
    ), patch(
        "tradingagents.sentiment_scan.analysis_runner.apply_china_vendor_overlay"
    ) as mock_overlay:
        run_single_analysis("600519", "2026-05-27", deadline)
    # Verify the China-vendor overlay was applied with the ticker (akshare routing).
    assert mock_overlay.called
    args, kwargs = mock_overlay.call_args
    assert args[1] == "600519"  # second positional arg is ticker


def test_gc_collect_called_in_finally():
    """gc.collect must run regardless of inner success/failure."""
    from tradingagents.sentiment_scan.analysis_runner import run_single_analysis

    fake_graph = MagicMock()
    fake_graph.graph.stream.return_value = iter([{"final_trade_decision": "Rating: Hold"}])
    fake_graph.propagator.create_initial_state.return_value = {}
    fake_graph.propagator.get_graph_args.return_value = {}

    deadline = datetime.now() + timedelta(minutes=30)
    with patch(
        "tradingagents.sentiment_scan.analysis_runner.TradingAgentsGraph",
        return_value=fake_graph,
    ), patch(
        "tradingagents.sentiment_scan.analysis_runner.apply_china_vendor_overlay"
    ), patch(
        "tradingagents.sentiment_scan.analysis_runner.gc.collect"
    ) as mock_gc:
        run_single_analysis("600519", "2026-05-27", deadline)
    assert mock_gc.called
```

- [ ] **Step 2: Run** — both should pass with current impl.

- [ ] **Step 3: Commit**

```bash
git add tests/sentiment_scan/test_analysis_runner.py
git commit -m "test(sentiment_scan): apply_china_vendor_overlay + gc.collect 守护

确认 A 股 ticker 路由 akshare、graph 跑完 gc 释放避免内存累积。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 3.5: Implement run_batch with hard_deadline + budget_exhausted path

- [ ] **Step 1: Write tests**

```python
def test_run_batch_processes_triple_first_then_double():
    """Tier order: triple → ab_only → ac_only → bc_only."""
    from tradingagents.sentiment_scan.analysis_runner import run_batch

    calls: list[str] = []

    def fake_runner(ticker, date, deadline):
        calls.append(ticker)
        return {"ticker": ticker, "status": "ok", "decision": None, "error": None, "elapsed_seconds": 1.0}

    intersection = {
        "triple": ["TRP1"],
        "ab_only": ["AB1", "AB2"],
        "ac_only": ["AC1"],
        "bc_only": ["BC1"],
    }
    hard_deadline = datetime.now() + timedelta(hours=2)
    with patch(
        "tradingagents.sentiment_scan.analysis_runner.run_single_analysis",
        side_effect=fake_runner,
    ):
        results = run_batch(intersection, "2026-05-27", hard_deadline)

    assert [r["ticker"] for r in results] == ["TRP1", "AB1", "AB2", "AC1", "BC1"]
    assert calls == ["TRP1", "AB1", "AB2", "AC1", "BC1"]


def test_run_batch_budget_exhausted_skips_remaining():
    """If hard_deadline already passed, remaining tickers get status=budget_exhausted."""
    from tradingagents.sentiment_scan.analysis_runner import run_batch

    intersection = {"triple": ["T1", "T2"], "ab_only": [], "ac_only": [], "bc_only": []}
    past = datetime.now() - timedelta(minutes=1)
    with patch(
        "tradingagents.sentiment_scan.analysis_runner.run_single_analysis",
    ) as mock_run:
        results = run_batch(intersection, "2026-05-27", past)

    # Neither ticker should have been actually analyzed.
    assert mock_run.call_count == 0
    assert len(results) == 2
    assert all(r["status"] == "budget_exhausted" for r in results)
    assert {r["ticker"] for r in results} == {"T1", "T2"}
```

- [ ] **Step 2: Run** — Expected FAIL (`run_batch` not defined).

- [ ] **Step 3: Implement run_batch**

Append to `analysis_runner.py`:

```python
from datetime import timedelta as _td

_SINGLE_TICKER_BUDGET = _td(minutes=30)


def run_batch(intersection: dict, date: str, hard_deadline: datetime) -> list[dict]:
    """Run analyses across all intersection tickers in tier priority order.

    Tickers not reached before hard_deadline get status=budget_exhausted.
    """
    ordered: list[str] = []
    for tier in ("triple", "ab_only", "ac_only", "bc_only"):
        for code in intersection.get(tier, []):
            ordered.append(code)

    results: list[dict] = []
    for ticker in ordered:
        if datetime.now() >= hard_deadline:
            results.append({
                "ticker": ticker,
                "status": "budget_exhausted",
                "decision": None,
                "error": "global deadline reached before this ticker",
                "elapsed_seconds": 0,
            })
            continue
        per_ticker_deadline = min(datetime.now() + _SINGLE_TICKER_BUDGET, hard_deadline)
        result = run_single_analysis(ticker, date, per_ticker_deadline)
        results.append(result)
    return results
```

- [ ] **Step 4: Run** — both pass.

- [ ] **Step 5: Commit**

```bash
git add tradingagents/sentiment_scan/analysis_runner.py tests/sentiment_scan/test_analysis_runner.py
git commit -m "feat(sentiment_scan): run_batch with tier priority + budget_exhausted

tier 处理顺序: triple → ab_only → ac_only → bc_only。
全批 hard_deadline 之后的 ticker 全标 budget_exhausted（不调 LLM）。
单只 deadline = min(now+30min, hard_deadline) 双重防超时。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase 4 — `snapshot_io.py`: atomic JSON write + tolerant read

### Task 4.1: Test round-trip + atomic write

**Files:**
- Create: `tests/sentiment_scan/test_snapshot_io.py`
- Create: `tradingagents/sentiment_scan/snapshot_io.py`

- [ ] **Step 1: Write tests**

```python
"""Tests for snapshot_io: atomic write + tolerant read."""
import json
from pathlib import Path

import pytest


def test_save_and_load_round_trip(tmp_path):
    from tradingagents.sentiment_scan.snapshot_io import save_snapshot, load_snapshot

    snapshot = {
        "schema_version": 1,
        "date": "2026-05-27",
        "sections": {"section_a": {"display": "..."}},
        "analyses": [{"ticker": "600519", "status": "ok"}],
    }
    target = tmp_path / "2026-05-27.json"
    save_snapshot(str(target), snapshot)
    loaded = load_snapshot(str(target))
    assert loaded == snapshot


def test_load_missing_file_returns_none(tmp_path):
    from tradingagents.sentiment_scan.snapshot_io import load_snapshot
    result = load_snapshot(str(tmp_path / "does-not-exist.json"))
    assert result is None


def test_load_malformed_json_returns_none(tmp_path):
    from tradingagents.sentiment_scan.snapshot_io import load_snapshot
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json")
    assert load_snapshot(str(bad)) is None


def test_load_schema_mismatch_returns_none(tmp_path):
    from tradingagents.sentiment_scan.snapshot_io import load_snapshot
    bad = tmp_path / "future.json"
    bad.write_text(json.dumps({"schema_version": 99, "date": "2099-01-01"}))
    assert load_snapshot(str(bad)) is None


def test_save_is_atomic_tmp_renamed(tmp_path):
    """save_snapshot writes to .tmp then renames — no half-written file visible."""
    from tradingagents.sentiment_scan.snapshot_io import save_snapshot
    target = tmp_path / "2026-05-27.json"
    save_snapshot(str(target), {"schema_version": 1, "date": "2026-05-27"})
    assert target.exists()
    # No leftover .tmp file
    assert not (tmp_path / "2026-05-27.json.tmp").exists()
```

- [ ] **Step 2: Run** — all 5 FAIL with ModuleNotFoundError.

- [ ] **Step 3: Implement**

```python
"""Atomic JSON snapshot read/write for the sentiment-scan cross-process state."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

_log = logging.getLogger(__name__)
SCHEMA_VERSION = 1


def save_snapshot(path: str, snapshot: dict) -> None:
    """Atomically write `snapshot` to `path` via tmp + rename."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(snapshot, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, p)  # atomic on POSIX


def load_snapshot(path: str) -> dict | None:
    """Return snapshot dict, or None on missing/malformed/schema-mismatch.

    Logs at WARNING on every failure mode so an operator can diagnose.
    """
    p = Path(path)
    if not p.exists():
        _log.warning("snapshot not found at %s", path)
        return None
    try:
        with p.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        _log.warning("failed to read snapshot %s: %s", path, exc)
        return None
    if not isinstance(data, dict):
        _log.warning("snapshot %s is not a dict", path)
        return None
    if data.get("schema_version") != SCHEMA_VERSION:
        _log.warning(
            "snapshot %s schema_version=%r != expected %r",
            path, data.get("schema_version"), SCHEMA_VERSION,
        )
        return None
    return data
```

- [ ] **Step 4: Run** — all 5 pass.

- [ ] **Step 5: Commit**

```bash
git add tradingagents/sentiment_scan/snapshot_io.py tests/sentiment_scan/test_snapshot_io.py
git commit -m "feat(sentiment_scan): snapshot_io atomic JSON read/write

save_snapshot: write tmp + os.replace 原子化（中途崩不留半截）。
load_snapshot: 任何失败 (missing/malformed/schema mismatch) → None + log warning，永不抛。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase 5 — `feishu_post_v2.py`: 变体 B 排版构建

### Task 5.1: Test full snapshot → post payload structure

**Files:**
- Create: `tests/sentiment_scan/test_feishu_post_v2.py`
- Create: `tradingagents/sentiment_scan/feishu_post_v2.py`

- [ ] **Step 1: Write failing test**

```python
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
```

- [ ] **Step 2: Run** — Expected FAIL ModuleNotFoundError.

### Task 5.2: Implement feishu_post_v2 skeleton + ordering

- [ ] **Step 1: Implement**

```python
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
        for line in display.splitlines():
            if not line.strip():
                continue
            # Skip the original Top-5 emoji header — we replaced it.
            if line.startswith(("🚀", "🐂", "📈", "🇺🇸")):
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
```

- [ ] **Step 2: Run tests** — both pass.

- [ ] **Step 3: Commit**

```bash
git add tradingagents/sentiment_scan/feishu_post_v2.py tests/sentiment_scan/test_feishu_post_v2.py
git commit -m "feat(sentiment_scan): feishu_post_v2 builder skeleton

变体 B 排版: 4 section → 🌟 决策卡块 → 📋 口诀。
━━ 分隔大节; ──── 分隔卡片内多 ticker。
ROE 百分比, FCF ¥亿/万 USD B/M HKD B/M 自动单位。
复用 scripts/daily_sentiment_scan._parse_line_to_feishu_elements 拿 ticker 超链接。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 5.3: Test failure-status cards + format edge cases

- [ ] **Step 1: Append tests**

```python
def test_timeout_ticker_shows_warning_card():
    snap = _make_snapshot()
    snap["analyses"].append({
        "code": "002230", "name": "科大讯飞", "market": "A_SHARE",
        "tier": "bc_only", "ranks": {"b": 3, "c": 4},
        "fundamentals": None, "decision": None, "status": "timeout",
        "elapsed_seconds": 1800, "error": "exceeded per-ticker deadline",
    })
    from tradingagents.sentiment_scan.feishu_post_v2 import build_feishu_post
    payload = build_feishu_post(snap, "2026-05-27")
    full = "\n".join("".join(e.get("text", "") for e in p) for p in payload["content"]["post"]["zh_cn"]["content"])
    assert "⚠ 002230 科大讯飞 — 分析超时" in full
    assert "龙虎榜#3" in full and "雪球飙升#4" in full


def test_error_ticker_shows_truncated_error():
    snap = _make_snapshot()
    long_err = "X" * 500
    snap["analyses"].append({
        "code": "888888", "name": "test", "market": "A_SHARE",
        "tier": "ab_only", "ranks": {"a": 9, "b": 9},
        "fundamentals": None, "decision": None, "status": "error",
        "elapsed_seconds": 5, "error": long_err,
    })
    from tradingagents.sentiment_scan.feishu_post_v2 import build_feishu_post
    payload = build_feishu_post(snap, "2026-05-27")
    full = "\n".join("".join(e.get("text", "") for e in p) for p in payload["content"]["post"]["zh_cn"]["content"])
    assert "分析失败" in full
    # Truncated to 80 chars
    assert full.count("X") <= 80


def test_roe_is_percent_not_decimal():
    """ROE 0.308 should render as 30.8%, not 0.308."""
    from tradingagents.sentiment_scan.feishu_post_v2 import build_feishu_post
    payload = build_feishu_post(_make_snapshot(), "2026-05-27")
    full = "\n".join("".join(e.get("text", "") for e in p) for p in payload["content"]["post"]["zh_cn"]["content"])
    assert "30.8%" in full
    assert "0.308" not in full


def test_fcf_cny_uses_yi_unit():
    """FCF 5.6e10 with CNY currency renders as ¥560.0亿 (not raw number)."""
    from tradingagents.sentiment_scan.feishu_post_v2 import build_feishu_post
    payload = build_feishu_post(_make_snapshot(), "2026-05-27")
    full = "\n".join("".join(e.get("text", "") for e in p) for p in payload["content"]["post"]["zh_cn"]["content"])
    assert "¥560.0亿" in full


def test_zero_intersection_omits_decision_block(monkeypatch):
    """If analyses=[], the 🌟 block is entirely omitted."""
    snap = _make_snapshot()
    snap["analyses"] = []
    snap["sections"]["intersection"] = {"triple": [], "ab_only": [], "ac_only": [], "bc_only": []}
    from tradingagents.sentiment_scan.feishu_post_v2 import build_feishu_post
    payload = build_feishu_post(snap, "2026-05-27")
    full = "\n".join("".join(e.get("text", "") for e in p) for p in payload["content"]["post"]["zh_cn"]["content"])
    assert "🌟" not in full
    # Mantra block still present
    assert "📋" in full
```

- [ ] **Step 2: Run all tests** — Expected: all pass (5 + earlier 2 = 7).

- [ ] **Step 3: Mutation test** — temporarily change `_fmt_roe` to return `f"{float(v):.3f}"` (no *100). Re-run `test_roe_is_percent_not_decimal` — should FAIL. Revert.

- [ ] **Step 4: Commit**

```bash
git add tests/sentiment_scan/test_feishu_post_v2.py
git commit -m "test(sentiment_scan): feishu_post_v2 failure cards + format edge cases

timeout/error 卡片 ⚠ + error 字符串截 80 字符。
ROE 百分比 (30.8% 而非 0.308), FCF ¥560亿 (CNY 单位)。
0 命中时 🌟 决策卡块整体省略，口诀仍展示。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase 6 — `scripts/daily_sentiment_scan.py`: --analyze / --push subcommands

### Task 6.1: Wire structured intersection extraction (helper in script)

Currently `section_e_intersection` returns a string only. We need a structured intersection dict for both snapshot JSON and analysis batch. Don't rewrite `section_e_intersection` — add a new helper alongside.

**Files:**
- Modify: `scripts/daily_sentiment_scan.py` — add `compute_intersection(sec_a, sec_b, sec_c) -> dict` helper

- [ ] **Step 1: Write failing test** (in `tests/sentiment_scan/test_daily_sentiment_scan_cli.py`)

```python
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
```

- [ ] **Step 2: Run** — FAIL `cannot import compute_intersection`.

- [ ] **Step 3: Implement** — add to `scripts/daily_sentiment_scan.py` after `section_e_intersection`:

```python
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
```

- [ ] **Step 4: Run** — PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/daily_sentiment_scan.py tests/sentiment_scan/test_daily_sentiment_scan_cli.py
git commit -m "feat(daily_sentiment_scan): compute_intersection helper

新增结构化版交集助手，4 tier dict（triple/ab/ac/bc）。
section_e_intersection 字符串版保留兼容 stdout 路径。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 6.2: --analyze subcommand

- [ ] **Step 1: Write failing test**

```python
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
```

- [ ] **Step 2: Run** — FAIL (function doesn't exist).

- [ ] **Step 3: Implement** — add to `scripts/daily_sentiment_scan.py`:

```python
# ---------------------------------------------------------------------------
# --analyze subcommand (new): scan + per-ticker analysis + JSON snapshot
# ---------------------------------------------------------------------------

from datetime import datetime, time as _time_cls, timedelta
from pathlib import Path

from tradingagents.sentiment_scan.fundamentals_snapshot import fetch_structured_fundamentals
from tradingagents.sentiment_scan.analysis_runner import run_batch
from tradingagents.sentiment_scan.snapshot_io import save_snapshot, SCHEMA_VERSION


def _default_snapshot_path(date: str) -> str:
    base = os.environ.get(
        "TRADINGAGENTS_SENTIMENT_SCAN_DIR",
        os.path.expanduser("~/.tradingagents/sentiment-scan"),
    )
    return os.path.join(base, f"{date}.json")


def _section_result_to_dict(sec) -> dict:
    return {
        "display": sec.display,
        "top20_codes": list(sec.top20_codes),
        "rank_by_code": dict(sec.rank_by_code),
        "summary_by_code": dict(sec.summary_by_code),
    }


def _cmd_analyze(date: str, output_path: str) -> int:
    """Run scan + per-ticker analysis, write JSON snapshot, return exit code."""
    scan_started = datetime.now().strftime("%H:%M:%S")

    sec_a = section_a_hot_up_rank()
    sec_b = section_b_lhb(date)
    sec_c = section_c_xueqiu_surge()
    sec_d = section_d_stocktwits()
    intersection = compute_intersection(sec_a, sec_b, sec_c)

    scan_done = datetime.now().strftime("%H:%M:%S")

    # Hard deadline: 8:50 today (push fires at 9:05 — leave 15 min buffer)
    today_dt = datetime.strptime(date, "%Y-%m-%d")
    hard_deadline = datetime.combine(today_dt.date(), _time_cls(8, 50))
    # If running for tomorrow's date or after 8:50, give 2.5h from now.
    if hard_deadline <= datetime.now():
        hard_deadline = datetime.now() + timedelta(hours=2, minutes=30)

    # Map tier per code (build rank dict per analysis result for snapshot).
    tier_by_code: dict[str, str] = {}
    ranks_by_code: dict[str, dict] = {}
    for tier in ("triple", "ab_only", "ac_only", "bc_only"):
        for code in intersection[tier]:
            tier_by_code[code] = tier
            ranks_by_code[code] = {
                "a": sec_a.rank_by_code.get(code),
                "b": sec_b.rank_by_code.get(code),
                "c": sec_c.rank_by_code.get(code),
            }

    # Run batch analyses (TradingAgents).
    batch_results = run_batch(intersection, date, hard_deadline)

    # Per-ticker: fetch fundamentals + merge with batch result.
    name_by_code = {c: sec_a.summary_by_code.get(c, "").split(" ")[0] for c in tier_by_code}
    analyses = []
    for r in batch_results:
        code = r["ticker"]
        fundamentals = fetch_structured_fundamentals(code)
        analyses.append({
            "code": code,
            "name": name_by_code.get(code, ""),
            "market": fundamentals.get("market", "A_SHARE"),
            "tier": tier_by_code.get(code, "unknown"),
            "ranks": ranks_by_code.get(code, {}),
            "fundamentals": fundamentals,
            "decision": r.get("decision"),
            "status": r["status"],
            "error": r.get("error"),
            "elapsed_seconds": r.get("elapsed_seconds", 0),
        })

    snapshot = {
        "schema_version": SCHEMA_VERSION,
        "date": date,
        "scan_completed_at": scan_done,
        "analysis_started_at": scan_done,
        "analysis_completed_at": datetime.now().strftime("%H:%M:%S"),
        "analysis_budget_exhausted": any(a["status"] == "budget_exhausted" for a in analyses),
        "sections": {
            "section_a": _section_result_to_dict(sec_a),
            "section_b": _section_result_to_dict(sec_b),
            "section_c": _section_result_to_dict(sec_c),
            "section_d": _section_result_to_dict(sec_d),
            "intersection": intersection,
        },
        "analyses": analyses,
    }

    save_snapshot(output_path, snapshot)
    return 0
```

- [ ] **Step 4: Run** — PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/daily_sentiment_scan.py tests/sentiment_scan/test_daily_sentiment_scan_cli.py
git commit -m "feat(daily_sentiment_scan): --analyze subcommand

跑现有 4 个 section → 计算结构化交集 → 对每只命中股调
fetch_structured_fundamentals + run_batch (单只 30min + 全批 8:50 deadline)
→ atomic write snapshot JSON。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 6.3: --push subcommand

- [ ] **Step 1: Write failing tests**

```python
def test_push_subcommand_reads_json_and_calls_webhook(tmp_path, monkeypatch):
    from scripts.daily_sentiment_scan import _cmd_push

    snap = {
        "schema_version": 1,
        "date": "2026-05-27",
        "scan_completed_at": "06:31",
        "analysis_completed_at": "08:42",
        "sections": {
            "section_a": {"display": "🚀 A股飙升榜\n🔥 SH600519 茅台", "top20_codes": [], "rank_by_code": {}, "summary_by_code": {}},
            "section_b": {"display": "🐂 龙虎榜\n", "top20_codes": [], "rank_by_code": {}, "summary_by_code": {}},
            "section_c": {"display": "📈 雪球\n", "top20_codes": [], "rank_by_code": {}, "summary_by_code": {}},
            "section_d": {"display": "🇺🇸 ST\n", "top20_codes": [], "rank_by_code": {}, "summary_by_code": {}},
            "intersection": {"triple": [], "ab_only": [], "ac_only": [], "bc_only": []},
        },
        "analyses": [],
    }
    snap_path = tmp_path / "2026-05-27.json"
    snap_path.write_text(json.dumps(snap))

    monkeypatch.setenv("TRADINGAGENTS_FEISHU_WEBHOOK", "https://example.test/hook")

    mock_response = MagicMock(status_code=200, text='{"code":0}')
    mock_response.json.return_value = {"code": 0}
    with patch("requests.post", return_value=mock_response) as mock_post:
        rc = _cmd_push(date="2026-05-27", input_path=str(snap_path), no_feishu=False)
    assert rc == 0
    assert mock_post.called
    payload = mock_post.call_args.kwargs["json"]
    assert payload["msg_type"] == "post"


def test_push_with_no_feishu_skips_webhook(tmp_path, monkeypatch):
    from scripts.daily_sentiment_scan import _cmd_push
    snap = {"schema_version": 1, "date": "2026-05-27", "sections": {"section_a": {"display": "", "top20_codes": [], "rank_by_code": {}, "summary_by_code": {}}, "section_b": {"display": "", "top20_codes": [], "rank_by_code": {}, "summary_by_code": {}}, "section_c": {"display": "", "top20_codes": [], "rank_by_code": {}, "summary_by_code": {}}, "section_d": {"display": "", "top20_codes": [], "rank_by_code": {}, "summary_by_code": {}}, "intersection": {"triple": [], "ab_only": [], "ac_only": [], "bc_only": []}}, "analyses": []}
    snap_path = tmp_path / "2026-05-27.json"
    snap_path.write_text(json.dumps(snap))
    monkeypatch.setenv("TRADINGAGENTS_FEISHU_WEBHOOK", "https://example.test/hook")
    with patch("requests.post") as mock_post:
        rc = _cmd_push(date="2026-05-27", input_path=str(snap_path), no_feishu=True)
    assert rc == 0
    assert not mock_post.called


def test_push_with_missing_snapshot_sends_degraded_alert(tmp_path, monkeypatch):
    from scripts.daily_sentiment_scan import _cmd_push
    monkeypatch.setenv("TRADINGAGENTS_FEISHU_WEBHOOK", "https://example.test/hook")
    nonexistent = str(tmp_path / "not-there.json")
    mock_response = MagicMock(status_code=200, text='{"code":0}')
    mock_response.json.return_value = {"code": 0}
    with patch("requests.post", return_value=mock_response) as mock_post:
        rc = _cmd_push(date="2026-05-27", input_path=nonexistent, no_feishu=False)
    # Exit 0 but webhook called with a degraded-alert payload
    assert rc == 0
    assert mock_post.called
    payload = mock_post.call_args.kwargs["json"]
    text_blob = json.dumps(payload, ensure_ascii=False)
    assert "未拿到分析快照" in text_blob or "snapshot" in text_blob.lower()
```

- [ ] **Step 2: Run** — FAIL (function doesn't exist).

- [ ] **Step 3: Implement**

```python
from tradingagents.sentiment_scan.snapshot_io import load_snapshot
from tradingagents.sentiment_scan.feishu_post_v2 import build_feishu_post


def _cmd_push(date: str, input_path: str, no_feishu: bool) -> int:
    """Read snapshot JSON, build 飞书 post, push to webhook."""
    snap = load_snapshot(input_path)
    if snap is None:
        payload = {
            "msg_type": "post",
            "content": {
                "post": {
                    "zh_cn": {
                        "title": f"散户情绪扫盘 {date} — 降级告警",
                        "content": [[{"tag": "text", "text": f"⚠️ 未拿到分析快照 ({input_path})。06:30 --analyze 可能未完成或被中止。"}]],
                    }
                }
            },
        }
    else:
        payload = build_feishu_post(snap, date)

    if no_feishu:
        return 0

    webhook = os.environ.get("TRADINGAGENTS_FEISHU_WEBHOOK")
    if not webhook:
        print("[warning] TRADINGAGENTS_FEISHU_WEBHOOK not set; skipping push", file=sys.stderr)
        return 0
    try:
        import requests
        r = requests.post(webhook, json=payload, timeout=10)
        resp_json = {}
        try:
            resp_json = r.json()
        except Exception:
            pass
        if r.status_code != 200 or resp_json.get("code") != 0:
            print(f"[warning] 飞书 webhook returned {r.status_code}: {r.text[:200]}", file=sys.stderr)
    except Exception as exc:
        print(f"[warning] 飞书 push failed: {exc}", file=sys.stderr)
    return 0
```

- [ ] **Step 4: Run** — both pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/daily_sentiment_scan.py tests/sentiment_scan/test_daily_sentiment_scan_cli.py
git commit -m "feat(daily_sentiment_scan): --push subcommand

读 JSON 快照 → build 飞书 post → POST webhook。
缺失/损坏 → 推降级告警 (\"未拿到分析快照\")。
--no-feishu 跳过 push (dry-run)。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 6.4: Wire subcommands into argparse + mutual exclusion + default unchanged

- [ ] **Step 1: Write tests**

```python
def test_no_flags_default_path_unchanged(tmp_path, monkeypatch, capsys):
    """No --analyze and no --push → existing behavior: print + push directly."""
    from scripts import daily_sentiment_scan as mod
    # Mock build_report + everything that touches the network/disk.
    monkeypatch.setattr(mod, "build_report", lambda d: "REPORT_PLACEHOLDER")
    monkeypatch.setattr(mod, "convert_to_feishu_post", lambda md, d: {"msg_type": "post"})
    monkeypatch.delenv("TRADINGAGENTS_FEISHU_WEBHOOK", raising=False)
    # main() reads sys.argv — set it minimally.
    monkeypatch.setattr(mod.sys, "argv", ["daily_sentiment_scan.py", "--date", "2026-05-27"])
    mod.main()
    out = capsys.readouterr().out
    assert "REPORT_PLACEHOLDER" in out  # current default writes to stdout


def test_analyze_and_push_are_mutually_exclusive(monkeypatch):
    from scripts import daily_sentiment_scan as mod
    monkeypatch.setattr(mod.sys, "argv", ["s.py", "--analyze", "--push"])
    with pytest.raises(SystemExit):
        mod.main()


def test_push_with_feishu_only_rejected(monkeypatch):
    from scripts import daily_sentiment_scan as mod
    monkeypatch.setattr(mod.sys, "argv", ["s.py", "--push", "--feishu-only"])
    with pytest.raises(SystemExit):
        mod.main()


def test_analyze_with_no_feishu_rejected(monkeypatch):
    from scripts import daily_sentiment_scan as mod
    monkeypatch.setattr(mod.sys, "argv", ["s.py", "--analyze", "--no-feishu"])
    with pytest.raises(SystemExit):
        mod.main()
```

- [ ] **Step 2: Run** — most FAIL (argparse hasn't been changed yet).

- [ ] **Step 3: Modify `main()` in `scripts/daily_sentiment_scan.py`**

Replace the body of `main()` with:

```python
def main():
    parser = argparse.ArgumentParser(
        description="Daily retail-attention scan + per-ticker TradingAgents analysis"
    )
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--analyze", action="store_true",
                      help="Scan + analyze + atomic write JSON. Does NOT push to 飞书.")
    mode.add_argument("--push", action="store_true",
                      help="Read JSON snapshot + push 飞书 post. Does NOT scan or analyze.")

    parser.add_argument("--output", default=None,
                        help="(--analyze) JSON output path. Default: ~/.tradingagents/sentiment-scan/<DATE>.json")
    parser.add_argument("--input", default=None,
                        help="(--push) JSON input path. Default: same as --output default.")
    parser.add_argument("--no-feishu", action="store_true",
                        help="Skip 飞书 webhook push.")
    parser.add_argument("--feishu-only", action="store_true",
                        help="(default mode only) Skip stdout, push only.")
    args = parser.parse_args()

    # Reject incompatible flag combinations.
    if args.analyze and (args.no_feishu or args.feishu_only):
        parser.error("--analyze does not push to 飞书; --no-feishu/--feishu-only are not allowed with it")
    if args.push and args.feishu_only:
        parser.error("--push has no stdout output; --feishu-only is redundant")

    if args.analyze:
        output_path = args.output or _default_snapshot_path(args.date)
        sys.exit(_cmd_analyze(date=args.date, output_path=output_path))

    if args.push:
        input_path = args.input or _default_snapshot_path(args.date)
        sys.exit(_cmd_push(date=args.date, input_path=input_path, no_feishu=args.no_feishu))

    # Default (no subcommand): unchanged legacy path.
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
                print(f"[warning] 飞书 webhook returned {r.status_code}: {r.text[:200]}", file=sys.stderr)
        except Exception as exc:
            print(f"[warning] 飞书 push failed: {exc}", file=sys.stderr)
```

- [ ] **Step 4: Run all CLI tests** — all 4 (or more) pass.

- [ ] **Step 5: Re-run baseline 78** — must still pass.

```bash
.venv/bin/pytest tests/web/ tests/test_cli_backend_url_override.py -q
```

Expected: 78 passed.

- [ ] **Step 6: Commit**

```bash
git add scripts/daily_sentiment_scan.py tests/sentiment_scan/test_daily_sentiment_scan_cli.py
git commit -m "feat(daily_sentiment_scan): wire --analyze/--push subcommands + flag exclusion

argparse mutually exclusive --analyze / --push。
--analyze + --no-feishu/--feishu-only 拒绝 (analyze 本来就不推飞书)。
--push + --feishu-only 拒绝 (push 本来就不写 stdout)。
默认无 flag 路径行为完全不变（兼容老 LaunchAgent，baseline 78 不动）。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase 7 — LaunchAgent plists

### Task 7.1: Create daily-analysis.plist (06:30 trigger)

**Files:**
- Create: `web/launchd/com.tradingagents.daily-analysis.plist`

- [ ] **Step 1: Write the plist template**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<!--
  TEMPLATE — do NOT load as-is.

  This LaunchAgent fires at 06:30 Mon-Fri and runs the analysis subcommand
  which writes ~/.tradingagents/sentiment-scan/<DATE>.json. The companion
  daily-feishu-push plist reads that JSON at 09:05.

  Install:
    cp web/launchd/com.tradingagents.daily-analysis.plist ~/Library/LaunchAgents/
    mkdir -p ~/.tradingagents/sentiment-scan
    launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.tradingagents.daily-analysis.plist

  Test manually:
    launchctl kickstart -k gui/$(id -u)/com.tradingagents.daily-analysis

  Unload:
    launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.tradingagents.daily-analysis.plist
-->
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.tradingagents.daily-analysis</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/a1/TradingAgents/.venv/bin/python</string>
        <string>/Users/a1/TradingAgents/scripts/daily_sentiment_scan.py</string>
        <string>--analyze</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/Users/a1/TradingAgents</string>
    <key>StartCalendarInterval</key>
    <array>
        <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>6</integer><key>Minute</key><integer>30</integer></dict>
        <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>6</integer><key>Minute</key><integer>30</integer></dict>
        <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>6</integer><key>Minute</key><integer>30</integer></dict>
        <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>6</integer><key>Minute</key><integer>30</integer></dict>
        <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>6</integer><key>Minute</key><integer>30</integer></dict>
    </array>
    <key>StandardOutPath</key>
    <string>/Users/a1/.tradingagents/logs/daily-analysis-stdout.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/a1/.tradingagents/logs/daily-analysis-stderr.log</string>
    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
```

- [ ] **Step 2: Verify XML well-formedness**

```bash
plutil -lint /Users/a1/TradingAgents/web/launchd/com.tradingagents.daily-analysis.plist
```

Expected: `OK`.

- [ ] **Step 3: Commit**

```bash
git add web/launchd/com.tradingagents.daily-analysis.plist
git commit -m "feat(launchd): com.tradingagents.daily-analysis plist template

Mon-Fri 06:30 启动 --analyze 子命令落 JSON 快照 (~/.tradingagents/sentiment-scan/).
Logs → ~/.tradingagents/logs/daily-analysis-*.log。
TEMPLATE — 安装步骤见文件头注释。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 7.2: Rename existing sentiment-scan plist to feishu-push + update args

**Files:**
- Rename: `web/launchd/com.tradingagents.daily-sentiment-scan.plist` → `web/launchd/com.tradingagents.daily-feishu-push.plist`
- Modify: `ProgramArguments` + `Label` + log paths

- [ ] **Step 1: Use `git mv` to preserve history**

```bash
cd /Users/a1/TradingAgents
git mv web/launchd/com.tradingagents.daily-sentiment-scan.plist web/launchd/com.tradingagents.daily-feishu-push.plist
```

- [ ] **Step 2: Edit the renamed file** to:
- Change `<key>Label</key>` value to `com.tradingagents.daily-feishu-push`
- Replace `<string>--feishu-only</string>` with `<string>--push</string>`
- Update log paths from `daily-sentiment-scan-*.log` → `daily-feishu-push-*.log`
- Keep `TRADINGAGENTS_FEISHU_WEBHOOK` env var slot (still REPLACE_WITH_USER_WEBHOOK_URL)
- Update the header comment block to point at the new bipartite design

```bash
plutil -lint /Users/a1/TradingAgents/web/launchd/com.tradingagents.daily-feishu-push.plist
```

Expected: `OK`.

- [ ] **Step 3: Commit**

```bash
git add web/launchd/
git commit -m "feat(launchd): rename daily-sentiment-scan plist → daily-feishu-push

ProgramArguments --feishu-only → --push (读 JSON 快照推飞书)。
Label / log path 同步重命名。webhook env var slot 保留。
原 09:05 触发时间不变。template only — 用户需 bootout 老 plist + bootstrap 新 plist。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase 8 — Mutation testing pass + dry run + docs

### Task 8.1: Mutation-test the new test suite

For each of these mutations (one at a time), run the relevant test file and verify at least ONE test FAILS. Then revert.

- [ ] **M1: fundamentals_snapshot** — change `_fields_status` to always return `("", "ok")`. Re-run `tests/sentiment_scan/test_fundamentals_snapshot.py`. Expected: `test_a_share_ticker_extracts_pe_roe_fcf_pe_forward_is_none` and `test_hk_ticker_extracts_pe_roe` should FAIL (status assertions). Revert.

- [ ] **M2: analysis_runner** — change `SIGNAL_ACTION_MAP.get(rating, "HOLD")` to `"HOLD"` always. Re-run. Expected: `test_happy_path_returns_ok_with_rating_and_action` should FAIL. Revert.

- [ ] **M3: feishu_post_v2** — change `_fmt_roe` to drop the `*100`. Re-run. Expected: `test_roe_is_percent_not_decimal` should FAIL. Revert.

- [ ] **M4: snapshot_io** — change `os.replace(tmp, p)` to `tmp.rename(p)` then change again to leave a leftover .tmp. Re-run. Expected: `test_save_is_atomic_tmp_renamed` should FAIL on the .tmp leftover. Revert.

- [ ] **M5: daily_sentiment_scan compute_intersection** — flip `(set_a & set_c) - set_b` to `(set_a & set_c) - set_a`. Re-run `tests/sentiment_scan/test_daily_sentiment_scan_cli.py`. Expected: `test_compute_intersection_returns_4_tier_dict` should FAIL. Revert.

If any mutation does NOT produce a failure, the test is too weak — add an assertion or split it.

- [ ] **Step 1: Commit (no production change, just confirmation)**

If any test was strengthened during mutation pass, commit those test changes:

```bash
git add tests/sentiment_scan/
git commit -m "test(sentiment_scan): strengthen assertions per mutation test pass

[describe which tests added detection power]

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

If no strengthening was needed, skip the commit.

### Task 8.2: Local dry-run weekend rehearsal

Run on a weekend day with a recent weekday date for realistic data.

- [ ] **Step 1: Run --analyze for a recent Friday**

```bash
cd /Users/a1/TradingAgents
mkdir -p ~/.tradingagents/sentiment-scan
./.venv/bin/python scripts/daily_sentiment_scan.py --analyze --date 2026-05-22 --output /tmp/test-scan.json
```

Expected: Process runs to completion within ~30-90 min (depends on how many intersection hits). Confirm `/tmp/test-scan.json` exists and JSON-parses.

```bash
.venv/bin/python -c "import json; d=json.load(open('/tmp/test-scan.json')); print('analyses:', len(d['analyses'])); [print(a['code'], a['status'], a.get('decision', {}).get('action')) for a in d['analyses']]"
```

Inspect the count and per-ticker status distribution. Reasonable target: 0-8 analyses with ≥ 1 `status=ok`.

- [ ] **Step 2: Run --push with --no-feishu**

```bash
./.venv/bin/python scripts/daily_sentiment_scan.py --push --input /tmp/test-scan.json --no-feishu
```

Expected: Exits 0, no webhook call. To inspect the payload, temporarily add a `print(json.dumps(payload, indent=2, ensure_ascii=False))` in `_cmd_push` (do NOT commit). Verify ordering, formatting, and that the 决策卡 block contains the expected ticker count.

- [ ] **Step 3: Visual review**

Eyeball the payload `content` array — check that:
- Each section has the `━━━━━━━━` header
- Intersection block has `────` separators between cards
- ROE shows as `XX.X%` (not `0.XXX`)
- FCF has currency symbol (¥/$ /HK$)
- No empty paragraph blocks
- A 股 ticker codes have xueqiu.com links; US tickers have stocktwits.com links

If any visual issue is found, fix and rerun.

### Task 8.3: Update README + memory notes

**Files:**
- Modify: `README.md` (if it documents the sentiment-scan workflow) — describe the new bipartite design
- (No memory file update — that's a separate user-driven step after deploy)

- [ ] **Step 1: Inspect README**

```bash
grep -n "sentiment_scan\|daily-sentiment\|散户情绪" /Users/a1/TradingAgents/README.md 2>/dev/null | head -10
```

If hits exist, edit the affected section to describe `--analyze` / `--push` bipartite flow + JSON snapshot location.

If no hits (README doesn't currently document sentiment-scan), skip README edit.

- [ ] **Step 2: Commit if README was edited**

```bash
git add README.md
git commit -m "docs: README — sentiment-scan bipartite (analyze + push) workflow

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 8.4: Final full-suite verification

- [ ] **Step 1: Run everything**

```bash
.venv/bin/pytest tests/web/ tests/test_cli_backend_url_override.py tests/sentiment_scan/ tests/test_rating_signal_action_map.py -q
```

Expected:
- baseline: 78 passed (tests/web/ + test_cli_backend_url_override)
- new sentiment_scan tests: ~28 passed
- rating signal action map: 2 passed
- total: ~108 passed

If anything red, fix before deploy.

- [ ] **Step 2: Final commit (if any cleanups)**

```bash
git status
# expect: clean tree, all phases committed
```

---

## Phase 9 — Deploy (manual step, user-triggered after Phase 0-8 land + push to origin)

NOT part of TDD/CI flow — this is the production deployment dance. The user runs this manually on weekend.

**Pre-deploy checklist:**
- [ ] All Phase 0-8 tasks committed on main
- [ ] `git push origin main` (user runs; git-guardrails hook will prompt — that's expected)
- [ ] `tests/web/` + baseline still 78 passed on origin/main
- [ ] User has webhook URL ready (same as current `com.tradingagents.daily-sentiment-scan.plist`'s `TRADINGAGENTS_FEISHU_WEBHOOK` env var slot)

**Deploy commands** (user runs at terminal — no Claude automation):

```bash
# 1. Backup current LaunchAgent
cp ~/Library/LaunchAgents/com.tradingagents.daily-sentiment-scan.plist \
   /tmp/backup-old-sentiment-scan-plist.plist

# 2. Bootout current LaunchAgent
launchctl bootout gui/$(id -u) \
  ~/Library/LaunchAgents/com.tradingagents.daily-sentiment-scan.plist

# 3. Copy new plists to LaunchAgents dir
cp web/launchd/com.tradingagents.daily-analysis.plist ~/Library/LaunchAgents/
cp web/launchd/com.tradingagents.daily-feishu-push.plist ~/Library/LaunchAgents/

# 4. Edit the daily-feishu-push.plist to replace REPLACE_WITH_USER_WEBHOOK_URL with real URL
#    (open in editor — the daily-analysis.plist does NOT need a webhook env var)
${EDITOR:-vi} ~/Library/LaunchAgents/com.tradingagents.daily-feishu-push.plist

# 5. Bootstrap both
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.tradingagents.daily-analysis.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.tradingagents.daily-feishu-push.plist

# 6. Create snapshot dir
mkdir -p ~/.tradingagents/sentiment-scan

# 7. Test-fire analyze once (not waiting for 06:30)
launchctl kickstart -k gui/$(id -u)/com.tradingagents.daily-analysis
#    Monitor ~/.tradingagents/logs/daily-analysis-stderr.log — Wait until file exists at
#    ~/.tradingagents/sentiment-scan/<TODAY>.json before moving on

# 8. Test-fire push (will use the JSON just written)
launchctl kickstart -k gui/$(id -u)/com.tradingagents.daily-feishu-push
#    Watch ~/.tradingagents/logs/daily-feishu-push-stderr.log and confirm 飞书 receives msg
```

**Monday-Wednesday observation:**
- 06:30 — process starts; `vm_stat` peak RSS during run
- 08:42-ish — JSON snapshot file timestamp updates
- 09:05 — 飞书 push fires

If peak RSS > 4 GB three days in a row, downgrade: edit `analysis_runner.py` line where `selected_analysts=["fundamentals", "news"]` → `selected_analysts=["fundamentals"]`. Recommit, redeploy.

**Rollback** (if any issue):

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.tradingagents.daily-analysis.plist
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.tradingagents.daily-feishu-push.plist
cp /tmp/backup-old-sentiment-scan-plist.plist ~/Library/LaunchAgents/com.tradingagents.daily-sentiment-scan.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.tradingagents.daily-sentiment-scan.plist
```

---

## Done Criteria

This implementation is complete when:

1. All Phase 0-8 commits land on main
2. Total test count = baseline 78 + ~28 new + 2 rating = ~108 passed, 0 failed
3. Mutation test pass complete (each failure mode has at least one test detecting it)
4. Local dry-run produced a valid JSON snapshot and a visually-clear 飞书 post payload
5. Spec line-by-line cross-checked: every "must" / "永不抛" / "iff" claim is covered by ≥1 test
6. User approves the deploy plan in Phase 9 and runs it manually
