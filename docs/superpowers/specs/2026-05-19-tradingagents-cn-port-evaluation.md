# TradingAgents-CN 移植评估与决策（2026-05-19）

> 状态：**评估稿，待用户确认范围后才会进入 TDD 实施计划与改代码。本文件不含任何代码改动。**

## 0. 结论先行（TL;DR）

- **绝大多数 CN 改动对本 fork 已无价值或更差**：CN 是 11 个月前的老 TradingAgents 上长出来的全栈中文硬分叉；本 fork 已是上游最新架构，CN 想解决的问题（多语言、国产 LLM、结构化 agents）本 fork 已有**更优的通用实现**。
- **唯一真正有价值、且能安全落地的**：CN 的 **AkShare A股/港股行情数据获取逻辑**——但不是搬代码，而是**以 CN 的 `akshare.py` 为参考，按本 fork 的 `route_to_vendor` 厂商架构重写一个自包含新 vendor 模块**（纯增量，不碰 graph/agents/web/）。
- **附带可一并修的小问题**（与 CN 无关）：裸 `ETH`/`eth` 不识别为 crypto；以及 A股/港股 ticker 的市场判定。属本 fork `web/app.py` + CLI `detect_asset_type` 范畴的小修。
- **明确不做**：CN 的 `app/ frontend/ web/ docker/ nginx/ mongo/redis/ config/ constants/ cache/ llm_adapters/` 以及全部中文 agent prompt——全部**不碰**，绝不 `git merge`，绝不文件级移植。

---

## 1. CN 与本 fork 的真实关系（这决定了一切）

| 事实 | 数据 | 含义 |
|---|---|---|
| CN 与上游的共同祖先 | `718df349`（2025-06-26，"Merge PR #29 save_results"） | CN 是在 **2025 年 6 月**的 TradingAgents 上分叉的 |
| CN 相对祖先 | **143 ahead / 1178 behind** 当前 upstream | CN **落后当前上游 1178 个提交**；它的"现代化/抽象/缓存"很多已被上游自己的新架构取代 |
| 本 fork 相对当前 upstream | **领先 35，落后 0** | 本 fork = 最新上游 + 35 个提交，核心仅改 `cli/main.py`(27行) + `tradingagents/__init__.py`(8行)，其余 35 提交全是 `web/` |
| 结论 | — | **CN 只能当"设计参考"，不能当"补丁/合并源"。** 任何 `git merge cn` 或文件级覆盖都会把本 fork 退回到 11 个月前并摧毁 web/ 与上游新架构。硬约束 #1/#2 在此有数据支撑。 |

本 fork 已具备的、CN 想做却做得更差的能力：
- **多语言输出**：本 fork `agents/utils/agent_utils.py` 有 `get_language_instruction()`，config 驱动 `output_language`，英文时零 token，集中应用到所有产报告的 agent。CN 是把"请用中文回答"**硬编码进每个 prompt**——Chinese-only 硬分叉，无开关。本 fork 严格更优。
- **国产 LLM**：本 fork `llm_clients/model_catalog.py` 已内置 **Qwen/DashScope（含 CN 端点）、GLM/智谱、MiniMax（含 CN 端点）**，`factory.py` 的 OpenAI-compatible 已含 **deepseek**；外加 AiCodeWith 中转。CN 的 `llm_adapters/` 全是 OpenAI-compatible `base_url` 子类（DashScope/DeepSeek/Google-OpenAI），**对本 fork 完全冗余**。
- **结构化 agents + asset_type**：本 fork 有 stock/crypto 的 `asset_type` 全链路（graph→analysts→researchers→trader）+ 模块化 `agents/utils/*_tools.py`。CN 没有 asset_type 概念，agent 层是给弱中文模型打的循环防护补丁，绑死在其重写的老文件上。

---

## 2. CN 改动分类与逐项裁决

| # | 类别 | CN 内容 | 裁决 | 理由 / 风险 |
|---|---|---|---|---|
| A | 全栈本土化（web/app/frontend/docker/nginx/mongo/redis） | 独立前后端 + nginx + Mongo + Redis + 商业授权 | **不碰** | 与本 fork 加固的单服务 `web/`（口令/CSP/纯ASGI/看门狗/动态版本号）架构完全冲突；硬约束 #1 红线 |
| B | 国产 LLM 适配 `llm_adapters/` | DashScope/DeepSeek/Google 的 OpenAI-compatible 子类 | **不移植（冗余）** | 本 fork `llm_clients` 已原生支持 Qwen/GLM/MiniMax/DeepSeek + CN 端点；移植只会引入第二套并行 LLM 体系，增加冲突面 |
| C | **中国/港股数据源 `dataflows/providers/`** | akshare(1676) / tushare(1609) / baostock(902) / hk(517+800) | **择优重写**（见 §3） | `akshare.py` 零内部耦合、无 token、无 DB，是本次唯一金矿；tushare 需付费 token+Mongo（不做）；baostock 自包含可作次选 |
| D | 中文 prompt + agent/graph | ~16 个 agent 文件中文化 + 循环防护 + 货币分支 + 新 `china_market_analyst.py` | **不移植（已被取代/更差）** | 子代理实证：节点/边名与祖先逐字节相同（对 state_tracker.py 本无破坏），但中文化与结构 hack 深度纠缠、不可分离；本 fork 的 `get_language_instruction()`+asset_type+结构化 agents 是其上位替代 |
| E | bug 修 | — | **基本无可移植** | CN 的"修复"几乎都绑死其重写的老文件（如 `706f0120` 改一个只存在于 CN 的硬编码日期 `start_date='2025-05-28'`），对领先 1178 提交的本 fork 不适用 |

---

## 3. 唯一推荐项：AkShare A股/港股 数据作为新 vendor（重写，非搬运）

### 3.1 为什么是 AkShare
子代理实证（`/tmp/tacn/tradingagents/dataflows/providers/china/akshare.py`，1676 行）：
- **零 `tradingagents.*` 内部导入**，只依赖 `akshare` + `pandas` + `requests`；无 Tushare token、无 Mongo/Redis。
- 覆盖：A股代码表、实时/批量报价、日线 OHLCV 历史、完整财报（资产负债/利润/现金流）、新闻（`stock_news_em`、`news_cctv`）。
- 是 CN 自己的默认首选数据源（优先级 AKShare > Tushare > BaoStock）。
- 港股：`improved_hk.py` 复用 akshare；`hk_stock.py` 走 yfinance（本 fork 已有 yfinance）。

### 3.2 本 fork 的集成接缝（已查清）
- `tradingagents/dataflows/interface.py`：`VENDOR_METHODS[method] = {vendor: impl}`；`route_to_vendor(method,*a)` 按 `config["data_vendors"]/["tool_vendors"]` 选 vendor，带 fallback 链（仅 `AlphaVantageRateLimitError` 触发回退）。
- `tradingagents/default_config.py`：`data_vendors`(L95) / `tool_vendors`(L102)；当前 `core_stock_apis` 默认 `yfinance`。
- agent 工具（`agents/utils/core_stock_tools.py` 等）：`@tool` → `route_to_vendor("get_stock_data", ...)` 薄封装。
- 测试闸门契约 `tests/test_dataflows_config.py`：get/set 深拷贝隔离 + 嵌套 partial merge——新增 vendor 只要"加 impl + 可选加 config 项"，不改这套语义即不破闸门。

### 3.3 移植形态（关键：重写而非合并）
- 新增**自包含**模块（拟名 `tradingagents/dataflows/akshare_china.py`），以 CN `akshare.py` 为**行为参考**重写：仅保留"取数→规整成本 fork dataflow 既有返回格式"的纯函数，**不带** CN 的 logging/config/缓存/provider 基类。
- 在 `VENDOR_METHODS` 为相关方法（`get_stock_data`、基本面、新闻等，具体方法集 Phase B 定）注册新 vendor `"akshare"`。
- `default_config.py`：**默认 vendor 保持不变**（yfinance/alpha_vantage），新 vendor 仅在 ticker 命中 A股/港股 或用户显式配置时生效——保证存量行为与 78 测试零回归。
- 依赖：`akshare`（及可选 `curl_cffi` 提升抗反爬）采用**模块内惰性 import**，不写进 `pyproject.toml` 硬依赖——避免污染已部署 LaunchAgent venv、规避 8GB 内存机的体积压力；缺库时给清晰报错。

### 3.4 风险与权衡（务必知情）
- AkShare 依赖网页抓取，**稳定性弱于商业 API**，偶发结构变动/反爬；生产可靠性需 `curl_cffi`。→ 对策：惰性导入 + 优雅降级 + 不设为默认 vendor + 失败回退到既有 vendor。
- 数据字段需适配成本 fork dataflow 既有格式（非直接复制 CN 输出）。
- 网络依赖：与现有 yfinance/alpha_vantage 同级，不新增系统性风险。

---

## 4. 附带小问题（与 CN 无关，可选一并修）

- **现象 1**：`web/app.py:184 resolve_asset()` 只按 `_CRYPTO_SUFFIXES` 后缀判 crypto，裸 `ETH`/`eth`→被当 stock（已知 bug）。
- **现象 2**：A股/港股 ticker 的市场/数据源判定——CLI 侧 `cli/main.py:514 detect_asset_type()` 已认 `000404.SZ/0700.HK`（help 文案有据），web 侧 `resolve_asset` 无此概念。
- 这两者同属"资产/市场解析"族，**与 CN 无关**，是本 fork `web/app.py` + 复用 CLI `detect_asset_type` 的小修。可与 §3 捆绑（A股数据 vendor 落地后正好需要市场判定把 A股 ticker 路由到新 vendor），也可单列。Phase B 需先读 `detect_asset_type` 现状再定。

---

## 5. 红线与"明确不做"清单（硬约束落实）

- 不 `git merge cn`、不文件级覆盖、不动 `origin/upstream`（CN 已隔离克隆在 `/tmp/tacn`，只读）。
- 不碰 `web/`（口令/CSP/纯ASGI中间件/看门狗1800s/历史/单服务/动态版本号防缓存）与两处个人定制（`cli/main.py build_analysis_config`、`tradingagents/__init__.py override=True`）。
- 不引入 CN 的 `config/constants/cache/llm_adapters`、不引入 Mongo/Redis/Tushare-token 依赖、不动 graph/agents 现有结构与 prompt。
- 新 vendor 默认关闭，不改既有默认 vendor，确保 78 测试零回归。

## 6. 对 web/ 与测试闸门的影响

- 新 vendor 纯增量、默认不启用 → **web/ 零影响**（不碰 app.py/state_tracker.py/前端/口令/CSP/隧道）。
- 闸门：改后必跑 `.venv/bin/python -m pytest tests/web/ tests/test_cli_backend_url_override.py -q` 应保持全过；并按 `scripts/update-from-upstream.sh` 思路做"脏树/测试失败即停"闸门；新增本模块单测覆盖纯函数（不真连网、用 mock）。
- 部署仍需用户点头（沿用现有 LaunchAgent 重载流程）。

## 7. 建议的分阶段计划（高层，待确认范围后展开 TDD 细则）

1. 读 `cli/main.py detect_asset_type` 与 `VENDOR_METHODS` 全量，定方法集与 A股/港股 ticker 判定规则。
2. TDD 写 `akshare_china.py` 纯函数（mock akshare，离线单测；不烧 LLM）。
3. 注册 vendor + 配置项（默认关闭）；A股/港股 ticker 路由到新 vendor。
4.（可选）修 `resolve_asset` 裸 crypto + A股/港股市场判定，web/CLI 对齐。
5. 跑闸门测试全过 → 给用户验收 → 用户点头后才部署。
