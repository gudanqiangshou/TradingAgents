# TradingAgents Web 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**目标：** 在现有 TradingAgents 仓库中新增 `web/` 目录，实现 FastAPI 后端 + 纯静态前端，通过 SSE 将多智能体分析过程实时推送到浏览器。

**架构：** FastAPI 在后台线程运行 TradingAgents，通过 `asyncio.Queue` + `call_soon_threadsafe` 桥接将 LangGraph stream 事件推送为 SSE；前端用 `EventSource` 接收并渲染。GitHub Pages 托管前端静态文件，Mac Mini 运行后端，Cloudflare Tunnel 提供公网访问。

**技术栈：** Python 3.11+, FastAPI, sse-starlette, uvicorn, Vanilla JS, marked.js (CDN), pytest, httpx

---

## 文件结构

| 文件 | 职责 |
|---|---|
| `web/state_tracker.py` | 从 LangGraph chunks 提取状态变化，产生结构化事件；移植自 `cli/main.py` 的检测逻辑，无 Rich 依赖 |
| `web/job_manager.py` | 内存任务状态（PENDING/RUNNING/DONE/ERROR）、1任务互斥锁、10分钟超时看门狗 |
| `web/sse_handler.py` | SSE 事件序列化、每任务事件缓冲列表（支持断线重放）、`asyncio.Queue` 封装 |
| `web/app.py` | FastAPI 应用：三个端点、CORS 配置、后台线程调度 |
| `web/requirements.txt` | Web 专用依赖 |
| `web/frontend/index.html` | 页面结构：导航栏、输入区、分栏面板、状态栏 |
| `web/frontend/style.css` | 暗色主题、响应式布局、状态配色 |
| `web/frontend/app.js` | EventSource、SSE 事件处理、UI 状态机 |
| `web/frontend/config.js.example` | 模板文件（真实 config.js 已 gitignore） |
| `tests/web/test_state_tracker.py` | state_tracker 单元测试 |
| `tests/web/test_job_manager.py` | job_manager 单元测试 |
| `tests/web/test_sse_handler.py` | sse_handler 单元测试 |
| `tests/web/test_app.py` | FastAPI 集成测试（TestClient） |

---

## Task 1：项目脚手架

**文件：**
- 创建：`web/__init__.py`
- 创建：`web/requirements.txt`
- 创建：`web/frontend/config.js.example`
- 创建：`tests/web/__init__.py`
- 修改：`.gitignore`

- [ ] **Step 1：创建目录结构**

```bash
mkdir -p /Users/a1/TradingAgents/web
mkdir -p /Users/a1/TradingAgents/web/frontend
mkdir -p /Users/a1/TradingAgents/tests/web
touch /Users/a1/TradingAgents/web/__init__.py
touch /Users/a1/TradingAgents/tests/web/__init__.py
```

- [ ] **Step 2：创建 `web/requirements.txt`**

```
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
sse-starlette>=2.1.0
python-dotenv>=1.0.0
httpx>=0.27.0
pytest>=8.0.0
pytest-asyncio>=0.23.0
```

- [ ] **Step 3：创建 `web/frontend/config.js.example`**

```js
// 复制此文件为 config.js 并填入你的 Cloudflare Tunnel URL
// config.js 已加入 .gitignore，不会被提交
const BACKEND_URL = "https://your-tunnel.trycloudflare.com";
```

- [ ] **Step 4：更新 `.gitignore`**

在 `.gitignore` 末尾追加：
```
web/frontend/config.js
```

- [ ] **Step 5：提交**

```bash
git add web/ tests/web/ .gitignore
git commit -m "feat(web): scaffold web directory structure"
```

---

## Task 2：Job Manager

**文件：**
- 创建：`web/job_manager.py`
- 创建：`tests/web/test_job_manager.py`

- [ ] **Step 1：写失败测试**

创建 `tests/web/test_job_manager.py`：

```python
import time
import threading
import pytest
from web.job_manager import JobManager, JobStatus, JobNotFoundError


def test_create_job_returns_id():
    mgr = JobManager()
    job_id = mgr.create_job()
    assert isinstance(job_id, str) and len(job_id) > 0


def test_new_job_is_pending():
    mgr = JobManager()
    job_id = mgr.create_job()
    assert mgr.get_status(job_id) == JobStatus.PENDING


def test_start_job_sets_running():
    mgr = JobManager()
    job_id = mgr.create_job()
    mgr.start_job(job_id)
    assert mgr.get_status(job_id) == JobStatus.RUNNING


def test_cannot_start_two_jobs():
    mgr = JobManager()
    job1 = mgr.create_job()
    job2 = mgr.create_job()
    mgr.start_job(job1)
    with pytest.raises(RuntimeError, match="already running"):
        mgr.start_job(job2)


def test_finish_job_sets_done():
    mgr = JobManager()
    job_id = mgr.create_job()
    mgr.start_job(job_id)
    mgr.finish_job(job_id)
    assert mgr.get_status(job_id) == JobStatus.DONE


def test_finish_job_releases_lock():
    mgr = JobManager()
    job1 = mgr.create_job()
    job2 = mgr.create_job()
    mgr.start_job(job1)
    mgr.finish_job(job1)
    mgr.start_job(job2)  # should not raise
    assert mgr.get_status(job2) == JobStatus.RUNNING


def test_error_job_releases_lock():
    mgr = JobManager()
    job_id = mgr.create_job()
    mgr.start_job(job_id)
    mgr.error_job(job_id, "something failed")
    assert mgr.get_status(job_id) == JobStatus.ERROR
    job2 = mgr.create_job()
    mgr.start_job(job2)  # lock released


def test_get_status_unknown_raises():
    mgr = JobManager()
    with pytest.raises(JobNotFoundError):
        mgr.get_status("no-such-id")


def test_has_running_job():
    mgr = JobManager()
    assert not mgr.has_running_job()
    job_id = mgr.create_job()
    mgr.start_job(job_id)
    assert mgr.has_running_job()


def test_stop_event_set_on_watchdog_timeout():
    mgr = JobManager(watchdog_timeout=0.1)
    job_id = mgr.create_job()
    mgr.start_job(job_id)
    time.sleep(0.3)
    assert mgr.get_stop_event(job_id).is_set()
    assert mgr.get_status(job_id) == JobStatus.ERROR


def test_get_report_and_set_report():
    mgr = JobManager()
    job_id = mgr.create_job()
    assert mgr.get_report(job_id) is None
    mgr.set_report(job_id, "# Report content")
    assert mgr.get_report(job_id) == "# Report content"
```

- [ ] **Step 2：运行测试确认失败**

```bash
cd /Users/a1/TradingAgents
python -m pytest tests/web/test_job_manager.py -v 2>&1 | head -30
```

期望：`ModuleNotFoundError: No module named 'web.job_manager'`

- [ ] **Step 3：实现 `web/job_manager.py`**

```python
from __future__ import annotations
import threading
import uuid
from enum import Enum
from typing import Optional


class JobStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"


class JobNotFoundError(Exception):
    pass


class _Job:
    def __init__(self, job_id: str, watchdog_timeout: float):
        self.job_id = job_id
        self.status = JobStatus.PENDING
        self.stop_event = threading.Event()
        self.report: Optional[str] = None
        self.error_message: Optional[str] = None
        self._watchdog_timeout = watchdog_timeout
        self._watchdog_timer: Optional[threading.Timer] = None

    def start_watchdog(self, on_timeout):
        self._watchdog_timer = threading.Timer(self._watchdog_timeout, on_timeout)
        self._watchdog_timer.daemon = True
        self._watchdog_timer.start()

    def cancel_watchdog(self):
        if self._watchdog_timer:
            self._watchdog_timer.cancel()


class JobManager:
    def __init__(self, watchdog_timeout: float = 600.0):
        self._jobs: dict[str, _Job] = {}
        self._lock = threading.Lock()
        self._running_job_id: Optional[str] = None
        self._watchdog_timeout = watchdog_timeout

    def create_job(self) -> str:
        job_id = str(uuid.uuid4())
        self._jobs[job_id] = _Job(job_id, self._watchdog_timeout)
        return job_id

    def _get(self, job_id: str) -> _Job:
        if job_id not in self._jobs:
            raise JobNotFoundError(f"Job {job_id} not found")
        return self._jobs[job_id]

    def start_job(self, job_id: str) -> None:
        with self._lock:
            if self._running_job_id is not None:
                raise RuntimeError(
                    f"Job {self._running_job_id} already running"
                )
            job = self._get(job_id)
            job.status = JobStatus.RUNNING
            self._running_job_id = job_id
            job.start_watchdog(lambda: self._watchdog_fire(job_id))

    def _watchdog_fire(self, job_id: str) -> None:
        try:
            job = self._get(job_id)
            if job.status == JobStatus.RUNNING:
                job.stop_event.set()
                self.error_job(job_id, "分析超时（10分钟）")
        except JobNotFoundError:
            pass

    def finish_job(self, job_id: str) -> None:
        with self._lock:
            job = self._get(job_id)
            job.cancel_watchdog()
            job.status = JobStatus.DONE
            if self._running_job_id == job_id:
                self._running_job_id = None

    def error_job(self, job_id: str, message: str = "") -> None:
        with self._lock:
            job = self._get(job_id)
            job.cancel_watchdog()
            job.status = JobStatus.ERROR
            job.error_message = message
            if self._running_job_id == job_id:
                self._running_job_id = None

    def get_status(self, job_id: str) -> JobStatus:
        return self._get(job_id).status

    def get_stop_event(self, job_id: str) -> threading.Event:
        return self._get(job_id).stop_event

    def has_running_job(self) -> bool:
        return self._running_job_id is not None

    def set_report(self, job_id: str, content: str) -> None:
        self._get(job_id).report = content

    def get_report(self, job_id: str) -> Optional[str]:
        return self._get(job_id).report
```

- [ ] **Step 4：运行测试确认通过**

```bash
python -m pytest tests/web/test_job_manager.py -v
```

期望：所有测试 PASS

- [ ] **Step 5：提交**

```bash
git add web/job_manager.py tests/web/test_job_manager.py
git commit -m "feat(web): add JobManager with 1-job lock and watchdog"
```

---

## Task 3：SSE Handler

**文件：**
- 创建：`web/sse_handler.py`
- 创建：`tests/web/test_sse_handler.py`

- [ ] **Step 1：写失败测试**

创建 `tests/web/test_sse_handler.py`：

```python
import asyncio
import pytest
from web.sse_handler import EventBuffer, format_sse


def test_format_sse_basic():
    result = format_sse("agent_status", {"agent": "市场分析师", "status": "in_progress"}, event_id=1)
    assert "event: agent_status\n" in result
    assert '"agent": "市场分析师"' in result
    assert "id: 1\n" in result
    assert result.endswith("\n\n")


def test_event_buffer_stores_events():
    buf = EventBuffer()
    buf.add("agent_status", {"agent": "A", "status": "pending"})
    buf.add("agent_status", {"agent": "A", "status": "in_progress"})
    assert len(buf.events) == 2


def test_event_buffer_ids_are_sequential():
    buf = EventBuffer()
    buf.add("agent_status", {"agent": "A", "status": "pending"})
    buf.add("agent_status", {"agent": "A", "status": "in_progress"})
    assert buf.events[0]["id"] == 1
    assert buf.events[1]["id"] == 2


def test_event_buffer_replay_from_id():
    buf = EventBuffer()
    buf.add("agent_status", {"agent": "A", "status": "pending"})
    buf.add("report_section", {"section": "market_report", "content": "hello"})
    buf.add("done", {"job_id": "x"})
    replayed = buf.get_events_after(1)
    assert len(replayed) == 2
    assert replayed[0]["id"] == 2


def test_event_buffer_replay_from_zero_returns_all():
    buf = EventBuffer()
    buf.add("agent_status", {"agent": "A", "status": "pending"})
    buf.add("done", {"job_id": "x"})
    assert len(buf.get_events_after(0)) == 2


@pytest.mark.asyncio
async def test_queue_receives_event():
    buf = EventBuffer()
    loop = asyncio.get_event_loop()

    loop.call_soon_threadsafe(buf.queue.put_nowait, {"type": "agent_status", "data": {"agent": "A", "status": "pending"}})
    event = await asyncio.wait_for(buf.queue.get(), timeout=1.0)
    assert event["type"] == "agent_status"
```

- [ ] **Step 2：运行测试确认失败**

```bash
python -m pytest tests/web/test_sse_handler.py -v 2>&1 | head -20
```

期望：`ModuleNotFoundError: No module named 'web.sse_handler'`

- [ ] **Step 3：实现 `web/sse_handler.py`**

```python
from __future__ import annotations
import asyncio
import json
from dataclasses import dataclass, field
from typing import Any


def format_sse(event_name: str, data: dict, event_id: int) -> str:
    """Serialize a single SSE event to wire format."""
    payload = json.dumps(data, ensure_ascii=False)
    return f"id: {event_id}\nevent: {event_name}\ndata: {payload}\n\n"


@dataclass
class EventBuffer:
    """Per-job event buffer: stores all events for replay and exposes an asyncio.Queue."""
    events: list[dict] = field(default_factory=list)
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    _next_id: int = 1

    def add(self, event_name: str, data: dict[str, Any]) -> dict:
        """Append an event to the buffer (called from SSE handler or background thread via queue)."""
        event = {"id": self._next_id, "type": event_name, "data": data}
        self.events.append(event)
        self._next_id += 1
        return event

    def get_events_after(self, last_id: int) -> list[dict]:
        """Return all events with id > last_id (for reconnect replay)."""
        return [e for e in self.events if e["id"] > last_id]
```

- [ ] **Step 4：运行测试确认通过**

```bash
python -m pytest tests/web/test_sse_handler.py -v
```

期望：所有测试 PASS

- [ ] **Step 5：提交**

```bash
git add web/sse_handler.py tests/web/test_sse_handler.py
git commit -m "feat(web): add EventBuffer and SSE serialization"
```

---

## Task 4：状态追踪器（移植 CLI 检测逻辑）

**文件：**
- 创建：`web/state_tracker.py`
- 创建：`tests/web/test_state_tracker.py`

- [ ] **Step 1：写失败测试**

创建 `tests/web/test_state_tracker.py`：

```python
import pytest
from web.state_tracker import AgentTracker, process_chunk, SIGNAL_ACTION_MAP


def make_tracker(analysts=None):
    return AgentTracker(analysts or ["market", "social", "news", "fundamentals"])


def test_initial_all_pending():
    tracker = make_tracker()
    assert all(s == "pending" for s in tracker.agent_status.values())


def test_analyst_completed_when_report_present():
    tracker = make_tracker(["market"])
    events = process_chunk(tracker, {"market_report": "## Market Analysis\nContent here"})
    status_events = [e for e in events if e["type"] == "agent_status" and e["data"]["agent"] == "Market Analyst"]
    completed = [e for e in status_events if e["data"]["status"] == "completed"]
    assert len(completed) >= 1


def test_report_section_event_emitted():
    tracker = make_tracker(["market"])
    events = process_chunk(tracker, {"market_report": "## Market Analysis\nContent here"})
    section_events = [e for e in events if e["type"] == "report_section"]
    assert any(e["data"]["section"] == "market_report" for e in section_events)


def test_no_duplicate_events_same_content():
    tracker = make_tracker(["market"])
    process_chunk(tracker, {"market_report": "Content"})
    events2 = process_chunk(tracker, {"market_report": "Content"})  # same content
    section_events = [e for e in events2 if e["type"] == "report_section" and e["data"]["section"] == "market_report"]
    assert len(section_events) == 0  # no duplicate


def test_research_team_in_progress_after_analysts_complete():
    tracker = make_tracker(["market"])
    process_chunk(tracker, {"market_report": "Content"})
    events = process_chunk(tracker, {"investment_debate_state": {
        "bull_history": "Bull analysis here",
        "bear_history": "",
        "judge_decision": "",
    }})
    status_map = {e["data"]["agent"]: e["data"]["status"] for e in events if e["type"] == "agent_status"}
    assert status_map.get("Bull Researcher") in ("in_progress", None) or \
           tracker.agent_status.get("Bull Researcher") == "in_progress"


def test_final_decision_events_on_portfolio_judge():
    tracker = make_tracker(["market"])
    events = process_chunk(tracker, {
        "risk_debate_state": {
            "aggressive_history": "Aggressive view",
            "conservative_history": "Conservative view",
            "neutral_history": "Neutral view",
            "judge_decision": "**Rating**: Buy\nFinal recommendation...",
        }
    })
    # Portfolio manager should be completed
    pm_events = [e for e in events if e["type"] == "agent_status"
                 and e["data"]["agent"] == "Portfolio Manager"
                 and e["data"]["status"] == "completed"]
    assert len(pm_events) >= 1


def test_signal_action_map_covers_all_five():
    assert SIGNAL_ACTION_MAP["Buy"] == "BUY"
    assert SIGNAL_ACTION_MAP["Overweight"] == "BUY"
    assert SIGNAL_ACTION_MAP["Hold"] == "HOLD"
    assert SIGNAL_ACTION_MAP["Underweight"] == "SELL"
    assert SIGNAL_ACTION_MAP["Sell"] == "SELL"
```

- [ ] **Step 2：运行测试确认失败**

```bash
python -m pytest tests/web/test_state_tracker.py -v 2>&1 | head -20
```

期望：`ModuleNotFoundError: No module named 'web.state_tracker'`

- [ ] **Step 3：实现 `web/state_tracker.py`**

```python
"""
Agent state tracker for the web backend.
Ported from cli/main.py (MessageBuffer + update_analyst_statuses + chunk handlers),
with all Rich/typer/display dependencies removed.
Produces structured event dicts suitable for SSE emission.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

ANALYST_ORDER = ["market", "social", "news", "fundamentals"]

ANALYST_AGENT_NAMES = {
    "market": "Market Analyst",
    "social": "Sentiment Analyst",
    "news": "News Analyst",
    "fundamentals": "Fundamentals Analyst",
}

ANALYST_REPORT_MAP = {
    "market": "market_report",
    "social": "sentiment_report",
    "news": "news_report",
    "fundamentals": "fundamentals_report",
}

FIXED_AGENTS = [
    "Bull Researcher", "Bear Researcher", "Research Manager",
    "Trader",
    "Aggressive Analyst", "Neutral Analyst", "Conservative Analyst",
    "Portfolio Manager",
]

SIGNAL_ACTION_MAP = {
    "Buy": "BUY",
    "Overweight": "BUY",
    "Hold": "HOLD",
    "Underweight": "SELL",
    "Sell": "SELL",
}


@dataclass
class AgentTracker:
    selected_analysts: list[str]
    agent_status: dict[str, str] = field(default_factory=dict)
    report_sections: dict[str, str | None] = field(default_factory=dict)

    def __post_init__(self):
        for key in self.selected_analysts:
            name = ANALYST_AGENT_NAMES.get(key)
            if name:
                self.agent_status[name] = "pending"
        for agent in FIXED_AGENTS:
            self.agent_status[agent] = "pending"
        for key in ANALYST_ORDER:
            if key in self.selected_analysts:
                self.report_sections[ANALYST_REPORT_MAP[key]] = None
        for section in ["investment_plan", "trader_investment_plan", "final_trade_decision"]:
            self.report_sections[section] = None


def _emit_status(tracker: AgentTracker, agent: str, status: str,
                 prev_status: dict, events: list) -> None:
    if tracker.agent_status.get(agent) != status:
        tracker.agent_status[agent] = status
        if prev_status.get(agent) != status:
            events.append({"type": "agent_status", "data": {"agent": agent, "status": status}})


def _emit_section(tracker: AgentTracker, section: str, content: str,
                  prev_sections: dict, events: list) -> None:
    if content and content != prev_sections.get(section):
        tracker.report_sections[section] = content
        events.append({"type": "report_section", "data": {"section": section, "content": content}})


def process_chunk(tracker: AgentTracker, chunk: dict[str, Any]) -> list[dict]:
    """Process one LangGraph stream chunk. Returns list of SSE event dicts."""
    events: list[dict] = []
    prev_status = dict(tracker.agent_status)
    prev_sections = dict(tracker.report_sections)

    # --- Analyst team ---
    found_active = False
    for key in ANALYST_ORDER:
        if key not in tracker.selected_analysts:
            continue
        agent_name = ANALYST_AGENT_NAMES[key]
        report_key = ANALYST_REPORT_MAP[key]
        if chunk.get(report_key):
            _emit_section(tracker, report_key, chunk[report_key], prev_sections, events)
        has_report = bool(tracker.report_sections.get(report_key))
        if has_report:
            _emit_status(tracker, agent_name, "completed", prev_status, events)
        elif not found_active:
            _emit_status(tracker, agent_name, "in_progress", prev_status, events)
            found_active = True

    if not found_active and tracker.selected_analysts:
        if tracker.agent_status.get("Bull Researcher") == "pending":
            _emit_status(tracker, "Bull Researcher", "in_progress", prev_status, events)

    # --- Research team ---
    if chunk.get("investment_debate_state"):
        debate = chunk["investment_debate_state"]
        bull = (debate.get("bull_history") or "").strip()
        bear = (debate.get("bear_history") or "").strip()
        judge = (debate.get("judge_decision") or "").strip()

        if bull or bear:
            for agent in ["Bull Researcher", "Bear Researcher", "Research Manager"]:
                if tracker.agent_status.get(agent) == "pending":
                    _emit_status(tracker, agent, "in_progress", prev_status, events)
        if bull:
            _emit_section(tracker, "investment_plan",
                          f"### Bull Researcher Analysis\n{bull}", prev_sections, events)
        if bear:
            _emit_section(tracker, "investment_plan",
                          f"### Bear Researcher Analysis\n{bear}", prev_sections, events)
        if judge:
            _emit_section(tracker, "investment_plan",
                          f"### Research Manager Decision\n{judge}", prev_sections, events)
            for agent in ["Bull Researcher", "Bear Researcher", "Research Manager"]:
                _emit_status(tracker, agent, "completed", prev_status, events)
            _emit_status(tracker, "Trader", "in_progress", prev_status, events)

    # --- Trading team ---
    if chunk.get("trader_investment_plan"):
        _emit_section(tracker, "trader_investment_plan",
                      chunk["trader_investment_plan"], prev_sections, events)
        _emit_status(tracker, "Trader", "completed", prev_status, events)
        _emit_status(tracker, "Aggressive Analyst", "in_progress", prev_status, events)

    # --- Risk & Portfolio ---
    if chunk.get("risk_debate_state"):
        risk = chunk["risk_debate_state"]
        agg = (risk.get("aggressive_history") or "").strip()
        con = (risk.get("conservative_history") or "").strip()
        neu = (risk.get("neutral_history") or "").strip()
        judge = (risk.get("judge_decision") or "").strip()

        if agg:
            _emit_status(tracker, "Aggressive Analyst", "in_progress", prev_status, events)
            _emit_section(tracker, "final_trade_decision",
                          f"### Aggressive Analyst Analysis\n{agg}", prev_sections, events)
        if con:
            _emit_status(tracker, "Conservative Analyst", "in_progress", prev_status, events)
            _emit_section(tracker, "final_trade_decision",
                          f"### Conservative Analyst Analysis\n{con}", prev_sections, events)
        if neu:
            _emit_status(tracker, "Neutral Analyst", "in_progress", prev_status, events)
            _emit_section(tracker, "final_trade_decision",
                          f"### Neutral Analyst Analysis\n{neu}", prev_sections, events)
        if judge:
            _emit_section(tracker, "final_trade_decision",
                          f"### Portfolio Manager Decision\n{judge}", prev_sections, events)
            for agent in ["Aggressive Analyst", "Conservative Analyst",
                          "Neutral Analyst", "Portfolio Manager"]:
                _emit_status(tracker, agent, "completed", prev_status, events)

    return events
```

- [ ] **Step 4：运行测试确认通过**

```bash
python -m pytest tests/web/test_state_tracker.py -v
```

期望：所有测试 PASS

- [ ] **Step 5：提交**

```bash
git add web/state_tracker.py tests/web/test_state_tracker.py
git commit -m "feat(web): add AgentTracker state machine ported from CLI"
```

---

## Task 5：FastAPI 应用

**文件：**
- 创建：`web/app.py`
- 创建：`tests/web/test_app.py`

- [ ] **Step 1：写失败测试**

创建 `tests/web/test_app.py`：

```python
import json
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock


@pytest.fixture
def client():
    # Reset job manager state between tests
    from web import app as app_module
    app_module.job_mgr._jobs.clear()
    app_module.job_mgr._running_job_id = None
    from web.app import app
    return TestClient(app)


def test_analyze_returns_job_id(client):
    with patch("web.app._run_analysis_thread"):
        resp = client.post("/api/analyze", json={
            "ticker": "TSLA",
            "date": "2026-05-18",
            "analysts": ["market"],
            "language": "Chinese",
        })
    assert resp.status_code == 200
    data = resp.json()
    assert "job_id" in data


def test_analyze_returns_429_when_busy(client):
    with patch("web.app._run_analysis_thread"):
        client.post("/api/analyze", json={
            "ticker": "TSLA", "date": "2026-05-18",
            "analysts": ["market"], "language": "Chinese",
        })
        resp2 = client.post("/api/analyze", json={
            "ticker": "NVDA", "date": "2026-05-18",
            "analysts": ["market"], "language": "Chinese",
        })
    assert resp2.status_code == 429


def test_stream_unknown_job_returns_404(client):
    resp = client.get("/api/stream/no-such-id")
    assert resp.status_code == 404


def test_report_unknown_job_returns_404(client):
    resp = client.get("/api/report/no-such-id")
    assert resp.status_code == 404


def test_report_returns_markdown_when_done(client):
    from web import app as app_module
    with patch("web.app._run_analysis_thread"):
        resp = client.post("/api/analyze", json={
            "ticker": "TSLA", "date": "2026-05-18",
            "analysts": ["market"], "language": "Chinese",
        })
    job_id = resp.json()["job_id"]
    app_module.job_mgr.finish_job(job_id)
    app_module.job_mgr.set_report(job_id, "# Full Report\n...")
    resp2 = client.get(f"/api/report/{job_id}")
    assert resp2.status_code == 200
    assert resp2.json()["content"] == "# Full Report\n..."


def test_analyze_validates_ticker(client):
    # Ticker with path traversal characters should be rejected
    resp = client.post("/api/analyze", json={
        "ticker": "../../../etc/passwd",
        "date": "2026-05-18",
        "analysts": ["market"],
        "language": "Chinese",
    })
    assert resp.status_code == 422
```

- [ ] **Step 2：运行测试确认失败**

```bash
python -m pytest tests/web/test_app.py -v 2>&1 | head -30
```

期望：`ModuleNotFoundError: No module named 'web.app'`

- [ ] **Step 3：实现 `web/app.py`**

```python
from __future__ import annotations
import asyncio
import os
import re
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

# Ensure repo root is on path when running as `uvicorn web.app:app`
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv
load_dotenv(_REPO_ROOT / ".env")

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator
from sse_starlette.sse import EventSourceResponse

from web.job_manager import JobManager, JobNotFoundError
from web.sse_handler import EventBuffer, format_sse
from web.state_tracker import AgentTracker, process_chunk, SIGNAL_ACTION_MAP

# --- Global state ---
job_mgr = JobManager(watchdog_timeout=600.0)
_buffers: dict[str, EventBuffer] = {}
_loop: Optional[asyncio.AbstractEventLoop] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _loop
    _loop = asyncio.get_event_loop()
    yield


app = FastAPI(title="TradingAgents Web API", lifespan=lifespan)

ALLOWED_ORIGINS = os.environ.get(
    "TRADINGAGENTS_CORS_ORIGINS",
    "https://your-github-username.github.io"
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_VALID_TICKER = re.compile(r"^[A-Za-z0-9.\-]{1,20}$")


class AnalyzeRequest(BaseModel):
    ticker: str
    date: str
    analysts: list[str]
    language: str = "Chinese"

    @field_validator("ticker")
    @classmethod
    def validate_ticker(cls, v: str) -> str:
        if not _VALID_TICKER.match(v):
            raise ValueError("Invalid ticker format")
        return v.upper()

    @field_validator("analysts")
    @classmethod
    def validate_analysts(cls, v: list[str]) -> list[str]:
        valid = {"market", "social", "news", "fundamentals"}
        for a in v:
            if a not in valid:
                raise ValueError(f"Unknown analyst: {a}")
        return v


@app.post("/api/analyze")
def start_analyze(req: AnalyzeRequest):
    if job_mgr.has_running_job():
        raise HTTPException(status_code=429, detail="当前有分析任务运行中，请稍后再试")
    job_id = job_mgr.create_job()
    buf = EventBuffer()
    _buffers[job_id] = buf
    job_mgr.start_job(job_id)
    thread = threading.Thread(
        target=_run_analysis_thread,
        args=(job_id, req.ticker, req.date, req.analysts, req.language),
        daemon=True,
    )
    thread.start()
    return {"job_id": job_id}


@app.get("/api/stream/{job_id}")
async def stream_events(job_id: str, request=None):
    try:
        job_mgr.get_status(job_id)
    except JobNotFoundError:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    buf = _buffers.get(job_id)
    if buf is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")

    last_id = 0
    if request is not None:
        try:
            last_id = int(request.headers.get("last-event-id", 0))
        except (ValueError, TypeError):
            last_id = 0

    async def event_generator():
        # Replay buffered events for reconnects
        for event in buf.get_events_after(last_id):
            yield format_sse(event["type"], event["data"], event["id"])

        # Stream live events
        while True:
            try:
                event = await asyncio.wait_for(buf.queue.get(), timeout=30.0)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
                continue
            buf.add(event["type"], event["data"])
            stored = buf.events[-1]
            yield format_sse(stored["type"], stored["data"], stored["id"])
            if event["type"] in ("done", "error"):
                break

    return EventSourceResponse(event_generator())


@app.get("/api/report/{job_id}")
def get_report(job_id: str):
    try:
        report = job_mgr.get_report(job_id)
    except JobNotFoundError:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    if report is None:
        raise HTTPException(status_code=404, detail="报告尚未生成")
    return {"content": report}


def _emit(job_id: str, event_type: str, data: dict) -> None:
    """Thread-safe emit: bridge sync thread → async queue."""
    buf = _buffers.get(job_id)
    if buf is None or _loop is None:
        return
    _loop.call_soon_threadsafe(buf.queue.put_nowait, {"type": event_type, "data": data})


def _run_analysis_thread(
    job_id: str,
    ticker: str,
    date: str,
    analysts: list[str],
    language: str,
) -> None:
    """Background thread: runs TradingAgents and emits SSE events."""
    from tradingagents.graph.trading_graph import TradingAgentsGraph
    from tradingagents.default_config import DEFAULT_CONFIG
    from tradingagents.graph.signal_processing import SignalProcessor

    stop_event = job_mgr.get_stop_event(job_id)
    final_report_parts: list[str] = []

    try:
        config = DEFAULT_CONFIG.copy()
        config["output_language"] = language

        graph = TradingAgentsGraph(
            selected_analysts=analysts,
            debug=False,
            config=config,
        )

        tracker = AgentTracker(analysts)

        # Emit initial pending statuses
        for agent, status in tracker.agent_status.items():
            _emit(job_id, "agent_status", {"agent": agent, "status": status})

        init_state = graph.propagator.create_initial_state(ticker, date)
        args = graph.propagator.get_graph_args()

        for chunk in graph.graph.stream(init_state, **args):
            if stop_event.is_set():
                break
            for event in process_chunk(tracker, chunk):
                _emit(job_id, event["type"], event["data"])
                if event["type"] == "report_section":
                    final_report_parts.append(
                        f"## {event['data']['section']}\n{event['data']['content']}"
                    )

        # Final decision
        final_decision = tracker.report_sections.get("final_trade_decision") or ""
        if final_decision:
            raw_signal = SignalProcessor().process_signal(final_decision)
            action = SIGNAL_ACTION_MAP.get(raw_signal or "", "HOLD")
            _emit(job_id, "final_decision", {"raw": final_decision, "action": action})

        full_report = "\n\n".join(final_report_parts)
        job_mgr.set_report(job_id, full_report)
        job_mgr.finish_job(job_id)
        _emit(job_id, "done", {"job_id": job_id})

    except Exception as exc:
        _emit(job_id, "error", {"message": str(exc)})
        try:
            job_mgr.error_job(job_id, str(exc))
        except Exception:
            pass
```

- [ ] **Step 4：运行测试确认通过**

```bash
python -m pytest tests/web/test_app.py -v
```

期望：所有测试 PASS

- [ ] **Step 5：提交**

```bash
git add web/app.py tests/web/test_app.py
git commit -m "feat(web): add FastAPI app with /api/analyze, /api/stream, /api/report"
```

---

## Task 6：前端 HTML + CSS

**文件：**
- 创建：`web/frontend/index.html`
- 创建：`web/frontend/style.css`

- [ ] **Step 1：创建 `web/frontend/index.html`**

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>TradingAgents — 多智能体金融分析</title>
  <link rel="stylesheet" href="style.css">
  <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
  <script src="config.js"></script>
</head>
<body>
  <!-- Top Nav -->
  <nav class="topnav">
    <div class="topnav-brand">
      <span class="brand-icon">⬡</span>
      <span class="brand-name">TradingAgents</span>
      <span class="brand-sep">|</span>
      <span class="brand-sub">多智能体金融分析</span>
    </div>
    <div class="topnav-model" id="model-label">Powered by DeepSeek</div>
  </nav>

  <!-- Input Bar -->
  <div class="input-bar">
    <div class="input-group">
      <label class="input-label">代码</label>
      <input id="ticker-input" class="ticker-input" type="text"
             placeholder="TSLA / BTC-USD / 700.HK" autocomplete="off" spellcheck="false">
    </div>
    <div class="input-group">
      <label class="input-label">日期</label>
      <input id="date-input" class="date-input" type="date">
    </div>
    <div class="analyst-pills" id="analyst-pills">
      <button class="pill active" data-analyst="market">市场</button>
      <button class="pill active" data-analyst="social">情绪</button>
      <button class="pill active" data-analyst="news">新闻</button>
      <button class="pill active" data-analyst="fundamentals">基本面</button>
    </div>
    <div class="input-group">
      <select id="language-select" class="language-select">
        <option value="Chinese">🌐 中文</option>
        <option value="English">🌐 English</option>
      </select>
    </div>
    <button id="submit-btn" class="submit-btn" onclick="startAnalysis()">开始分析</button>
  </div>

  <!-- Busy Overlay -->
  <div id="busy-overlay" class="busy-overlay hidden">
    <div class="busy-message">当前有分析任务运行中，请稍后再试</div>
  </div>

  <!-- Main Panel -->
  <div class="main-panel">
    <!-- Left: Agent Progress -->
    <div class="progress-panel">
      <div class="progress-header">
        <span class="panel-title">智能体进度</span>
        <button class="collapse-btn" id="collapse-btn" onclick="toggleProgress()">▾</button>
      </div>
      <div id="progress-body">
        <div class="progress-bar-row">
          <div class="progress-track">
            <div class="progress-fill" id="progress-fill" style="width:0%"></div>
          </div>
          <span class="progress-label" id="progress-label">0 / 0</span>
        </div>
        <div id="agent-list" class="agent-list"></div>
      </div>
    </div>

    <!-- Right: Reports -->
    <div class="reports-panel" id="reports-panel">
      <!-- Decision Card (pinned top) -->
      <div id="decision-card" class="decision-card hidden">
        <div class="decision-action" id="decision-action">—</div>
        <div class="decision-detail" id="decision-detail"></div>
      </div>
      <!-- Report cards injected here -->
      <div id="report-cards"></div>
    </div>
  </div>

  <!-- Status Bar -->
  <div class="statusbar">
    <span id="status-text">就绪</span>
    <span id="status-hint"></span>
  </div>

  <script src="app.js"></script>
</body>
</html>
```

- [ ] **Step 2：创建 `web/frontend/style.css`**

```css
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

:root {
  --bg-base: #0a0c14;
  --bg-surface: #0f1117;
  --bg-elevated: #1a1d2e;
  --bg-hover: #12172a;
  --border: #1e2235;
  --border-dim: #374151;
  --text-primary: #e2e8f0;
  --text-secondary: #94a3b8;
  --text-dim: #6b7280;
  --accent: #7c85ff;
  --accent-light: #a5b4ff;
  --green: #22c55e;
  --green-bg: #14532d;
  --green-bg2: #166534;
  --red: #ef4444;
  --red-bg: #450a0a;
  --red-bg2: #7f1d1d;
  --amber: #f59e0b;
  --amber-bg: #451a03;
  --amber-bg2: #92400e;
}

html, body { height: 100%; background: var(--bg-base); color: var(--text-primary);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; font-size: 14px; }

/* Top Nav */
.topnav { display: flex; align-items: center; justify-content: space-between;
  padding: 10px 20px; background: var(--bg-surface); border-bottom: 1px solid var(--border); }
.topnav-brand { display: flex; align-items: center; gap: 8px; }
.brand-icon { color: var(--accent); font-size: 18px; }
.brand-name { font-weight: 700; font-size: 15px; }
.brand-sep { color: var(--border-dim); }
.brand-sub { color: var(--text-dim); font-size: 12px; }
.topnav-model { color: var(--text-dim); font-size: 11px; }

/* Input Bar */
.input-bar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  padding: 10px 20px; background: var(--bg-surface); border-bottom: 1px solid var(--border); }
.input-group { display: flex; align-items: center; gap: 6px;
  background: var(--bg-elevated); border: 1px solid var(--border-dim);
  border-radius: 6px; padding: 0 12px; }
.input-label { color: var(--text-dim); font-size: 10px; white-space: nowrap; }
.ticker-input, .date-input { background: transparent; border: none; outline: none;
  color: var(--text-primary); font-size: 13px; font-family: monospace;
  padding: 8px 0; width: 120px; }
.date-input { width: 130px; }
.language-select { background: var(--bg-elevated); border: 1px solid var(--border-dim);
  border-radius: 6px; color: var(--text-secondary); font-size: 11px;
  padding: 8px 10px; outline: none; cursor: pointer; }
.analyst-pills { display: flex; gap: 5px; flex-wrap: wrap; }
.pill { background: transparent; border: 1px solid var(--border-dim);
  color: var(--text-dim); padding: 5px 12px; border-radius: 12px;
  font-size: 10px; cursor: pointer; transition: all 0.15s; }
.pill.active { background: rgba(124,133,255,0.15); border-color: rgba(124,133,255,0.5);
  color: var(--accent-light); }
.submit-btn { background: var(--accent); color: #fff; border: none;
  border-radius: 6px; padding: 8px 20px; font-size: 13px; font-weight: 700;
  cursor: pointer; white-space: nowrap; transition: opacity 0.15s; }
.submit-btn:disabled { opacity: 0.4; cursor: not-allowed; }

/* Busy overlay */
.busy-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.7);
  display: flex; align-items: center; justify-content: center; z-index: 100; }
.busy-message { background: var(--bg-elevated); border: 1px solid var(--amber);
  color: var(--amber); padding: 20px 32px; border-radius: 8px; font-size: 14px; }
.hidden { display: none !important; }

/* Main Panel */
.main-panel { display: grid; grid-template-columns: 220px 1fr;
  height: calc(100vh - 44px - 52px - 36px); overflow: hidden; }

/* Progress Panel */
.progress-panel { border-right: 1px solid var(--border); background: #0d0f1a;
  display: flex; flex-direction: column; overflow: hidden; }
.progress-header { display: flex; align-items: center; justify-content: space-between;
  padding: 10px 12px; border-bottom: 1px solid var(--border); flex-shrink: 0; }
.panel-title { color: var(--text-dim); font-size: 9px; text-transform: uppercase; letter-spacing: 1px; }
.collapse-btn { background: none; border: none; color: var(--text-dim);
  cursor: pointer; font-size: 12px; padding: 0 4px; }
#progress-body { overflow-y: auto; padding: 10px 12px; flex: 1; }
.progress-bar-row { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; }
.progress-track { flex: 1; background: var(--bg-elevated); border-radius: 2px; height: 3px; }
.progress-fill { background: linear-gradient(90deg, var(--accent), var(--accent-light));
  height: 3px; border-radius: 2px; transition: width 0.4s; }
.progress-label { color: var(--accent); font-size: 9px; white-space: nowrap; }
.agent-list { display: flex; flex-direction: column; gap: 2px; }
.agent-team-label { color: var(--text-dim); font-size: 9px; margin: 8px 0 4px 2px; }
.agent-item { display: flex; align-items: center; gap: 7px; padding: 4px 6px; border-radius: 4px; }
.agent-item.in_progress { background: var(--bg-elevated); border: 1px solid rgba(245,158,11,0.2); }
.agent-dot { width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; }
.agent-dot.pending { background: var(--border-dim); }
.agent-dot.in_progress { background: var(--amber); animation: pulse 1s infinite; }
.agent-dot.completed { background: var(--green); }
.agent-name { font-size: 10px; }
.agent-name.pending { color: var(--text-dim); }
.agent-name.in_progress { color: #fbbf24; }
.agent-name.completed { color: var(--text-primary); }
.agent-check { margin-left: auto; font-size: 9px; color: var(--green); }

/* Reports Panel */
.reports-panel { overflow-y: auto; padding: 12px 16px;
  background: var(--bg-base); display: flex; flex-direction: column; gap: 10px; }

/* Decision Card */
.decision-card { border-radius: 8px; padding: 14px 18px;
  display: flex; align-items: center; gap: 18px; flex-shrink: 0; }
.decision-card.BUY { background: linear-gradient(135deg, var(--green-bg), var(--green-bg2));
  border: 1px solid rgba(34,197,94,0.4); }
.decision-card.SELL { background: linear-gradient(135deg, var(--red-bg), var(--red-bg2));
  border: 1px solid rgba(239,68,68,0.4); }
.decision-card.HOLD { background: linear-gradient(135deg, var(--amber-bg), var(--amber-bg2));
  border: 1px solid rgba(245,158,11,0.4); }
.decision-card.pending { background: var(--bg-elevated); border: 1px solid var(--border);
  opacity: 0.4; }
.decision-action { font-size: 32px; font-weight: 900; letter-spacing: 2px; min-width: 90px; text-align: center; }
.decision-card.BUY .decision-action { color: var(--green); }
.decision-card.SELL .decision-action { color: var(--red); }
.decision-card.HOLD .decision-action { color: var(--amber); }
.decision-card.pending .decision-action { color: var(--text-dim); }
.decision-detail { flex: 1; color: var(--text-secondary); font-size: 11px; line-height: 1.6; }

/* Report Cards */
.report-card { background: var(--bg-surface); border: 1px solid var(--border);
  border-radius: 6px; overflow: hidden; }
.report-card.in_progress { border-color: rgba(245,158,11,0.3); }
.report-card-header { display: flex; align-items: center; justify-content: space-between;
  padding: 8px 12px; background: var(--bg-hover); border-bottom: 1px solid var(--border); }
.report-card-title { font-size: 10px; font-weight: 700; }
.report-card-title.completed { color: var(--green); }
.report-card-title.in_progress { color: var(--amber); }
.report-card-team { color: var(--border-dim); font-size: 9px; }
.report-card-body { padding: 10px 14px; color: var(--text-secondary);
  font-size: 12px; line-height: 1.7; }
.report-card-body h1,.report-card-body h2,.report-card-body h3 {
  color: var(--text-primary); margin: 10px 0 6px; font-size: 13px; }
.report-card-body table { border-collapse: collapse; width: 100%; font-size: 11px; margin: 8px 0; }
.report-card-body th { background: var(--bg-elevated); color: var(--text-secondary);
  padding: 4px 8px; text-align: left; border: 1px solid var(--border); }
.report-card-body td { padding: 4px 8px; border: 1px solid var(--border); color: var(--text-secondary); }
.report-card-body code { background: var(--bg-elevated); padding: 1px 5px; border-radius: 3px; font-size: 11px; }
.cursor { display: inline-block; animation: blink 0.8s infinite; }

/* Status Bar */
.statusbar { display: flex; justify-content: space-between; align-items: center;
  padding: 6px 20px; background: var(--bg-surface); border-top: 1px solid var(--border);
  font-size: 10px; color: var(--text-dim); height: 36px; }

@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.3} }
@keyframes blink { 0%,100%{opacity:1} 50%{opacity:0} }

/* Mobile */
@media (max-width: 640px) {
  .main-panel { grid-template-columns: 1fr; grid-template-rows: auto 1fr; height: auto; min-height: calc(100vh - 44px - 100px - 36px); }
  .progress-panel { border-right: none; border-bottom: 1px solid var(--border); }
  #progress-body.collapsed { display: none; }
  .collapse-btn { display: block; }
  .input-bar { gap: 8px; }
  .ticker-input { width: 80px; }
  .date-input { width: 110px; }
}
@media (min-width: 641px) { .collapse-btn { display: none; } }
```

- [ ] **Step 3：提交**

```bash
git add web/frontend/index.html web/frontend/style.css
git commit -m "feat(web): add frontend HTML structure and dark theme CSS"
```

---

## Task 7：前端 JavaScript

**文件：**
- 创建：`web/frontend/app.js`

- [ ] **Step 1：创建 `web/frontend/app.js`**

```javascript
// ---- Config (loaded from config.js) ----
// BACKEND_URL is injected by config.js before this file loads

// ---- State ----
const AGENT_TEAMS = [
  { label: "分析师团队", agents: ["Market Analyst", "Sentiment Analyst", "News Analyst", "Fundamentals Analyst"] },
  { label: "研究团队",   agents: ["Bull Researcher", "Bear Researcher", "Research Manager"] },
  { label: "交易/风控/组合", agents: ["Trader", "Aggressive Analyst", "Neutral Analyst", "Conservative Analyst", "Portfolio Manager"] },
];

const SECTION_LABELS = {
  market_report: "市场分析报告",
  sentiment_report: "情绪分析报告",
  news_report: "新闻分析报告",
  fundamentals_report: "基本面分析报告",
  investment_plan: "研究团队决策",
  trader_investment_plan: "交易员计划",
  final_trade_decision: "投资组合经理决策",
};

const SECTION_TEAM = {
  market_report: "Market Analyst",
  sentiment_report: "Sentiment Analyst",
  news_report: "News Analyst",
  fundamentals_report: "Fundamentals Analyst",
  investment_plan: "Research Manager",
  trader_investment_plan: "Trader",
  final_trade_decision: "Portfolio Manager",
};

let state = { agentStatus: {}, reportSections: {}, jobId: null, es: null };

// ---- Init ----
window.addEventListener("DOMContentLoaded", () => {
  const today = new Date().toISOString().slice(0, 10);
  document.getElementById("date-input").value = today;
  document.querySelectorAll(".pill").forEach(btn => {
    btn.addEventListener("click", () => btn.classList.toggle("active"));
  });
  renderAgentList({});
  showDecisionCard("pending", null);
});

// ---- UI helpers ----
function setStatus(msg, hint = "") {
  document.getElementById("status-text").textContent = msg;
  document.getElementById("status-hint").textContent = hint;
}

function toggleProgress() {
  const body = document.getElementById("progress-body");
  const btn = document.getElementById("collapse-btn");
  body.classList.toggle("collapsed");
  btn.textContent = body.classList.contains("collapsed") ? "▸" : "▾";
}

function renderAgentList(agentStatus) {
  const list = document.getElementById("agent-list");
  list.innerHTML = "";
  let total = 0, completed = 0;
  AGENT_TEAMS.forEach(team => {
    const visible = team.agents.filter(a => a in agentStatus || Object.keys(agentStatus).length === 0);
    if (visible.length === 0) return;
    const label = document.createElement("div");
    label.className = "agent-team-label";
    label.textContent = team.label;
    list.appendChild(label);
    team.agents.forEach(agent => {
      if (!(agent in agentStatus) && Object.keys(agentStatus).length > 0) return;
      const status = agentStatus[agent] || "pending";
      total++;
      if (status === "completed") completed++;
      const item = document.createElement("div");
      item.className = `agent-item ${status}`;
      item.id = `agent-${agent.replace(/ /g, "-")}`;
      item.innerHTML = `
        <span class="agent-dot ${status}"></span>
        <span class="agent-name ${status}">${agent}</span>
        ${status === "completed" ? '<span class="agent-check">✓</span>' : ""}
        ${status === "in_progress" ? '<span class="agent-check" style="color:var(--amber)">···</span>' : ""}
      `;
      list.appendChild(item);
    });
  });
  const pct = total > 0 ? Math.round((completed / total) * 100) : 0;
  document.getElementById("progress-fill").style.width = pct + "%";
  document.getElementById("progress-label").textContent = `${completed} / ${total}`;
}

function updateAgentStatus(agent, status) {
  state.agentStatus[agent] = status;
  renderAgentList(state.agentStatus);
}

function upsertReportCard(section, content, status) {
  const cards = document.getElementById("report-cards");
  let card = document.getElementById(`card-${section}`);
  if (!card) {
    card = document.createElement("div");
    card.className = "report-card";
    card.id = `card-${section}`;
    card.innerHTML = `
      <div class="report-card-header">
        <span class="report-card-title" id="title-${section}"></span>
        <span class="report-card-team">${SECTION_TEAM[section] || ""}</span>
      </div>
      <div class="report-card-body" id="body-${section}"></div>
    `;
    cards.appendChild(card);
  }
  const titleEl = document.getElementById(`title-${section}`);
  const bodyEl = document.getElementById(`body-${section}`);
  const isInProgress = status === "in_progress";
  card.className = `report-card ${isInProgress ? "in_progress" : ""}`;
  const label = SECTION_LABELS[section] || section;
  titleEl.className = `report-card-title ${isInProgress ? "in_progress" : "completed"}`;
  titleEl.textContent = isInProgress ? `⟳ ${label} 生成中` : `✓ ${label}`;
  bodyEl.innerHTML = marked.parse(content) + (isInProgress ? '<span class="cursor">▌</span>' : "");
}

function showDecisionCard(type, data) {
  const card = document.getElementById("decision-card");
  const actionEl = document.getElementById("decision-action");
  const detailEl = document.getElementById("decision-detail");
  card.classList.remove("hidden", "BUY", "SELL", "HOLD", "pending");
  if (type === "pending") {
    card.classList.add("pending");
    actionEl.textContent = "等待分析完成...";
    detailEl.textContent = "";
  } else {
    card.classList.add(data.action);
    actionEl.textContent = data.action;
    detailEl.innerHTML = marked.parse(data.raw || "").slice(0, 500);
  }
}

// ---- Analysis flow ----
async function startAnalysis() {
  const ticker = document.getElementById("ticker-input").value.trim().toUpperCase();
  const date = document.getElementById("date-input").value;
  const analysts = [...document.querySelectorAll(".pill.active")].map(p => p.dataset.analyst);
  const language = document.getElementById("language-select").value;

  if (!ticker) { alert("请输入股票代码"); return; }
  if (!date) { alert("请选择分析日期"); return; }
  if (analysts.length === 0) { alert("请至少选择一个分析师"); return; }

  document.getElementById("submit-btn").disabled = true;
  document.getElementById("report-cards").innerHTML = "";
  state = { agentStatus: {}, reportSections: {}, jobId: null, es: null };
  renderAgentList({});
  showDecisionCard("pending", null);

  let resp;
  try {
    resp = await fetch(`${BACKEND_URL}/api/analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ticker, date, analysts, language }),
    });
  } catch {
    setStatus("无法连接到分析服务器");
    document.getElementById("submit-btn").disabled = false;
    return;
  }

  if (resp.status === 429) {
    document.getElementById("busy-overlay").classList.remove("hidden");
    setTimeout(() => document.getElementById("busy-overlay").classList.add("hidden"), 3000);
    document.getElementById("submit-btn").disabled = false;
    return;
  }

  if (!resp.ok) {
    setStatus(`启动失败: ${resp.status}`);
    document.getElementById("submit-btn").disabled = false;
    return;
  }

  const { job_id } = await resp.json();
  state.jobId = job_id;
  setStatus(`分析中 · ${ticker}`, "约需 3-8 分钟");
  connectSSE(job_id);
}

function connectSSE(jobId) {
  const es = new EventSource(`${BACKEND_URL}/api/stream/${jobId}`);
  state.es = es;

  es.addEventListener("agent_status", e => {
    const { agent, status } = JSON.parse(e.data);
    updateAgentStatus(agent, status);
  });

  es.addEventListener("report_section", e => {
    const { section, content } = JSON.parse(e.data);
    state.reportSections[section] = content;
    const agentForSection = SECTION_TEAM[section];
    const agentStatus = state.agentStatus[agentForSection] || "in_progress";
    upsertReportCard(section, content, agentStatus === "completed" ? "completed" : "in_progress");
  });

  es.addEventListener("final_decision", e => {
    const data = JSON.parse(e.data);
    showDecisionCard("final", data);
    // Mark final_trade_decision card as completed
    if (state.reportSections["final_trade_decision"]) {
      upsertReportCard("final_trade_decision", state.reportSections["final_trade_decision"], "completed");
    }
  });

  es.addEventListener("done", () => {
    es.close();
    setStatus("分析完成", "");
    document.getElementById("submit-btn").disabled = false;
    // Mark all in-progress report cards as completed
    document.querySelectorAll(".report-card.in_progress").forEach(card => {
      card.classList.remove("in_progress");
      const section = card.id.replace("card-", "");
      const titleEl = document.getElementById(`title-${section}`);
      if (titleEl) {
        titleEl.className = "report-card-title completed";
        titleEl.textContent = `✓ ${SECTION_LABELS[section] || section}`;
      }
      const bodyEl = document.getElementById(`body-${section}`);
      const cursor = bodyEl && bodyEl.querySelector(".cursor");
      if (cursor) cursor.remove();
    });
    // Show download button
    showDownloadButton(state.jobId);
  });

  es.addEventListener("error", e => {
    es.close();
    let msg = "分析出错";
    try { msg = JSON.parse(e.data).message; } catch {}
    setStatus(`错误: ${msg}`);
    document.getElementById("submit-btn").disabled = false;
  });

  es.onerror = () => {
    // EventSource will auto-reconnect using Last-Event-ID
    setStatus("连接中断，正在重连...", "");
  };
}

async function showDownloadButton(jobId) {
  try {
    const resp = await fetch(`${BACKEND_URL}/api/report/${jobId}`);
    if (!resp.ok) return;
    const { content } = await resp.json();
    const btn = document.createElement("button");
    btn.className = "submit-btn";
    btn.style.cssText = "margin:8px 0;font-size:12px;padding:6px 16px;background:#374151";
    btn.textContent = "⬇ 下载完整报告";
    btn.onclick = () => {
      const blob = new Blob([content], { type: "text/markdown;charset=utf-8" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `report-${jobId.slice(0, 8)}.md`;
      a.click();
    };
    document.getElementById("report-cards").prepend(btn);
  } catch {}
}
```

- [ ] **Step 2：手动验证前端（启动后端后测试）**

```bash
# 先启动后端（从仓库根目录）
cd /Users/a1/TradingAgents
pip install -r web/requirements.txt
TRADINGAGENTS_CORS_ORIGINS="*" uvicorn web.app:app --host 127.0.0.1 --port 8000 &

# 在浏览器中打开 web/frontend/index.html（用 file:// 或 Live Server）
# 修改 config.js.example 为 config.js，将 BACKEND_URL 设为 http://localhost:8000
# 输入 TSLA，点击"开始分析"，验证：
# ✓ 智能体状态逐一更新
# ✓ 报告卡片逐个出现
# ✓ 分析完成后决策卡变色
```

- [ ] **Step 3：提交**

```bash
git add web/frontend/app.js
git commit -m "feat(web): add frontend JavaScript with EventSource and state machine"
```

---

## Task 8：运行完整测试套件

- [ ] **Step 1：安装依赖**

```bash
cd /Users/a1/TradingAgents
pip install -r web/requirements.txt
```

- [ ] **Step 2：运行所有 Web 测试**

```bash
python -m pytest tests/web/ -v
```

期望：所有测试 PASS，无失败

- [ ] **Step 3：如有失败，修复后重新运行**

---

## Task 9：部署配置

**文件：**
- 创建：`web/frontend/config.js.example`（已在 Task 1 创建，此步补充内容）
- 创建：`web/launchd/tradingagents-web.plist`（macOS LaunchAgent）

- [ ] **Step 1：创建 LaunchAgent plist**

```bash
mkdir -p /Users/a1/TradingAgents/web/launchd
mkdir -p /Users/a1/.tradingagents/logs
```

创建 `web/launchd/tradingagents-web.plist`：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.tradingagents.web</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/a1/TradingAgents/.venv/bin/uvicorn</string>
    <string>web.app:app</string>
    <string>--host</string>
    <string>127.0.0.1</string>
    <string>--port</string>
    <string>8000</string>
  </array>
  <key>WorkingDirectory</key>
  <string>/Users/a1/TradingAgents</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>/Users/a1/.tradingagents/logs/web-stdout.log</string>
  <key>StandardErrorPath</key>
  <string>/Users/a1/.tradingagents/logs/web-stderr.log</string>
</dict>
</plist>
```

- [ ] **Step 2：更新 README 中的部署说明**

在 `README.md` 末尾追加如下内容：

```markdown
## Web Interface

A browser-based interface is available in `web/`. See `docs/superpowers/specs/2026-05-18-tradingagents-web-design.md` for full architecture details.

### Quick Start (Local)

```bash
# 1. Install web dependencies
pip install -r web/requirements.txt

# 2. Copy and configure backend URL
cp web/frontend/config.js.example web/frontend/config.js
# Edit config.js: set BACKEND_URL to http://localhost:8000

# 3. Set CORS to allow local file access
export TRADINGAGENTS_CORS_ORIGINS="*"

# 4. Start backend (from repo root)
uvicorn web.app:app --host 127.0.0.1 --port 8000

# 5. Open web/frontend/index.html in browser
```

### Production Deployment (Mac Mini + Cloudflare Tunnel)

```bash
# Install LaunchAgent
cp web/launchd/tradingagents-web.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.tradingagents.web.plist

# Set up Cloudflare named tunnel (one-time)
cloudflared tunnel create tradingagents-web
cloudflared tunnel route dns tradingagents-web tradingagents-api.yourdomain.com
cloudflared tunnel run tradingagents-web

# Configure GitHub Pages to serve web/frontend/ from main branch
# Update config.js with your tunnel URL
# Set TRADINGAGENTS_CORS_ORIGINS in .env to your GitHub Pages URL
```
```

- [ ] **Step 3：提交**

```bash
git add web/launchd/ README.md
git commit -m "feat(web): add LaunchAgent plist and deployment documentation"
```

---

## 验收检查清单

- [ ] `python -m pytest tests/web/ -v` — 全部通过
- [ ] 后端启动无报错：`uvicorn web.app:app --host 127.0.0.1 --port 8000`
- [ ] 浏览器打开前端，输入 TSLA，点击"开始分析"
- [ ] 智能体进度左侧面板实时更新
- [ ] 报告卡片逐个出现，Markdown 正确渲染
- [ ] 分析完成后决策卡高亮（BUY/SELL/HOLD 对应颜色）
- [ ] 手机浏览器访问正常，进度面板可折叠
- [ ] 断开网络后重连，SSE 事件正确重放
