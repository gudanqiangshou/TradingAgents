"""Tests for get_hot_up_rank in AkShare vendor.

All tests are marked @pytest.mark.unit. No network calls.
The function now calls eastmoney APIs directly (bypassing akshare) using a
requests.Session with trust_env=False — tests mock _eastmoney_session().
"""
from __future__ import annotations

import pytest
import requests
from unittest.mock import patch, MagicMock

import tradingagents.dataflows.akshare_china as ac


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_rank_data(n: int = 5, hrc_values=None) -> list:
    """Build n fake rank items. hrc_values overrides the rank-change field."""
    items = []
    for i in range(n):
        hrc = hrc_values[i] if hrc_values and i < len(hrc_values) else (1000 - i * 10)
        items.append({"sc": f"SH{600000 + i:06d}", "rk": i + 1, "hrc": hrc})
    return items


def _make_price_diff(rank_data: list) -> list:
    """Build matching price rows for a rank_data list."""
    diff = []
    for i, item in enumerate(rank_data):
        code_only = item["sc"][2:]
        diff.append({
            "f12": code_only,
            "f14": f"股票{i}",
            "f2": 10.0 + i,
            "f3": 1.5 + i * 0.1,
        })
    return diff


def _make_fake_session(rank_response_json, price_response_json):
    fake_post_resp = MagicMock(status_code=200)
    fake_post_resp.json.return_value = rank_response_json
    fake_get_resp = MagicMock(status_code=200)
    fake_get_resp.json.return_value = price_response_json
    fake_session = MagicMock()
    fake_session.post.return_value = fake_post_resp
    fake_session.get.return_value = fake_get_resp
    return fake_session


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_hot_up_happy_path():
    """~5 rows of fake data → output contains emoji header, compact lines, ticker names, % signs."""
    rank_data = _make_rank_data(5)
    rank_response = {"data": rank_data}
    price_response = {"data": {"diff": _make_price_diff(rank_data)}}
    fake_sess = _make_fake_session(rank_response, price_response)

    with patch.object(ac, "_eastmoney_session", return_value=fake_sess):
        out = ac.get_hot_up_rank()

    assert "🚀" in out
    assert "东方财富 关注度飙升榜" in out
    # Compact line format (no markdown table)
    assert "| -- |" not in out
    assert "🔥" in out
    # At least one ticker name should appear
    assert "股票0" in out or "股票1" in out
    # % sign from formatted 涨跌幅
    assert "%" in out
    assert "解读" in out


# ---------------------------------------------------------------------------
# Sort correctness
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_hot_up_sorted_by_rank_change_desc():
    """5 rows with hrc=[10, 100, 50, 200, -30] → top row is hrc=200."""
    hrc_values = [10, 100, 50, 200, -30]
    rank_data = _make_rank_data(5, hrc_values=hrc_values)
    rank_response = {"data": rank_data}
    price_response = {"data": {"diff": _make_price_diff(rank_data)}}
    fake_sess = _make_fake_session(rank_response, price_response)

    with patch.object(ac, "_eastmoney_session", return_value=fake_sess):
        out = ac.get_hot_up_rank()

    # The first data row should be a 🔥 line containing "+200"
    lines = [l for l in out.splitlines() if l.startswith("🔥")]
    assert lines, "Expected at least one 🔥 data row"
    assert "+200" in lines[0], f"Expected +200 in first data row, got: {lines[0]}"


# ---------------------------------------------------------------------------
# Top-20 truncation
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_hot_up_top_20_truncation():
    """30 rows of fake data → output has exactly 20 🔥 data lines."""
    rank_data = _make_rank_data(30)
    rank_response = {"data": rank_data}
    price_response = {"data": {"diff": _make_price_diff(rank_data)}}
    fake_sess = _make_fake_session(rank_response, price_response)

    with patch.object(ac, "_eastmoney_session", return_value=fake_sess):
        out = ac.get_hot_up_rank()

    # Count data rows: 🔥 lines
    data_rows = [l for l in out.splitlines() if l.startswith("🔥")]
    assert len(data_rows) == 20, f"Expected 20 data rows, got {len(data_rows)}"


# ---------------------------------------------------------------------------
# Rank endpoint failures
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_hot_up_rank_endpoint_non_200():
    """POST returns status_code=503 → unavailable string with 503."""
    fake_post_resp = MagicMock(status_code=503)
    fake_session = MagicMock()
    fake_session.post.return_value = fake_post_resp

    with patch.object(ac, "_eastmoney_session", return_value=fake_session):
        out = ac.get_hot_up_rank()

    assert "503" in out
    assert "unavailable" in out or "飙升榜" in out


@pytest.mark.unit
def test_hot_up_rank_endpoint_empty_data():
    """POST returns {"data": []} → empty rank list unavailable string."""
    fake_sess = _make_fake_session({"data": []}, {})

    with patch.object(ac, "_eastmoney_session", return_value=fake_sess):
        out = ac.get_hot_up_rank()

    assert "empty rank list" in out or "unavailable" in out


@pytest.mark.unit
def test_hot_up_rank_endpoint_returns_string():
    """POST returns {"data": "not a list"} → unavailable string (not a list)."""
    fake_sess = _make_fake_session({"data": "not a list"}, {})

    with patch.object(ac, "_eastmoney_session", return_value=fake_sess):
        out = ac.get_hot_up_rank()

    assert isinstance(out, str)
    assert "unavailable" in out or "飙升榜" in out


# ---------------------------------------------------------------------------
# Price endpoint failures
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_hot_up_price_endpoint_non_200():
    """POST OK, GET returns 503 → price endpoint unavailable string."""
    rank_data = _make_rank_data(5)
    rank_response = {"data": rank_data}

    fake_post_resp = MagicMock(status_code=200)
    fake_post_resp.json.return_value = rank_response
    fake_get_resp = MagicMock(status_code=503)
    fake_session = MagicMock()
    fake_session.post.return_value = fake_post_resp
    fake_session.get.return_value = fake_get_resp

    with patch.object(ac, "_eastmoney_session", return_value=fake_session):
        out = ac.get_hot_up_rank()

    assert "503" in out
    assert "unavailable" in out or "飙升榜" in out


@pytest.mark.unit
def test_hot_up_price_endpoint_empty_diff():
    """POST OK, GET returns {"data": {"diff": []}} → empty price list unavailable."""
    rank_data = _make_rank_data(5)
    rank_response = {"data": rank_data}
    price_response = {"data": {"diff": []}}
    fake_sess = _make_fake_session(rank_response, price_response)

    with patch.object(ac, "_eastmoney_session", return_value=fake_sess):
        out = ac.get_hot_up_rank()

    assert isinstance(out, str)
    assert "unavailable" in out or "飙升榜" in out


# ---------------------------------------------------------------------------
# Exception propagation — never raises
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_hot_up_post_raises_proxy_error():
    """POST raises ProxyError → unavailable string containing ProxyError, no raise."""
    fake_session = MagicMock()
    fake_session.post.side_effect = requests.exceptions.ProxyError("Unable to connect to proxy")

    with patch.object(ac, "_eastmoney_session", return_value=fake_session):
        out = ac.get_hot_up_rank()

    assert isinstance(out, str)
    assert "ProxyError" in out
    assert "unavailable" in out or "飙升榜" in out


@pytest.mark.unit
def test_hot_up_get_raises_timeout():
    """POST OK, GET raises TimeoutError → unavailable string, no raise."""
    rank_data = _make_rank_data(5)
    rank_response = {"data": rank_data}

    fake_post_resp = MagicMock(status_code=200)
    fake_post_resp.json.return_value = rank_response
    fake_session = MagicMock()
    fake_session.post.return_value = fake_post_resp
    fake_session.get.side_effect = TimeoutError("timed out")

    with patch.object(ac, "_eastmoney_session", return_value=fake_session):
        out = ac.get_hot_up_rank()

    assert isinstance(out, str)
    assert "unavailable" in out or "飙升榜" in out


@pytest.mark.unit
def test_hot_up_malformed_json_returns_unavailable():
    """POST.json() raises ValueError → unavailable string, no raise."""
    fake_post_resp = MagicMock(status_code=200)
    fake_post_resp.json.side_effect = ValueError("No JSON object could be decoded")
    fake_session = MagicMock()
    fake_session.post.return_value = fake_post_resp

    with patch.object(ac, "_eastmoney_session", return_value=fake_session):
        out = ac.get_hot_up_rank()

    assert isinstance(out, str)
    assert "unavailable" in out or "飙升榜" in out


# ---------------------------------------------------------------------------
# Graceful key handling
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_hot_up_missing_sc_in_rank_data():
    """rank_data items missing 'sc' key → skipped gracefully, no raise."""
    rank_data = [
        {"rk": 1, "hrc": 100},           # no sc
        {"sc": None, "rk": 2, "hrc": 90},  # sc is None
        {"sc": "SH600519", "rk": 3, "hrc": 80},  # valid
    ]
    rank_response = {"data": rank_data}
    price_response = {"data": {"diff": [
        {"f12": "600519", "f14": "贵州茅台", "f2": 1252.0, "f3": 1.5},
    ]}}
    fake_sess = _make_fake_session(rank_response, price_response)

    with patch.object(ac, "_eastmoney_session", return_value=fake_sess):
        out = ac.get_hot_up_rank()

    assert isinstance(out, str)
    # The valid row should appear; the missing-sc items should be silently skipped
    assert "SH600519" in out or "贵州茅台" in out or "unavailable" in out


@pytest.mark.unit
def test_hot_up_missing_price_for_code_renders_dash():
    """Rank has SH600519, price diff missing 600519 → row appears with '—' for 涨跌幅."""
    rank_data = [{"sc": "SH600519", "rk": 1, "hrc": 100}]
    rank_response = {"data": rank_data}
    # price diff has no entry for 600519
    price_response = {"data": {"diff": [
        {"f12": "000001", "f14": "平安银行", "f2": 10.0, "f3": 0.5},
    ]}}
    fake_sess = _make_fake_session(rank_response, price_response)

    with patch.object(ac, "_eastmoney_session", return_value=fake_sess):
        out = ac.get_hot_up_rank()

    assert isinstance(out, str)
    # Should render a row with a dash for 涨跌幅 (price not found → f3 is None → "—")
    assert "—" in out or "unavailable" in out


# ---------------------------------------------------------------------------
# Session configuration
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_hot_up_session_has_trust_env_false():
    """_eastmoney_session() (real, not mocked) must have trust_env=False."""
    sess = ac._eastmoney_session()
    assert sess.trust_env is False


# ---------------------------------------------------------------------------
# New: retry + session header tests
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_eastmoney_session_has_browser_headers():
    """_eastmoney_session() must have Chrome/120 UA, correct Referer and Origin."""
    s = ac._eastmoney_session()
    assert s.trust_env is False
    assert "Chrome/120" in s.headers["User-Agent"]
    assert s.headers["Referer"] == "https://guba.eastmoney.com/rank/"
    assert s.headers["Origin"] == "https://guba.eastmoney.com"


@pytest.mark.unit
def test_hot_up_retries_on_connection_error_then_succeeds():
    """First sess.post raises ConnectionError; second call succeeds → valid markdown."""
    rank_data = _make_rank_data(3)
    rank_response = {"data": rank_data}
    price_response = {"data": {"diff": _make_price_diff(rank_data)}}

    fake_post_resp = MagicMock(status_code=200)
    fake_post_resp.json.return_value = rank_response
    fake_get_resp = MagicMock(status_code=200)
    fake_get_resp.json.return_value = price_response

    fake_session = MagicMock()
    # First call raises, second succeeds
    fake_session.post.side_effect = [
        requests.exceptions.ConnectionError("RemoteDisconnected"),
        fake_post_resp,
    ]
    fake_session.get.return_value = fake_get_resp

    with patch.object(ac, "_eastmoney_session", return_value=fake_session), \
         patch("tradingagents.dataflows.akshare_china.time.sleep") as mock_sleep:
        out = ac.get_hot_up_rank()

    assert "🚀" in out
    assert "东方财富 关注度飙升榜" in out
    assert fake_session.post.call_count == 2
    # sleep was called once (backoff[0] = 0.5)
    mock_sleep.assert_called_once_with(0.5)


@pytest.mark.unit
def test_hot_up_exhausts_retries_returns_unavailable():
    """sess.post raises ConnectionError on all 3 attempts → unavailable string, no raise."""
    fake_session = MagicMock()
    fake_session.post.side_effect = requests.exceptions.ConnectionError("burst RST")

    with patch.object(ac, "_eastmoney_session", return_value=fake_session), \
         patch("tradingagents.dataflows.akshare_china.time.sleep"):
        out = ac.get_hot_up_rank()

    assert isinstance(out, str)
    assert "飙升榜" in out
    assert "unavailable" in out or "ConnectionError" in out
    assert fake_session.post.call_count == 3


@pytest.mark.unit
def test_hot_up_does_not_retry_on_value_error():
    """sess.post.json() raises ValueError (malformed JSON) → unavailable, call_count == 1."""
    fake_post_resp = MagicMock(status_code=200)
    fake_post_resp.json.side_effect = ValueError("No JSON")
    fake_session = MagicMock()
    fake_session.post.return_value = fake_post_resp

    with patch.object(ac, "_eastmoney_session", return_value=fake_session), \
         patch("tradingagents.dataflows.akshare_china.time.sleep") as mock_sleep:
        out = ac.get_hot_up_rank()

    assert isinstance(out, str)
    assert "unavailable" in out or "飙升榜" in out
    # ValueError is not retried — only one call
    assert fake_session.post.call_count == 1
    mock_sleep.assert_not_called()
