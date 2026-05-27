---
title: Sentiment Scan with Per-Ticker TradingAgents Analysis
date: 2026-05-27
status: design
related_memory:
  - project_tradingagents_cn_port
---

# Sentiment Scan with Per-Ticker TradingAgents Analysis

## Problem

当前每日散户情绪扫盘 LaunchAgent (`com.tradingagents.daily-sentiment-scan`, Mon–Fri 09:05) 推送 4 个 Top 5 retail-attention sections + 多源交集（仅 codes 与命中级别）。多源交集 codes 出现得多，用户每天需手动对每只命中股 cross-check BUY/HOLD/SELL + PE / 远期PE / FCF / ROE，效率低。

飞书消息排版（emoji + 紧凑行）当前段落间无视觉分隔，扫读吃力。

## Goal

1. **自动对多源交集命中的股票跑 TradingAgents 分析** — A 股 triple-hit + 三个 double-hit 集合的所有 ticker。分析结果中的 BUY/HOLD/SELL + 关键基本面（PE / 远期 PE / FCF / ROE）inline 体现在飞书消息中。
2. **分析提前预跑** —— 单只 ticker 分析 10–15 分钟，最坏每天 0–8 只 = 0–2 小时，必须在 09:05 推送窗口前完成。
3. **飞书排版改造为更清晰、美观、好阅读** — 用 emoji + 字符横线（飞书 post 无 horizontal rule 元素）分隔大节与卡片。

## Hard constraints

- **8GB Mac mini M2 内存预算** — 必须串行分析 ticker，禁止并发。
- **与生产 web 共存** — 生产 web (`com.tradingagents.web`) 共用同台机器；分析进程不得走 web 路径（HTTP/SSE/job_capacity 耦合），避免污染 web history。
- **`scripts/daily_sentiment_scan.py` 现有默认行为不破** — 当前 LaunchAgent 用 `--feishu-only` 不带 `--analyze`/`--push` flag；新增子命令必须 additive。
- **`tests/web/` + `test_cli_backend_url_override` 78 测试 baseline** 保持精确不动。
- **vendor 永不抛契约** 保留（按 4 轮 Codex 审计结论）。

## Architecture

### Two-LaunchAgent split

| File | Schedule | Program |
|---|---|---|
| `com.tradingagents.daily-analysis.plist` (新增) | Mon–Fri **06:30** | `python scripts/daily_sentiment_scan.py --analyze` |
| `com.tradingagents.daily-feishu-push.plist` (替换原 daily-sentiment-scan.plist) | Mon–Fri **09:05** | `python scripts/daily_sentiment_scan.py --push` |

跨进程通信 = 单一 JSON 快照文件 `~/.tradingagents/sentiment-scan/<YYYY-MM-DD>.json`。

### Data flow

```
06:30 cron → --analyze
  1. 跑现有 4 个 section + 交集（复用 section_a/b/c/d/e_intersection 现有函数）
  2. 取交集股 (triple + ab_only + ac_only + bc_only 的 A 股 codes)
  3. 对每只股串行：
     a. fetch_structured_fundamentals(ticker) → vendor 原生 PE/远期PE/FCF/ROE dict
     b. run_single_analysis(ticker, date, deadline) → TradingAgentsGraph
        - selected_analysts=["fundamentals", "news"], max_debate_rounds=1, output_language="zh-Hans"
        - apply_china_vendor_overlay(config, ticker)
        - 单只 watchdog 30 min + 全批 08:50 hard deadline
        - SignalProcessor → BUY/HOLD/SELL
     c. del graph; gc.collect() (释放每只股 ~1GB heap)
  4. atomic write JSON 到 ~/.tradingagents/sentiment-scan/<DATE>.json

09:05 cron → --push
  1. 读 JSON (失败/不存在 → 推降级告警 "未拿到分析快照")
  2. 重组飞书 post payload (变体 B 排版)
  3. POST webhook (TRADINGAGENTS_FEISHU_WEBHOOK env)
```

### JSON snapshot contract

```jsonc
{
  "schema_version": 1,
  "date": "2026-05-27",
  "scan_completed_at": "06:31:08",
  "analysis_started_at": "06:31:08",
  "analysis_completed_at": "08:42:13",
  "analysis_budget_exhausted": false,        // iff any analyses[].status == "budget_exhausted"
  "sections": {
    "section_a": {                            // 复用 SectionResult NamedTuple → dict
      "display": "...",                       // 原 stdout-friendly 文本，push 直接用
      "top20_codes": [...],
      "rank_by_code": {...},
      "summary_by_code": {...}
    },
    "section_b": { /* 同上 */ },
    "section_c": { /* 同上 */ },
    "section_d": { /* 同上 */ },
    "intersection": {                         // 结构化替代原字符串
      "triple": ["600519"],
      "ab_only": ["300866"],
      "ac_only": [],
      "bc_only": ["002230"]
    }
  },
  "analyses": [
    {
      "code": "600519",
      "name": "贵州茅台",
      "market": "A_SHARE",
      "tier": "triple",                       // triple | ab_only | ac_only | bc_only
      "ranks": {"a": 3, "b": 1, "c": 8},     // per-section rank (None if not in that section)
      "fundamentals": {
        "pe_ttm": 25.3,
        "pe_forward": 22.1,                   // None if vendor doesn't expose (A 股/HK 常见)
        "fcf": 5.6e10,                        // float (元/USD/HKD)
        "roe": 0.31,                          // decimal (0.31 = 31%)
        "market_cap": 3.2e12,
        "currency": "CNY",
        "as_of": "2026-05-27",
        "source": "akshare",
        "missing_fields": [],
        "status": "ok"                        // ok | partial | error
      },
      "decision": {
        "rating": "Overweight",               // SignalProcessor → parse_rating() 5-tier: Buy/Overweight/Hold/Underweight/Sell (LLM-faithful)
        "action": "BUY",                      // SIGNAL_ACTION_MAP[rating] 3-tier collapse: Buy/Overweight→BUY, Hold→HOLD, Underweight/Sell→SELL
        "summary_1line": "..."                // 抽自 final_trade_decision 的 Executive Summary 首句
      },
      "elapsed_seconds": 612,
      "status": "ok"                          // ok | partial | incomplete | timeout | error | budget_exhausted
    }
  ]
}
```

注：`analysis_budget_exhausted` (top-level) **iff** `any(a["status"] == "budget_exhausted" for a in analyses)`。两者必须保持一致（push 逻辑单一真相）。

`analyses[].status` 取值与推送呈现规则：

| status | 含义 | 飞书呈现 |
|---|---|---|
| `ok` | fundamentals 全字段 + final_trade_decision 产出 | 完整决策卡 |
| `partial` | fundamentals 部分 None / final_trade_decision 产出 | 缺字段标 "—"，决策正常 |
| `incomplete` | graph 跑完但 final_trade_decision 空 | ⚠ "决策未产出"，仅显示基本面 |
| `timeout` | 单只 30min / 全批 08:50 超 | ⚠ "分析超时"，仅原始信号 |
| `error` | 异常 | ⚠ "分析失败" + 截短 error string |
| `budget_exhausted` | 08:50 前未轮到 | ⚠ "未分析（时间预算用尽）" |

## Modules

```
scripts/daily_sentiment_scan.py             # entrypoint, 新增 --analyze / --push 子命令
tradingagents/sentiment_scan/               # 新包
    __init__.py
    fundamentals_snapshot.py                # fetch_structured_fundamentals(ticker) → dict
    analysis_runner.py                      # run_single_analysis + run_batch
    snapshot_io.py                          # save_snapshot / load_snapshot
    feishu_post_v2.py                       # build_feishu_post(snapshot, date) → post payload
```

### Prerequisite refactor: 把 `SIGNAL_ACTION_MAP` 搬到 rating.py

当前 `SIGNAL_ACTION_MAP`（`Buy/Overweight → BUY`、`Hold → HOLD`、`Underweight/Sell → SELL` 的 5→3 collapse 字典）住在 `web/state_tracker.py:34-40`，但语义属于 rating utilities — 移到 `tradingagents/agents/utils/rating.py` 与 `RATINGS_5_TIER`、`parse_rating()` 同居作为单一真相。

变更：
- 在 `tradingagents/agents/utils/rating.py` 顶部新增 `SIGNAL_ACTION_MAP = {...}` 常量
- `web/state_tracker.py` 删除原定义体，改为 `from tradingagents.agents.utils.rating import SIGNAL_ACTION_MAP`，**且必须保留 `SIGNAL_ACTION_MAP` 在 `web.state_tracker` 模块的公共表面**（不写下划线前缀、不 `del`）——因为 `web/app.py:48` 与 `tests/web/test_state_tracker.py`、`tests/web/test_app.py` 通过 `from web.state_tracker import ... SIGNAL_ACTION_MAP` 导入，re-export 不可丢
- 触动 web 包但只是行号位移与 import path，**不影响 baseline 78 测试语义**

`sentiment_scan/analysis_runner.py` 通过 `from tradingagents.agents.utils.rating import SIGNAL_ACTION_MAP` 拿到，**不依赖 web 包**（保持"分析进程不走 web"约束）。

### `fundamentals_snapshot.py`

`fetch_structured_fundamentals(ticker: str) -> dict`

按市场分发：
- **US**: `yf.Ticker(t).info` via `yf_retry` (复用 vendor helper) — `trailingPE`→pe_ttm / `forwardPE`→pe_forward / `freeCashflow`→fcf / `returnOnEquity`→roe / `marketCap`→market_cap / `currency`→currency
- **A_SHARE**: `akshare.stock_financial_abstract(symbol=code)` — 中文行名匹配："市盈率"→pe_ttm；"净资产收益率(ROE)"→roe；"自由现金流"（若存在）→fcf；"总市值"→market_cap。pe_forward 通常 None（akshare 不暴露 forward consensus）。
- **HK**: `akshare.stock_financial_hk_analysis_indicator_em(symbol=code)` — 列名匹配 (PE_TTM / ROE_AVG / 等)。pe_forward 通常 None。

**永不抛**。任何 vendor 失败 → `status="error"`, `error=msg`, 所有字段 None。部分字段 None → `status="partial"`, `missing_fields=[...]`。

### `analysis_runner.py`

`run_single_analysis(ticker: str, date: str, deadline: datetime) -> dict`

```python
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.dataflows.akshare_china import apply_china_vendor_overlay  # 真实位置, 不在 interface.py
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.graph.signal_processing import SignalProcessor
from tradingagents.agents.utils.rating import SIGNAL_ACTION_MAP                # 见 Modules 节: 本变更将该常量从 web 迁到 rating.py

config = DEFAULT_CONFIG.copy()                  # env vars 已通过 DEFAULT_CONFIG 自动注入
config["max_debate_rounds"] = 1                 # 最快
config["max_risk_discuss_rounds"] = 1
config["output_language"] = "zh-Hans"           # 中文输出
config["checkpoint_enabled"] = False
apply_china_vendor_overlay(config, ticker)     # A 股/HK 自动路由 akshare

graph = TradingAgentsGraph(
    selected_analysts=["fundamentals", "news"],
    debug=False, config=config,
)
asset_type = "stock"                            # 交集股全是 A 股
init_state = graph.propagator.create_initial_state(ticker, date, asset_type=asset_type)

# Iterate stream, check deadline between chunks
final_state = None
for chunk in graph.graph.stream(init_state, **graph.propagator.get_graph_args()):
    if datetime.now() >= deadline:
        return {"status": "timeout", ...}
    final_state = chunk                          # 累积更新

final_decision_md = (final_state or {}).get("final_trade_decision") or ""
if not final_decision_md:
    return {"status": "incomplete", ...}

rating = SignalProcessor().process_signal(final_decision_md)    # 5-tier: Buy/Overweight/Hold/Underweight/Sell
action = SIGNAL_ACTION_MAP.get(rating, "HOLD")                  # 3-tier: BUY/HOLD/SELL
```

**整个函数体（包含所有 import、`apply_china_vendor_overlay`、`TradingAgentsGraph` 构造、stream 迭代、rating 抽取）必须包在外层 `try/except Exception as exc` 中，任何异常都 → `{"status": "error", "error": str(exc)[:200], ...}`。** `apply_china_vendor_overlay` 对未知 ticker 形态可能抛（虽然按 vendor 公开方法契约不应抛，但 `apply_china_vendor_overlay` 不在那个契约范围内）。

调用方负责 `del graph; gc.collect()`。

`run_batch(intersection: dict, date: str, hard_deadline: datetime) -> list[dict]`

- 处理顺序：`triple` → `ab_only` → `ac_only` → `bc_only`（按信号强度 priority）
- 每只前检查 `datetime.now() >= hard_deadline`；若超 → 剩余 ticker `status="budget_exhausted"`
- 每只 ticker 单只 deadline = `min(now + 30 min, hard_deadline)`
- 每只结束 `gc.collect()` 释放 graph heap

### `snapshot_io.py`

- `save_snapshot(path: str, snapshot: dict)` — atomic write（写 `<path>.tmp` 再 `os.rename`）
- `load_snapshot(path: str) -> dict | None` — 失败/不存在/malformed JSON → None + log warning
- Schema 字段 `"schema_version": 1` 写入，load 时校验，不匹配 → None + log warning

### `feishu_post_v2.py`

`build_feishu_post(snapshot: dict, date: str) -> dict`

返回 飞书 post payload。结构（变体 B 顺序）：

1. **Header**: `散户情绪扫盘 · 决策分析 — YYYY-MM-DD`
   subline: `扫描完成 HH:MM · 分析完成 HH:MM · {N_ok} 完整 / {N_partial+incomplete} 部分 / {N_timeout+error+budget} 失败`
2. 🚀 飙升榜 Top 5 (来源 `snapshot.sections.section_a.display`)
3. 🐂 龙虎榜 Top 5
4. 📈 雪球飙升榜 Top 5
5. 🇺🇸 StockTwits Top 5
6. 🌟 **重点关注 · 多源交集决策**（per ticker card，tier 顺序: triple → ab → ac → bc）
7. 📋 决策口诀（4 bullets static text，保持现状）

Section separator: `━━━━━━━━ {emoji} {title} ━━━━━━━━`
Per-ticker separator (in 决策卡 block): `────────────────────────────────`

**Per-ticker card format (status=ok/partial)**:
```
{tier_emoji} {code} {name}
     {tier_label}：{rank_summary}
     💰 建议：{action} ({rating})
     📊 PE {pe_ttm} · 远期PE {pe_forward} · ROE {roe_pct} · FCF {fcf_fmt}
     💡 {summary_1line}
```

tier_emoji map:
- triple → ⭐⭐⭐ (label "三源命中")
- ab_only → ⭐⭐ (label "双源命中 飙升榜∩龙虎榜")
- ac_only → ⭐⭐ (label "双源命中 飙升榜∩雪球")
- bc_only → ⭐⭐ (label "双源命中 龙虎榜∩雪球")

**status=timeout/error/incomplete/budget_exhausted**:
```
⚠ {code} {name} — {status 中文}
     {tier_label}：{rank_summary}
     仅原始信号可参考
```

数字格式化规则：
- ROE → 百分比 `30.8%`（输入 0.308），None → "—"
- FCF：CNY → `¥560亿` (>1e8) / `¥1234.5万` (>1e4) / `¥1234` (<1e4)；USD → `$5.6B`/`$123.4M`/`$1234`；HKD → `HK$XXX`
- PE / 远期PE: 一位小数 `25.3`, None → "—"

A 股 ticker 6 位码继续 link 到 `xueqiu.com/S/{prefix}{code}`；StockTwits section US ticker 继续 link 到 `stocktwits.com/symbol/{ticker}`（与现状一致，复用 `_parse_line_to_feishu_elements`）。

### `scripts/daily_sentiment_scan.py` 改动

新加 subcommand 选项（互斥）：

| Flag 组合 | 行为 |
|---|---|
| 无 flag（与 `--no-feishu` / `--feishu-only` 组合） | **现状不变** — 跑扫描 + stdout + 直推飞书（与 LaunchAgent 当前用法一致）|
| `--analyze [--date YYYY-MM-DD] [--output PATH]` | 跑扫描 + 跑分析 + atomic write JSON。**不**推飞书。|
| `--push [--date YYYY-MM-DD] [--input PATH] [--no-feishu]` | 读 JSON + 重组飞书 post + 推。**不**跑扫描或分析。|

默认 JSON 路径：`~/.tradingagents/sentiment-scan/<DATE>.json`（环境变量 `TRADINGAGENTS_SENTIMENT_SCAN_DIR` 可覆盖）。

Flag 互斥与组合语义：
- `--analyze` 与 `--push` 互斥 — 同时传 → argparse 拒绝
- `--push --no-feishu`: 读 JSON 但不推（dry-run，用于本地验证 payload 形态）
- `--push --feishu-only`: 与 push 语义冗余（`--push` 本来就不写 stdout），CLI 拒绝该组合
- `--analyze` 下 `--no-feishu` / `--feishu-only` 都被 CLI 拒绝（`--analyze` 本来就不推飞书）

## Tests

`tests/sentiment_scan/` (新目录)：

- `test_fundamentals_snapshot.py` (~6 tests)
  - US ticker AAPL → 字段全齐（mock `yf.Ticker.info`）
  - A 股 600519 → pe_ttm/roe/fcf/market_cap 拿到，pe_forward=None
  - HK 0700.HK → 同上
  - yfinance 异常 → status="error"
  - akshare 异常 → status="error"
  - 5 种坏输入永不抛（None / "" / "INVALID" / list / int）

- `test_analysis_runner.py` (~7 tests)
  - mock graph stream 返完整 final_state → status="ok"，rating=5-tier 抽取正确，action=3-tier 来自 SIGNAL_ACTION_MAP[rating]
  - mock graph 中途 deadline 到 → status="timeout"
  - mock graph 跑完但 final_trade_decision="" → status="incomplete"
  - mock graph 抛 → status="error"，error message 截断到 200 字符
  - `apply_china_vendor_overlay` 被调（mock target = `tradingagents.sentiment_scan.analysis_runner.apply_china_vendor_overlay`，匹配新 import path；A 股 ticker 验证 akshare 路由）
  - `apply_china_vendor_overlay` 抛 → 外层 try 兜住 → status="error"（覆盖 reviewer 提的 advisory）
  - per-ticker `del graph; gc.collect()` 验证（mock gc.collect）

- `test_snapshot_io.py` (~4 tests)
  - round-trip (write → read = identity)
  - 读 broken JSON → None + log
  - 读不存在文件 → None
  - schema_version 不匹配 → None

- `test_feishu_post_v2.py` (~6 tests)
  - 完整 snapshot → post payload 结构正确（顺序 + 字段）
  - 0 命中 → 决策卡块整体省略
  - timeout 股 → ⚠ + tier+rank，无基本面/决策行
  - error 股 → ⚠ + 截短 error string
  - ROE 30.8% 而非 0.308；FCF ¥560亿
  - A 股 ticker 链 xueqiu；US ticker 链 stocktwits

- `test_daily_sentiment_scan_cli.py` (~5 tests)
  - `--analyze` → JSON 文件创建（mock 所有 vendor + graph）
  - `--push` 读 JSON → 调 mock webhook，payload 正确
  - `--push` 无 JSON → 推降级告警
  - 无 flag 默认 → 走老路（与现状测试一致）
  - `--analyze --push` 互斥 → CLI 拒绝

**mutation test 守护**（按 `feedback_test_assertion_pollution` 记忆）：写完测试后 revert 一行 production code（如 `status="ok"` 改成 `status="bad"`），跑测试看是否 fail。不 fail 的测试要补 assertion。

**baseline 不动**: `tests/web/` + `test_cli_backend_url_override` 78 测试精确不动。

## Deployment

1. **本地 dry-run（周末 2026-05-30/31）**:
   ```
   ./.venv/bin/python scripts/daily_sentiment_scan.py --analyze --date 2026-05-29 --output /tmp/test.json
   ./.venv/bin/python scripts/daily_sentiment_scan.py --push --input /tmp/test.json --no-feishu
   ```

2. **周日 (2026-05-31) 部署**:
   ```
   # 备份老 plist
   cp ~/Library/LaunchAgents/com.tradingagents.daily-sentiment-scan.plist /tmp/backup-old-scan-plist.plist

   # bootout 老 plist
   launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.tradingagents.daily-sentiment-scan.plist

   # 安装两个新 plists（templates 在 web/launchd/，替换 webhook URL 后 cp 到 ~/Library/LaunchAgents/）
   cp web/launchd/com.tradingagents.daily-analysis.plist ~/Library/LaunchAgents/
   cp web/launchd/com.tradingagents.daily-feishu-push.plist ~/Library/LaunchAgents/

   # bootstrap
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.tradingagents.daily-analysis.plist
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.tradingagents.daily-feishu-push.plist

   # 准备目录
   mkdir -p ~/.tradingagents/sentiment-scan

   # test-fire --analyze 一次（不等到 06:30）
   launchctl kickstart -k gui/$(id -u)/com.tradingagents.daily-analysis
   # 检查 ~/.tradingagents/logs/daily-analysis-stderr.log 和 ~/.tradingagents/sentiment-scan/<DATE>.json
   ```

3. **周一 (2026-06-01) 观察**:
   - 06:30 stderr log
   - JSON 文件 08:42 完整生成
   - 09:05 飞书推送
   - peak RSS via `vm_stat` —— 若超 4GB 降级到只跑 `fundamentals` 一个分析师

4. **Rollback**:
   ```
   launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.tradingagents.daily-analysis.plist
   launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.tradingagents.daily-feishu-push.plist
   cp /tmp/backup-old-scan-plist.plist ~/Library/LaunchAgents/com.tradingagents.daily-sentiment-scan.plist
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.tradingagents.daily-sentiment-scan.plist
   ```

## Out of scope (explicitly deferred)

- StockTwits Top 5 美股不自动跑 TradingAgents 分析（用户明确选 A 股全交集 only）。
- 飞书内"完整报告"链接 → 飞书消息自包含；TradingAgents propagate 已落盘 `~/.tradingagents/logs/<ticker>/<date>/reports/*.md` 供人工查。
- Web UI 集成 sentiment scan 历史 — 分析进程不走 web，不污染 web history。
- Parallel ticker analysis — 8GB 内存不允许。
- Crypto 交集分析 — 交集只算 A 股 sections（A/B/C）。

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| 8GB 内存压力实测未知（单 graph 估 0.8-1.5GB） | 部署后头三天 peak RSS 监控；超 4GB 降级到只跑 `fundamentals` 单一分析师 |
| macOS sleep 期 06:30 plist 不 fire（已知 LaunchAgent 行为，见 `project_shadowrocket_proxy_guard`） | 周一观察；若 miss 加 `pmset schedule` calendar wake |
| 单 LLM 调用 hang（web 已见过 trader 输入过长上游返空） | 单 ticker 30 分钟 watchdog + 全批 08:50 hard deadline 双层防 |
| vendor schema drift（yfinance `.info` 字段名 / akshare 字段名变） | partial status 路径吸收缺字段，run 不 crash |
| JSON 快照缺失/损坏 → 09:05 push 无内容 | push 推降级告警 "未拿到分析快照"（不再独立扫描；保 simplicity） |
| 分析进程异常退出留半截 JSON | atomic write (tmp + rename) 保证 push 要么读全要么读不到 |

## Open questions answered (during brainstorming)

1. 分析范围 → **A 股全交集**（triple + 三个 double，不分析 StockTwits 美股 Top 5）
2. 分析师选择 → **fundamentals + news**（10–15 min/只，最稳）
3. 调度架构 → **拆两个 plist**（06:30 analyze + 09:05 push）
4. 超时策略 → **单只 skip**（status=timeout/error，其它继续）
5. 基本面数据源 → **vendor 原生 + LLM 决策串行**
6. 与生产 web 共存 → **独立进程，直接 import**
7. 排版风格 → **变体 B**（保持现状 4-section 顺序 + 交集块嵌决策卡）
8. 飞书完整报告链接 → **不放**（飞书消息自包含）
