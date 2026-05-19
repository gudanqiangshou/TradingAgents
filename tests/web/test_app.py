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
    # Gate OFF by default so tests are deterministic regardless of whether
    # the developer's real .env sets TRADINGAGENTS_WEB_PASSWORD (app.py loads
    # .env at import). Gate-specific tests monkeypatch WEB_PASSWORD back on.
    app_module.WEB_PASSWORD = ""
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


def test_stream_replays_buffered_events_and_terminates(client):
    from web import app as app_module
    with patch("web.app._run_analysis_thread"):
        resp = client.post("/api/analyze", json={
            "ticker": "TSLA", "date": "2026-05-18",
            "analysts": ["market"], "language": "Chinese",
        })
    job_id = resp.json()["job_id"]
    buf = app_module._buffers[job_id]
    buf.add("agent_status", {"agent": "Market Analyst", "status": "completed"})
    buf.add("done", {"job_id": job_id})
    with client.stream("GET", f"/api/stream/{job_id}") as r:
        body = "".join(r.iter_text())
    # Parse SSE frames: each frame separated by blank line; collect event: lines.
    event_types = []
    for line in body.splitlines():
        if line.startswith("event:"):
            event_types.append(line.split(":", 1)[1].strip())
    assert "agent_status" in event_types
    assert "done" in event_types
    # Guard against the double-wrap regression: the data line must be raw JSON,
    # never literal "data: event: ..." text.
    assert "data: event:" not in body
    assert "data: id:" not in body


def test_buffer_eviction_caps_memory(client):
    from web import app as app_module
    with patch("web.app._run_analysis_thread"):
        for _ in range(app_module._MAX_BUFFERS + 5):
            r = client.post("/api/analyze", json={
                "ticker": "TSLA", "date": "2026-05-18",
                "analysts": ["market"], "language": "Chinese",
            })
            jid = r.json()["job_id"]
            # finish each job so the next POST is allowed and FIFO eviction applies
            app_module.job_mgr.finish_job(jid)
    assert len(app_module._buffers) <= app_module._MAX_BUFFERS


def test_report_includes_final_decision_heading():
    # Unit-test the report-assembly contract: the persisted report must carry
    # an explicit decision heading. We exercise the same join the thread does.
    from web.state_tracker import SIGNAL_ACTION_MAP
    parts = ["## market_report\nTSLA technicals..."]
    action = SIGNAL_ACTION_MAP.get("Buy", "HOLD")
    parts.append(f"## 最终交易决策\n\n**{action}**")
    report = "\n\n".join(parts)
    assert "## 最终交易决策" in report
    assert "**BUY**" in report


def test_root_serves_frontend_index(client):
    # Single-origin: FastAPI serves the static frontend at "/".
    resp = client.get("/")
    assert resp.status_code == 200
    assert "TradingAgents" in resp.text
    assert 'id="ticker-input"' in resp.text


def test_static_assets_served(client):
    # app.js and style.css are served from the same origin as the API.
    js = client.get("/app.js")
    assert js.status_code == 200
    assert "/api/analyze" in js.text  # relative path, no BACKEND_URL
    css = client.get("/style.css")
    assert css.status_code == 200


def test_static_assets_sent_no_cache(client):
    # Cloudflare edge-caches .js/.css for hours by default; the origin must
    # send no-cache so a deploy is never masked by a stale edge copy.
    for path in ("/", "/app.js", "/style.css"):
        r = client.get(path)
        assert r.status_code == 200
        assert "no-cache" in r.headers.get("cache-control", "").lower(), path


def test_index_stamps_dynamic_asset_version(client):
    # "/" is served dynamically (Cloudflare never caches HTML) and rewrites
    # the asset URLs with an mtime token, so a deploy auto-busts the edge
    # cache for app.js/style.css with no dashboard and no manual version bump.
    import re
    r = client.get("/")
    assert r.status_code == 200
    m = re.search(r"app\.js\?v=(\d+)", r.text)
    assert m, "index.html must reference app.js with a numeric ?v= token"
    assert m.group(1) != "2", "version must be the dynamic mtime, not the placeholder"
    assert f"style.css?v={m.group(1)}" in r.text


def test_api_routes_take_precedence_over_static_mount(client):
    # The "/" StaticFiles mount must not shadow /api/* routes.
    resp = client.get("/api/report/unknown-id")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "任务不存在或已过期"


def test_no_gate_when_password_unset(client):
    # Default (no TRADINGAGENTS_WEB_PASSWORD): analyze works without a header.
    from web import app as app_module
    assert app_module.WEB_PASSWORD == ""
    with patch("web.app._run_analysis_thread"):
        resp = client.post("/api/analyze", json={
            "ticker": "TSLA", "date": "2026-05-18",
            "analysts": ["market"], "language": "Chinese",
        })
    assert resp.status_code == 200


def test_gate_rejects_missing_and_wrong_password(client, monkeypatch):
    from web import app as app_module
    monkeypatch.setattr(app_module, "WEB_PASSWORD", "s3cret")
    body = {"ticker": "TSLA", "date": "2026-05-18",
            "analysts": ["market"], "language": "Chinese"}
    # Missing header
    r1 = client.post("/api/analyze", json=body)
    assert r1.status_code == 401
    assert r1.json()["detail"] == "访问口令缺失或错误"
    # Wrong password
    r2 = client.post("/api/analyze", json=body,
                      headers={"X-Access-Password": "nope"})
    assert r2.status_code == 401
    # No job slot consumed by a rejected request
    assert not app_module.job_mgr.has_running_job()


def test_gate_accepts_correct_password(client, monkeypatch):
    from web import app as app_module
    monkeypatch.setattr(app_module, "WEB_PASSWORD", "s3cret")
    with patch("web.app._run_analysis_thread"):
        resp = client.post("/api/analyze", json={
            "ticker": "TSLA", "date": "2026-05-18",
            "analysts": ["market"], "language": "Chinese",
        }, headers={"X-Access-Password": "s3cret"})
    assert resp.status_code == 200
    assert "job_id" in resp.json()


@pytest.fixture
def seeded_history(tmp_path, monkeypatch):
    from web import history
    d = tmp_path / "web"
    monkeypatch.setattr(history, "HISTORY_DIR", d)
    monkeypatch.setattr(history, "_INDEX", d / "history.json")
    e1 = history.save_analysis("AAPL", "2026-05-19", "BUY", "# AAPL\nbody")
    e2 = history.save_analysis("TSLA", "2026-05-19", "SELL", "# TSLA\nbody")
    return e1, e2


def test_history_list_returns_entries(client, seeded_history):
    e1, e2 = seeded_history
    resp = client.get("/api/history")
    assert resp.status_code == 200
    ids = [i["id"] for i in resp.json()["items"]]
    assert ids == [e2["id"], e1["id"]]  # newest first


def test_history_report_roundtrip(client, seeded_history):
    e1, _ = seeded_history
    resp = client.get(f"/api/history/{e1['id']}")
    assert resp.status_code == 200
    assert resp.json()["content"] == "# AAPL\nbody"


def test_history_unknown_returns_404(client, seeded_history):
    resp = client.get("/api/history/NOPE_20260101-000000")
    assert resp.status_code == 404


def test_history_endpoints_gated(client, monkeypatch, seeded_history):
    from web import app as app_module
    monkeypatch.setattr(app_module, "WEB_PASSWORD", "s3cret")
    assert client.get("/api/history").status_code == 401
    assert client.get(f"/api/history/{seeded_history[0]['id']}").status_code == 401
    ok = client.get("/api/history", headers={"X-Access-Password": "s3cret"})
    assert ok.status_code == 200
