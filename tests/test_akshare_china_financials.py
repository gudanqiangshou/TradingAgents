"""
Tests for Phase 3: A-share financials in the AkShare vendor.

Covers: get_fundamentals, get_balance_sheet, get_cashflow, get_income_statement.
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
# Helpers — build fake DataFrames that mimic akshare endpoint shapes
# ---------------------------------------------------------------------------

def _make_fundamentals_df():
    """Mimic ak.stock_financial_abstract(symbol=...) output.
    Wide-form: column 0 = 选项 (category), column 1 = 指标 (indicator name),
    columns 2..N = 8-digit period strings (YYYYMMDD) in descending date order.
    """
    return pd.DataFrame({
        "选项":   ["常用指标", "常用指标", "财务指标"],
        "指标":   ["归母净利润", "营业总收入", "ROE"],
        "20260331": [2.724251e+10, 5.470291e+10, 0.32],
        "20251231": [8.232007e+10, 1.720542e+11, 0.30],
        "20250930": [6.462675e+10, 1.309039e+11, 0.29],
    })


def _make_balance_df():
    """Mimic ak.stock_balance_sheet_by_report_em(symbol=...) output."""
    return pd.DataFrame({
        "REPORT_DATE": ["2024-09-30", "2024-06-30"],
        "TOTAL_ASSETS": [350_000_000_000, 340_000_000_000],
        "TOTAL_LIABILITIES": [120_000_000_000, 115_000_000_000],
        "TOTAL_EQUITY": [230_000_000_000, 225_000_000_000],
    })


def _make_cashflow_df():
    """Mimic ak.stock_cash_flow_sheet_by_report_em(symbol=...) output."""
    return pd.DataFrame({
        "REPORT_DATE": ["2024-09-30", "2024-06-30"],
        "NETCASH_OPERATE": [65_000_000_000, 62_000_000_000],
        "NETCASH_INVEST": [-10_000_000_000, -8_000_000_000],
    })


def _make_income_df():
    """Mimic ak.stock_profit_sheet_by_report_em(symbol=...) output."""
    return pd.DataFrame({
        "REPORT_DATE": ["2024-09-30", "2024-06-30"],
        "TOTAL_OPERATE_INCOME": [150_000_000_000, 145_000_000_000],
        "NET_PROFIT": [60_000_000_000, 58_000_000_000],
    })


def _fake_ak_with_all():
    """Return a fake akshare module with all 4 endpoints populated."""
    ak = MagicMock()
    ak.stock_financial_abstract.return_value = _make_fundamentals_df()
    ak.stock_balance_sheet_by_report_em.return_value = _make_balance_df()
    ak.stock_cash_flow_sheet_by_report_em.return_value = _make_cashflow_df()
    ak.stock_profit_sheet_by_report_em.return_value = _make_income_df()
    return ak


# ---------------------------------------------------------------------------
# get_fundamentals
# ---------------------------------------------------------------------------

class TestGetFundamentals:

    @pytest.mark.unit
    def test_happy_path_header_and_body(self):
        """Happy path: header matches exact contract; body has indicator: value lines
        using the REAL akshare schema (选项/指标 + 8-digit period columns).
        """
        ak = _fake_ak_with_all()
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            result = _vendor_mod.get_fundamentals("600519.SH")

        # Header line 1
        assert result.startswith("# Company Fundamentals for 600519.SH")
        # Header line 2
        assert "# Data retrieved on:" in result
        # Header line 3: latest period annotation
        assert "# Latest period: 20260331" in result
        # Blank line separator after header block
        assert "\n\n" in result

        # Body: indicator name paired with latest-period numeric value
        body = result.split("\n\n", 1)[1]
        lines = [l for l in body.splitlines() if l.strip()]
        assert len(lines) > 0
        for line in lines:
            assert ": " in line, f"Expected 'label: value' format, got: {line!r}"

        # Body must contain each indicator with its latest-period (:,.2f) value
        # 2.724251e+10 formatted as :,.2f → "27,242,510,000.00"
        assert "归母净利润: 27,242,510,000.00" in result
        # 5.470291e+10 formatted → "54,702,910,000.00"
        assert "营业总收入: 54,702,910,000.00" in result
        # 0.32 formatted → "0.32"
        assert "ROE: 0.32" in result

        # Category label "常用指标" must NOT appear as a key in body lines
        for line in lines:
            key = line.split(": ", 1)[0]
            assert key != "常用指标", f"Category label appeared as key: {line!r}"

        # No "nan" literal in body
        assert ": nan" not in result
        assert "nan\n" not in result

    @pytest.mark.unit
    def test_symbol_normalization_bare_code_passed_to_akshare(self):
        """Suffix stripped; bare 6-digit code passed as symbol= kwarg to akshare."""
        ak = _fake_ak_with_all()
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            _vendor_mod.get_fundamentals("600519.SH")
        call_kwargs = ak.stock_financial_abstract.call_args[1]
        assert call_kwargs["symbol"] == "600519"

    @pytest.mark.unit
    def test_symbol_normalization_sz_suffix(self):
        ak = _fake_ak_with_all()
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            _vendor_mod.get_fundamentals("000858.SZ")
        call_kwargs = ak.stock_financial_abstract.call_args[1]
        assert call_kwargs["symbol"] == "000858"

    @pytest.mark.unit
    def test_empty_df_returns_exact_no_data_message(self):
        ak = MagicMock()
        ak.stock_financial_abstract.return_value = pd.DataFrame()
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            result = _vendor_mod.get_fundamentals("600519")
        assert result == "No fundamentals data found for symbol '600519'"

    @pytest.mark.unit
    def test_none_df_returns_exact_no_data_message(self):
        ak = MagicMock()
        ak.stock_financial_abstract.return_value = None
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            result = _vendor_mod.get_fundamentals("600519")
        assert result == "No fundamentals data found for symbol '600519'"

    @pytest.mark.unit
    def test_dependency_unavailable_returns_error_string_no_raise(self):
        with patch(
            "tradingagents.dataflows.akshare_china._dep_bootstrap.ensure",
            side_effect=DependencyUnavailable("akshare: pip install failed"),
        ):
            result = _vendor_mod.get_fundamentals("600519")
        assert isinstance(result, str)
        assert "Error:" in result
        assert "unavailable" in result.lower() or "akshare" in result.lower()

    @pytest.mark.unit
    def test_akshare_exception_returns_error_string_no_raise(self):
        ak = MagicMock()
        ak.stock_financial_abstract.side_effect = ConnectionError("net")
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            result = _vendor_mod.get_fundamentals("600519")
        assert isinstance(result, str)
        assert "Error:" in result
        assert "fundamentals" in result.lower() or "600519" in result

    @pytest.mark.unit
    def test_non_a_share_returns_a_share_only_message_no_ensure(self):
        ensure_mock = MagicMock()
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", ensure_mock):
            result = _vendor_mod.get_fundamentals("AAPL")
        ensure_mock.assert_not_called()
        assert isinstance(result, str)
        lower = result.lower()
        assert "a-share" in lower or "a_share" in lower or "a share" in lower

    @pytest.mark.unit
    def test_curr_date_accepted_and_ignored(self):
        """curr_date parameter must be accepted without error."""
        ak = _fake_ak_with_all()
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            result = _vendor_mod.get_fundamentals("600519", curr_date="2024-09-30")
        assert result.startswith("# Company Fundamentals for")

    @pytest.mark.unit
    def test_skips_rows_with_blank_or_nan_values(self):
        """Rows whose value is NaN or None in the latest period are omitted from output."""
        ak = MagicMock()
        ak.stock_financial_abstract.return_value = pd.DataFrame({
            "选项":    ["常用指标", "常用指标", "财务指标", "财务指标", "财务指标"],
            "指标":    ["PE", "净利润", "营业收入", "每股收益", "ROE"],
            "20260331": [12.3, np.nan, None, np.nan, 15.6],
            "20251231": [10.0, 80_000_000_000.0, 150_000_000_000.0, 42.5, 14.0],
        })
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            result = _vendor_mod.get_fundamentals("600519")

        # The latest period is 20260331; real-valued rows at that period must appear.
        assert "PE: 12.30" in result
        assert "ROE: 15.60" in result

        # Blank/NaN rows in the latest period must NOT appear in output.
        assert ": nan" not in result
        assert "nan\n" not in result
        assert ": None" not in result
        # 净利润 and 营业收入 and 每股收益 have NaN/None in latest period → must be absent.
        body = result.split("\n\n", 1)[1]
        body_keys = {line.split(": ", 1)[0] for line in body.splitlines() if ": " in line}
        assert "净利润" not in body_keys
        assert "营业收入" not in body_keys
        assert "每股收益" not in body_keys

    @pytest.mark.unit
    def test_old_schema_uses_xiangmu_fallback(self):
        """When 指标 is absent but 项目 is present, use 项目 as the indicator column."""
        ak = MagicMock()
        ak.stock_financial_abstract.return_value = pd.DataFrame({
            "选项": ["常用指标", "财务指标"],
            "项目": ["营业收入", "净利润"],
            "20260331": [5.0e10, 8.0e9],
            "20251231": [4.5e10, 7.5e9],
        })
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            result = _vendor_mod.get_fundamentals("600519")

        # Should succeed and use 项目 column
        assert result.startswith("# Company Fundamentals for 600519")
        assert "# Latest period: 20260331" in result
        assert "\n\n" in result
        body = result.split("\n\n", 1)[1]
        body_keys = {line.split(": ", 1)[0] for line in body.splitlines() if ": " in line}
        # Indicator names from 项目 column must appear as keys
        assert "营业收入" in body_keys
        assert "净利润" in body_keys
        # Category label must not appear as a key
        assert "常用指标" not in body_keys
        # No nan literals
        assert ": nan" not in result

    @pytest.mark.unit
    def test_no_period_columns_returns_shaping_error_string(self):
        """If no 8-digit period columns are found, the shaping guard returns an error string."""
        ak = MagicMock()
        # DataFrame has 选项 and 指标 but no YYYYMMDD columns — only non-8-digit ones.
        ak.stock_financial_abstract.return_value = pd.DataFrame({
            "选项": ["常用指标", "财务指标"],
            "指标": ["营业收入", "净利润"],
            "2024Q3": [1e10, 2e9],   # NOT 8 digits
            "2024Q2": [0.9e10, 1.8e9],
        })
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            result = _vendor_mod.get_fundamentals("600519")

        # Must return an error string, not raise.
        assert isinstance(result, str)
        assert result.startswith("Error:"), f"Expected 'Error:...' string, got: {result!r}"
        # Must NOT raise — the test itself is the proof (no exception escaped).


# ---------------------------------------------------------------------------
# get_balance_sheet
# ---------------------------------------------------------------------------

class TestGetBalanceSheet:

    @pytest.mark.unit
    def test_happy_path_header_and_csv_body(self):
        ak = _fake_ak_with_all()
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            result = _vendor_mod.get_balance_sheet("600519.SH", freq="quarterly")

        assert result.startswith("# Balance Sheet data for 600519.SH (quarterly)")
        assert "# Data retrieved on:" in result
        assert "\n\n" in result
        # Body is CSV with a header row
        body = result.split("\n\n", 1)[1]
        csv_lines = [l for l in body.splitlines() if l.strip()]
        # First line should be CSV column headers (contains at least one comma)
        assert "," in csv_lines[0]
        # Must have data rows beyond header
        assert len(csv_lines) > 1

    @pytest.mark.unit
    def test_symbol_normalization_bare_code_passed_to_akshare(self):
        ak = _fake_ak_with_all()
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            _vendor_mod.get_balance_sheet("600519.SH")
        call_kwargs = ak.stock_balance_sheet_by_report_em.call_args[1]
        assert call_kwargs["symbol"] == "600519"

    @pytest.mark.unit
    def test_empty_df_returns_exact_no_data_message(self):
        ak = MagicMock()
        ak.stock_balance_sheet_by_report_em.return_value = pd.DataFrame()
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            result = _vendor_mod.get_balance_sheet("600519")
        assert result == "No balance sheet data found for symbol '600519'"

    @pytest.mark.unit
    def test_none_df_returns_exact_no_data_message(self):
        ak = MagicMock()
        ak.stock_balance_sheet_by_report_em.return_value = None
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            result = _vendor_mod.get_balance_sheet("600519")
        assert result == "No balance sheet data found for symbol '600519'"

    @pytest.mark.unit
    def test_dependency_unavailable_returns_error_string_no_raise(self):
        with patch(
            "tradingagents.dataflows.akshare_china._dep_bootstrap.ensure",
            side_effect=DependencyUnavailable("akshare: pip install failed"),
        ):
            result = _vendor_mod.get_balance_sheet("600519")
        assert isinstance(result, str)
        assert "Error:" in result
        assert "unavailable" in result.lower() or "akshare" in result.lower()

    @pytest.mark.unit
    def test_akshare_exception_returns_error_string_no_raise(self):
        ak = MagicMock()
        ak.stock_balance_sheet_by_report_em.side_effect = ConnectionError("net")
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            result = _vendor_mod.get_balance_sheet("600519")
        assert isinstance(result, str)
        assert "Error:" in result
        assert "balance sheet" in result.lower() or "600519" in result

    @pytest.mark.unit
    def test_non_a_share_returns_a_share_only_message_no_ensure(self):
        ensure_mock = MagicMock()
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", ensure_mock):
            result = _vendor_mod.get_balance_sheet("AAPL")
        ensure_mock.assert_not_called()
        lower = result.lower()
        assert "a-share" in lower or "a_share" in lower or "a share" in lower

    @pytest.mark.unit
    def test_curr_date_filter_drops_future_rows_when_report_date_col_present(self):
        """Rows with REPORT_DATE > curr_date should be filtered out."""
        ak = MagicMock()
        ak.stock_balance_sheet_by_report_em.return_value = pd.DataFrame({
            "REPORT_DATE": ["2025-03-31", "2024-12-31", "2024-09-30"],
            "TOTAL_ASSETS": [1, 2, 3],
        })
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            result = _vendor_mod.get_balance_sheet("600519", curr_date="2024-12-31")
        # 2025-03-31 is after curr_date 2024-12-31, should be filtered out
        assert "2025-03-31" not in result
        assert "2024-12-31" in result
        assert "2024-09-30" in result

    @pytest.mark.unit
    def test_curr_date_filter_skipped_when_no_date_col(self):
        """If no date-like column found, silently skip filter."""
        ak = MagicMock()
        ak.stock_balance_sheet_by_report_em.return_value = pd.DataFrame({
            "COL_A": [1, 2],
            "COL_B": [3, 4],
        })
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            # Should not raise; both rows intact
            result = _vendor_mod.get_balance_sheet("600519", curr_date="2024-12-31")
        assert "# Balance Sheet data for" in result

    @pytest.mark.unit
    def test_bj_suffix_normalized(self):
        """BJ (Beijing exchange) suffix should be stripped to bare 6-digit code."""
        ak = _fake_ak_with_all()
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            _vendor_mod.get_balance_sheet("430047.BJ")
        call_kwargs = ak.stock_balance_sheet_by_report_em.call_args[1]
        assert call_kwargs["symbol"] == "430047"


# ---------------------------------------------------------------------------
# get_cashflow
# ---------------------------------------------------------------------------

class TestGetCashflow:

    @pytest.mark.unit
    def test_happy_path_header_and_csv_body(self):
        ak = _fake_ak_with_all()
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            result = _vendor_mod.get_cashflow("600519.SH", freq="quarterly")

        assert result.startswith("# Cash Flow data for 600519.SH (quarterly)")
        assert "# Data retrieved on:" in result
        assert "\n\n" in result
        body = result.split("\n\n", 1)[1]
        csv_lines = [l for l in body.splitlines() if l.strip()]
        assert "," in csv_lines[0]
        assert len(csv_lines) > 1

    @pytest.mark.unit
    def test_symbol_normalization_bare_code_passed_to_akshare(self):
        ak = _fake_ak_with_all()
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            _vendor_mod.get_cashflow("600519.SH")
        call_kwargs = ak.stock_cash_flow_sheet_by_report_em.call_args[1]
        assert call_kwargs["symbol"] == "600519"

    @pytest.mark.unit
    def test_empty_df_returns_exact_no_data_message(self):
        ak = MagicMock()
        ak.stock_cash_flow_sheet_by_report_em.return_value = pd.DataFrame()
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            result = _vendor_mod.get_cashflow("600519")
        assert result == "No cash flow data found for symbol '600519'"

    @pytest.mark.unit
    def test_none_df_returns_exact_no_data_message(self):
        ak = MagicMock()
        ak.stock_cash_flow_sheet_by_report_em.return_value = None
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            result = _vendor_mod.get_cashflow("600519")
        assert result == "No cash flow data found for symbol '600519'"

    @pytest.mark.unit
    def test_dependency_unavailable_returns_error_string_no_raise(self):
        with patch(
            "tradingagents.dataflows.akshare_china._dep_bootstrap.ensure",
            side_effect=DependencyUnavailable("akshare: pip install failed"),
        ):
            result = _vendor_mod.get_cashflow("600519")
        assert isinstance(result, str)
        assert "Error:" in result
        assert "unavailable" in result.lower() or "akshare" in result.lower()

    @pytest.mark.unit
    def test_akshare_exception_returns_error_string_no_raise(self):
        ak = MagicMock()
        ak.stock_cash_flow_sheet_by_report_em.side_effect = ConnectionError("net")
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            result = _vendor_mod.get_cashflow("600519")
        assert isinstance(result, str)
        assert "Error:" in result
        assert "cash flow" in result.lower() or "600519" in result

    @pytest.mark.unit
    def test_non_a_share_returns_a_share_only_message_no_ensure(self):
        ensure_mock = MagicMock()
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", ensure_mock):
            result = _vendor_mod.get_cashflow("AAPL")
        ensure_mock.assert_not_called()
        lower = result.lower()
        assert "a-share" in lower or "a_share" in lower or "a share" in lower

    @pytest.mark.unit
    def test_curr_date_filter_drops_future_rows(self):
        ak = MagicMock()
        ak.stock_cash_flow_sheet_by_report_em.return_value = pd.DataFrame({
            "REPORT_DATE": ["2025-03-31", "2024-12-31"],
            "NETCASH_OPERATE": [1, 2],
        })
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            result = _vendor_mod.get_cashflow("600519", curr_date="2024-12-31")
        assert "2025-03-31" not in result
        assert "2024-12-31" in result


# ---------------------------------------------------------------------------
# get_income_statement
# ---------------------------------------------------------------------------

class TestGetIncomeStatement:

    @pytest.mark.unit
    def test_happy_path_header_and_csv_body(self):
        ak = _fake_ak_with_all()
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            result = _vendor_mod.get_income_statement("600519.SH", freq="quarterly")

        assert result.startswith("# Income Statement data for 600519.SH (quarterly)")
        assert "# Data retrieved on:" in result
        assert "\n\n" in result
        body = result.split("\n\n", 1)[1]
        csv_lines = [l for l in body.splitlines() if l.strip()]
        assert "," in csv_lines[0]
        assert len(csv_lines) > 1

    @pytest.mark.unit
    def test_symbol_normalization_bare_code_passed_to_akshare(self):
        ak = _fake_ak_with_all()
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            _vendor_mod.get_income_statement("600519.SH")
        call_kwargs = ak.stock_profit_sheet_by_report_em.call_args[1]
        assert call_kwargs["symbol"] == "600519"

    @pytest.mark.unit
    def test_empty_df_returns_exact_no_data_message(self):
        ak = MagicMock()
        ak.stock_profit_sheet_by_report_em.return_value = pd.DataFrame()
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            result = _vendor_mod.get_income_statement("600519")
        assert result == "No income statement data found for symbol '600519'"

    @pytest.mark.unit
    def test_none_df_returns_exact_no_data_message(self):
        ak = MagicMock()
        ak.stock_profit_sheet_by_report_em.return_value = None
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            result = _vendor_mod.get_income_statement("600519")
        assert result == "No income statement data found for symbol '600519'"

    @pytest.mark.unit
    def test_dependency_unavailable_returns_error_string_no_raise(self):
        with patch(
            "tradingagents.dataflows.akshare_china._dep_bootstrap.ensure",
            side_effect=DependencyUnavailable("akshare: pip install failed"),
        ):
            result = _vendor_mod.get_income_statement("600519")
        assert isinstance(result, str)
        assert "Error:" in result
        assert "unavailable" in result.lower() or "akshare" in result.lower()

    @pytest.mark.unit
    def test_akshare_exception_returns_error_string_no_raise(self):
        ak = MagicMock()
        ak.stock_profit_sheet_by_report_em.side_effect = ConnectionError("net")
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            result = _vendor_mod.get_income_statement("600519")
        assert isinstance(result, str)
        assert "Error:" in result
        assert "income statement" in result.lower() or "600519" in result

    @pytest.mark.unit
    def test_non_a_share_returns_a_share_only_message_no_ensure(self):
        ensure_mock = MagicMock()
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", ensure_mock):
            result = _vendor_mod.get_income_statement("AAPL")
        ensure_mock.assert_not_called()
        lower = result.lower()
        assert "a-share" in lower or "a_share" in lower or "a share" in lower

    @pytest.mark.unit
    def test_curr_date_filter_drops_future_rows(self):
        ak = MagicMock()
        ak.stock_profit_sheet_by_report_em.return_value = pd.DataFrame({
            "REPORT_DATE": ["2025-03-31", "2024-12-31"],
            "NET_PROFIT": [1, 2],
        })
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            result = _vendor_mod.get_income_statement("600519", curr_date="2024-12-31")
        assert "2025-03-31" not in result
        assert "2024-12-31" in result

    @pytest.mark.unit
    def test_ss_suffix_normalized(self):
        """SS (Shanghai alternate) suffix should be stripped."""
        ak = _fake_ak_with_all()
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            _vendor_mod.get_income_statement("600519.SS")
        call_kwargs = ak.stock_profit_sheet_by_report_em.call_args[1]
        assert call_kwargs["symbol"] == "600519"
