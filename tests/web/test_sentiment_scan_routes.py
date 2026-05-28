"""Tests for the four Phase-10 sentiment-scan viewer/API routes in web/app.py.

Coverage:
  GET /api/sentiment-scan/{date}                    — list ticker analyses
  GET /api/sentiment-scan/{date}/{code}             — per-ticker metadata
  GET /api/sentiment-scan/{date}/{code}/reports/{name}  — markdown body
  GET /sentiment-scan/{date}/{code}                 — HTML viewer page
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A TestClient pointed at an isolated TRADINGAGENTS_SENTIMENT_SCAN_DIR.

    Each test gets its own tmp_path so writes from one test don't leak into
    another. The fixture also strips the access password (Phase-10 routes
    are ungated by design, but the file-level fixture in test_app.py shows
    the override pattern).
    """
    monkeypatch.setenv("TRADINGAGENTS_SENTIMENT_SCAN_DIR", str(tmp_path))
    from web import app as app_module

    app_module.WEB_PASSWORD = ""
    # Reset job state between tests (lifted from test_app.py)
    app_module.job_mgr._jobs.clear()
    app_module.job_mgr._running_job_id = None
    from web.app import app
    return TestClient(app)


def _make_snapshot(code="600519", name="贵州茅台") -> dict:
    """A minimal but realistic snapshot — schema_version 1."""
    return {
        "schema_version": 1,
        "date": "2026-06-01",
        "scan_completed_at": "06:31:08",
        "analysis_completed_at": "08:42:13",
        "analysis_budget_exhausted": False,
        "sections": {
            "section_a": {"display": "", "top20_codes": [], "rank_by_code": {}, "summary_by_code": {}},
            "section_b": {"display": "", "top20_codes": [], "rank_by_code": {}, "summary_by_code": {}},
            "section_c": {"display": "", "top20_codes": [], "rank_by_code": {}, "summary_by_code": {}},
            "section_d": {"display": "", "top20_codes": [], "rank_by_code": {}, "summary_by_code": {}},
            "intersection": {"triple": [code], "ab_only": [], "ac_only": [], "bc_only": []},
        },
        "analyses": [
            {
                "code": code,
                "name": name,
                "market": "A_SHARE",
                "tier": "triple",
                "ranks": {"a": 3, "b": 1, "c": 8},
                "fundamentals": {
                    "pe_ttm": 25.3, "pe_forward": 22.1, "fcf": 5.6e10, "roe": 0.308,
                    "market_cap": 3.2e12, "currency": "CNY", "as_of": "2026-06-01",
                    "source": "akshare", "missing_fields": [], "status": "ok",
                },
                "decision": {
                    "rating": "Overweight", "action": "BUY",
                    "summary_1line": "高端白酒龙头机构净买入背书",
                },
                "elapsed_seconds": 612,
                "status": "ok",
                "report_paths": {
                    "fundamentals_report": "/abs/fundamentals_report.md",
                    "news_report": "/abs/news_report.md",
                    "investment_plan": "/abs/investment_plan.md",
                    "trader_investment_plan": "/abs/trader_investment_plan.md",
                    "final_trade_decision": "/abs/final_trade_decision.md",
                },
            },
        ],
    }


def _write_snapshot(tmp_path, date: str, snap: dict | None = None) -> None:
    (tmp_path / f"{date}.json").write_text(
        json.dumps(snap or _make_snapshot()), encoding="utf-8"
    )


def _write_report(tmp_path, date: str, code: str, name: str, body: str) -> None:
    rdir = tmp_path / "reports" / date / code
    rdir.mkdir(parents=True, exist_ok=True)
    (rdir / f"{name}.md").write_text(body, encoding="utf-8")


# ---------------------------------------------------------------------------
# /api/sentiment-scan/{date}
# ---------------------------------------------------------------------------

def test_get_date_returns_analyses_list_no_full_reports(client, tmp_path):
    """200 + JSON with the trimmed analyses list (no full markdown body)."""
    _write_snapshot(tmp_path, "2026-06-01")
    resp = client.get("/api/sentiment-scan/2026-06-01")
    assert resp.status_code == 200
    data = resp.json()
    assert data["date"] == "2026-06-01"
    assert data["scan_completed_at"] == "06:31:08"
    assert data["analysis_completed_at"] == "08:42:13"
    assert len(data["analyses"]) == 1
    item = data["analyses"][0]
    assert item["code"] == "600519"
    assert item["name"] == "贵州茅台"
    assert item["tier"] == "triple"
    assert item["status"] == "ok"
    assert item["decision"]["action"] == "BUY"
    # report_paths included, but the response does NOT carry the full markdown
    # body (that's a separate route — keeps this endpoint cheap).
    assert "fundamentals_report" in item["report_paths"]
    # No "markdown" or "body" field smuggled in
    assert "body" not in item
    assert "markdown" not in item


def test_get_date_404_when_snapshot_missing(client):
    resp = client.get("/api/sentiment-scan/2099-12-31")
    assert resp.status_code == 404
    assert "snapshot not found" in resp.json()["detail"]


@pytest.mark.parametrize("bad_date", [
    "2026-13-99",       # invalid month
    "2026-06-1",        # missing zero-padding
    "20260601",         # no dashes
    "../../etc/passwd", # path-traversal attempt
    "2026-06-01x",      # trailing junk
])
def test_get_date_400_when_invalid_date_format(client, bad_date):
    resp = client.get(f"/api/sentiment-scan/{bad_date}")
    # 400 = our explicit regex rejection. FastAPI may also 404 if the path
    # doesn't match (e.g. when the date contains slashes that hit a
    # different sub-route). Both are safe; this test rejects 200.
    assert resp.status_code in (400, 404, 405)
    if resp.status_code == 400:
        assert "invalid date" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# /api/sentiment-scan/{date}/{code}
# ---------------------------------------------------------------------------

def test_get_ticker_returns_analysis_data(client, tmp_path):
    _write_snapshot(tmp_path, "2026-06-01")
    resp = client.get("/api/sentiment-scan/2026-06-01/600519")
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == "600519"
    assert data["name"] == "贵州茅台"
    assert data["status"] == "ok"
    assert data["fundamentals"]["pe_ttm"] == 25.3
    # Full report_paths surfaced so the viewer can offer tabs
    assert set(data["report_paths"].keys()) == {
        "fundamentals_report",
        "news_report",
        "investment_plan",
        "trader_investment_plan",
        "final_trade_decision",
    }


def test_get_ticker_404_when_not_in_snapshot(client, tmp_path):
    _write_snapshot(tmp_path, "2026-06-01")
    resp = client.get("/api/sentiment-scan/2026-06-01/NOTEXIST")
    assert resp.status_code == 404
    assert "ticker not found" in resp.json()["detail"]


def test_get_ticker_400_when_invalid_code(client):
    # Slashes / dots-only / overlong / unicode — all rejected
    for bad in ["!!!@", "../etc", "X" * 13]:
        resp = client.get(f"/api/sentiment-scan/2026-06-01/{bad}")
        assert resp.status_code in (400, 404), bad


def test_get_ticker_404_when_snapshot_missing(client):
    resp = client.get("/api/sentiment-scan/2099-12-31/600519")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# /api/sentiment-scan/{date}/{code}/reports/{report_name}
# ---------------------------------------------------------------------------

def test_get_report_returns_markdown_plain_text(client, tmp_path):
    md = "## 基本面分析报告\n\n茅台估值合理，机构净买入信号强。\n"
    _write_report(tmp_path, "2026-06-01", "600519", "fundamentals_report", md)
    resp = client.get("/api/sentiment-scan/2026-06-01/600519/reports/fundamentals_report")
    assert resp.status_code == 200
    assert "text/markdown" in resp.headers["content-type"]
    assert resp.text == md


def test_get_report_path_traversal_rejected(client, tmp_path):
    """The whitelist enforces the only legal report names; anything else 400s.

    A real attack — URL-encoded ..%2Fetc%2Fpasswd — gets normalized by the
    HTTP framework before hitting the param. Whitelist is the real defense.
    """
    # URL-encoded dot-segment attack — should be rejected by the whitelist
    bad_names = [
        "..%2Fetc%2Fpasswd",
        "../../../etc/passwd",
        "fundamentals_report.md",       # with .md suffix not in whitelist
        "FUNDAMENTALS_REPORT",          # different case (whitelist is strict)
        "market_report",                # we don't run market analyst
        "sentiment_report",             # we don't run social analyst
        "",                             # empty
    ]
    for bad in bad_names:
        resp = client.get(
            f"/api/sentiment-scan/2026-06-01/600519/reports/{bad}"
        )
        # Acceptable: 400 (whitelist), 404 (route mismatch), 405 (method).
        # The forbidden response is 200 — that would be a vulnerability.
        assert resp.status_code != 200, f"path traversal vector accepted: {bad!r}"


def test_get_report_404_when_file_missing(client, tmp_path):
    # Whitelisted name, but the file doesn't exist on disk
    resp = client.get("/api/sentiment-scan/2026-06-01/600519/reports/news_report")
    assert resp.status_code == 404
    assert "report not found" in resp.json()["detail"]


def test_get_report_400_when_invalid_date(client):
    resp = client.get(
        "/api/sentiment-scan/bad-date/600519/reports/fundamentals_report"
    )
    assert resp.status_code == 400


def test_get_report_400_when_invalid_code(client):
    resp = client.get(
        "/api/sentiment-scan/2026-06-01/!!!/reports/fundamentals_report"
    )
    assert resp.status_code == 400


def test_get_report_each_whitelisted_section_servable(client, tmp_path):
    """All 5 whitelisted names work when their file exists on disk."""
    sections = {
        "fundamentals_report":     "# fund\nbody-fund",
        "news_report":             "# news\nbody-news",
        "investment_plan":         "# inv\nbody-inv",
        "trader_investment_plan":  "# trd\nbody-trd",
        "final_trade_decision":    "# fin\nbody-fin",
    }
    for name, body in sections.items():
        _write_report(tmp_path, "2026-06-01", "600519", name, body)
    for name, body in sections.items():
        resp = client.get(f"/api/sentiment-scan/2026-06-01/600519/reports/{name}")
        assert resp.status_code == 200, name
        assert resp.text == body


# ---------------------------------------------------------------------------
# /sentiment-scan/{date}/{code}  (HTML viewer)
# ---------------------------------------------------------------------------

def test_serve_viewer_html_returns_html(client):
    """The HTML page is served even when no data exists yet — the JS shows
    a 404 inline when the API call fails. This matches the spec."""
    resp = client.get("/sentiment-scan/2026-06-01/600519")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    # The viewer-specific markers
    assert "viewer-wrap" in resp.text
    assert "/sentiment-scan.js" in resp.text  # references our JS bundle


def test_serve_viewer_400_when_invalid_path(client):
    resp = client.get("/sentiment-scan/bad-date/600519")
    assert resp.status_code == 400
    resp = client.get("/sentiment-scan/2026-06-01/!!!")
    assert resp.status_code == 400


def test_viewer_js_is_reachable_via_static_mount(client):
    """The viewer's <script src='/sentiment-scan.js'> must resolve."""
    resp = client.get("/sentiment-scan.js")
    assert resp.status_code == 200
    assert "javascript" in resp.headers["content-type"]
    # Quick sanity on the contents
    assert "/api/sentiment-scan/" in resp.text
    assert "renderMarkdown" in resp.text
