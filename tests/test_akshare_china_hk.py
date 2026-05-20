"""
Tests for Phase 5: HK market support in the AkShare vendor.

Covers: get_stock_data (HK branch), get_fundamentals (HK branch).
Fully offline — mocks _dep_bootstrap.ensure so that akshare is never imported.
All tests are marked @pytest.mark.unit.
"""

from __future__ import annotations

import numpy as np
import pytest
import pandas as pd
from unittest.mock import MagicMock, patch

import tradingagents.dataflows.akshare_china as _vendor_mod

DependencyUnavailable = _vendor_mod._dep_bootstrap.DependencyUnavailable


# ---------------------------------------------------------------------------
# Helpers — build fake DataFrames that mimic akshare HK endpoint shapes
# ---------------------------------------------------------------------------

def _make_hk_daily_df(rows=None):
    """Mimic ak.stock_hk_daily(symbol=..., adjust="qfq") output.

    English columns: date, open, high, low, close, volume.
    Intentionally unsorted, floats needing rounding.
    """
    if rows is None:
        rows = [
            {"date": "2024-01-09", "open": 345.123, "high": 352.789, "low": 342.111, "close": 350.456, "volume": 12000000},
            {"date": "2024-01-05", "open": 338.001, "high": 341.555, "low": 335.333, "close": 340.999, "volume": 9500000},
            {"date": "2024-01-07", "open": 341.222, "high": 348.777, "low": 339.444, "close": 347.888, "volume": 11000000},
        ]
    return pd.DataFrame(rows)


def _make_hk_daily_df_wide(n_rows=5):
    """Return a wider fake with rows spanning 2024-01-01 to 2024-01-31."""
    rows = [
        {"date": "2024-01-02", "open": 330.0, "high": 335.0, "low": 328.0, "close": 333.0, "volume": 8000000},
        {"date": "2024-01-03", "open": 333.5, "high": 337.0, "low": 331.0, "close": 336.0, "volume": 8200000},
        {"date": "2024-01-05", "open": 336.5, "high": 341.0, "low": 334.0, "close": 340.0, "volume": 9000000},
        {"date": "2024-01-08", "open": 340.5, "high": 345.0, "low": 338.0, "close": 344.0, "volume": 9800000},
        {"date": "2024-01-15", "open": 344.5, "high": 349.0, "low": 342.0, "close": 348.0, "volume": 10200000},
    ]
    return pd.DataFrame(rows)


def _make_hk_fundamentals_df():
    """Mimic ak.stock_financial_hk_analysis_indicator_em(symbol=...) output.

    Wide-form DataFrame, UPPERCASE column names, latest period first (iloc[0]).
    """
    return pd.DataFrame({
        "REPORT_DATE": ["2024-06-30", "2023-12-31"],
        "BASIC_EPS": [12.3, 11.8],
        "EPS_TTM": [13.1, 12.5],
        "BPS": [85.4, 82.1],
        "ROE_AVG": [np.nan, 15.2],       # NaN → should be skipped
        "ROA": [None, 8.5],              # None → should be skipped
        "GROSS_PROFIT_RATIO": [0.45, 0.44],
        "NET_PROFIT_RATIO": [0.32, 0.31],
        "DEBT_ASSET_RATIO": [0.28, 0.27],
    })


# ---------------------------------------------------------------------------
# Tests: get_stock_data — HK
# ---------------------------------------------------------------------------

class TestGetStockDataHK:

    @pytest.mark.unit
    def test_happy_path_header_and_csv_body(self):
        """Happy path: header matches contract; CSV has correct columns; dates ascending."""
        ak = MagicMock()
        ak.stock_hk_daily.return_value = _make_hk_daily_df()

        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            result = _vendor_mod.get_stock_data("00700.HK", "2024-01-01", "2024-01-10")

        # Exact header line 1: display symbol is the original ticker upper-cased
        assert result.startswith("# Stock data for 00700.HK from 2024-01-01 to 2024-01-10")
        # Header line 2
        assert "# Total records: 3" in result
        # Header line 3
        assert "# Data retrieved on:" in result

        # Blank line separator before CSV
        parts = result.split("\n\n", 1)
        assert len(parts) == 2, "Expected blank line between header and CSV"
        csv_part = parts[1]

        # CSV header row
        lines = [l for l in csv_part.splitlines() if l]
        assert lines[0] == "Date,Open,High,Low,Close,Volume"

        # Dates must be in ascending order
        date_values = [l.split(",")[0] for l in lines[1:]]
        assert date_values == sorted(date_values), "Dates are not in ascending order"

        # OHLC values rounded to 2dp (spot-check first data row — sorted → 2024-01-05)
        first_row = lines[1].split(",")
        for col_idx in [1, 2, 3, 4]:  # Open, High, Low, Close
            val = first_row[col_idx]
            assert "." in val
            decimal_places = len(val.split(".")[1])
            assert decimal_places <= 2, f"Column index {col_idx} has >2 decimal places: {val}"

    @pytest.mark.unit
    @pytest.mark.parametrize("ticker, expected_code", [
        ("0700.HK",  "00700"),
        ("00700.HK", "00700"),
        ("9988.HK",  "09988"),
        ("700.hk",   "00700"),   # lowercase .hk
    ])
    def test_symbol_normalization_5_digit(self, ticker, expected_code):
        """HK tickers are normalized to 5-digit zero-padded code passed to akshare."""
        ak = MagicMock()
        ak.stock_hk_daily.return_value = _make_hk_daily_df()

        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            _vendor_mod.get_stock_data(ticker, "2024-01-01", "2024-01-10")

        call_kwargs = ak.stock_hk_daily.call_args[1]
        assert call_kwargs["symbol"] == expected_code, (
            f"Expected symbol={expected_code!r} for ticker {ticker!r}, "
            f"got {call_kwargs['symbol']!r}"
        )

    @pytest.mark.unit
    def test_no_start_end_date_passed_to_akshare(self):
        """HK endpoint must NOT receive start_date/end_date kwargs."""
        ak = MagicMock()
        ak.stock_hk_daily.return_value = _make_hk_daily_df()

        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            _vendor_mod.get_stock_data("00700.HK", "2024-01-01", "2024-01-10")

        call_kwargs = ak.stock_hk_daily.call_args[1]
        assert "start_date" not in call_kwargs, "HK endpoint must not receive start_date"
        assert "end_date" not in call_kwargs, "HK endpoint must not receive end_date"

    @pytest.mark.unit
    def test_client_side_date_filter(self):
        """Client-side filter: only rows within start_date..end_date appear in output."""
        ak = MagicMock()
        # 5-row DataFrame spanning ~Jan 2024; call with a narrow 1-week window
        ak.stock_hk_daily.return_value = _make_hk_daily_df_wide()

        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            result = _vendor_mod.get_stock_data("00700.HK", "2024-01-05", "2024-01-08")

        # Only rows 2024-01-05 and 2024-01-08 fall within the window
        assert "2024-01-02" not in result
        assert "2024-01-03" not in result
        assert "2024-01-05" in result
        assert "2024-01-08" in result
        assert "2024-01-15" not in result
        # Record count in header must be 2
        assert "# Total records: 2" in result

    @pytest.mark.unit
    def test_empty_df_returns_no_data_message(self):
        """Empty DataFrame from akshare → exact no-data string with original ticker."""
        ak = MagicMock()
        ak.stock_hk_daily.return_value = pd.DataFrame(
            columns=["date", "open", "high", "low", "close", "volume"]
        )

        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            result = _vendor_mod.get_stock_data("0700.HK", "2024-01-01", "2024-01-10")

        assert result == "No data found for symbol '0700.HK' between 2024-01-01 and 2024-01-10"

    @pytest.mark.unit
    def test_none_df_returns_no_data_message(self):
        """None return from akshare → no-data string with original ticker."""
        ak = MagicMock()
        ak.stock_hk_daily.return_value = None

        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            result = _vendor_mod.get_stock_data("9988.HK", "2024-01-01", "2024-01-10")

        assert result == "No data found for symbol '9988.HK' between 2024-01-01 and 2024-01-10"

    @pytest.mark.unit
    def test_client_side_filter_produces_empty_result(self):
        """Client-side filter removes all rows → no-data string with original ticker."""
        ak = MagicMock()
        # All rows are outside the requested range
        ak.stock_hk_daily.return_value = pd.DataFrame([
            {"date": "2024-03-01", "open": 350.0, "high": 355.0, "low": 348.0, "close": 353.0, "volume": 10000000},
        ])

        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            result = _vendor_mod.get_stock_data("00700.HK", "2024-01-01", "2024-01-10")

        assert result == "No data found for symbol '00700.HK' between 2024-01-01 and 2024-01-10"

    @pytest.mark.unit
    def test_dependency_unavailable_returns_error_string_no_raise(self):
        """DependencyUnavailable → error string with 'Error:' prefix; never raises."""
        with patch(
            "tradingagents.dataflows.akshare_china._dep_bootstrap.ensure",
            side_effect=DependencyUnavailable("akshare: pip install failed"),
        ):
            result = _vendor_mod.get_stock_data("00700.HK", "2024-01-01", "2024-01-10")

        assert isinstance(result, str)
        assert result.startswith("Error:")
        assert "unavailable" in result.lower() or "akshare" in result.lower()

    @pytest.mark.unit
    def test_endpoint_exception_returns_error_string_no_raise(self):
        """Arbitrary endpoint exception → error string; never raises."""
        ak = MagicMock()
        ak.stock_hk_daily.side_effect = ConnectionError("timeout")

        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            result = _vendor_mod.get_stock_data("00700.HK", "2024-01-01", "2024-01-10")

        assert isinstance(result, str)
        assert result.startswith("Error:")
        assert "00700.HK" in result

    @pytest.mark.unit
    def test_shaping_exception_returns_error_string_no_raise(self):
        """Missing expected English columns → shaping error string; never raises."""
        ak = MagicMock()
        # DataFrame with wrong column names — shaping will fail to find 'date' etc.
        ak.stock_hk_daily.return_value = pd.DataFrame([
            {"wrong_col": "2024-01-05", "price": 340.0},
        ])

        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            result = _vendor_mod.get_stock_data("00700.HK", "2024-01-01", "2024-01-10")

        assert isinstance(result, str)
        assert result.startswith("Error:")

    @pytest.mark.unit
    def test_non_hk_non_ashare_symbol_returns_vendor_message_no_ensure(self):
        """Non-A-share, non-HK symbol (e.g. 'AAPL') → message; ensure NOT called."""
        ensure_mock = MagicMock()

        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", ensure_mock):
            result = _vendor_mod.get_stock_data("AAPL", "2024-01-01", "2024-01-10")

        ensure_mock.assert_not_called()
        assert isinstance(result, str)
        lower = result.lower()
        assert "a-share" in lower or "hk" in lower

    @pytest.mark.unit
    def test_hk_ticker_header_uses_original_ticker_upper(self):
        """Header must use original ticker upper-cased (not just the 5-digit code)."""
        ak = MagicMock()
        ak.stock_hk_daily.return_value = _make_hk_daily_df()

        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            result = _vendor_mod.get_stock_data("0700.HK", "2024-01-01", "2024-01-10")

        # Original ticker uppercased is "0700.HK"
        assert "# Stock data for 0700.HK from" in result
        # Must NOT use the 5-digit code in the header
        assert "# Stock data for 00700 from" not in result

    @pytest.mark.unit
    def test_invalid_date_returns_error_string_ensure_not_called(self):
        """Malformed date → error string; ensure must NOT be called."""
        ensure_mock = MagicMock()

        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", ensure_mock):
            result = _vendor_mod.get_stock_data("00700.HK", "2024/01/01", "2024-01-10")

        ensure_mock.assert_not_called()
        assert isinstance(result, str)
        assert result.startswith("Error:")
        assert "2024/01/01" in result


# ---------------------------------------------------------------------------
# Tests: get_fundamentals — HK
# ---------------------------------------------------------------------------

class TestGetFundamentalsHK:

    @pytest.mark.unit
    def test_happy_path_header_and_body(self):
        """Happy path: header matches contract; body has UPPERCASE_FIELD: value lines;
        NaN/None fields are absent."""
        ak = MagicMock()
        ak.stock_financial_hk_analysis_indicator_em.return_value = _make_hk_fundamentals_df()

        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            result = _vendor_mod.get_fundamentals("00700.HK")

        # Header
        assert result.startswith("# Company Fundamentals for 00700.HK")
        assert "# Data retrieved on:" in result
        assert "\n\n" in result

        body = result.split("\n\n", 1)[1]
        lines = [l for l in body.splitlines() if l.strip()]
        assert len(lines) > 0

        # Each line must be label: value
        for line in lines:
            assert ": " in line, f"Expected 'FIELD: value' format, got: {line!r}"

        # Specific expected values from iloc[0] (2024-06-30 row)
        assert "BASIC_EPS: 12.3" in body
        assert "EPS_TTM: 13.1" in body
        assert "BPS: 85.4" in body
        assert "GROSS_PROFIT_RATIO: 0.45" in body

        # NaN and None fields must NOT appear
        assert "ROE_AVG" not in body, "NaN field ROE_AVG should be skipped"
        assert "ROA" not in body, "None field ROA should be skipped"

    @pytest.mark.unit
    @pytest.mark.parametrize("ticker, expected_code", [
        ("0700.HK",  "00700"),
        ("00700.HK", "00700"),
        ("9988.HK",  "09988"),
        ("700.hk",   "00700"),
    ])
    def test_symbol_normalization_5_digit(self, ticker, expected_code):
        """HK tickers normalized to 5-digit code passed to akshare."""
        ak = MagicMock()
        ak.stock_financial_hk_analysis_indicator_em.return_value = _make_hk_fundamentals_df()

        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            _vendor_mod.get_fundamentals(ticker)

        call_kwargs = ak.stock_financial_hk_analysis_indicator_em.call_args[1]
        assert call_kwargs["symbol"] == expected_code, (
            f"Expected symbol={expected_code!r} for ticker {ticker!r}, "
            f"got {call_kwargs['symbol']!r}"
        )

    @pytest.mark.unit
    def test_empty_df_returns_no_fundamentals_message(self):
        """Empty DataFrame → exact no-fundamentals string with original ticker."""
        ak = MagicMock()
        ak.stock_financial_hk_analysis_indicator_em.return_value = pd.DataFrame()

        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            result = _vendor_mod.get_fundamentals("00700.HK")

        assert result == "No fundamentals data found for symbol '00700.HK'"

    @pytest.mark.unit
    def test_none_df_returns_no_fundamentals_message(self):
        """None return → no-fundamentals string with original ticker."""
        ak = MagicMock()
        ak.stock_financial_hk_analysis_indicator_em.return_value = None

        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            result = _vendor_mod.get_fundamentals("9988.HK")

        assert result == "No fundamentals data found for symbol '9988.HK'"

    @pytest.mark.unit
    def test_dependency_unavailable_returns_error_string_no_raise(self):
        """DependencyUnavailable → error string; never raises."""
        with patch(
            "tradingagents.dataflows.akshare_china._dep_bootstrap.ensure",
            side_effect=DependencyUnavailable("akshare: pip install failed"),
        ):
            result = _vendor_mod.get_fundamentals("00700.HK")

        assert isinstance(result, str)
        assert "Error:" in result
        assert "unavailable" in result.lower() or "akshare" in result.lower()

    @pytest.mark.unit
    def test_endpoint_exception_returns_error_string_no_raise(self):
        """Arbitrary endpoint exception → error string; never raises."""
        ak = MagicMock()
        ak.stock_financial_hk_analysis_indicator_em.side_effect = ConnectionError("net")

        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            result = _vendor_mod.get_fundamentals("00700.HK")

        assert isinstance(result, str)
        assert "Error:" in result
        assert "00700.HK" in result

    @pytest.mark.unit
    def test_shaping_exception_returns_error_string_no_raise(self):
        """Shaping exception during iloc[0] access → error string; never raises.

        We simulate a DataFrame where iloc raises on access — this triggers
        the shaping try/except block in get_fundamentals.
        """
        ak = MagicMock()

        class _BadIloc:
            def __getitem__(self, idx):
                raise KeyError("iloc access failed — simulated shaping error")

        bad_df = MagicMock(spec=pd.DataFrame)
        bad_df.empty = False
        bad_df.iloc = _BadIloc()
        bad_df.columns = pd.Index(["COL_A"])
        ak.stock_financial_hk_analysis_indicator_em.return_value = bad_df

        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            result = _vendor_mod.get_fundamentals("00700.HK")

        assert isinstance(result, str)
        assert result.startswith("Error:")

    @pytest.mark.unit
    def test_non_hk_non_ashare_symbol_returns_vendor_message_no_ensure(self):
        """Non-A-share, non-HK symbol → message; ensure NOT called."""
        ensure_mock = MagicMock()

        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", ensure_mock):
            result = _vendor_mod.get_fundamentals("AAPL")

        ensure_mock.assert_not_called()
        assert isinstance(result, str)
        lower = result.lower()
        assert "a-share" in lower or "hk" in lower

    @pytest.mark.unit
    def test_nan_and_none_fields_skipped(self):
        """NaN and None values in the wide-form HK row are not emitted."""
        ak = MagicMock()
        ak.stock_financial_hk_analysis_indicator_em.return_value = pd.DataFrame({
            "BASIC_EPS": [12.3],
            "EPS_TTM": [np.nan],      # NaN → skip
            "BPS": [None],             # None → skip
            "ROE_AVG": [15.2],
        })

        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            result = _vendor_mod.get_fundamentals("00700.HK")

        body = result.split("\n\n", 1)[1]
        assert "BASIC_EPS: 12.3" in body
        assert "ROE_AVG: 15.2" in body
        # NaN/None skipped
        assert "EPS_TTM" not in body
        assert "BPS" not in body

    @pytest.mark.unit
    def test_header_uses_ticker_upper(self):
        """Header uses ticker.upper() — lowercase .hk input is uppercased."""
        ak = MagicMock()
        ak.stock_financial_hk_analysis_indicator_em.return_value = pd.DataFrame({
            "BASIC_EPS": [12.3],
        })

        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            result = _vendor_mod.get_fundamentals("00700.hk")

        # ticker.strip().upper() → "00700.HK"
        assert result.startswith("# Company Fundamentals for 00700.HK")
