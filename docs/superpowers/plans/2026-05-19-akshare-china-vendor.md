# AkShare 中国/港股数据 vendor 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **执行隔离（硬要求）：** 已部署的 LaunchAgent uvicorn 直接从 `/Users/a1/TradingAgents` 工作树运行。**必须用 superpowers:using-git-worktrees 在独立 worktree 执行**，跑完闸门后由用户决定合并+部署。绝不在生产工作树边改边跑。

**Goal:** 以 CN `akshare.py` 为行为参考，在本 fork 现有 `route_to_vendor` 厂商架构上新增一个自包含 `akshare` 数据 vendor（A股+港股），并把 A股/港股/裸 crypto 的 ticker 正确路由过去；纯增量、默认行为不变、78 测试零回归。

**Architecture:** 新增 `tradingagents/dataflows/akshare_china.py`（零内部耦合，惰性+按需自动安装 akshare）→ 注册进 `interface.py` `VENDOR_METHODS` → 新增共享 `resolve_market()`（ticker→市场）→ 在 CLI `build_analysis_config()` 与 `web/app.py` 注入 per-run 配置：A股/港股把相关类目 vendor 覆写为 `akshare`，并顺带修裸 `ETH`→crypto。default_config 默认 vendor **不变**。

**Tech Stack:** Python 3.10+, akshare(惰性,按需pip), pandas(已有), pytest(mock akshare/pip，全程离线), 现有 langchain `@tool` + `route_to_vendor` 路由。

---

## 约束回顾（每个任务都要守）

- 不碰 `web/`（口令/CSP/纯ASGI/看门狗1800s/历史/单服务/动态版本号）与个人定制语义（`tradingagents/__init__.py override=True`；`cli/main.py build_analysis_config` 仅**追加** market→vendor 覆写，不动其 `TRADINGAGENTS_LLM_BACKEND_URL` 逻辑）。
- 不 `git merge cn`；CN 仅作 `/tmp/tacn` 只读参考。不引入 CN 的 config/constants/cache/llm_adapters/Mongo/Redis/Tushare-token。
- `default_config.DEFAULT_CONFIG` 的 `data_vendors` 默认值保持 `yfinance` 不变；akshare 仅经 per-run 覆写或显式配置启用 → 存量美股/crypto 流程与 `tests/test_dataflows_config.py` 契约零影响。
- 闸门：每阶段结束跑 `.venv/bin/python -m pytest tests/web/ tests/test_cli_backend_url_override.py -q` 必须全过；并跑新加测试。脏树/测试失败即停，不部署。
- 不为验证跑真实多智能体 LLM 分析。数据正确性用「直接调用数据函数的离线 smoke」验证，不经 graph/LLM。

## CN akshare 调用配方（实现参考，来自 `/tmp/tacn/.../providers/china/akshare.py` 与 `hk/improved_hk.py`）

| 用途 | akshare 调用 | 关键点 |
|---|---|---|
| A股符号规整 | 原始 6 位码直传；交易所仅元数据：`60/68/90`→SH `00/30/20`→SZ `8/4`→BJ；news 用 `code.zfill(6)` | 不给 akshare 加 `sh/.SH` |
| A股日线 | `ak.stock_zh_a_hist(symbol, period="daily", start_date=YYYYMMDD, end_date=YYYYMMDD, adjust="qfq")` | 中文列→重命名 日期/开盘/收盘/最高/最低/成交量/成交额/涨跌幅 |
| A股实时 | `ak.stock_bid_ask_em(symbol)` 主；回退 `stock_zh_a_spot`/`_em`/`stock_zh_a_hist` | 长表 item/value |
| A股财务 | `stock_financial_abstract(symbol)` / `stock_balance_sheet_by_report_em` / `stock_profit_sheet_by_report_em` / `stock_cash_flow_sheet_by_report_em` | `.to_dict('records')` 整取 |
| A股个股信息/列表 | `stock_individual_info_em(symbol)` / `stock_info_a_code_name()` | item∈股票简称/所属行业/所属地区/上市时间 |
| A股新闻 | `ak.stock_news_em(code.zfill(6))`；大盘 `ak.news_cctv(limit)` | 需 akshare≥1.17.86（`KeyError cmsArticleWebOld`）|
| 港股符号规整 | 去 `.HK`，左补 0 到 **5 位**（`0700`→`00700`）| |
| 港股行情/日线/财务 | `ak.stock_hk_spot()`(match 代码) / `ak.stock_hk_daily(sym5, adjust="qfq")`(无起止，客户端过滤) / `ak.stock_financial_hk_analysis_indicator_em(sym5)` | PE/PB 由 price/EPS_TTM、price/BPS 导出 |
| 可靠性 | eastmoney 用 `curl_cffi` impersonate chrome120；≥0.5s 节流；SSL/JSON 重试3×；`socket.setdefaulttimeout(60)`；港股 spot 全局锁+10min 缓存 | curl_cffi 缺失则降级注入 UA/Referer |

## 文件结构

- **Create** `tradingagents/dataflows/akshare_china.py` — 自包含 A股/港股取数 + 惰性按需安装；导出与现有 vendor 同签名的函数（`get_stock_data(symbol,start,end)` 等），返回与 yfinance vendor 一致的字符串/格式化契约。
- **Create** `tradingagents/dataflows/_dep_bootstrap.py` — 通用「按需 pip 安装」单飞引导（akshare/curl_cffi 固定版本；mock 友好）。
- **Create** `tradingagents/market_resolver.py` — 单一真相 `resolve_market(ticker) -> Market`（us/a_share/hk/crypto）；裸 `ETH/eth` 等基础币种识别（修已知 bug）。
- **Create** `scripts/install-china-data.sh` — 手动预装兜底口子（可选）。
- **Modify** `tradingagents/dataflows/interface.py` — `VENDOR_METHODS` 各方法加 `"akshare": ...`；`TOOLS_CATEGORIES`/路由逻辑不动。
- **Modify** `cli/main.py` — `build_analysis_config()` 末尾**追加** market→vendor 覆写（A股/港股→akshare）；用 `resolve_market` 替代/补强 crypto 判定，与 web 对齐。不动 LLM_BACKEND_URL 段。
- **Modify** `web/app.py` — `resolve_asset()` 改用 `resolve_market`（裸 ETH→crypto；A股/港股→设置 per-run vendor 覆写并保留 fundamentals）。仅改此函数，不碰口令/CSP/中间件/SSE/历史。
- **Create tests** `tests/test_market_resolver.py`, `tests/test_akshare_china_vendor.py`, `tests/test_dep_bootstrap.py`, `tests/test_akshare_routing_overlay.py`（全部离线 mock akshare/pip）。

## 阶段划分（每阶段独立可测可提交，用户可在任一阶段后叫停）

- **阶段 0**：worktree + 基线闸门
- **阶段 1**：`resolve_market` + 裸 ETH 修 + web/CLI 对齐（最小、立即有用、零数据依赖）
- **阶段 2**：依赖引导 `_dep_bootstrap` + `akshare_china` 骨架 + `get_stock_data`(A股日线) 打通 + 路由覆写
- **阶段 3**：A股 财务（fundamentals/balance/cashflow/income）
- **阶段 4**：A股 新闻 `get_news`
- **阶段 5**：港股（hk_daily/hk_spot/hk financials）
- **阶段 6**：离线 smoke（真连 akshare 取 1 支 A股+1 支港股，不经 LLM）+ 全闸门 + 交付用户决定部署

---

### 阶段 0：worktree 与基线

- [ ] **Step 0.1**：用 @superpowers:using-git-worktrees 建独立 worktree（基于当前 `824de6f`），后续所有改动在 worktree 内。
- [ ] **Step 0.2**：worktree 内建 venv 并装本项目：`python -m venv .venv && .venv/bin/pip install -e . -q`（**不装 akshare**，验证默认面不依赖它）。
- [ ] **Step 0.3**：跑基线闸门 `\.venv/bin/python -m pytest tests/web/ tests/test_cli_backend_url_override.py -q`
  - Expected: 全过（确立绿基线）。失败则先停，报告用户。

---

### 阶段 1：市场解析 + 裸 ETH 修复（web/CLI 对齐）

**Files:** Create `tradingagents/market_resolver.py`, `tests/test_market_resolver.py`; Modify `web/app.py`(仅 `resolve_asset`), `cli/main.py`(仅 crypto 判定处)

- [ ] **Step 1.1：先读现状**：`detect_asset_type`/`AssetType`/`CRYPTO_SUFFIXES` 实际定义在 **`cli/utils.py`(约 L52)**，`cli/main.py` 仅 `from cli.utils import *` 再导出——读 `cli/utils.py` 的这三者全文，以及 `web/app.py:182-195 resolve_asset`/`_CRYPTO_SUFFIXES`。记录两者现有规则，确保新解析器**向后兼容**（现有 `-USD` 等后缀行为不变）。

- [ ] **Step 1.2：写失败测试** `tests/test_market_resolver.py`：

```python
import pytest
from tradingagents.market_resolver import resolve_market, Market

@pytest.mark.unit
@pytest.mark.parametrize("tk,exp", [
    ("AAPL", Market.US), ("SPY", Market.US),
    ("BTC-USD", Market.CRYPTO), ("ETH-USDT", Market.CRYPTO),
    ("ETH", Market.CRYPTO), ("eth", Market.CRYPTO), ("btc", Market.CRYPTO),  # 已知 bug：裸币种
    ("600519", Market.A_SHARE), ("000001", Market.A_SHARE),
    ("600519.SH", Market.A_SHARE), ("000001.SZ", Market.A_SHARE), ("430047.BJ", Market.A_SHARE),
    ("0700.HK", Market.HK), ("00700.HK", Market.HK), ("9988.HK", Market.HK),
])
def test_resolve_market(tk, exp):
    assert resolve_market(tk) == exp
```

- [ ] **Step 1.3：跑测试确认失败**：`\.venv/bin/python -m pytest tests/test_market_resolver.py -q` → Expected: FAIL (module/Market 不存在)。

- [ ] **Step 1.4：实现** `tradingagents/market_resolver.py`：`Market` Enum(US/A_SHARE/HK/CRYPTO)；`resolve_market(ticker)`：优先现有 crypto 后缀集（保持兼容）→ 已知裸币基集合（`{"BTC","ETH","SOL","BNB","XRP","DOGE","ADA",...}` 可保守取主流，文档注明可扩）→ `^\d{6}(\.(SH|SS|SZ|BJ))?$`→A_SHARE → `^\d{1,5}\.HK$`→HK → 否则 US。纯函数无副作用。

- [ ] **Step 1.5：跑测试确认通过**：同 1.3 → Expected: PASS 全绿。

- [ ] **Step 1.6：接入 web** `web/app.py`：`resolve_asset` 内部改调 `resolve_market`，但**返回签名 `tuple[str,list[str]]` 与调用点完全不变**：crypto→维持「drop fundamentals」原行为；A_SHARE/HK→返回 `"stock"`（保留 fundamentals，A股有财报）。**不**经 `resolve_asset` 传递任何市场标记（避免改签名）；阶段 2 的 vendor 覆写改为在配置构造点**独立再调一次** `resolve_market(ticker)`（见 Step 2.12 更正）。**只改此函数体**，口令/CSP/中间件/SSE/历史不动。

- [ ] **Step 1.7：接入 CLI** `cli/main.py`：crypto 判定改用/补强 `resolve_market`（保持 `detect_asset_type` 对外行为；仅让裸 ETH 等也被识别为 crypto，与 web 一致）。不动 `build_analysis_config` 的 `TRADINGAGENTS_LLM_BACKEND_URL` 段。

- [ ] **Step 1.8：闸门**：`\.venv/bin/python -m pytest tests/web/ tests/test_cli_backend_url_override.py tests/test_market_resolver.py tests/test_crypto_asset_mode.py tests/test_ticker_symbol_handling.py -q` → Expected: 全过（含既有 crypto/ticker 测试无回归）。

- [ ] **Step 1.9：提交**：`git add tradingagents/market_resolver.py tests/test_market_resolver.py web/app.py cli/main.py && git commit`（信息说明：新增 resolve_market、修裸 ETH、web/CLI 对齐）。

---

### 阶段 2：依赖按需安装引导 + akshare vendor 骨架 + A股日线打通

**Files:** Create `tradingagents/dataflows/_dep_bootstrap.py`, `tradingagents/dataflows/akshare_china.py`, `scripts/install-china-data.sh`, `tests/test_dep_bootstrap.py`, `tests/test_akshare_china_vendor.py`, `tests/test_akshare_routing_overlay.py`; Modify `tradingagents/dataflows/interface.py`

- [ ] **Step 2.1：写失败测试** `tests/test_dep_bootstrap.py`：mock `importlib.import_module` 与 `subprocess`，断言：(a) 已装→直接返回不调 pip；(b) 未装→调用 `[sys.executable,"-m","pip","install","akshare==<pin>","curl_cffi==<pin>"]` 恰一次（单飞：并发只装一次）；(c) pip 失败→抛 `DependencyUnavailable`，**不**崩溃进程；(d) 全程无真实网络/真实 pip。

- [ ] **Step 2.2：跑→FAIL**：`\.venv/bin/python -m pytest tests/test_dep_bootstrap.py -q`。

- [ ] **Step 2.3：实现** `_dep_bootstrap.py`：`ensure(pkgs: list[str], import_name: str)`：先 `import_module` 试探；失败则进程级 `threading.Lock`+一次性 sentinel，`subprocess.run([sys.executable,"-m","pip","install","--quiet",*pinned], timeout=600)`，结构化日志（stderr：包名/耗时/结果，**不**记任何密钥），成功后重试 import，失败抛 `DependencyUnavailable`。**版本 pin 单一真相**：在 `_dep_bootstrap.py` 定义模块常量 `CHINA_DATA_PINS = ["akshare==<pin>", "curl_cffi==<pin>"]`（`akshare` 下限取 CN 的 `1.17.86`，执行时确认 PyPI 最新稳定后定最终 pin）；`scripts/install-china-data.sh` 与所有测试都引用/复用同一常量，禁止各处各写一份 pin（防漂移）。

- [ ] **Step 2.4：跑→PASS**：同 2.2。

- [ ] **Step 2.5：写失败测试** `tests/test_akshare_china_vendor.py`：用 `unittest.mock` 注入假 `akshare` 模块，喂入按上文配方构造的**仿真 DataFrame**（中文列），断言 `akshare_china.get_stock_data("600519","2026-01-02","2026-01-10")` 返回与 yfinance vendor 同形的格式化字符串（含规整后的 OHLCV、日期升序、symbol 元数据）。再断言 akshare 缺失时触发 `_dep_bootstrap.ensure` 且失败时返回明确错误串而非异常。

- [ ] **Step 2.6：跑→FAIL**：`\.venv/bin/python -m pytest tests/test_akshare_china_vendor.py -q`。

- [ ] **Step 2.7：实现** `akshare_china.py`：模块顶部不 import akshare；函数内 `ak = _dep_bootstrap.ensure(["akshare","curl_cffi"], "akshare")`。实现 `get_stock_data(symbol,start,end)`：`resolve_market` 判 A股/港股；A股走 `ak.stock_zh_a_hist(... adjust="qfq")`→按配方表重命名中文列→复用现有 vendor 的 df→字符串格式化辅助（与 y_finance vendor 输出对齐，必要时抽共享 formatter，DRY）。eastmoney 调用包一层 0.5s 节流 + SSL/JSON 重试（参考配方，保守实现）。

- [ ] **Step 2.8：跑→PASS**：同 2.6。

- [ ] **Step 2.9：注册路由** `interface.py`：`VENDOR_METHODS["get_stock_data"]["akshare"] = akshare_china.get_stock_data`（惰性 import 该模块以免顶层拖累）。`TOOLS_CATEGORIES`、`route_to_vendor`、`get_vendor`、`default_config` **不改**。

- [ ] **Step 2.10：写失败测试** `tests/test_akshare_routing_overlay.py`：构造 per-run 配置覆写（A股 ticker → `data_vendors.core_stock_apis="akshare"`），断言 `route_to_vendor("get_stock_data","600519",...)` 命中 akshare 实现（mock 之）；且**未覆写时**默认仍 `yfinance`（保护 `test_dataflows_config` 契约）。

- [ ] **Step 2.11：跑→FAIL**，实现 per-run 覆写注入点（见 2.12），**跑→PASS**。

- [ ] **Step 2.12：接路由覆写**：在 `cli/main.py build_analysis_config()` **末尾、`return config` 前追加**：`resolve_market(ticker)` 为 A_SHARE/HK 时，把该 config 的 `data_vendors` 中 `core_stock_apis/fundamental_data/news_data` 覆写为 `"akshare"`（仅本次运行的 config dict，不写 `default_config`；不动其上方 `TRADINGAGENTS_LLM_BACKEND_URL` 段）。web 侧：在 `_run_analysis_thread`（`web/app.py` 约 L361，`config = DEFAULT_CONFIG.copy()` 处）**直接再调 `resolve_market(ticker)`** 计算并应用同样的 vendor 覆写——不依赖 `resolve_asset` 的返回，从而 Step 1.6「不改签名」字面成立。只在该构造处加，不碰其它。

- [ ] **Step 2.13：scripts/install-china-data.sh**：兜底脚本，**不硬编码 pin**——而是 `exec "${PYTHON:-.venv/bin/python}" -c "from tradingagents.dataflows._dep_bootstrap import CHINA_DATA_PINS,_pip_install; _pip_install(CHINA_DATA_PINS)"`（或等价：由 Python 侧打印 pins 再装），确保与 `_dep_bootstrap.py` 同一真相；README/计划注明「默认按需自动装，无需手动」。

- [ ] **Step 2.14：闸门**：`\.venv/bin/python -m pytest tests/web/ tests/test_cli_backend_url_override.py tests/test_dataflows_config.py tests/test_dep_bootstrap.py tests/test_akshare_china_vendor.py tests/test_akshare_routing_overlay.py tests/test_market_resolver.py -q` → 全过。

- [ ] **Step 2.15：提交**。

---

### 阶段 3：A股财务（fundamentals/balance/cashflow/income）

**Files:** Modify `akshare_china.py`, `interface.py`, `tests/test_akshare_china_vendor.py`

- [ ] **3.1** 写失败测试：mock akshare `stock_financial_abstract`/`stock_balance_sheet_by_report_em`/`stock_profit_sheet_by_report_em`/`stock_cash_flow_sheet_by_report_em`，断言四个函数返回与对应 yfinance vendor 同形的字符串。
- [ ] **3.2** 跑→FAIL。
- [ ] **3.3** 实现 `get_fundamentals/get_balance_sheet/get_cashflow/get_income_statement`（按配方表，`.to_dict('records')` → 规整 → 同形格式化）。
- [ ] **3.4** 跑→PASS。
- [ ] **3.5** `interface.py` 这四个方法各加 `"akshare"` 实现。
- [ ] **3.6** 闸门（全套）→ 全过。 **3.7** 提交。

---

### 阶段 4：A股新闻

**Files:** Modify `akshare_china.py`, `interface.py`, tests

- [ ] **4.1** 写失败测试：mock `stock_news_em`（中文列）+ `news_cctv`，断言 `get_news` 返回同形字符串；akshare 版本过低/`KeyError cmsArticleWebOld` 时优雅降级（返回明确提示串，不抛）。
- [ ] **4.2** FAIL → **4.3** 实现 `get_news`（个股 `stock_news_em(code.zfill(6))`；可选大盘 `news_cctv`）→ **4.4** PASS。
- [ ] **4.5** `interface.py` `get_news` 加 `"akshare"`。 **4.6** 闸门全过。 **4.7** 提交。

---

### 阶段 5：港股

**Files:** Modify `market_resolver.py`(已含 HK)、`akshare_china.py`, `interface.py`, tests

- [ ] **5.1** 写失败测试：mock `stock_hk_spot`/`stock_hk_daily`(无起止→客户端过滤)/`stock_financial_hk_analysis_indicator_em`；HK 符号 5 位补零；PE/PB 由 price/EPS_TTM、price/BPS 导出。
- [ ] **5.2** FAIL → **5.3** 实现 HK 分支（符号规整 + 三类数据 + 全局锁/缓存按需简化实现）→ **5.4** PASS。
- [ ] **5.5** 闸门全过。 **5.6** 提交。

---

### 阶段 6：离线 smoke + 交付

- [ ] **6.1** 在 worktree venv **真装一次**（验证按需安装路径真跑通）：触发 `_dep_bootstrap`（或跑 `scripts/install-china-data.sh`）。
- [ ] **6.2** **离线 smoke（不经 LLM/graph）**：写一次性脚本直接调 `akshare_china.get_stock_data("600519", <近10日>, ...)` 与一支港股（如 `0700.HK`），人工核对返回结构/数值合理（A股近 5 日收盘、港股日线）。**不跑多智能体分析**。失败时**先区分**：网络抖动/AkShare 反爬瞬时失败（隔几分钟重试 2-3 次，换时段）vs 真实代码缺陷（结构/字段/规整错）——仅后者回到对应阶段修；前者重试并在交付说明里标注 AkShare 抓取本身的不稳定性。
- [ ] **6.3** 终极闸门：`\.venv/bin/python -m pytest tests/web/ tests/test_cli_backend_url_override.py -q`（必须与基线一样全过）+ 全部新测试通过 + `git status` 干净。
- [ ] **6.4** 按 `scripts/update-from-upstream.sh` 思路做「脏树/测试失败即停」闸门校验，输出彩色「下一步」给用户。
- [ ] **6.5** **停**：汇总 diff 与闸门结果交用户。**部署（合并回 `824de6f` 之上、推 origin、`launchctl kickstart -k gui/$(id -u)/com.tradingagents.web`）需用户明确点头**后才做（沿用现有 LaunchAgent 流程；akshare 在生产首个 A股请求时按需自动装）。

---

## 风险与回退

- akshare 抓取不稳/反爬：保守实现节流+重试+`curl_cffi`；失败时 vendor 返回明确错误串（不抛、不污染历史）。因 `route_to_vendor` 仅 rate-limit 回退，A股 ticker 下 yfinance 本就无解，错误串可接受且信息清晰。
- 按需 pip 在生产首个 A股请求会慢一次（一次性，单飞，超时 600s，日志可查 `web-stderr.log`）；非 A股/crypto 路径永不触发。
- 任一阶段失败可独立回退该阶段提交，不影响已上线主干（worktree 隔离 + 未部署）。
- 若用户阶段 1 后即满足（只要裸 ETH 修 + 市场识别），可在阶段 1 提交后停止，后续阶段为可选增量。
