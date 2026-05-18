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
