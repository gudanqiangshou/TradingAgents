"""Tests for stocktwits.py — including the refined 404 error handling."""

from __future__ import annotations

import json
import pytest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError
from io import BytesIO

from tradingagents.dataflows.stocktwits import fetch_stocktwits_messages


def _make_http_error(code: int) -> HTTPError:
    """Build a minimal HTTPError with the given code."""
    return HTTPError(
        url="https://api.stocktwits.com/api/2/streams/symbol/TEST.json",
        code=code,
        msg=f"HTTP {code}",
        hdrs=None,
        fp=None,
    )


# ---------------------------------------------------------------------------
# 404 — distinct message
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_stocktwits_404_returns_no_data_message():
    """HTTPError 404 → message contains 'no data for' and 'symbol not in their US-equity database'. No raise."""
    with patch("tradingagents.dataflows.stocktwits.urlopen", side_effect=_make_http_error(404)):
        result = fetch_stocktwits_messages("600519")
    assert isinstance(result, str)
    assert "no data for" in result
    assert "symbol not in their US-equity database" in result


# ---------------------------------------------------------------------------
# Other HTTP errors
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_stocktwits_other_http_error_returns_unavailable():
    """HTTPError 429 → 'HTTP 429' in output. No raise."""
    with patch("tradingagents.dataflows.stocktwits.urlopen", side_effect=_make_http_error(429)):
        result = fetch_stocktwits_messages("AAPL")
    assert isinstance(result, str)
    assert "HTTP 429" in result


@pytest.mark.unit
def test_stocktwits_http_500_returns_unavailable():
    """HTTPError 500 → 'HTTP 500' in output. No raise."""
    with patch("tradingagents.dataflows.stocktwits.urlopen", side_effect=_make_http_error(500)):
        result = fetch_stocktwits_messages("AAPL")
    assert isinstance(result, str)
    assert "HTTP 500" in result


# ---------------------------------------------------------------------------
# URLError / TimeoutError — unchanged behavior
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_stocktwits_timeout_unchanged():
    """TimeoutError → '<stocktwits unavailable: TimeoutError>' shape preserved. No raise."""
    with patch("tradingagents.dataflows.stocktwits.urlopen", side_effect=TimeoutError("timed out")):
        result = fetch_stocktwits_messages("AAPL")
    assert isinstance(result, str)
    assert "<stocktwits unavailable: TimeoutError>" == result


@pytest.mark.unit
def test_stocktwits_url_error_returns_unavailable():
    """URLError → '<stocktwits unavailable: URLError>'. No raise."""
    with patch("tradingagents.dataflows.stocktwits.urlopen", side_effect=URLError("no route")):
        result = fetch_stocktwits_messages("AAPL")
    assert isinstance(result, str)
    assert "<stocktwits unavailable: URLError>" == result
