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

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator
from sse_starlette.sse import EventSourceResponse

from web.job_manager import JobManager, JobNotFoundError
from web.sse_handler import EventBuffer, sse_stream
from web.state_tracker import AgentTracker, process_chunk, SIGNAL_ACTION_MAP

# --- Global state ---
job_mgr = JobManager(watchdog_timeout=600.0)
_buffers: dict[str, EventBuffer] = {}
_MAX_BUFFERS = 20
_loop: Optional[asyncio.AbstractEventLoop] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _loop
    _loop = asyncio.get_running_loop()
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
    # Evict oldest buffers beyond the cap (single-user, jobs run sequentially,
    # so FIFO eviction never drops the currently-running job's buffer).
    while len(_buffers) > _MAX_BUFFERS:
        oldest_id = next(iter(_buffers))
        if oldest_id == job_id:
            break
        _buffers.pop(oldest_id, None)
    try:
        job_mgr.start_job(job_id)
    except RuntimeError:
        # Lost the race against a concurrent POST: discard this job+buffer
        # and report busy cleanly instead of leaking a 500.
        _buffers.pop(job_id, None)
        job_mgr.remove_job(job_id)
        raise HTTPException(status_code=429, detail="当前有分析任务运行中，请稍后再试")
    thread = threading.Thread(
        target=_run_analysis_thread,
        args=(job_id, req.ticker, req.date, req.analysts, req.language),
        daemon=True,
    )
    thread.start()
    return {"job_id": job_id}


@app.get("/api/stream/{job_id}")
async def stream_events(job_id: str, request: Request):
    try:
        job_mgr.get_status(job_id)
    except JobNotFoundError:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    buf = _buffers.get(job_id)
    if buf is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")

    try:
        last_id = int(request.headers.get("last-event-id", 0))
    except (ValueError, TypeError):
        last_id = 0

    return EventSourceResponse(sse_stream(buf, last_id))


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
    """Thread-safe emit: append to the durable buffer on the event loop.

    Scheduling buf.add via call_soon_threadsafe makes the loop thread the
    sole mutator of buf.events, so id assignment and the wakeup are race-free.
    """
    buf = _buffers.get(job_id)
    if buf is None or _loop is None:
        return
    _loop.call_soon_threadsafe(buf.add, event_type, data)


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

        # NOTE: stop_event is only checked between chunks. A watchdog timeout
        # fired during a single blocked LLM call cannot interrupt that call;
        # the job lock is still released and an error is emitted to the client,
        # but this daemon thread keeps running until the LLM call returns.
        # Accepted limitation (see spec "超出范围").
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
            final_report_parts.append(f"## 最终交易决策\n\n**{action}**")

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
