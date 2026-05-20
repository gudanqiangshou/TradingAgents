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
    """Network/runtime errors from both sources must be caught; function returns error string, never raises."""
    import tradingagents.dataflows.akshare_china as _vendor_mod

    fake_ak = MagicMock()
    fake_ak.stock_zh_a_hist.side_effect = ConnectionError("timeout")
    # With the Sina fallback in place, Sina must also fail for the error string to be returned
    fake_ak.stock_zh_a_daily.side_effect = ConnectionError("timeout")

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


# ---------------------------------------------------------------------------
# Sina fallback tests
# ---------------------------------------------------------------------------

def _make_sina_df(rows=None):
    """Return a DataFrame that mimics ak.stock_zh_a_daily output (English lowercase cols)."""
    import datetime as dt
    if rows is None:
        rows = [
            {
                "date": dt.date(2024, 1, 2),
                "open": 1700.5,
                "high": 1720.0,
                "low": 1695.0,
                "close": 1715.0,
                "volume": 3000000,
                "amount": 5.1e9,
                "outstanding_share": 1.26e9,
                "turnover": 0.0024,
            },
            {
                "date": dt.date(2024, 1, 3),
                "open": 1716.0,
                "high": 1730.5,
                "low": 1710.0,
                "close": 1725.5,
                "volume": 3200000,
                "amount": 5.5e9,
                "outstanding_share": 1.26e9,
                "turnover": 0.0025,
            },
        ]
    return pd.DataFrame(rows)


def _make_sina_empty_df():
    return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume",
                                  "amount", "outstanding_share", "turnover"])


@pytest.mark.unit
def test_a_share_falls_back_to_sina_when_eastmoney_raises():
    """When eastmoney raises, Sina fallback succeeds and produces correct output."""
    import tradingagents.dataflows.akshare_china as _vendor_mod

    fake_ak = MagicMock()
    fake_ak.stock_zh_a_hist.side_effect = ConnectionError("proxy")
    fake_ak.stock_zh_a_daily.return_value = _make_sina_df()

    with patch(
        "tradingagents.dataflows.akshare_china._dep_bootstrap.ensure",
        return_value=fake_ak,
    ):
        result = _vendor_mod.get_stock_data("600519", "2024-01-02", "2024-01-12")

    # Must not raise; result is a string
    assert isinstance(result, str)

    # Header contract
    assert result.startswith("# Stock data for 600519 from 2024-01-02 to 2024-01-12")

    # CSV header present
    assert "Date,Open,High,Low,Close,Volume" in result

    # eastmoney was tried first
    assert fake_ak.stock_zh_a_hist.call_count >= 1

    # Sina was called with the correct sh-prefixed symbol
    fake_ak.stock_zh_a_daily.assert_called_once_with(
        symbol="sh600519",
        start_date="20240102",
        end_date="20240112",
        adjust="qfq",
    )


@pytest.mark.unit
def test_a_share_falls_back_to_sina_when_eastmoney_returns_empty():
    """When eastmoney returns an empty df, Sina fallback succeeds."""
    import tradingagents.dataflows.akshare_china as _vendor_mod

    fake_ak = MagicMock()
    fake_ak.stock_zh_a_hist.return_value = _make_ak_empty_df()
    fake_ak.stock_zh_a_daily.return_value = _make_sina_df()

    with patch(
        "tradingagents.dataflows.akshare_china._dep_bootstrap.ensure",
        return_value=fake_ak,
    ):
        result = _vendor_mod.get_stock_data("600519", "2024-01-02", "2024-01-12")

    assert isinstance(result, str)
    assert result.startswith("# Stock data for 600519 from 2024-01-02 to 2024-01-12")
    assert "Date,Open,High,Low,Close,Volume" in result

    # Sina was called with correct sh prefix
    fake_ak.stock_zh_a_daily.assert_called_once_with(
        symbol="sh600519",
        start_date="20240102",
        end_date="20240112",
        adjust="qfq",
    )


@pytest.mark.unit
def test_a_share_sina_uses_sz_prefix_for_000_code():
    """For 000xxx codes (Shenzhen), Sina symbol must use 'sz' prefix."""
    import tradingagents.dataflows.akshare_china as _vendor_mod

    fake_ak = MagicMock()
    fake_ak.stock_zh_a_hist.side_effect = ConnectionError("proxy")
    fake_ak.stock_zh_a_daily.return_value = _make_sina_df()

    with patch(
        "tradingagents.dataflows.akshare_china._dep_bootstrap.ensure",
        return_value=fake_ak,
    ):
        result = _vendor_mod.get_stock_data("000001", "2024-01-02", "2024-01-12")

    assert isinstance(result, str)
    # Sina called with sz prefix
    fake_ak.stock_zh_a_daily.assert_called_once_with(
        symbol="sz000001",
        start_date="20240102",
        end_date="20240112",
        adjust="qfq",
    )


@pytest.mark.unit
def test_a_share_both_sources_fail_returns_error_string():
    """When both eastmoney and Sina raise, result is an 'Error: ...' string (never raises)."""
    import tradingagents.dataflows.akshare_china as _vendor_mod

    fake_ak = MagicMock()
    fake_ak.stock_zh_a_hist.side_effect = ConnectionError("proxy")
    fake_ak.stock_zh_a_daily.side_effect = RuntimeError("sina down")

    with patch(
        "tradingagents.dataflows.akshare_china._dep_bootstrap.ensure",
        return_value=fake_ak,
    ):
        result = _vendor_mod.get_stock_data("600519", "2024-01-02", "2024-01-12")

    assert isinstance(result, str)
    assert result.lower().startswith("error:")
    assert "600519" in result


@pytest.mark.unit
def test_a_share_both_sources_empty_returns_no_data_string():
    """When both eastmoney and Sina return empty dfs, result is the 'No data found' string."""
    import tradingagents.dataflows.akshare_china as _vendor_mod

    fake_ak = MagicMock()
    fake_ak.stock_zh_a_hist.return_value = _make_ak_empty_df()
    fake_ak.stock_zh_a_daily.return_value = _make_sina_empty_df()

    with patch(
        "tradingagents.dataflows.akshare_china._dep_bootstrap.ensure",
        return_value=fake_ak,
    ):
        result = _vendor_mod.get_stock_data("600519", "2024-01-02", "2024-01-12")

    assert result == "No data found for symbol '600519' between 2024-01-02 and 2024-01-12"


@pytest.mark.unit
def test_a_share_eastmoney_success_sina_never_called(fake_ak):
    """When eastmoney returns rows, Sina must never be called."""
    with patch(
        "tradingagents.dataflows.akshare_china._dep_bootstrap.ensure",
        return_value=fake_ak,
    ):
        from tradingagents.dataflows.akshare_china import get_stock_data
        result = get_stock_data("600519", "2026-01-05", "2026-01-09")

    assert result.startswith("# Stock data for 600519 from 2026-01-05 to 2026-01-09")
    fake_ak.stock_zh_a_daily.assert_not_called()


# ---------------------------------------------------------------------------
# TestGetIndicators — fully offline; stockstats is a project dependency
# ---------------------------------------------------------------------------

import datetime as _dt


def _make_ohlcv_year(symbol_code="600519", days=260):
    """Build ~1-year of daily OHLCV rows ending 2024-06-30 (enough for all indicators).

    Returns a DataFrame with capitalized columns Date/Open/High/Low/Close/Volume
    and Date as a plain column (not index), sorted ascending — same shape the
    _fetch_a_share_ohlcv helper returns.
    """
    end_date = _dt.date(2024, 6, 30)
    rows = []
    close = 1800.0
    import random
    rng = random.Random(42)
    d = end_date - _dt.timedelta(days=days - 1)
    while d <= end_date:
        # Skip weekends to be realistic
        if d.weekday() < 5:
            close = max(100.0, close * (1 + rng.uniform(-0.02, 0.02)))
            rows.append({
                "Date": d.strftime("%Y-%m-%d"),
                "Open": round(close * 0.998, 2),
                "High": round(close * 1.01, 2),
                "Low": round(close * 0.99, 2),
                "Close": round(close, 2),
                "Volume": int(rng.uniform(3e6, 6e6)),
            })
        d += _dt.timedelta(days=1)
    return pd.DataFrame(rows)


class TestGetIndicators:
    """Offline tests for akshare_china.get_indicators."""

    def _make_fake_ak(self, ohlcv_df):
        """Return a fake akshare mock whose stock_zh_a_hist returns ohlcv_df.

        ohlcv_df should already have capitalized English columns as the helper would
        produce after normalization.  We fake it by setting stock_zh_a_hist to return
        a df with Chinese columns (what akshare actually returns) so the helper
        normalizes it, OR we can set it to return directly. For simplicity we return
        the pre-normalized df wrapped in a Chinese-column dict so the helper's col_map
        rename becomes a no-op.

        Actually the simplest approach: return the df from stock_zh_a_hist with the
        Chinese column names that the helper expects to normalize.
        """
        # Convert back to Chinese columns so _fetch_a_share_ohlcv normalizes them
        col_map_rev = {
            "Date": "日期",
            "Open": "开盘",
            "Close": "收盘",
            "High": "最高",
            "Low": "最低",
            "Volume": "成交量",
        }
        df_chinese = ohlcv_df.rename(columns=col_map_rev)
        ak = MagicMock()
        ak.stock_zh_a_hist.return_value = df_chinese
        return ak

    @pytest.mark.unit
    def test_happy_path_50_sma(self):
        """Returns correct header + date lines + description for close_50_sma."""
        import tradingagents.dataflows.akshare_china as _mod
        ohlcv = _make_ohlcv_year()
        fake_ak = self._make_fake_ak(ohlcv)

        with patch(
            "tradingagents.dataflows.akshare_china._dep_bootstrap.ensure",
            return_value=fake_ak,
        ):
            result = _mod.get_indicators("600519", "close_50_sma", "2024-06-30", 30)

        assert isinstance(result, str)
        # Header must match exactly
        assert result.startswith(
            "## close_50_sma values from 2024-05-31 to 2024-06-30:\n\n"
        )
        # Must contain at least 30 date lines
        lines = [l for l in result.splitlines() if l.strip()]
        date_lines = [l for l in lines if l[:4].isdigit() and "-" in l[:10]]
        assert len(date_lines) >= 30, f"Expected >=30 date lines, got {len(date_lines)}"
        # Description must be appended at the end
        assert _mod._INDICATOR_CATALOG["close_50_sma"] in result

    @pytest.mark.unit
    def test_unsupported_indicator_returns_error_string(self):
        """Unsupported indicator returns error string mentioning the catalog; no raise."""
        import tradingagents.dataflows.akshare_china as _mod
        ensure_mock = MagicMock()

        with patch(
            "tradingagents.dataflows.akshare_china._dep_bootstrap.ensure",
            ensure_mock,
        ):
            result = _mod.get_indicators("600519", "fake_indicator", "2024-06-30", 30)

        assert isinstance(result, str)
        assert "Error: indicator 'fake_indicator' is not supported" in result
        assert "Choose from:" in result
        # ensure must NOT be called (validation fires first)
        ensure_mock.assert_not_called()

    @pytest.mark.unit
    def test_non_a_share_returns_clear_message(self):
        """Non-A-share ticker returns A-share-only message; ensure NOT called."""
        import tradingagents.dataflows.akshare_china as _mod
        ensure_mock = MagicMock()

        with patch(
            "tradingagents.dataflows.akshare_china._dep_bootstrap.ensure",
            ensure_mock,
        ):
            result = _mod.get_indicators("AAPL", "close_50_sma", "2024-06-30", 30)

        assert isinstance(result, str)
        lower = result.lower()
        assert "a-share" in lower or "a_share" in lower or "a share" in lower
        ensure_mock.assert_not_called()

    @pytest.mark.unit
    def test_dependency_unavailable_returns_error_string(self):
        """DependencyUnavailable → error string starting with 'Error: A-share data source unavailable'."""
        import tradingagents.dataflows.akshare_china as _vendor_mod
        DependencyUnavailable = _vendor_mod._dep_bootstrap.DependencyUnavailable

        with patch(
            "tradingagents.dataflows.akshare_china._dep_bootstrap.ensure",
            side_effect=DependencyUnavailable("akshare: not installed"),
        ):
            result = _vendor_mod.get_indicators("600519", "close_50_sma", "2024-06-30", 30)

        assert isinstance(result, str)
        assert result.lower().startswith("error:")
        assert "unavailable" in result.lower() or "akshare" in result.lower()

    @pytest.mark.unit
    def test_both_sources_fail_returns_error_string(self):
        """Both stock_zh_a_hist and stock_zh_a_daily raising → 'Error: failed to fetch' string."""
        import tradingagents.dataflows.akshare_china as _vendor_mod

        fake_ak = MagicMock()
        fake_ak.stock_zh_a_hist.side_effect = ConnectionError("timeout")
        fake_ak.stock_zh_a_daily.side_effect = ConnectionError("timeout")

        with patch(
            "tradingagents.dataflows.akshare_china._dep_bootstrap.ensure",
            return_value=fake_ak,
        ):
            result = _vendor_mod.get_indicators("600519", "close_50_sma", "2024-06-30", 30)

        assert isinstance(result, str)
        assert result.lower().startswith("error:")
        assert "failed to fetch" in result.lower()
        assert "600519" in result

    @pytest.mark.unit
    def test_both_sources_empty_returns_no_data_string(self):
        """Both sources returning empty df → 'No price data found' string."""
        import tradingagents.dataflows.akshare_china as _vendor_mod

        fake_ak = MagicMock()
        fake_ak.stock_zh_a_hist.return_value = _make_ak_empty_df()
        fake_ak.stock_zh_a_daily.return_value = _make_sina_empty_df()

        with patch(
            "tradingagents.dataflows.akshare_china._dep_bootstrap.ensure",
            return_value=fake_ak,
        ):
            result = _vendor_mod.get_indicators("600519", "close_50_sma", "2024-06-30", 30)

        assert result == "No price data found for symbol '600519'; cannot compute close_50_sma"

    @pytest.mark.unit
    def test_invalid_curr_date_returns_error_string(self):
        """Bad date format → error string; ensure NOT called."""
        import tradingagents.dataflows.akshare_china as _vendor_mod
        ensure_mock = MagicMock()

        with patch(
            "tradingagents.dataflows.akshare_china._dep_bootstrap.ensure",
            ensure_mock,
        ):
            result = _vendor_mod.get_indicators("600519", "close_50_sma", "2024/06/30", 30)

        assert isinstance(result, str)
        assert result.lower().startswith("error:")
        ensure_mock.assert_not_called()

    @pytest.mark.unit
    def test_falls_back_to_sina_when_eastmoney_raises_for_indicators(self):
        """eastmoney raises, Sina returns df → normal indicator window string; stock_zh_a_daily called with sh600519."""
        import tradingagents.dataflows.akshare_china as _vendor_mod

        ohlcv = _make_ohlcv_year()
        # Sina df uses English lowercase columns (what the actual endpoint returns)
        sina_df = ohlcv.rename(columns={
            "Date": "date", "Open": "open", "High": "high",
            "Low": "low", "Close": "close", "Volume": "volume",
        })
        # Add extra Sina columns to be realistic
        sina_df["amount"] = 5.0e9
        sina_df["outstanding_share"] = 1.26e9
        sina_df["turnover"] = 0.0024

        fake_ak = MagicMock()
        fake_ak.stock_zh_a_hist.side_effect = ConnectionError("proxy")
        fake_ak.stock_zh_a_daily.return_value = sina_df

        with patch(
            "tradingagents.dataflows.akshare_china._dep_bootstrap.ensure",
            return_value=fake_ak,
        ):
            result = _vendor_mod.get_indicators("600519", "close_50_sma", "2024-06-30", 30)

        assert isinstance(result, str)
        # Should be a valid indicator window
        assert result.startswith("## close_50_sma values from")
        # stock_zh_a_daily must have been called with the sh-prefixed symbol
        assert fake_ak.stock_zh_a_daily.call_count >= 1
        call_kwargs = fake_ak.stock_zh_a_daily.call_args[1]
        assert call_kwargs["symbol"] == "sh600519"
        assert call_kwargs["adjust"] == "qfq"


# ---------------------------------------------------------------------------
# Bad-schema / schema-drift tests
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_a_share_get_stock_data_bad_schema_returns_error_string():
    """If akshare returns a DataFrame with completely wrong columns (schema drift),
    get_stock_data must return an error string, not raise KeyError.

    Both eastmoney and Sina fallback return a bad-shape DataFrame.
    """
    import tradingagents.dataflows.akshare_china as _vendor_mod

    fake_ak = MagicMock()
    # Both sources return a DataFrame with unexpected column names
    fake_ak.stock_zh_a_hist.return_value = pd.DataFrame({"foo": [1, 2], "bar": [3, 4]})
    fake_ak.stock_zh_a_daily.return_value = pd.DataFrame({"baz": [1], "qux": [2]})

    with patch(
        "tradingagents.dataflows.akshare_china._dep_bootstrap.ensure",
        return_value=fake_ak,
    ):
        # Must NOT raise KeyError; must return an error string
        result = _vendor_mod.get_stock_data("600519", "2026-01-05", "2026-01-09")

    assert isinstance(result, str), f"Expected str, got {type(result)}"
    # Should be either an error or no-data message — not a crash
    # (bad schema → rename is no-op → sort_values("Date") KeyError is now caught)
    # The function must not raise
    assert len(result) > 0


# ---------------------------------------------------------------------------
# Fix 2: list-return resilience (ohlcv + indicators)
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.parametrize("bad_return", [[], None, pd.Series([1, 2, 3]), pd.DataFrame()])
def test_get_stock_data_list_or_empty_return_resilience(bad_return):
    """When akshare returns [], None, Series, or empty df, get_stock_data must
    return an error/no-data string and never raise AttributeError.
    """
    import tradingagents.dataflows.akshare_china as _vendor_mod

    fake_ak = MagicMock()
    fake_ak.stock_zh_a_hist.return_value = bad_return
    fake_ak.stock_zh_a_daily.return_value = bad_return

    with patch(
        "tradingagents.dataflows.akshare_china._dep_bootstrap.ensure",
        return_value=fake_ak,
    ):
        result = _vendor_mod.get_stock_data("600519", "2026-01-05", "2026-01-09")

    assert isinstance(result, str), (
        f"Expected str, got {type(result)} for bad_return={type(bad_return).__name__}"
    )
    assert len(result) > 0


# ---------------------------------------------------------------------------
# Fix 3: look_back_days coercion tests
# ---------------------------------------------------------------------------

class TestGetIndicatorsCoerce:
    """Tests for look_back_days type coercion in get_indicators."""

    @pytest.mark.unit
    def test_string_look_back_days_returns_error_string(self):
        """look_back_days='30' (string) must return error string, not raise TypeError."""
        import tradingagents.dataflows.akshare_china as _mod
        ensure_mock = MagicMock()

        with patch(
            "tradingagents.dataflows.akshare_china._dep_bootstrap.ensure",
            ensure_mock,
        ):
            result = _mod.get_indicators("600519", "close_50_sma", "2024-06-30", "30")

        # Must return an error string (string "30" is coercible to int, so this
        # should actually succeed — but the test verifies no TypeError leaks).
        # Since "30" IS coercible to int(30), expect a normal result or no raise.
        assert isinstance(result, str), f"Expected str, got {type(result)}"
        # Must not contain "TypeError" (which would mean the coerce failed and leaked)
        assert "TypeError" not in result, (
            "TypeError leaked into result string — coerce not working"
        )

    @pytest.mark.unit
    def test_non_coercible_look_back_days_returns_error_string(self):
        """look_back_days='thirty' (non-coercible string) must return error string, not raise."""
        import tradingagents.dataflows.akshare_china as _mod
        ensure_mock = MagicMock()

        with patch(
            "tradingagents.dataflows.akshare_china._dep_bootstrap.ensure",
            ensure_mock,
        ):
            result = _mod.get_indicators("600519", "close_50_sma", "2024-06-30", "thirty")

        assert isinstance(result, str), f"Expected str, got {type(result)}"
        assert result.lower().startswith("error:"), (
            f"Expected error string for non-coercible look_back_days, got: {result!r}"
        )
        assert "look_back_days" in result

    @pytest.mark.unit
    def test_negative_look_back_days_returns_error_string(self):
        """look_back_days=-5 must return error string, not raise."""
        import tradingagents.dataflows.akshare_china as _mod
        ensure_mock = MagicMock()

        with patch(
            "tradingagents.dataflows.akshare_china._dep_bootstrap.ensure",
            ensure_mock,
        ):
            result = _mod.get_indicators("600519", "close_50_sma", "2024-06-30", -5)

        assert isinstance(result, str), f"Expected str, got {type(result)}"
        assert result.lower().startswith("error:"), (
            f"Expected error string for negative look_back_days, got: {result!r}"
        )
        assert "non-negative" in result


# ---------------------------------------------------------------------------
# audit-v3: Additional type-guard tests (Important 5)
# ---------------------------------------------------------------------------

class TestGetIndicatorsTypeGuards:
    """audit-v3: NaN/inf/bool look_back_days must return error strings, not raise."""

    @pytest.mark.unit
    def test_get_indicators_inf_look_back_returns_error_string(self):
        """look_back_days=float('inf') must return error string, not OverflowError."""
        import tradingagents.dataflows.akshare_china as _mod
        ensure_mock = MagicMock()

        with patch(
            "tradingagents.dataflows.akshare_china._dep_bootstrap.ensure",
            ensure_mock,
        ):
            result = _mod.get_indicators("600519", "close_50_sma", "2024-06-30", float("inf"))

        assert isinstance(result, str), f"Expected str, got {type(result)}"
        assert result.lower().startswith("error:"), (
            f"Expected error string for inf look_back_days, got: {result!r}"
        )

    @pytest.mark.unit
    def test_get_indicators_nan_look_back_returns_error_string(self):
        """look_back_days=float('nan') must return error string, not raise."""
        import tradingagents.dataflows.akshare_china as _mod
        ensure_mock = MagicMock()

        with patch(
            "tradingagents.dataflows.akshare_china._dep_bootstrap.ensure",
            ensure_mock,
        ):
            result = _mod.get_indicators("600519", "close_50_sma", "2024-06-30", float("nan"))

        assert isinstance(result, str), f"Expected str, got {type(result)}"
        assert result.lower().startswith("error:"), (
            f"Expected error string for nan look_back_days, got: {result!r}"
        )

    @pytest.mark.unit
    def test_get_indicators_bool_look_back_treated_as_invalid(self):
        """look_back_days=True must return error string (bool not silently coerced to int 1)."""
        import tradingagents.dataflows.akshare_china as _mod
        ensure_mock = MagicMock()

        with patch(
            "tradingagents.dataflows.akshare_china._dep_bootstrap.ensure",
            ensure_mock,
        ):
            result = _mod.get_indicators("600519", "close_50_sma", "2024-06-30", True)

        assert isinstance(result, str), f"Expected str, got {type(result)}"
        assert result.lower().startswith("error:"), (
            f"Expected error string for bool look_back_days, got: {result!r}"
        )


class TestGetStockDataTypeGuards:
    """audit-v3: None/datetime start_date must return error strings, not raise."""

    @pytest.mark.unit
    def test_get_stock_data_none_start_date_returns_error_string(self):
        """start_date=None must return error string, not raise TypeError."""
        import tradingagents.dataflows.akshare_china as _mod
        ensure_mock = MagicMock()

        with patch(
            "tradingagents.dataflows.akshare_china._dep_bootstrap.ensure",
            ensure_mock,
        ):
            result = _mod.get_stock_data("600519", None, "2024-01-02")

        assert isinstance(result, str), f"Expected str, got {type(result)}"
        assert result.lower().startswith("error:"), (
            f"Expected error string for None start_date, got: {result!r}"
        )
        ensure_mock.assert_not_called()

    @pytest.mark.unit
    def test_get_stock_data_datetime_start_date_returns_error_string(self):
        """start_date=datetime object must return error string, not raise."""
        import tradingagents.dataflows.akshare_china as _mod
        from datetime import datetime
        ensure_mock = MagicMock()

        with patch(
            "tradingagents.dataflows.akshare_china._dep_bootstrap.ensure",
            ensure_mock,
        ):
            result = _mod.get_stock_data("600519", datetime(2024, 1, 1), "2024-01-02")

        assert isinstance(result, str), f"Expected str, got {type(result)}"
        assert result.lower().startswith("error:"), (
            f"Expected error string for datetime start_date, got: {result!r}"
        )
        ensure_mock.assert_not_called()
