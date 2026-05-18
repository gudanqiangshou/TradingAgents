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
    assert "event: agent_status" in body
    assert "event: done" in body
