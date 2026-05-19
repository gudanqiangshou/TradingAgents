from __future__ import annotations
import asyncio
import hmac
import logging
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

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator
from starlette.responses import Response


class NoCacheStaticFiles(StaticFiles):
    """Serve the frontend with Cache-Control: no-cache.

    The site is fronted by a Cloudflare tunnel. Cloudflare edge-caches static
    extensions (.js/.css) by default for hours, so a deploy would otherwise
    keep serving stale UI until the edge TTL expired. Cloudflare honours an
    origin `no-cache`, so every fetch revalidates against the ETag FastAPI
    already sends (still 304-efficient when unchanged, always fresh on deploy).
    """

    def file_response(self, *args, **kwargs) -> Response:
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-cache"
        return resp
from sse_starlette.sse import EventSourceResponse

from web import history
from web.job_manager import JobManager, JobNotFoundError, JobStatus
from web.sse_handler import EventBuffer, sse_stream
from web.state_tracker import AgentTracker, process_chunk, SIGNAL_ACTION_MAP

_log = logging.getLogger("tradingagents.web")

# --- Global state ---
job_mgr = JobManager(watchdog_timeout=600.0)
_buffers: dict[str, EventBuffer] = {}
_MAX_BUFFERS = 20
_MAX_REPORT_CHARS = 2_000_000  # ~2MB safety cap on a single stored report
_loop: Optional[asyncio.AbstractEventLoop] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _loop
    if WEB_REQUIRE_PASSWORD and not WEB_PASSWORD:
        raise RuntimeError(
            "TRADINGAGENTS_WEB_REQUIRE_PASSWORD is set but "
            "TRADINGAGENTS_WEB_PASSWORD is empty — refusing to start an "
            "ungated, money-spending API (check .env is present/loaded)."
        )
    _loop = asyncio.get_running_loop()
    yield


app = FastAPI(title="TradingAgents Web API", lifespan=lifespan)

# Single-origin app (FastAPI serves both UI and API), so no CORS is needed.
# These headers harden against XSS/clickjacking. The strict CSP is only safe
# because the frontend has NO inline scripts/handlers (all wired via
# addEventListener) and uses locally-vendored, pinned marked + DOMPurify.
_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "object-src 'none'; base-uri 'none'; "
    "frame-ancestors 'none'; form-action 'self'"
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["Content-Security-Policy"] = _CSP
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    # Observability for password probing: log every rejected gated request
    # (never the password itself — it's in a header we don't read here).
    # Behind Cloudflare the socket peer is localhost, so prefer CF's real-IP
    # header. Owner can `grep "auth-fail" web-stderr.log`.
    if resp.status_code == 401 and request.url.path.startswith("/api/"):
        ip = (request.headers.get("cf-connecting-ip")
              or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
              or (request.client.host if request.client else "?"))
        _log.warning(
            "auth-fail path=%s ip=%s ua=%r",
            request.url.path, ip, request.headers.get("user-agent", "")[:120],
        )
    return resp

# Optional access gate. When TRADINGAGENTS_WEB_PASSWORD is set, POST /api/analyze
# (the only endpoint that spends LLM budget) requires a matching X-Access-Password
# header. Empty/unset = no gate (local dev). The gate is enforced server-side
# because a client-side check would be trivially bypassed by calling the API
# directly. /api/stream and /api/report need a job_id that only a successful
# (authorized) /api/analyze hands out, so gating analyze gates the whole flow.
WEB_PASSWORD = os.environ.get("TRADINGAGENTS_WEB_PASSWORD", "")

# Fail-closed switch. Production (the LaunchAgent) sets this to 1 in the plist
# itself — NOT in .env — so if .env is missing/renamed the server refuses to
# start rather than silently serving an ungated, money-spending API.
WEB_REQUIRE_PASSWORD = os.environ.get(
    "TRADINGAGENTS_WEB_REQUIRE_PASSWORD", ""
).strip().lower() in ("1", "true", "yes", "on")


def _check_access(supplied: str | None) -> None:
    if not WEB_PASSWORD:
        return
    if not supplied or not hmac.compare_digest(supplied, WEB_PASSWORD):
        raise HTTPException(status_code=401, detail="访问口令缺失或错误")


_VALID_TICKER = re.compile(r"^[A-Za-z0-9.\-]{1,20}$")
_VALID_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_VALID_ANALYSTS = {"market", "social", "news", "fundamentals"}
_CRYPTO_SUFFIXES = ("-USD", "-USDT", "-USDC", "-BTC", "-ETH")


def resolve_asset(ticker: str, analysts: list[str]) -> tuple[str, list[str]]:
    """Mirror the CLI's detect_asset_type/filter_analysts: crypto tickers run
    as 'crypto' and drop fundamentals (no company financials for a coin)."""
    if ticker.upper().endswith(_CRYPTO_SUFFIXES):
        return "crypto", [a for a in analysts if a != "fundamentals"]
    return "stock", analysts


class AnalyzeRequest(BaseModel):
    ticker: str
    date: str
    analysts: list[str]
    language: str = "Chinese"

    @field_validator("ticker")
    @classmethod
    def validate_ticker(cls, v: str) -> str:
        # Reject `..` / leading `.` too: a ticker becomes the history dir
        # name, and the history reader rejects `..` — keep the two consistent.
        if not _VALID_TICKER.match(v) or ".." in v or v.startswith("."):
            raise ValueError("Invalid ticker format")
        return v.upper()

    @field_validator("date")
    @classmethod
    def validate_date(cls, v: str) -> str:
        from datetime import datetime, date as _date
        if not _VALID_DATE.match(v):
            raise ValueError("Date must be YYYY-MM-DD")
        try:
            d = datetime.strptime(v, "%Y-%m-%d").date()
        except ValueError:
            raise ValueError("Invalid calendar date")
        if d > _date.today():
            raise ValueError("Date cannot be in the future")
        return v

    @field_validator("analysts")
    @classmethod
    def validate_analysts(cls, v: list[str]) -> list[str]:
        if not v or len(v) > len(_VALID_ANALYSTS):
            raise ValueError("Pick 1–4 analysts")
        for a in v:
            if a not in _VALID_ANALYSTS:
                raise ValueError(f"Unknown analyst: {a}")
        return list(dict.fromkeys(v))  # dedupe, preserve order

    @field_validator("language")
    @classmethod
    def validate_language(cls, v: str) -> str:
        if v not in ("Chinese", "English"):
            raise ValueError("language must be Chinese or English")
        return v


@app.post("/api/analyze")
def start_analyze(
    req: AnalyzeRequest,
    x_access_password: str | None = Header(default=None),
):
    _check_access(x_access_password)
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
def get_report(job_id: str, x_access_password: str | None = Header(default=None)):
    # Gated like /api/analyze. (/api/stream cannot be header-gated — EventSource
    # cannot set headers — so it relies on the unguessable uuid4 job_id as a
    # capability; uvicorn access logging is disabled in the LaunchAgent so the
    # id never lands in a log file.)
    _check_access(x_access_password)
    try:
        report = job_mgr.get_report(job_id)
    except JobNotFoundError:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    if report is None:
        raise HTTPException(status_code=404, detail="报告尚未生成")
    return {"content": report}


@app.get("/api/history")
def get_history(
    limit: int = 100,
    x_access_password: str | None = Header(default=None),
):
    # Gated like /api/analyze: past analyses are as private as the ability to
    # run them. `limit` is clamped so a client can't ask for an unbounded
    # response.
    _check_access(x_access_password)
    limit = max(1, min(limit, 500))
    return {"items": history.list_history(limit=limit)}


@app.get("/api/history/{entry_id}")
def get_history_report(
    entry_id: str,
    x_access_password: str | None = Header(default=None),
):
    _check_access(x_access_password)
    content = history.get_report(entry_id)
    if content is None:
        raise HTTPException(status_code=404, detail="历史记录不存在")
    return {"content": content}


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

        # Crypto tickers run as 'crypto' and drop fundamentals — matching the
        # CLI, so e.g. BTC-USD isn't analysed with stock-only logic.
        asset_type, analysts = resolve_asset(ticker, analysts)

        graph = TradingAgentsGraph(
            selected_analysts=analysts,
            debug=False,
            config=config,
        )

        tracker = AgentTracker(analysts)

        # Emit initial pending statuses
        for agent, status in tracker.agent_status.items():
            _emit(job_id, "agent_status", {"agent": agent, "status": status})

        init_state = graph.propagator.create_initial_state(
            ticker, date, asset_type=asset_type
        )
        args = graph.propagator.get_graph_args()

        # stop_event is only checked between chunks. A watchdog timeout
        # during a single blocked LLM call cannot interrupt that call; the
        # thread keeps running until the call returns. The capacity slot is
        # therefore NOT freed by the watchdog — only by the finally below
        # when this thread truly exits — so a timed-out zombie can never run
        # concurrently with a new job and double-spend budget.
        for chunk in graph.graph.stream(init_state, **args):
            if stop_event.is_set():
                break
            for event in process_chunk(tracker, chunk):
                _emit(job_id, event["type"], event["data"])
                if event["type"] == "report_section":
                    final_report_parts.append(
                        f"## {event['data']['section']}\n{event['data']['content']}"
                    )

        if stop_event.is_set():
            # Timed out / aborted: the watchdog already marked the job ERROR.
            _emit(job_id, "error", {"message": "分析超时或被中止，请重试"})
            return

        # Final decision
        final_decision = tracker.report_sections.get("final_trade_decision") or ""
        action = "—"
        if final_decision:
            raw_signal = SignalProcessor().process_signal(final_decision)
            action = SIGNAL_ACTION_MAP.get(raw_signal or "", "HOLD")
            _emit(job_id, "final_decision", {"raw": final_decision, "action": action})
            final_report_parts.append(f"## 最终交易决策\n\n**{action}**")

        full_report = "\n\n".join(final_report_parts)
        if len(full_report) > _MAX_REPORT_CHARS:
            full_report = (
                full_report[:_MAX_REPORT_CHARS]
                + "\n\n> ⚠️ 报告过长，已截断。"
            )
        job_mgr.set_report(job_id, full_report)
        # Persist to disk so the analysis survives a backend restart and shows
        # up in the browsable history. Never let a persistence failure abort
        # the job — the in-memory result + SSE stream still work.
        try:
            history.save_analysis(ticker, date, action, full_report)
        except Exception:
            pass
        job_mgr.finish_job(job_id)  # won't override a watchdog ERROR
        if job_mgr.get_status(job_id) == JobStatus.DONE:
            _emit(job_id, "done", {"job_id": job_id})
        else:
            _emit(job_id, "error", {"message": "分析超时或被中止，请重试"})

    except Exception as exc:
        try:
            job_mgr.error_job(job_id, str(exc))
        except Exception:
            pass
        _emit(job_id, "error", {"message": str(exc)})
    finally:
        # The ONLY place the single-job slot is freed: guarantees it stays
        # held until this worker actually exits, even on watchdog timeout.
        job_mgr.release(job_id)


_FRONTEND_DIR = _REPO_ROOT / "web" / "frontend"


def _asset_version() -> str:
    """Cache-bust token = newest mtime of the frontend assets.

    Cloudflare's zone default overrides the origin `no-cache` and edge-caches
    .js/.css for hours, so after a deploy users keep the old UI. `/` is HTML
    and Cloudflare does NOT cache it (cf-cache-status: DYNAMIC), so we serve
    index.html dynamically and stamp the asset URLs with this token. A deploy
    changes the files' mtime -> new ?v=... -> a URL Cloudflare has never
    cached -> it fetches fresh. Zero dashboard, zero per-deploy steps.
    """
    latest = 0.0
    for name in ("app.js", "style.css", "index.html"):
        try:
            latest = max(latest, (_FRONTEND_DIR / name).stat().st_mtime)
        except OSError:
            pass
    return str(int(latest))


@app.get("/", include_in_schema=False)
@app.get("/index.html", include_in_schema=False)
def index():
    html = (_FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
    ver = _asset_version()
    html = html.replace("app.js?v=2", f"app.js?v={ver}")
    html = html.replace("style.css?v=2", f"style.css?v={ver}")
    return Response(
        content=html,
        media_type="text/html",
        headers={"Cache-Control": "no-cache"},
    )


# Serve the rest of the static frontend (/app.js, /style.css). Mounted last so
# the /api/* routes and the dynamic index above take precedence. Single-origin
# means no CORS and no separate frontend host — one tunnel serves it all.
app.mount(
    "/",
    NoCacheStaticFiles(directory=str(_FRONTEND_DIR), html=True),
    name="frontend",
)
