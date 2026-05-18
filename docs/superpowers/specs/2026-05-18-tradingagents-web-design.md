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
│  app.js (EventSource)           │        │  Cloudflare Tunnel (public URL) │
└─────────────────────────────────┘        └─────────────────────────────────┘
```

**Frontend** — static HTML/CSS/Vanilla JS pushed to GitHub Pages (the `web/frontend/` subdirectory of the TradingAgents repo, configured as the Pages source).

**Backend** — FastAPI service added to the existing TradingAgents repo under `web/`, running permanently on the Mac Mini, exposed via Cloudflare Tunnel. Reuses existing `.env` (DeepSeek via AiCodeWith proxy).

**Real-time protocol** — Server-Sent Events (SSE). The server pushes one-directional updates to the browser as agents complete their work. No WebSocket needed.

---

## Repository Structure

```
TradingAgents/               ← existing repo
├── tradingagents/           ← unchanged
├── cli/                     ← unchanged
├── web/
│   ├── app.py               ← FastAPI application
│   ├── sse_handler.py       ← SSE queue + event serialization
│   ├── job_manager.py       ← in-memory job state (1 concurrent job max)
│   ├── requirements.txt     ← fastapi, uvicorn, python-dotenv
│   └── frontend/            ← GitHub Pages source
│       ├── index.html
│       ├── style.css
│       └── app.js
└── docs/superpowers/specs/
    └── 2026-05-18-tradingagents-web-design.md
```

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

SSE endpoint. Streams events until the analysis completes or errors.

**Response headers:** `Content-Type: text/event-stream`

---

### `GET /api/report/{job_id}`

Return the complete final report as Markdown. Used to restore state after a page refresh.

---

## SSE Event Schema

All events carry JSON in the `data` field.

| Event | When | Payload |
|---|---|---|
| `agent_status` | Agent state changes | `{ "agent": "新闻分析师", "status": "pending\|in_progress\|completed" }` |
| `report_section` | A report section is ready | `{ "section": "news_report", "content": "## ...(markdown)" }` |
| `final_decision` | Portfolio Manager finishes | `{ "action": "BUY\|SELL\|HOLD", "quantity": "20%", "stop_loss": "$395", "take_profit": "$460", "rationale": "..." }` |
| `done` | Analysis complete | `{ "job_id": "..." }` |
| `error` | Analysis failed | `{ "message": "..." }` |

---

## TradingAgents Integration

The backend reuses the `MessageBuffer` pattern from `cli/main.py` to detect state changes from LangGraph stream chunks:

1. `POST /api/analyze` spawns a background thread running `TradingAgentsGraph.propagate(ticker, date)`.
2. The thread iterates over LangGraph stream chunks (same as CLI), detecting `agent_status` and `report_sections` changes via the `MessageBuffer` logic.
3. Each state change is pushed to a per-job `asyncio.Queue`.
4. The SSE handler (`GET /api/stream/{job_id}`) consumes the queue and formats events.
5. On completion, `final_trade_decision` is parsed and emitted as `final_decision`, followed by `done`.

**Concurrency:** Only 1 job may run at a time. New requests while a job is active return HTTP 429. The frontend shows a "分析进行中，请稍后" banner.

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
│  [总进度 3/8] ━━━━   │  │  BUY  20%  stop $395     │  │
│                      │  └──────────────────────────┘  │
│  分析师团队           │  ┌─ 市场分析报告 ✓ ──────────┐  │
│  ● 市场分析师 ✓       │  │  ...markdown content...   │  │
│  ● 情绪分析师 ✓       │  └──────────────────────────┘  │
│  ⟳ 新闻分析师 ···     │  ┌─ 情绪分析报告 ✓ ──────────┐  │
│  ○ 基本面分析师       │  │  ...                      │  │
│                      │  └──────────────────────────┘  │
│  研究团队             │  ┌─ ⟳ 新闻分析报告 生成中 ───┐  │
│  ○ 多头研究员         │  │  ▌                        │  │
│  ○ 空头研究员         │  └──────────────────────────┘  │
│  ○ 研究经理           │                                │
│                      │                                │
│  交易/风控/组合       │                                │
│  ○ 交易员             │                                │
│  ○ 风控团队           │                                │
│  ○ 组合经理           │                                │
├──────────────────────┴────────────────────────────────┤
│  Status bar: ⟳ 分析进行中 · 新闻分析师运行中    ~3-5分钟 │
└───────────────────────────────────────────────────────┘
```

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
- Body: Markdown rendered via `marked.js` (CDN, no build)
- In-progress state: blinking cursor at end of content

---

## Deployment

### Backend (Mac Mini)

```bash
# Install dependencies
cd TradingAgents/web
pip install -r requirements.txt

# Run (uses existing .env in repo root)
uvicorn app:app --host 127.0.0.1 --port 8000

# Cloudflare Tunnel (persistent, via LaunchAgent)
cloudflared tunnel run tradingagents-web
```

CORS is configured to allow requests from the GitHub Pages domain (`https://<user>.github.io`).

### Frontend (GitHub Pages)

Configure GitHub Pages to serve from `web/frontend/` on the `main` branch. The `app.js` reads the backend URL from a `BACKEND_URL` constant at the top of the file — update this to the Cloudflare Tunnel URL before pushing.

---

## Error Handling

| Scenario | Behaviour |
|---|---|
| SSE connection drops | `EventSource` auto-reconnects; backend resumes from current job state via the queue |
| LLM API timeout | Backend catches exception, pushes `error` event, marks job done |
| Invalid ticker | yfinance raises on data fetch; caught and returned as `error` event |
| Mac Mini offline | GitHub Pages loads fine; "无法连接到分析服务器" message on submit |

---

## Out of Scope

- Job persistence across server restarts (in-memory only)
- Rate limiting beyond the 1-concurrent-job rule
- User authentication
- Saving/sharing past reports
