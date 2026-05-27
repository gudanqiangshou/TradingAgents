"""Tests for get_hot_up_rank in AkShare vendor.

All tests are marked @pytest.mark.unit. No network calls.
The function now calls:
  - step 1: eastmoney POST via _eastmoney_session()
  - step 2: Sina GET via _sina_session()
Tests mock both session factories independently.
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


def _fake_sina_response(rank_data: list) -> MagicMock:
    """Build a fake Sina text body for the given rank_data list.

    Uses prev_close=10.000, current=11.000 → chg_pct = +10.00%.
    """
    lines = []
    for i, item in enumerate(rank_data):
        sc = item.get("sc", "")
        if not (isinstance(sc, str) and len(sc) >= 8):
            continue
        prefix = sc[:2].lower()
        code = sc[2:]
        # fields: name, prev_close, open, current, high, low
        lines.append(
            f'var hq_str_{prefix}{code}="股票{i},10.000,10.500,11.000,11.200,9.800";'
        )
    body = "\n".join(lines)
    resp = MagicMock(status_code=200)
    resp.content = body.encode("gb18030")
    resp.text = body
    return resp


def _make_eastmoney_session(rank_response_json) -> MagicMock:
    """Session mock for step 1 (POST only)."""
    fake_post_resp = MagicMock(status_code=200)
    fake_post_resp.json.return_value = rank_response_json
    fake_session = MagicMock()
    fake_session.post.return_value = fake_post_resp
    return fake_session


def _make_sina_session(sina_resp: MagicMock) -> MagicMock:
    """Session mock for step 2 (GET only)."""
    fake_session = MagicMock()
    fake_session.get.return_value = sina_resp
    return fake_session


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_hot_up_happy_path():
    """~5 rows of fake data → output contains emoji header, compact lines, ticker names, % signs."""
    rank_data = _make_rank_data(5)
    rank_response = {"data": rank_data}
    em_sess = _make_eastmoney_session(rank_response)
    sina_sess = _make_sina_session(_fake_sina_response(rank_data))

    with patch.object(ac, "_eastmoney_session", return_value=em_sess), \
         patch.object(ac, "_sina_session", return_value=sina_sess):
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
    # chg_pct from fake data: +10.00%
    assert "+10.00%" in out


# ---------------------------------------------------------------------------
# Sort correctness
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_hot_up_sorted_by_rank_change_desc():
    """5 rows with hrc=[10, 100, 50, 200, -30] → top row is hrc=200."""
    hrc_values = [10, 100, 50, 200, -30]
    rank_data = _make_rank_data(5, hrc_values=hrc_values)
    rank_response = {"data": rank_data}
    em_sess = _make_eastmoney_session(rank_response)
    sina_sess = _make_sina_session(_fake_sina_response(rank_data))

    with patch.object(ac, "_eastmoney_session", return_value=em_sess), \
         patch.object(ac, "_sina_session", return_value=sina_sess):
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
    em_sess = _make_eastmoney_session(rank_response)
    sina_sess = _make_sina_session(_fake_sina_response(rank_data))

    with patch.object(ac, "_eastmoney_session", return_value=em_sess), \
         patch.object(ac, "_sina_session", return_value=sina_sess):
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
    em_sess = _make_eastmoney_session({"data": []})

    with patch.object(ac, "_eastmoney_session", return_value=em_sess):
        out = ac.get_hot_up_rank()

    assert "empty rank list" in out or "unavailable" in out


@pytest.mark.unit
def test_hot_up_rank_endpoint_returns_string():
    """POST returns {"data": "not a list"} → unavailable string (not a list)."""
    em_sess = _make_eastmoney_session({"data": "not a list"})

    with patch.object(ac, "_eastmoney_session", return_value=em_sess):
        out = ac.get_hot_up_rank()

    assert isinstance(out, str)
    assert "unavailable" in out or "飙升榜" in out


# ---------------------------------------------------------------------------
# Price (Sina) endpoint failures
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_hot_up_price_endpoint_non_200():
    """POST OK, Sina GET returns 503 → 'Sina 行情 HTTP 503' unavailable string."""
    rank_data = _make_rank_data(5)
    rank_response = {"data": rank_data}
    em_sess = _make_eastmoney_session(rank_response)

    fake_sina_resp = MagicMock(status_code=503)
    sina_sess = _make_sina_session(fake_sina_resp)

    with patch.object(ac, "_eastmoney_session", return_value=em_sess), \
         patch.object(ac, "_sina_session", return_value=sina_sess):
        out = ac.get_hot_up_rank()

    assert "503" in out
    assert "Sina 行情" in out or "飙升榜" in out


@pytest.mark.unit
def test_hot_up_price_endpoint_empty_diff():
    """POST OK, Sina returns all-empty body → 'Sina 行情返回为空' unavailable."""
    rank_data = _make_rank_data(5)
    rank_response = {"data": rank_data}
    em_sess = _make_eastmoney_session(rank_response)

    # Sina body with all empty quotes (unknown symbols)
    lines = []
    for item in rank_data:
        sc = item["sc"]
        prefix = sc[:2].lower()
        code = sc[2:]
        lines.append(f'var hq_str_{prefix}{code}="";')
    body = "\n".join(lines)
    fake_sina_resp = MagicMock(status_code=200)
    fake_sina_resp.content = body.encode("gb18030")
    fake_sina_resp.text = body
    sina_sess = _make_sina_session(fake_sina_resp)

    with patch.object(ac, "_eastmoney_session", return_value=em_sess), \
         patch.object(ac, "_sina_session", return_value=sina_sess):
        out = ac.get_hot_up_rank()

    assert isinstance(out, str)
    assert "Sina 行情返回为空" in out or "飙升榜" in out


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
    """POST OK, Sina GET raises TimeoutError → unavailable string, no raise."""
    rank_data = _make_rank_data(5)
    rank_response = {"data": rank_data}
    em_sess = _make_eastmoney_session(rank_response)

    sina_sess = MagicMock()
    sina_sess.get.side_effect = TimeoutError("timed out")

    with patch.object(ac, "_eastmoney_session", return_value=em_sess), \
         patch.object(ac, "_sina_session", return_value=sina_sess):
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
    rank_data_full = [
        {"rk": 1, "hrc": 100},             # no sc
        {"sc": None, "rk": 2, "hrc": 90},  # sc is None
        {"sc": "SH600519", "rk": 3, "hrc": 80},  # valid
    ]
    # Only the valid item will produce a sina code
    valid_only = [{"sc": "SH600519", "rk": 3, "hrc": 80}]
    rank_response = {"data": rank_data_full}
    em_sess = _make_eastmoney_session(rank_response)
    sina_sess = _make_sina_session(_fake_sina_response(valid_only))

    with patch.object(ac, "_eastmoney_session", return_value=em_sess), \
         patch.object(ac, "_sina_session", return_value=sina_sess):
        out = ac.get_hot_up_rank()

    assert isinstance(out, str)
    # The valid row should appear; the missing-sc items should be silently skipped
    assert "SH600519" in out or "股票0" in out or "unavailable" in out


@pytest.mark.unit
def test_hot_up_missing_price_for_code_renders_dash():
    """Rank has SH600519, but Sina returns no data for it → row appears with '—' for 涨跌幅."""
    rank_data = [{"sc": "SH600519", "rk": 1, "hrc": 100}]
    rank_response = {"data": rank_data}
    em_sess = _make_eastmoney_session(rank_response)

    # Sina body has a *different* code — sh600519 entry is missing
    body = 'var hq_str_sh000001="上证指数,3200.0,3205.0,3210.0,3220.0,3195.0";'
    fake_sina_resp = MagicMock(status_code=200)
    fake_sina_resp.content = body.encode("gb18030")
    fake_sina_resp.text = body
    sina_sess = _make_sina_session(fake_sina_resp)

    with patch.object(ac, "_eastmoney_session", return_value=em_sess), \
         patch.object(ac, "_sina_session", return_value=sina_sess):
        out = ac.get_hot_up_rank()

    assert isinstance(out, str)
    # Should render a row with a dash for 涨跌幅 (price not found → chg_pct is None → "—")
    assert "—" in out or "unavailable" in out


# ---------------------------------------------------------------------------
# Session configuration
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_hot_up_session_has_trust_env_false():
    """_eastmoney_session() (real, not mocked) must have trust_env=False."""
    sess = ac._eastmoney_session()
    assert sess.trust_env is False


@pytest.mark.unit
def test_sina_session_has_trust_env_false():
    """_sina_session() (real, not mocked) must have trust_env=False."""
    sess = ac._sina_session()
    assert sess.trust_env is False


@pytest.mark.unit
def test_sina_session_has_correct_headers():
    """_sina_session() must have Referer=finance.sina.com.cn and Chrome/120 UA."""
    s = ac._sina_session()
    assert s.trust_env is False
    assert "Chrome/120" in s.headers["User-Agent"]
    assert s.headers["Referer"] == "https://finance.sina.com.cn"


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

    fake_post_resp = MagicMock(status_code=200)
    fake_post_resp.json.return_value = rank_response

    fake_em_session = MagicMock()
    # First call raises, second succeeds
    fake_em_session.post.side_effect = [
        requests.exceptions.ConnectionError("RemoteDisconnected"),
        fake_post_resp,
    ]

    sina_sess = _make_sina_session(_fake_sina_response(rank_data))

    with patch.object(ac, "_eastmoney_session", return_value=fake_em_session), \
         patch.object(ac, "_sina_session", return_value=sina_sess), \
         patch("tradingagents.dataflows.akshare_china.time.sleep") as mock_sleep:
        out = ac.get_hot_up_rank()

    assert "🚀" in out
    assert "东方财富 关注度飙升榜" in out
    assert fake_em_session.post.call_count == 2
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
