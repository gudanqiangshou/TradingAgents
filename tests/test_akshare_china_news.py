"""
Tests for Phase 4: A-share news in the AkShare vendor.

Covers: get_news — output format, symbol normalisation, column-variant handling,
date filtering, article-limit truncation, summary fallback, all fail-safe branches.
Fully offline — mocks _dep_bootstrap.ensure so that akshare is never imported.
All tests are marked @pytest.mark.unit.
"""

from __future__ import annotations

import copy
import pytest
import pandas as pd
from datetime import datetime
from unittest.mock import MagicMock, patch, call

import tradingagents.dataflows.akshare_china as _vendor_mod
import tradingagents.dataflows.config as _cfg_mod
import tradingagents.default_config as _dc

DependencyUnavailable = _vendor_mod._dep_bootstrap.DependencyUnavailable

START = "2024-01-01"
END   = "2024-01-31"


# ---------------------------------------------------------------------------
# Helpers — build fake DataFrames that mimic akshare news endpoint shapes
# ---------------------------------------------------------------------------

def _make_news_df_variant_a(n: int = 3) -> pd.DataFrame:
    """Column variant A: 新闻标题 / 新闻内容 / 新闻摘要 / 新闻链接 / 文章来源 / 发布时间."""
    return pd.DataFrame({
        "新闻标题": [f"标题{i}" for i in range(n)],
        "新闻内容": [f"内容{i}" for i in range(n)],
        "新闻摘要": [f"摘要{i}" for i in range(n)],
        "新闻链接": [f"http://example.com/{i}" for i in range(n)],
        "文章来源": [f"来源{i}" for i in range(n)],
        "发布时间": ["2024-01-15 10:00:00"] * n,
    })


def _make_news_df_variant_b(n: int = 3) -> pd.DataFrame:
    """Column variant B: 标题 / 内容 / 摘要 / 链接 / 来源 / 时间."""
    return pd.DataFrame({
        "标题": [f"标题{i}" for i in range(n)],
        "内容": [f"内容{i}" for i in range(n)],
        "摘要": [f"摘要{i}" for i in range(n)],
        "链接": [f"http://example.com/{i}" for i in range(n)],
        "来源": [f"来源{i}" for i in range(n)],
        "时间": ["2024-01-15 10:00:00"] * n,
    })


def _fake_ak(df: pd.DataFrame) -> MagicMock:
    """Return a fake akshare module whose stock_news_em returns *df*."""
    ak = MagicMock()
    ak.stock_news_em.return_value = df
    return ak


# ---------------------------------------------------------------------------
# 1. Output format contract (yfinance-compatible)
# ---------------------------------------------------------------------------

class TestGetNewsOutputFormat:

    @pytest.mark.unit
    def test_get_news_formats_like_yfinance_contract(self):
        """Header line, article sections with title/summary/link, blank lines between."""
        ak = _fake_ak(_make_news_df_variant_a(2))
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            result = _vendor_mod.get_news("600519", START, END)

        # Header line exactly
        assert result.startswith(f"## 600519 News, from {START} to {END}:\n")
        # Each article has ### title (source: publisher)
        assert "### 标题0 (source: 来源0)" in result
        assert "### 标题1 (source: 来源1)" in result
        # Summary present when non-empty
        assert "摘要0" in result
        assert "摘要1" in result
        # Link present when non-empty
        assert "Link: http://example.com/0" in result
        assert "Link: http://example.com/1" in result
        # Blank line between articles (double newline in body)
        body = result.split("\n\n", 1)[1]
        assert "\n\n" in body

    @pytest.mark.unit
    def test_summary_omitted_when_empty_and_no_content(self):
        """If summary AND content are both empty, summary line omitted."""
        df = pd.DataFrame({
            "新闻标题": ["T1"],
            "新闻内容": [""],
            "新闻摘要": [""],
            "新闻链接": ["http://x.com"],
            "文章来源": ["SRC"],
            "发布时间": ["2024-01-15 10:00:00"],
        })
        ak = _fake_ak(df)
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            result = _vendor_mod.get_news("600519", START, END)
        # Must have the title line
        assert "### T1 (source: SRC)" in result
        # The line immediately after the title should NOT be a blank summary line
        lines = result.splitlines()
        title_idx = next(i for i, l in enumerate(lines) if l.startswith("### T1"))
        after = lines[title_idx + 1] if title_idx + 1 < len(lines) else ""
        # After title must be "Link: ..." or blank line (not an empty summary line)
        assert after.startswith("Link:") or after == ""

    @pytest.mark.unit
    def test_link_omitted_when_empty(self):
        """Link: line omitted when link is empty."""
        df = pd.DataFrame({
            "新闻标题": ["T1"],
            "新闻内容": ["content"],
            "新闻摘要": ["summary"],
            "新闻链接": [""],
            "文章来源": ["SRC"],
            "发布时间": ["2024-01-15 10:00:00"],
        })
        ak = _fake_ak(df)
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            result = _vendor_mod.get_news("600519", START, END)
        assert "Link:" not in result


# ---------------------------------------------------------------------------
# 2. Symbol normalisation
# ---------------------------------------------------------------------------

class TestSymbolNormalisation:

    @pytest.mark.unit
    def test_get_news_passes_zfilled_symbol_to_akshare(self):
        """Bare ticker '600519' → symbol='600519' (already 6 digits)."""
        ak = _fake_ak(_make_news_df_variant_a(1))
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            _vendor_mod.get_news("600519", START, END)
        kwargs = ak.stock_news_em.call_args[1]
        assert kwargs["symbol"] == "600519"

    @pytest.mark.unit
    def test_short_code_zfilled(self):
        """Ticker '000001' (already 6 digits) → symbol='000001' (zfill is no-op)."""
        ak = _fake_ak(_make_news_df_variant_a(1))
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            _vendor_mod.get_news("000001", START, END)
        kwargs = ak.stock_news_em.call_args[1]
        assert kwargs["symbol"] == "000001"

    @pytest.mark.unit
    def test_suffix_stripped_then_zfilled_sh(self):
        """'600519.SH' → symbol='600519' (suffix strip then zfill)."""
        ak = _fake_ak(_make_news_df_variant_a(1))
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            _vendor_mod.get_news("600519.SH", START, END)
        kwargs = ak.stock_news_em.call_args[1]
        assert kwargs["symbol"] == "600519"

    @pytest.mark.unit
    def test_suffix_stripped_then_zfilled_bj(self):
        """'000043.BJ' → symbol='000043' (suffix strip; 6 digits preserved)."""
        ak = _fake_ak(_make_news_df_variant_a(1))
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            _vendor_mod.get_news("000043.BJ", START, END)
        kwargs = ak.stock_news_em.call_args[1]
        assert kwargs["symbol"] == "000043"

    @pytest.mark.unit
    def test_bj_suffix_normalized(self):
        """'430047.BJ' → symbol='430047'."""
        ak = _fake_ak(_make_news_df_variant_a(1))
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            _vendor_mod.get_news("430047.BJ", START, END)
        kwargs = ak.stock_news_em.call_args[1]
        assert kwargs["symbol"] == "430047"


# ---------------------------------------------------------------------------
# 3. Column variant handling
# ---------------------------------------------------------------------------

class TestColumnVariants:

    @pytest.mark.unit
    def test_chinese_columns_variant_a(self):
        """Variant A (新闻标题/新闻摘要/…) produces non-empty body."""
        ak = _fake_ak(_make_news_df_variant_a(2))
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            result = _vendor_mod.get_news("600519", START, END)
        assert "标题0" in result
        assert "标题1" in result

    @pytest.mark.unit
    def test_chinese_columns_variant_b(self):
        """Variant B (标题/摘要/…) produces non-empty body."""
        ak = _fake_ak(_make_news_df_variant_b(2))
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            result = _vendor_mod.get_news("600519", START, END)
        assert "标题0" in result
        assert "标题1" in result

    @pytest.mark.unit
    def test_both_variants_include_title_text(self):
        """Both column variants produce the article title text in output."""
        for df in (_make_news_df_variant_a(1), _make_news_df_variant_b(1)):
            ak = _fake_ak(df)
            with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
                result = _vendor_mod.get_news("600519", START, END)
            assert "标题0" in result, f"Title not found in output for df columns {list(df.columns)}"


# ---------------------------------------------------------------------------
# 4. Summary fallback to truncated content
# ---------------------------------------------------------------------------

class TestSummaryFallback:

    @pytest.mark.unit
    def test_summary_fallback_to_truncated_content(self):
        """Empty 新闻摘要 → body uses first 200 chars of 新闻内容."""
        long_content = "X" * 300
        df = pd.DataFrame({
            "新闻标题": ["T1"],
            "新闻内容": [long_content],
            "新闻摘要": [""],        # empty summary → should fall back to content
            "新闻链接": [""],
            "文章来源": ["SRC"],
            "发布时间": ["2024-01-15 10:00:00"],
        })
        ak = _fake_ak(df)
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            result = _vendor_mod.get_news("600519", START, END)
        # Output must contain the truncated content (≤200 chars)
        assert "X" * 200 in result
        # Should NOT contain the full 300-char string (truncation applied)
        assert "X" * 300 not in result

    @pytest.mark.unit
    def test_summary_preferred_over_content(self):
        """Non-empty 新闻摘要 is used verbatim; 新闻内容 ignored for display."""
        df = pd.DataFrame({
            "新闻标题": ["T1"],
            "新闻内容": ["content_text"],
            "新闻摘要": ["summary_text"],
            "新闻链接": [""],
            "文章来源": ["SRC"],
            "发布时间": ["2024-01-15 10:00:00"],
        })
        ak = _fake_ak(df)
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            result = _vendor_mod.get_news("600519", START, END)
        assert "summary_text" in result


# ---------------------------------------------------------------------------
# 5. Date filtering
# ---------------------------------------------------------------------------

class TestDateFiltering:

    @pytest.mark.unit
    def test_date_filter_includes_end_date_day_and_excludes_outside(self):
        """Articles on end_date are included; articles after end_date+1 are excluded."""
        df = pd.DataFrame({
            "新闻标题": ["before_range", "in_range", "on_end_date", "after_range"],
            "新闻内容": ["c"] * 4,
            "新闻摘要": ["s"] * 4,
            "新闻链接": [""] * 4,
            "文章来源": ["SRC"] * 4,
            "发布时间": [
                "2023-12-31 10:00:00",  # before start_date 2024-01-01 → excluded
                "2024-01-15 10:00:00",  # in range → included
                "2024-01-31 23:59:00",  # on end_date → included
                "2024-02-01 10:00:00",  # after end_date → excluded
            ],
        })
        ak = _fake_ak(df)
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            result = _vendor_mod.get_news("600519", START, END)
        assert "in_range" in result
        assert "on_end_date" in result
        assert "before_range" not in result
        assert "after_range" not in result

    @pytest.mark.unit
    def test_articles_with_nat_pub_time_included_unfiltered(self):
        """Articles whose 発布時間 is unparseable (NaT) must be included without filter."""
        df = pd.DataFrame({
            "新闻标题": ["unparseable_time"],
            "新闻内容": ["c"],
            "新闻摘要": ["s"],
            "新闻链接": [""],
            "文章来源": ["SRC"],
            "发布时间": [""],    # empty → NaT → include without filter
        })
        ak = _fake_ak(df)
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            result = _vendor_mod.get_news("600519", START, END)
        assert "unparseable_time" in result


# ---------------------------------------------------------------------------
# 6. Article limit
# ---------------------------------------------------------------------------

class TestArticleLimit:

    @pytest.mark.unit
    def test_article_limit_truncates(self):
        """If news_article_limit=2 and endpoint returns 5, only 2 appear."""
        original_cfg = _cfg_mod._config
        try:
            new_cfg = copy.deepcopy(_dc.DEFAULT_CONFIG)
            new_cfg["news_article_limit"] = 2
            _cfg_mod._config = new_cfg

            ak = _fake_ak(_make_news_df_variant_a(5))
            with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
                result = _vendor_mod.get_news("600519", START, END)

            # Count occurrences of "### 标题" to count articles
            count = result.count("### 标题")
            assert count == 2, f"Expected 2 articles, got {count}"
        finally:
            _cfg_mod._config = original_cfg


# ---------------------------------------------------------------------------
# 7. Empty / no-match results
# ---------------------------------------------------------------------------

class TestEmptyResults:

    @pytest.mark.unit
    def test_empty_df_returns_no_news_msg(self):
        """Empty DataFrame → exact 'No news found for 600519'."""
        ak = _fake_ak(pd.DataFrame())
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            result = _vendor_mod.get_news("600519", START, END)
        assert result == "No news found for 600519"

    @pytest.mark.unit
    def test_none_df_returns_no_news_msg(self):
        """None result → 'No news found for ...'."""
        ak = MagicMock()
        ak.stock_news_em.return_value = None
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            result = _vendor_mod.get_news("600519", START, END)
        assert result == "No news found for 600519"

    @pytest.mark.unit
    def test_zero_after_filter_returns_range_msg(self):
        """Non-empty df but all articles outside date range → range message."""
        df = pd.DataFrame({
            "新闻标题": ["old_news"],
            "新闻内容": ["c"],
            "新闻摘要": ["s"],
            "新闻链接": [""],
            "文章来源": ["SRC"],
            "发布时间": ["2023-01-01 10:00:00"],  # way before START
        })
        ak = _fake_ak(df)
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            result = _vendor_mod.get_news("600519", START, END)
        assert result == f"No news found for 600519 between {START} and {END}"


# ---------------------------------------------------------------------------
# 8. Fail-safe error paths
# ---------------------------------------------------------------------------

class TestFailSafe:

    @pytest.mark.unit
    def test_dependency_unavailable_returns_error_string_not_exception(self):
        """DependencyUnavailable from ensure → returns error string; does NOT raise."""
        with patch(
            "tradingagents.dataflows.akshare_china._dep_bootstrap.ensure",
            side_effect=DependencyUnavailable("akshare: pip install failed"),
        ):
            result = _vendor_mod.get_news("600519", START, END)
        assert isinstance(result, str)
        assert result.startswith("Error:")
        assert "unavailable" in result.lower() or "akshare" in result.lower()

    @pytest.mark.unit
    def test_akshare_runtime_exception_returns_error_string(self):
        """Endpoint raises ConnectionError → returns error string; does NOT raise."""
        ak = MagicMock()
        ak.stock_news_em.side_effect = ConnectionError("timeout")
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            result = _vendor_mod.get_news("600519", START, END)
        assert isinstance(result, str)
        assert result.startswith("Error:")
        assert "600519" in result
        assert "news" in result.lower() or "fetch" in result.lower()

    @pytest.mark.unit
    def test_shaping_exception_returns_error_string(self):
        """DataFrame missing ALL expected title columns → schema-guard branch."""
        df = pd.DataFrame({"foo": [1, 2]})
        ak = _fake_ak(df)
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
            result = _vendor_mod.get_news("600519", START, END)
        assert isinstance(result, str)
        assert result.startswith("Error:")
        assert "600519" in result

    @pytest.mark.unit
    def test_non_a_share_symbol_returns_clear_message(self):
        """AAPL → A-share-only message; ensure NOT called."""
        ensure_mock = MagicMock()
        with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", ensure_mock):
            result = _vendor_mod.get_news("AAPL", START, END)
        ensure_mock.assert_not_called()
        assert isinstance(result, str)
        lower = result.lower()
        assert "a-share" in lower or "a_share" in lower or "a share" in lower

    @pytest.mark.unit
    def test_never_raises_under_any_input(self):
        """get_news never raises regardless of inputs (exhaustive smoke test)."""
        scenarios = [
            # (ticker, start, end, ak_setup)
            ("600519", START, END, lambda ak: setattr(ak, "stock_news_em", MagicMock(side_effect=RuntimeError("boom")))),
            ("AAPL", START, END, lambda ak: None),   # non-A-share
            ("", START, END, lambda ak: None),        # empty ticker
        ]
        for ticker, start, end, setup in scenarios:
            ak = MagicMock()
            setup(ak)
            with patch("tradingagents.dataflows.akshare_china._dep_bootstrap.ensure", return_value=ak):
                try:
                    result = _vendor_mod.get_news(ticker, start, end)
                    assert isinstance(result, str)
                except Exception as exc:
                    pytest.fail(f"get_news raised for ticker={ticker!r}: {exc}")
