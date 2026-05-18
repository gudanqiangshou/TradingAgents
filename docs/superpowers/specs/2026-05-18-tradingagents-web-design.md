# TradingAgents Web — Design Spec

**Date:** 2026-05-18  
**Status:** Approved  

---

## Overview

Build a responsive web interface on top of the existing TradingAgents multi-agent LLM trading framework. Users enter a stock ticker and configuration, then watch the multi-agent analysis process unfold in real time before seeing the final BUY/SELL/HOLD decision.

---

## Goals

- Public-accessible website anyone can use via a URL
- Real-time visibility into each agent's progress and output
- Mobile and desktop compatible
- Minimal new infrastructure — reuse the existing TradingAgents Python codebase and `.env` configuration

---

## Non-Goals

- Authentication / user accounts
- Storing historical analyses in a database
- Multiple simultaneous analyses
- Backtesting or portfolio tracking

---

## Architecture

```
┌─────────────────────────────────┐        ┌─────────────────────────────────┐
│   GitHub Pages (Frontend)       │        │   Mac Mini (Backend)            │
│                                 │        │                                 │
│  index.html                     │──POST──▶  FastAPI  web/app.py           │
│  style.css              ◀──SSE──│        │  TradingAgents (existing)       │
│  app.js (EventSource)           │        │  Cloudflare Tunnel (stable URL) │
└─────────────────────────────────┘        └─────────────────────────────────┘
```

**Frontend** — static HTML/CSS/Vanilla JS pushed to GitHub Pages (the `web/frontend/` subdirectory of the TradingAgents repo, configured as the Pages source).

**Backend** — FastAPI service added to the existing TradingAgents repo under `web/`, running permanently on the Mac Mini, exposed via a **named Cloudflare Tunnel with a stable custom subdomain** (e.g., `tradingagents-api.yourdomain.com`). Reuses existing `.env` (DeepSeek via AiCodeWith proxy).

**Real-time protocol** — Server-Sent Events (SSE). The server pushes one-directional updates to the browser as agents complete their work. No WebSocket needed.

---

## Repository Structure

```
TradingAgents/               ← existing repo
├── tradingagents/           ← unchanged
├── cli/                     ← unchanged
├── web/
│   ├── app.py               ← FastAPI application (entry point)
│   ├── sse_handler.py       ← SSE event queue and serialization
│   ├── job_manager.py       ← in-memory job state, 1-job lock, watchdog
│   ├── requirements.txt     ← fastapi, uvicorn, sse-starlette, python-dotenv
│   └── frontend/            ← GitHub Pages source
│       ├── index.html
│       ├── config.js        ← BACKEND_URL (gitignored, set per deployment)
│       ├── style.css
│       └── app.js
└── docs/superpowers/specs/
    └── 2026-05-18-tradingagents-web-design.md
```

`config.js` is listed in `.gitignore` and contains only:
```js
const BACKEND_URL = "https://tradingagents-api.yourdomain.com";
```
`app.js` reads `BACKEND_URL` from this file. This avoids baking the tunnel URL into version-controlled source.

---

## Backend API

### `POST /api/analyze`

Start an analysis job. Returns immediately with a `job_id`.

**Request body:**
```json
{
  "ticker": "TSLA",
  "date": "2026-05-18",
  "analysts": ["market", "social", "news", "fundamentals"],
  "language": "Chinese"
}
```

**Response:**
```json
{ "job_id": "uuid4-string" }
```

**Errors:**
- `429` — another job is already running (1 concurrent job limit)
- `422` — validation error (invalid ticker, date format, etc.)

---

### `GET /api/stream/{job_id}`

SSE endpoint. Streams all events for the job: replays buffered events first (for reconnects), then streams live events until `done` or `error`.

**Response headers:** `Content-Type: text/event-stream`

Uses the `Last-Event-ID` header: each SSE event carries a monotonically increasing `id`. On reconnect, the server replays events whose `id` is greater than the `Last-Event-ID` from the event buffer.

---

### `GET /api/report/{job_id}`

Return the complete final report as Markdown. Returns 404 with `{"detail": "job not found or expired"}` if the job is unknown (e.g., after a server restart).

---

## SSE Event Schema

All events carry JSON in the `data` field and a monotonically increasing integer `id`.

| Event | When | Payload |
|---|---|---|
| `agent_status` | Agent state changes | `{ "agent": "新闻分析师", "status": "pending\|in_progress\|completed" }` |
| `report_section` | A report section is ready | `{ "section": "news_report", "content": "## ...(markdown)" }` |
| `final_decision` | Portfolio Manager finishes | `{ "raw": "<full markdown of final_trade_decision>", "action": "BUY\|SELL\|HOLD" }` |
| `done` | Analysis complete | `{ "job_id": "..." }` |
| `error` | Analysis failed | `{ "message": "..." }` |

**`final_decision.action` extraction:** The backend uses `signal_processing.SignalProcessor.process_signal()` (already in the codebase) to extract the signal from `final_trade_decision`. `process_signal()` returns a 5-tier value (`Buy`, `Overweight`, `Hold`, `Underweight`, `Sell`). The backend maps this to a 3-tier `action` for the frontend:

| `process_signal()` result | `action` sent to frontend |
|---|---|
| `Buy` | `BUY` |
| `Overweight` | `BUY` |
| `Hold` | `HOLD` |
| `Underweight` | `SELL` |
| `Sell` | `SELL` |

If `process_signal()` returns `None` or an unrecognised string, `action` defaults to `HOLD`. The full markdown is also included as `raw` so the frontend can render the complete rationale.

---

## TradingAgents Integration

The backend calls the LangGraph stream directly (as the CLI does in `cli/main.py`) rather than calling `propagate()`, which uses `graph.invoke()` and does not stream:

1. `POST /api/analyze` acquires the 1-job lock, then spawns a **daemon background thread** (via `threading.Thread`) to run the analysis. A fresh `TradingAgentsGraph` instance is created **inside the thread** to avoid shared mutable state.

2. The thread calls `graph.graph.stream(init_state, **propagator.get_graph_args())` directly — the same call path used by the CLI. It iterates chunks and uses the `MessageBuffer` detection logic (ported from `cli/main.py`) to detect `agent_status` and `report_sections` changes.

3. Each state-change event is bridged from the sync thread to the async event loop via `asyncio.get_event_loop().call_soon_threadsafe(queue.put_nowait, event)`. A per-job `asyncio.Queue` holds pending events, and a parallel **event buffer list** retains all events for replay on reconnect.

4. The SSE handler (`GET /api/stream/{job_id}`) is an `async` generator that awaits events from the queue and yields them as SSE lines. On reconnect (detected via `Last-Event-ID` header), it replays buffered events before resuming live.

5. On completion, `SignalProcessor.process_signal()` extracts the action from `final_trade_decision`; the backend emits `final_decision` then `done`, and releases the job lock.

**Concurrency:** Only 1 job may run at a time. New requests while a job is active return HTTP 429. The frontend shows a "分析进行中，请稍后再试" overlay.

**Job timeout watchdog:** `job_manager.py` starts a watchdog timer (10 minutes) when a job begins. If the thread has not emitted `done` within 10 minutes, the watchdog pushes an `error` event, terminates the thread (via a threading Event flag checked in the stream loop), and releases the lock. This prevents the server from being permanently stuck.

**Side effects:** Each analysis writes files to `results_dir` (`~/.tradingagents/logs/`) and `data_cache_dir` (`~/.tradingagents/cache/`), and may update `memory_log_path`. These are the same side effects as the CLI and are acceptable for web use.

---

## Frontend UI

### Layout (Desktop)

```
┌─────────────── Top Nav ───────────────────────────────┐
│  ⬡ TradingAgents  |  多智能体金融分析    Powered by DeepSeek │
├───────────────────────────────────────────────────────┤
│  Input Bar: [TSLA] [2026-05-18] [✓市场][✓情绪][✓新闻][✓基本面] [中文▾] [开始分析] │
├──────────────────────┬────────────────────────────────┤
│  Agent Progress      │  Reports Panel                 │
│  (220px fixed)       │  (flex remaining)              │
│                      │  ┌─ Decision Card (pinned) ─┐  │
│  [总进度 N/Total]━━━  │  │  BUY  20%  stop $395     │  │
│  (computed from SSE) │  └──────────────────────────┘  │
│                      │  ┌─ 市场分析报告 ✓ ──────────┐  │
│  分析师团队           │  │  ...markdown content...   │  │
│  ● 市场分析师 ✓       │  └──────────────────────────┘  │
│  ● 情绪分析师 ✓       │  ┌─ 情绪分析报告 ✓ ──────────┐  │
│  ⟳ 新闻分析师 ···     │  │  ...                      │  │
│  ○ 基本面分析师       │  └──────────────────────────┘  │
│                      │  ┌─ ⟳ 新闻分析报告 生成中 ───┐  │
│  研究团队             │  │  ▌                        │  │
│  ○ 多头研究员         │  └──────────────────────────┘  │
│  ○ 空头研究员         │                                │
│  ○ 研究经理           │                                │
│                      │                                │
│  交易/风控/组合       │                                │
│  ○ 交易员             │                                │
│  ○ 风控团队 (3人)     │                                │
│  ○ 组合经理           │                                │
├──────────────────────┴────────────────────────────────┤
│  Status bar: ⟳ 分析进行中 · 新闻分析师运行中    ~3-5分钟 │
└───────────────────────────────────────────────────────┘
```

Agent count and progress fraction are computed dynamically from `agent_status` SSE events — not hardcoded.

### Layout (Mobile)

- Input bar wraps to two rows
- Agent progress panel collapses to a single progress bar strip (tap to expand)
- Reports panel fills full width below
- Decision card stays pinned at top of reports when complete

### Key UI States

| State | Description |
|---|---|
| **Idle** | Input form ready, no analysis running |
| **Analyzing** | Left panel updates live; reports appear one by one; decision card dimmed/placeholder |
| **Complete** | Decision card highlighted (BUY=green / SELL=red / HOLD=yellow); all reports visible; download button appears |
| **Error** | Error banner in status bar; partial reports still visible |
| **Busy (429)** | "当前有分析任务运行中，请稍后再试" overlay |

### Decision Card Colors

- **BUY** — green gradient (`#14532d → #166534`, border `#22c55e`)
- **SELL** — red gradient (`#450a0a → #7f1d1d`, border `#ef4444`)
- **HOLD** — amber gradient (`#451a03 → #92400e`, border `#f59e0b`)

### Report Cards

Each report section renders as a card with:
- Header row: agent name + status badge + team label
- Body: Markdown rendered via `marked.js` (CDN, no build step)
- In-progress state: blinking cursor appended to content

---

## Deployment

### Backend (Mac Mini)

Run from the **repo root** so `tradingagents` package is importable:

```bash
# Install web dependencies (in addition to existing tradingagents deps)
pip install fastapi uvicorn sse-starlette python-dotenv

# Run from repo root — keeps tradingagents on the Python path
uvicorn web.app:app --host 127.0.0.1 --port 8000

# Cloudflare named tunnel (persistent via macOS LaunchAgent)
# tunnel must be pre-created: cloudflared tunnel create tradingagents-web
cloudflared tunnel run tradingagents-web
```

CORS is configured in `app.py` to allow requests from the GitHub Pages origin (`https://<user>.github.io`).

### Frontend (GitHub Pages)

1. Create `web/frontend/config.js` locally (gitignored): set `BACKEND_URL` to the Cloudflare Tunnel URL.
2. Configure GitHub Pages to serve from `web/frontend/` on the `main` branch.
3. `index.html` loads `config.js` before `app.js` with a `<script>` tag.

**Tunnel URL stability:** Use a named Cloudflare Tunnel with a custom domain or a free `*.trycloudflare.com` subdomain locked to the tunnel name. Avoid ephemeral `cloudflared tunnel --url` quick tunnels — those URLs change on restart.

---

## Error Handling

| Scenario | Behaviour |
|---|---|
| SSE connection drops | `EventSource` auto-reconnects with `Last-Event-ID`; server replays missed events from buffer |
| Job ID not found (404) | Frontend shows "会话已过期，请重新提交" and resets to Idle |
| LLM API timeout / exception | Backend catches, pushes `error` event, releases job lock |
| Job watchdog timeout (10 min) | Backend pushes `error` event, releases lock, partial reports remain visible |
| Invalid ticker | yfinance raises on data fetch; caught and returned as `error` event |
| Mac Mini offline | GitHub Pages loads fine; "无法连接到分析服务器" message shown on submit |

---

## Out of Scope

- Job persistence across server restarts (in-memory only)
- Rate limiting beyond the 1-concurrent-job rule
- User authentication
- Saving/sharing past reports
