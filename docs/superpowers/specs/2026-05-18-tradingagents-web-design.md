# TradingAgents Web — 设计规格

**日期：** 2026-05-18  
**状态：** 已确认  

---

## 概述

在现有 TradingAgents 多智能体 LLM 交易框架之上构建一个响应式 Web 界面。用户输入股票代码和配置参数，实时观察多智能体分析过程，最终获得 BUY/SELL/HOLD 交易决策。

---

## 目标

- 公网可访问，任何人可通过 URL 使用
- 实时展示每个智能体的运行进度和输出内容
- 兼容手机和电脑
- 最小化新增基础设施——复用现有 TradingAgents Python 代码库和 `.env` 配置

---

## 非目标

- 用户认证 / 账户体系
- 历史分析记录持久化存储
- 多任务并发分析
- 回测或投资组合追踪

---

## 系统架构

```
┌─────────────────────────────────┐        ┌─────────────────────────────────┐
│   GitHub Pages（前端）           │        │   Mac Mini（后端）               │
│                                 │        │                                 │
│  index.html                     │──POST──▶  FastAPI  web/app.py           │
│  style.css              ◀──SSE──│        │  TradingAgents（现有项目）        │
│  app.js (EventSource)           │        │  Cloudflare Tunnel（稳定公网URL） │
└─────────────────────────────────┘        └─────────────────────────────────┘
```

**前端** — 纯静态 HTML/CSS/Vanilla JS，推送到 GitHub Pages（TradingAgents 仓库的 `web/frontend/` 子目录，配置为 Pages 来源）。

**后端** — FastAPI 服务，新增在现有 TradingAgents 仓库的 `web/` 目录下，常驻运行于 Mac Mini，通过**命名 Cloudflare Tunnel 的稳定自定义子域名**对外暴露（如 `tradingagents-api.yourdomain.com`）。复用现有 `.env` 配置（DeepSeek via AiCodeWith 中转）。

**实时协议** — Server-Sent Events（SSE）。服务器单向推送智能体状态更新到浏览器，无需 WebSocket。

---

## 仓库结构

```
TradingAgents/               ← 现有仓库
├── tradingagents/           ← 不改动
├── cli/                     ← 不改动
├── web/
│   ├── app.py               ← FastAPI 应用入口
│   ├── sse_handler.py       ← SSE 事件队列与序列化
│   ├── job_manager.py       ← 内存任务状态、1任务锁、超时看门狗
│   ├── requirements.txt     ← fastapi, uvicorn, sse-starlette, python-dotenv
│   └── frontend/            ← GitHub Pages 来源目录
│       ├── index.html
│       ├── config.js        ← BACKEND_URL（已加入 .gitignore，按部署环境设置）
│       ├── style.css
│       └── app.js
└── docs/superpowers/specs/
    └── 2026-05-18-tradingagents-web-design.md
```

`config.js` 加入 `.gitignore`，文件内容仅一行：
```js
const BACKEND_URL = "https://tradingagents-api.yourdomain.com";
```
`app.js` 从该文件读取 `BACKEND_URL`，避免将 Tunnel URL 硬编码进版本控制。

---

## 后端 API

### `POST /api/analyze`

启动分析任务，立即返回 `job_id`。

**请求体：**
```json
{
  "ticker": "TSLA",
  "date": "2026-05-18",
  "analysts": ["market", "social", "news", "fundamentals"],
  "language": "Chinese"
}
```

**响应：**
```json
{ "job_id": "uuid4字符串" }
```

**错误码：**
- `429` — 已有任务在运行（1任务并发限制）
- `422` — 参数校验失败（股票代码非法、日期格式错误等）

---

### `GET /api/stream/{job_id}`

SSE 端点。先重放缓冲事件（支持断线重连），再推送实时事件，直至 `done` 或 `error`。

**响应头：** `Content-Type: text/event-stream`

支持 `Last-Event-ID` 头：每个 SSE 事件携带单调递增整数 `id`。断线重连时，服务端从事件缓冲列表中重放 `id` 大于 `Last-Event-ID` 的事件。

---

### `GET /api/report/{job_id}`

以 Markdown 格式返回完整最终报告。若 job 不存在（如服务重启后），返回 404：`{"detail": "任务不存在或已过期"}`。

---

## SSE 事件格式

所有事件的 `data` 字段为 JSON，携带单调递增整数 `id`。

| 事件名 | 触发时机 | 数据结构 |
|---|---|---|
| `agent_status` | 智能体状态变更 | `{ "agent": "新闻分析师", "status": "pending\|in_progress\|completed" }` |
| `report_section` | 某个报告章节就绪 | `{ "section": "news_report", "content": "## ...(Markdown全文)" }` |
| `final_decision` | 投资组合经理完成 | `{ "raw": "最终决策Markdown全文", "action": "BUY\|SELL\|HOLD" }` |
| `done` | 分析全部完成 | `{ "job_id": "..." }` |
| `error` | 分析失败 | `{ "message": "错误描述" }` |

**`final_decision.action` 提取逻辑：** 后端调用 `signal_processing.SignalProcessor.process_signal()` 从 `final_trade_decision` 字符串中提取信号。`process_signal()` 返回5档值，映射为前端3档 `action`：

| `process_signal()` 返回值 | `action` |
|---|---|
| `Buy` | `BUY` |
| `Overweight` | `BUY` |
| `Hold` | `HOLD` |
| `Underweight` | `SELL` |
| `Sell` | `SELL` |

若返回 `None` 或无法识别的字符串，`action` 默认为 `HOLD`。`raw` 字段同时传递完整 Markdown，供前端渲染完整分析理由。

---

## TradingAgents 集成方式

后端直接调用 LangGraph stream（与 CLI 一致），而非调用 `propagate()`——`propagate()` 内部使用 `graph.invoke()`，不流式：

1. `POST /api/analyze` 获取1任务锁，然后通过 `threading.Thread` 启动**守护后台线程**执行分析。**每个任务在线程内部新建一个 `TradingAgentsGraph` 实例**，避免共享可变状态。

2. 线程直接调用 `graph.graph.stream(init_state, **propagator.get_graph_args())`（与 CLI 相同的调用路径），迭代 chunks，复用 `cli/main.py` 中的 `MessageBuffer` 检测逻辑，识别 `agent_status` 和 `report_sections` 的变化。

3. 每次状态变化通过 `asyncio.get_event_loop().call_soon_threadsafe(queue.put_nowait, event)` 从同步线程桥接到异步事件循环。每个任务维护一个 `asyncio.Queue` 存放待发事件，同时维护一个**事件缓冲列表**保存所有历史事件以支持重连重放。

4. SSE 处理器（`GET /api/stream/{job_id}`）是一个 `async` 生成器，`await` 队列中的事件并格式化为 SSE 行输出。断线重连时（通过 `Last-Event-ID` 头检测），先从缓冲列表重放历史事件，再继续推送实时事件。

5. 分析完成后，调用 `SignalProcessor.process_signal()` 提取 action，推送 `final_decision` 事件，再推送 `done`，最后释放任务锁。

**并发限制：** 同一时间只允许1个任务运行。有任务在跑时新请求返回 HTTP 429，前端显示"当前有分析任务运行中，请稍后再试"遮罩层。

**任务超时看门狗：** `job_manager.py` 在任务启动时开启10分钟计时器。若线程在10分钟内未推送 `done`，看门狗推送 `error` 事件，通过 threading Event 标志终止线程，并释放任务锁，防止服务器永久阻塞。

**磁盘副作用：** 每次分析会向 `results_dir`（`~/.tradingagents/logs/`）和 `data_cache_dir`（`~/.tradingagents/cache/`）写入文件，并可能更新 `memory_log_path`。这与 CLI 行为一致，Web 场景下可接受。

---

## 前端 UI

### 桌面端布局

```
┌─────────────── 顶部导航栏 ────────────────────────────────┐
│  ⬡ TradingAgents  |  多智能体金融分析        Powered by DeepSeek │
├───────────────────────────────────────────────────────────┤
│  输入区：[TSLA] [2026-05-18] [✓市场][✓情绪][✓新闻][✓基本面] [中文▾] [开始分析] │
├──────────────────────┬────────────────────────────────────┤
│  智能体进度面板        │  报告面板                           │
│  （固定220px宽）      │  （flex剩余宽度）                    │
│                      │  ┌─── 决策卡（置顶固定）────────┐   │
│  [总进度 N/总数]━━━━  │  │  BUY  建仓20%  止损$395      │   │
│  （从SSE动态计算）    │  └────────────────────────────┘   │
│                      │  ┌─── 市场分析报告 ✓ ───────────┐  │
│  分析师团队           │  │  ...Markdown内容...           │  │
│  ● 市场分析师 ✓       │  └───────────────────────────── ┘  │
│  ● 情绪分析师 ✓       │  ┌─── 情绪分析报告 ✓ ───────────┐  │
│  ⟳ 新闻分析师 ···     │  │  ...                         │  │
│  ○ 基本面分析师       │  └────────────────────────────┘   │
│                      │  ┌─── ⟳ 新闻分析报告 生成中 ────┐  │
│  研究团队             │  │  ▌                           │  │
│  ○ 多头研究员         │  └────────────────────────────┘   │
│  ○ 空头研究员         │                                   │
│  ○ 研究经理           │                                   │
│                      │                                   │
│  交易/风控/组合       │                                   │
│  ○ 交易员             │                                   │
│  ○ 风控团队（3人）    │                                   │
│  ○ 投资组合经理       │                                   │
├──────────────────────┴────────────────────────────────────┤
│  状态栏：⟳ 分析进行中 · 新闻分析师运行中          约需3-5分钟 │
└───────────────────────────────────────────────────────────┘
```

智能体数量和进度分数从 `agent_status` SSE 事件中动态计算，不硬编码。

### 移动端布局

- 输入区折行为两排
- 智能体进度面板折叠为单行进度条（点击展开）
- 报告面板占满全宽显示在下方
- 分析完成后决策卡固定在报告区顶部

### 页面状态机

| 状态 | 说明 |
|---|---|
| **空闲** | 输入表单就绪，无任务运行 |
| **分析中** | 左侧面板实时更新；报告卡片逐个出现；决策卡半透明占位 |
| **完成** | 决策卡高亮（BUY=绿色 / SELL=红色 / HOLD=琥珀色）；所有报告可见；出现下载按钮 |
| **错误** | 状态栏显示错误提示；已生成的报告仍可查看 |
| **繁忙（429）** | 显示"当前有分析任务运行中，请稍后再试"遮罩层 |

### 决策卡配色

- **BUY** — 绿色渐变（`#14532d → #166534`，边框 `#22c55e`）
- **SELL** — 红色渐变（`#450a0a → #7f1d1d`，边框 `#ef4444`）
- **HOLD** — 琥珀色渐变（`#451a03 → #92400e`，边框 `#f59e0b`）

### 报告卡片

每个报告章节渲染为一张卡片：
- 头部行：智能体名称 + 状态徽章 + 团队标签
- 正文：通过 `marked.js`（CDN引入，无需构建）渲染 Markdown
- 生成中状态：内容末尾显示闪烁光标

---

## 部署说明

### 后端（Mac Mini）

从**仓库根目录**运行，确保 `tradingagents` 包可被导入：

```bash
# 安装 Web 依赖（在现有 tradingagents 依赖基础上追加）
pip install fastapi uvicorn sse-starlette python-dotenv

# 从仓库根目录启动——tradingagents 包自动在 Python 路径中
uvicorn web.app:app --host 127.0.0.1 --port 8000

# Cloudflare 命名隧道（通过 macOS LaunchAgent 持久化运行）
# 需提前创建：cloudflared tunnel create tradingagents-web
cloudflared tunnel run tradingagents-web
```

`app.py` 中配置 CORS，允许来自 GitHub Pages 域名（`https://<用户名>.github.io`）的请求。

### 前端（GitHub Pages）

1. 在本地创建 `web/frontend/config.js`（已加入 `.gitignore`），将 `BACKEND_URL` 设为 Cloudflare Tunnel URL。
2. 在 GitHub 仓库设置中，将 Pages 来源配置为 `web/frontend/` 目录（`main` 分支）。
3. `index.html` 在加载 `app.js` 前先通过 `<script>` 标签加载 `config.js`。

**Tunnel URL 稳定性：** 使用命名 Cloudflare Tunnel 绑定自定义域名或固定的 `*.trycloudflare.com` 子域名。避免使用 `cloudflared tunnel --url` 快速隧道——该方式每次重启 URL 会变化。

---

## 错误处理

| 场景 | 处理方式 |
|---|---|
| SSE 连接断开 | `EventSource` 自动重连，携带 `Last-Event-ID`；服务端从缓冲重放未收到的事件 |
| job_id 不存在（404） | 前端显示"会话已过期，请重新提交"并重置为空闲状态 |
| LLM API 超时 / 异常 | 后端捕获异常，推送 `error` 事件，释放任务锁 |
| 任务看门狗超时（10分钟）| 后端推送 `error` 事件，释放任务锁，已生成的报告仍可查看 |
| 股票代码无效 | yfinance 数据获取时抛出异常，捕获后以 `error` 事件返回 |
| Mac Mini 离线 | GitHub Pages 正常加载；提交时显示"无法连接到分析服务器" |

---

## 超出范围

- 服务重启后的任务持久化（仅内存存储）
- 超出1任务限制的速率控制
- 用户认证
- 历史报告保存与分享
