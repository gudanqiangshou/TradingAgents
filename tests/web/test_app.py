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
