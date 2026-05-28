from __future__ import annotations
import asyncio
import hmac
import json
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
from tradingagents.dataflows.akshare_china import apply_china_vendor_overlay
from tradingagents.market_resolver import Market, resolve_market


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
# A full multi-analyst pipeline (4 analysts + research debate + trader + 3
# risk debators + portfolio) via DeepSeek is slow: ~10 min just for
# analysts+research, so 600s killed every 4-analyst run before the Trader
# ever ran. 30 min lets the full pipeline finish; still bounded so a truly
# hung run can't hold the single slot forever. Override via env if needed.
_WATCHDOG_SEC = float(os.environ.get("TRADINGAGENTS_WEB_WATCHDOG_SEC", "1800"))
job_mgr = JobManager(watchdog_timeout=_WATCHDOG_SEC)
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


class SecurityHeadersMiddleware:
    """Pure-ASGI header injection.

    NOT a BaseHTTPMiddleware (@app.middleware("http")): that wraps the
    response body and breaks long-lived SSE streams ("ASGI callable
    returned without completing response", premature stream cut). This
    injects headers on the http.response.start message and never touches
    the body, so SSE streaming is unaffected.
    """

    _EXTRA = [
        (b"content-security-policy", _CSP.encode()),
        (b"x-content-type-options", b"nosniff"),
        (b"x-frame-options", b"DENY"),
        (b"referrer-policy", b"no-referrer"),
    ]

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                message.setdefault("headers", [])
                message["headers"].extend(self._EXTRA)
                # Log rejected gated requests (never the password — it's a
                # header we never read here). Owner: grep auth-fail in
                # web-stderr.log.
                if message["status"] == 401 and scope.get("path", "").startswith("/api/"):
                    hdr = {k.decode().lower(): v.decode("latin-1")
                           for k, v in scope.get("headers", [])}
                    ip = (hdr.get("cf-connecting-ip")
                          or hdr.get("x-forwarded-for", "").split(",")[0].strip()
                          or "?")
                    _log.warning(
                        "auth-fail path=%s ip=%s ua=%r",
                        scope.get("path", ""), ip,
                        hdr.get("user-agent", "")[:120],
                    )
            await send(message)

        await self.app(scope, receive, send_wrapper)


app.add_middleware(SecurityHeadersMiddleware)

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


# Saved-report section order + titles (kept in sync with the frontend
# SECTION_LABELS so the history report reads the same as the live UI).
_REPORT_SECTION_ORDER = [
    ("market_report", "市场分析报告"),
    ("sentiment_report", "情绪分析报告"),
    ("news_report", "新闻分析报告"),
    ("fundamentals_report", "基本面分析报告"),
    ("investment_plan", "研究团队决策"),
    ("trader_investment_plan", "交易员计划"),
    ("final_trade_decision", "投资组合经理决策"),
]

_VALID_TICKER = re.compile(r"^[A-Za-z0-9.\-]{1,20}$")
_VALID_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_VALID_ANALYSTS = {"market", "social", "news", "fundamentals"}


def resolve_asset(ticker: str, analysts: list[str]) -> tuple[str, list[str]]:
    """Mirror the CLI's detect_asset_type/filter_analysts: crypto tickers run
    as 'crypto' and drop fundamentals (no company financials for a coin)."""
    if resolve_market(ticker) == Market.CRYPTO:
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

    try:
        config = DEFAULT_CONFIG.copy()
        config["output_language"] = language

        # Crypto tickers run as 'crypto' and drop fundamentals — matching the
        # CLI, so e.g. BTC-USD isn't analysed with stock-only logic.
        asset_type, analysts = resolve_asset(ticker, analysts)
        # Applied here, not inside build_analysis_config: that fn has no ticker param (a regression test calls it without one).
        apply_china_vendor_overlay(config, ticker)

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

        if stop_event.is_set():
            # Timed out / aborted: the watchdog already marked the job ERROR.
            _emit(job_id, "error", {"message": "分析超时（超过 30 分钟）或被中止。多分析师全流程很慢，建议减少分析师（如只选 市场+新闻）后重试。"})
            return

        # The graph ran to END but, with no final trade decision, the
        # Trader/Risk/Portfolio nodes produced nothing — the upstream model
        # very likely returned empty for the oversized Trader prompt (the
        # whole multi-round debate is embedded; more analysts -> longer ->
        # empty). The framework swallows that (no exception), so DON'T
        # report success or save a misleading "—" history entry — tell the
        # user honestly and log what was/wasn't produced for diagnosis.
        final_decision = tracker.report_sections.get("final_trade_decision") or ""
        if not final_decision:
            produced = sorted(s for s, v in tracker.report_sections.items() if v)
            _log.warning(
                "incomplete-run job=%s ticker=%s analysts=%s produced=%s "
                "(no final_trade_decision; upstream likely returned empty)",
                job_id, ticker, analysts, produced,
            )
            job_mgr.error_job(job_id, "未产出交易决策")
            _emit(job_id, "error", {"message": (
                "分析未产出最终交易决策。常见原因：选的分析师过多，导致交易员"
                "环节输入过长、上游模型返回空。请减少分析师数量（如只选市场+新闻）"
                "后重试。"
            )})
            return

        raw_signal = SignalProcessor().process_signal(final_decision)
        action = SIGNAL_ACTION_MAP.get(raw_signal or "", "HOLD")
        _emit(job_id, "final_decision", {"raw": final_decision, "action": action})

        # Build the saved report from the FINAL value of each section (one
        # block per section, ordered) — NOT by appending every streamed
        # event. This deduplicates the growing investment_plan /
        # final_trade_decision sections so the saved/history report matches
        # exactly what the live UI shows.
        report_parts = []
        for key, title in _REPORT_SECTION_ORDER:
            content = tracker.report_sections.get(key)
            if content:
                report_parts.append(f"## {title}\n\n{content}")
        report_parts.append(f"## 最终交易决策\n\n**{action}**")
        full_report = "\n\n".join(report_parts)
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
            _emit(job_id, "error", {"message": "分析超时（超过 30 分钟）或被中止。多分析师全流程很慢，建议减少分析师（如只选 市场+新闻）后重试。"})

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


# ---------------------------------------------------------------------------
# Sentiment-scan history viewer (Phase 10)
#
# Backend has been writing per-ticker markdown reports to
#   ~/.tradingagents/sentiment-scan/reports/<DATE>/<code>/<name>.md
# since the analysis_runner gained `report_dir`. These four routes expose
# them as JSON + a static viewer HTML page. The 飞书 decision cards link
# to /sentiment-scan/<DATE>/<code>; that page's JS fetches the API routes
# below. Everything is UNGATED (no password) — past sentiment snapshots are
# considered shareable, and per-job streaming auth is unrelated.
#
# Security: every path param is validated against a strict regex BEFORE we
# touch the filesystem. report_name is also whitelisted so a malicious
# request like .../reports/..%2Fetc%2Fpasswd cannot escape the sandbox.
# ---------------------------------------------------------------------------
_SS_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SS_CODE_RE = re.compile(r"^[A-Za-z0-9.]{1,12}$")
_SS_REPORT_NAME_WHITELIST = {
    "fundamentals_report",
    "news_report",
    "investment_plan",
    "trader_investment_plan",
    "final_trade_decision",
}


def _sentiment_scan_dir() -> Path:
    """Filesystem root for sentiment-scan snapshots + per-day reports.

    Matches the CLI's `TRADINGAGENTS_SENTIMENT_SCAN_DIR` env-var pivot so a
    relocated SCAN_DIR is read consistently from both writer and viewer.
    """
    return Path(os.environ.get(
        "TRADINGAGENTS_SENTIMENT_SCAN_DIR",
        os.path.expanduser("~/.tradingagents/sentiment-scan"),
    ))


@app.get("/api/sentiment-scan/{date}")
def get_sentiment_scan_date(date: str):
    """List of analyses for a given date (without the full report bodies)."""
    if not _SS_DATE_RE.match(date):
        raise HTTPException(status_code=400, detail="invalid date format")
    snap_path = _sentiment_scan_dir() / f"{date}.json"
    if not snap_path.exists():
        raise HTTPException(status_code=404, detail="snapshot not found")
    try:
        snap = json.loads(snap_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _log.warning("sentiment-scan snapshot %s unreadable: %s", snap_path, exc)
        raise HTTPException(status_code=500, detail="snapshot unreadable")
    return {
        "date": snap.get("date"),
        "scan_completed_at": snap.get("scan_completed_at"),
        "analysis_completed_at": snap.get("analysis_completed_at"),
        "analyses": [
            {
                "code": a.get("code"),
                "name": a.get("name"),
                "market": a.get("market"),
                "tier": a.get("tier"),
                "status": a.get("status"),
                "decision": a.get("decision"),
                "fundamentals": a.get("fundamentals"),
                "report_paths": a.get("report_paths", {}),
            }
            for a in snap.get("analyses", [])
        ],
    }


@app.get("/api/sentiment-scan/{date}/{code}")
def get_sentiment_scan_ticker(date: str, code: str):
    """Per-ticker metadata + decision + fundamentals (no report body)."""
    if not _SS_DATE_RE.match(date):
        raise HTTPException(status_code=400, detail="invalid date format")
    if not _SS_CODE_RE.match(code):
        raise HTTPException(status_code=400, detail="invalid code format")
    snap_path = _sentiment_scan_dir() / f"{date}.json"
    if not snap_path.exists():
        raise HTTPException(status_code=404, detail="snapshot not found")
    try:
        snap = json.loads(snap_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _log.warning("sentiment-scan snapshot %s unreadable: %s", snap_path, exc)
        raise HTTPException(status_code=500, detail="snapshot unreadable")
    for a in snap.get("analyses", []):
        if a.get("code") == code:
            return a
    raise HTTPException(status_code=404, detail="ticker not found in this date's snapshot")


@app.get("/api/sentiment-scan/{date}/{code}/reports/{report_name}")
def get_sentiment_scan_report(date: str, code: str, report_name: str):
    """Plain-text markdown body for one report section."""
    if not _SS_DATE_RE.match(date):
        raise HTTPException(status_code=400, detail="invalid date format")
    if not _SS_CODE_RE.match(code):
        raise HTTPException(status_code=400, detail="invalid code format")
    if report_name not in _SS_REPORT_NAME_WHITELIST:
        raise HTTPException(
            status_code=400,
            detail=f"invalid report name (allowed: {sorted(_SS_REPORT_NAME_WHITELIST)})",
        )
    report_path = (
        _sentiment_scan_dir() / "reports" / date / code / f"{report_name}.md"
    )
    if not report_path.exists():
        raise HTTPException(status_code=404, detail="report not found on disk")
    return Response(
        content=report_path.read_text(encoding="utf-8"),
        media_type="text/markdown; charset=utf-8",
    )


@app.get("/sentiment-scan/{date}/{code}", include_in_schema=False)
def serve_sentiment_scan_viewer(date: str, code: str):
    """Serve the static HTML viewer; its JS pulls data from the API routes."""
    # Reject obviously malformed paths early; the API routes the JS calls
    # will re-validate before touching disk.
    if not _SS_DATE_RE.match(date) or not _SS_CODE_RE.match(code):
        raise HTTPException(status_code=400, detail="invalid path")
    viewer_path = _REPO_ROOT / "web" / "frontend" / "sentiment-scan.html"
    if not viewer_path.exists():
        raise HTTPException(status_code=500, detail="viewer not built")
    return Response(
        content=viewer_path.read_text(encoding="utf-8"),
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-cache"},
    )


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
