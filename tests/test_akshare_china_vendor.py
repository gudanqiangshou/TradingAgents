"""
Tests for the AkShare A-share data vendor (tradingagents/dataflows/akshare_china.py).

Fully offline — mocks _dep_bootstrap.ensure so that akshare is never imported.
All tests are marked @pytest.mark.unit.
"""

import pytest
import pandas as pd
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers — build a realistic fake akshare DataFrame with Chinese columns
# ---------------------------------------------------------------------------

def _make_ak_df(rows=None):
    """Return a DataFrame that mimics ak.stock_zh_a_hist output."""
    if rows is None:
        # 3 rows, intentionally unsorted, floats needing rounding
        rows = [
            {"日期": "2026-01-09", "开盘": 1800.123, "收盘": 1820.456, "最高": 1850.789, "最低": 1790.111, "成交量": 5000000, "成交额": 9.1e9, "涨跌幅": 1.12},
            {"日期": "2026-01-05", "开盘": 1750.001, "收盘": 1760.999, "最高": 1770.555, "最低": 1740.333, "成交量": 4500000, "成交额": 7.9e9, "涨跌幅": 0.50},
            {"日期": "2026-01-07", "开盘": 1780.222, "收盘": 1795.888, "最高": 1810.777, "最低": 1765.444, "成交量": 4800000, "成交额": 8.6e9, "涨跌幅": 0.90},
        ]
    return pd.DataFrame(rows)


def _make_ak_empty_df():
    return pd.DataFrame(columns=["日期", "开盘", "收盘", "最高", "最低", "成交量", "成交额", "涨跌幅"])


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_ak():
    """A MagicMock pretending to be the akshare module."""
    ak = MagicMock()
    ak.stock_zh_a_hist.return_value = _make_ak_df()
    return ak


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_get_stock_data_formats_like_yfinance_contract(fake_ak):
    """Output string must match the exact 3-line header + blank + CSV contract."""
    with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=fake_ak):
        from tradingagents.dataflows.akshare_china import get_stock_data
        result = get_stock_data("600519", "2026-01-05", "2026-01-09")

    # Header line 1
    assert result.startswith("# Stock data for 600519 from 2026-01-05 to 2026-01-09")
    # Header line 2
    assert "# Total records: 3" in result
    # Header line 3
    assert "# Data retrieved on:" in result

    # Blank line separator before CSV
    # The header ends with "\n\n" then the CSV starts
    parts = result.split("\n\n", 1)
    assert len(parts) == 2, "Expected blank line between header and CSV"
    csv_part = parts[1]

    # CSV header row
    lines = [l for l in csv_part.splitlines() if l]
    assert lines[0] == "Date,Open,High,Low,Close,Volume"

    # Dates must be in ascending order
    date_values = [l.split(",")[0] for l in lines[1:]]
    assert date_values == sorted(date_values), "Dates are not in ascending order"

    # OHLC values rounded to 2dp (spot-check first data row — sorted → 2026-01-05)
    first_row = lines[1].split(",")
    # first_row: Date, Open, High, Low, Close, Volume
    for col_idx in [1, 2, 3, 4]:  # Open, High, Low, Close
        val = first_row[col_idx]
        assert "." in val
        decimal_places = len(val.split(".")[1])
        assert decimal_places <= 2, f"Column index {col_idx} has >2 decimal places: {val}"


@pytest.mark.unit
def test_symbol_suffix_stripped_for_akshare(fake_ak):
    """Strip .SH/.SZ/.SS/.BJ suffixes and whitespace; pass bare 6-digit code to akshare."""
    with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=fake_ak):
        from tradingagents.dataflows.akshare_china import get_stock_data

        # .SH suffix
        fake_ak.reset_mock()
        get_stock_data("600519.SH", "2026-01-05", "2026-01-09")
        kwargs = fake_ak.stock_zh_a_hist.call_args[1]
        assert kwargs["symbol"] == "600519"
        assert kwargs["period"] == "daily"
        assert kwargs["adjust"] == "qfq"

        # lowercase .sz suffix with surrounding spaces
        fake_ak.reset_mock()
        get_stock_data(" 600519.sz ", "2026-01-05", "2026-01-09")
        kwargs = fake_ak.stock_zh_a_hist.call_args[1]
        assert kwargs["symbol"] == "600519"
        assert kwargs["period"] == "daily"
        assert kwargs["adjust"] == "qfq"

        # .SS suffix (Shanghai, alternate form)
        fake_ak.reset_mock()
        get_stock_data("600519.SS", "2026-01-05", "2026-01-09")
        kwargs = fake_ak.stock_zh_a_hist.call_args[1]
        assert kwargs["symbol"] == "600519"
        assert kwargs["period"] == "daily"
        assert kwargs["adjust"] == "qfq"

        # .BJ suffix (Beijing Stock Exchange)
        fake_ak.reset_mock()
        get_stock_data("430047.BJ", "2026-01-05", "2026-01-09")
        kwargs = fake_ak.stock_zh_a_hist.call_args[1]
        assert kwargs["symbol"] == "430047"
        assert kwargs["period"] == "daily"
        assert kwargs["adjust"] == "qfq"


@pytest.mark.unit
def test_dates_converted_to_YYYYMMDD(fake_ak):
    """Dates passed as yyyy-mm-dd must be forwarded to akshare as YYYYMMDD (no dashes)."""
    with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=fake_ak):
        from tradingagents.dataflows.akshare_china import get_stock_data
        get_stock_data("600519", "2026-01-05", "2026-01-09")

    kwargs = fake_ak.stock_zh_a_hist.call_args[1]
    assert kwargs["start_date"] == "20260105"
    assert kwargs["end_date"] == "20260109"


@pytest.mark.unit
def test_empty_dataframe_returns_no_data_message():
    """Empty DataFrame from akshare → exact no-data string."""
    ak = MagicMock()
    ak.stock_zh_a_hist.return_value = _make_ak_empty_df()

    with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
        from tradingagents.dataflows.akshare_china import get_stock_data
        result = get_stock_data("600519", "2026-01-05", "2026-01-09")

    assert result == "No data found for symbol '600519' between 2026-01-05 and 2026-01-09"


@pytest.mark.unit
def test_dependency_unavailable_returns_error_string_not_exception():
    """DependencyUnavailable must be caught; function returns an error string, never raises."""
    # Import akshare_china first so its _dep_bootstrap reference is stable.
    # Then use the DependencyUnavailable class from THAT same module object so
    # the except clause in get_stock_data catches the right class identity
    # (test_dep_bootstrap.py's autouse fixture deletes _dep_bootstrap from
    # sys.modules between tests, which would create a fresh class object that
    # wouldn't match the one already stored in akshare_china._dep_bootstrap).
    import tradingagents.dataflows.akshare_china as _vendor_mod
    DependencyUnavailable = _vendor_mod._dep_bootstrap.DependencyUnavailable

    with patch(
        "tradingagents.dataflows.akshare_china._dep_bootstrap.ensure",
        side_effect=DependencyUnavailable("akshare: pip install failed"),
    ):
        # Must NOT raise
        result = _vendor_mod.get_stock_data("600519", "2026-01-05", "2026-01-09")

    assert isinstance(result, str)
    assert result.lower().startswith("error:")
    # Should mention akshare or unavailable
    assert "akshare" in result.lower() or "unavailable" in result.lower()


@pytest.mark.unit
def test_non_a_share_symbol_returns_clear_message():
    """Non-A-share symbols (e.g. AAPL) must not call akshare at all."""
    ensure_mock = MagicMock()

    with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", ensure_mock):
        from tradingagents.dataflows.akshare_china import get_stock_data
        result = get_stock_data("AAPL", "2026-01-05", "2026-01-09")

    # ensure must NOT have been called (no akshare lookup)
    ensure_mock.assert_not_called()

    # Result must be a clear, non-empty string
    assert isinstance(result, str)
    assert len(result) > 0
    # Must convey that this vendor handles A-share only
    lower = result.lower()
    assert "a-share" in lower or "a_share" in lower or "a share" in lower


@pytest.mark.unit
def test_akshare_runtime_exception_returns_error_string():
    """Network/runtime errors from akshare must be caught; function returns error string, never raises."""
    import tradingagents.dataflows.akshare_china as _vendor_mod

    fake_ak = MagicMock()
    fake_ak.stock_zh_a_hist.side_effect = ConnectionError("timeout")

    with patch(
        "tradingagents.dataflows.akshare_china._dep_bootstrap.ensure",
        return_value=fake_ak,
    ):
        # Must NOT raise
        result = _vendor_mod.get_stock_data("600519", "2026-01-05", "2026-01-09")

    assert isinstance(result, str)
    assert "Error" in result
    assert "600519" in result


@pytest.mark.unit
def test_invalid_date_returns_error_string():
    """Malformed date (e.g. slash-separated) must return an error string, never raise."""
    import tradingagents.dataflows.akshare_china as _vendor_mod

    ensure_mock = MagicMock()

    with patch(
        "tradingagents.dataflows.akshare_china._dep_bootstrap.ensure",
        ensure_mock,
    ):
        # Must NOT raise
        result = _vendor_mod.get_stock_data("600519", "2026/01/05", "2026-01-09")

    # Date guard fires before akshare is loaded — ensure must not be called
    ensure_mock.assert_not_called()

    assert isinstance(result, str)
    assert "Error" in result
    assert "2026/01/05" in result
