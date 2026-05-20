"""
Tests for Phase 3: A-share financials in the AkShare vendor.

Covers: get_fundamentals, get_balance_sheet, get_cashflow, get_income_statement.
Fully offline — mocks _dep_bootstrap.ensure so that akshare is never imported.
All tests are marked @pytest.mark.unit.
"""

from __future__ import annotations

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
    Long-form: columns like '指标' (item name) and one or more period columns.
    """
    return pd.DataFrame({
        "指标": ["营业收入", "净利润", "每股收益", "市盈率"],
        "2024Q3": [150_000_000_000, 60_000_000_000, 47.69, 25.1],
        "2024Q2": [145_000_000_000, 58_000_000_000, 46.21, 24.8],
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
        """Happy path: header matches exact contract; body has label: value lines."""
        ak = _fake_ak_with_all()
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            result = _vendor_mod.get_fundamentals("600519.SH")

        # Exact header line 1
        assert result.startswith("# Company Fundamentals for 600519.SH")
        # Header line 2
        assert "# Data retrieved on:" in result
        # Blank line separator
        assert "\n\n" in result
        # Body: label: value lines
        body = result.split("\n\n", 1)[1]
        lines = [l for l in body.splitlines() if l.strip()]
        assert len(lines) > 0
        for line in lines:
            assert ": " in line, f"Expected 'label: value' format, got: {line!r}"

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
