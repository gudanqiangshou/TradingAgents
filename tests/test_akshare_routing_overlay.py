"""
Tests for Phase 2c + Phase 5: AkShare vendor registration, A-share/HK routing overlay,
and aliasing safety.

All tests are offline; akshare is never actually imported.
"""

from __future__ import annotations

import copy
import pytest


# ---------------------------------------------------------------------------
# 1. overlay sets akshare for A-share tickers
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_overlay_sets_akshare_for_a_share():
    import tradingagents.default_config as dc
    from tradingagents.dataflows.akshare_china import apply_china_vendor_overlay

    cfg = copy.deepcopy(dc.DEFAULT_CONFIG)
    apply_china_vendor_overlay(cfg, "600519")

    # Phase 4: all three categories overlaid for A_SHARE
    assert cfg["data_vendors"]["core_stock_apis"] == "akshare"
    assert cfg["data_vendors"]["fundamental_data"] == "akshare"
    assert cfg["data_vendors"]["news_data"] == "akshare"
    # Other categories untouched
    assert cfg["data_vendors"]["technical_indicators"] == "yfinance"
    # A_SHARE must NOT pollute tool_vendors (no per-method HK overrides for A_SHARE)
    assert cfg.get("tool_vendors", {}) == {}, (
        "A_SHARE overlay must not set tool_vendors"
    )


# ---------------------------------------------------------------------------
# 2. overlay sets per-method tool_vendors for HK tickers (Phase 5)
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_overlay_sets_per_method_for_hk():
    """HK tickers get per-method tool_vendors overrides for the 2 supported methods."""
    import tradingagents.default_config as dc
    from tradingagents.dataflows.akshare_china import apply_china_vendor_overlay

    cfg = copy.deepcopy(dc.DEFAULT_CONFIG)
    apply_china_vendor_overlay(cfg, "0700.HK")

    # tool_vendors must have exactly the two HK-supported methods
    assert cfg["tool_vendors"]["get_stock_data"] == "akshare"
    assert cfg["tool_vendors"]["get_fundamentals"] == "akshare"

    # The other 4 methods must NOT appear in tool_vendors (they fall back to yfinance)
    for unsupported in ("get_balance_sheet", "get_cashflow", "get_income_statement", "get_news"):
        assert unsupported not in cfg["tool_vendors"], (
            f"HK overlay must not set tool_vendors[{unsupported!r}]; "
            "that method should fall back to yfinance"
        )

    # HK must NOT touch data_vendors (other categories remain yfinance defaults)
    assert cfg["data_vendors"]["core_stock_apis"] == "yfinance", (
        "HK overlay must not touch data_vendors; other categories stay at yfinance"
    )
    assert cfg["data_vendors"]["fundamental_data"] == "yfinance"
    assert cfg["data_vendors"]["news_data"] == "yfinance"
    assert cfg["data_vendors"]["technical_indicators"] == "yfinance"


# ---------------------------------------------------------------------------
# 3. overlay is no-op for US and crypto tickers
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_overlay_noop_for_us_and_crypto():
    import tradingagents.default_config as dc
    from tradingagents.dataflows.akshare_china import apply_china_vendor_overlay

    for ticker in ("AAPL", "BTC-USD"):
        cfg = copy.deepcopy(dc.DEFAULT_CONFIG)
        apply_china_vendor_overlay(cfg, ticker)
        assert cfg["data_vendors"]["core_stock_apis"] == "yfinance", (
            f"Expected yfinance for {ticker!r}, got "
            f"{cfg['data_vendors']['core_stock_apis']!r}"
        )


# ---------------------------------------------------------------------------
# 4. overlay must NOT mutate the shared nested dict (aliasing guard)
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_overlay_does_not_mutate_global_default_config():
    """Shallow copy (like the real call sites) must not corrupt DEFAULT_CONFIG.

    Covers BOTH A_SHARE (data_vendors aliasing) AND HK (tool_vendors aliasing).
    """
    import tradingagents.default_config as dc
    from tradingagents.dataflows.akshare_china import apply_china_vendor_overlay

    # --- A_SHARE: data_vendors aliasing ---
    # SHALLOW copy — the shared data_vendors dict is the same object as in
    # DEFAULT_CONFIG, just like cli/main.py and web/app.py do.
    cfg = dc.DEFAULT_CONFIG.copy()
    apply_china_vendor_overlay(cfg, "600519")

    # cfg should see akshare for all three overlaid categories
    assert cfg["data_vendors"]["core_stock_apis"] == "akshare"
    assert cfg["data_vendors"]["fundamental_data"] == "akshare"
    assert cfg["data_vendors"]["news_data"] == "akshare"

    # DEFAULT_CONFIG must remain untouched for all three overlaid keys
    assert dc.DEFAULT_CONFIG["data_vendors"]["core_stock_apis"] == "yfinance", (
        "apply_china_vendor_overlay mutated DEFAULT_CONFIG in place — aliasing bug!"
    )
    assert dc.DEFAULT_CONFIG["data_vendors"]["fundamental_data"] == "yfinance", (
        "apply_china_vendor_overlay mutated DEFAULT_CONFIG fundamental_data — aliasing bug!"
    )
    assert dc.DEFAULT_CONFIG["data_vendors"]["news_data"] == "yfinance", (
        "apply_china_vendor_overlay mutated DEFAULT_CONFIG news_data — aliasing bug!"
    )

    # --- HK: tool_vendors aliasing ---
    # Verify DEFAULT_CONFIG["tool_vendors"] stays as empty dict after HK overlay
    # on a fresh shallow copy.
    original_tool_vendors = dict(dc.DEFAULT_CONFIG.get("tool_vendors", {}))
    cfg_hk = dc.DEFAULT_CONFIG.copy()
    apply_china_vendor_overlay(cfg_hk, "0700.HK")

    # cfg_hk should have the two HK method overrides
    assert cfg_hk["tool_vendors"]["get_stock_data"] == "akshare"
    assert cfg_hk["tool_vendors"]["get_fundamentals"] == "akshare"

    # DEFAULT_CONFIG["tool_vendors"] must be byte-identical to what it was before
    assert dict(dc.DEFAULT_CONFIG.get("tool_vendors", {})) == original_tool_vendors, (
        "apply_china_vendor_overlay mutated DEFAULT_CONFIG['tool_vendors'] — aliasing bug!"
    )
    # Specifically, the two HK keys must NOT have leaked into DEFAULT_CONFIG
    assert "get_stock_data" not in dc.DEFAULT_CONFIG.get("tool_vendors", {}), (
        "HK overlay leaked get_stock_data into DEFAULT_CONFIG['tool_vendors']"
    )
    assert "get_fundamentals" not in dc.DEFAULT_CONFIG.get("tool_vendors", {}), (
        "HK overlay leaked get_fundamentals into DEFAULT_CONFIG['tool_vendors']"
    )


# ---------------------------------------------------------------------------
# 5. route_to_vendor dispatches to akshare when overlaid
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_route_to_vendor_uses_akshare_when_overlaid(monkeypatch):
    """set_config(data_vendors.core_stock_apis=akshare) → route_to_vendor calls akshare impl."""
    import copy
    import tradingagents.default_config as dc
    import tradingagents.dataflows.config as cfg_mod
    from tradingagents.dataflows import interface

    # Directly replace _config with a clean deep-copy that has akshare as the
    # core_stock_apis vendor.  We cannot use set_config() here because its
    # one-level-deep merge leaves stale tool_vendors keys from earlier tests
    # (e.g. test_dataflows_config sets tool_vendors["get_stock_data"]="alpha_vantage",
    # and merging an empty dict over it with .update({}) is a no-op — the key
    # survives and the tool-level override then wins over the category setting).
    clean = copy.deepcopy(dc.DEFAULT_CONFIG)
    clean["data_vendors"]["core_stock_apis"] = "akshare"
    original_cfg = cfg_mod._config  # save for teardown
    monkeypatch.setattr(cfg_mod, "_config", clean)

    # Sentinel value returned by the stub
    SENTINEL = "akshare_sentinel_result"

    def stub_get_stock_data(symbol, start_date, end_date):
        return SENTINEL

    # Monkeypatch the akshare entry in VENDOR_METHODS
    monkeypatch.setitem(
        interface.VENDOR_METHODS["get_stock_data"],
        "akshare",
        stub_get_stock_data,
    )

    result = interface.route_to_vendor(
        "get_stock_data", "600519", "2026-01-05", "2026-01-09"
    )
    assert result == SENTINEL, (
        f"Expected sentinel from akshare stub, got {result!r}"
    )


# ---------------------------------------------------------------------------
# 6. DEFAULT_CONFIG unchanged; akshare registered in VENDOR_METHODS
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_default_config_unchanged():
    import tradingagents.default_config as dc
    from tradingagents.dataflows import interface

    # Default vendor must still be yfinance
    assert dc.DEFAULT_CONFIG["data_vendors"]["core_stock_apis"] == "yfinance"

    # akshare must be registered in VENDOR_METHODS (from the import in interface.py)
    assert "akshare" in interface.VENDOR_METHODS["get_stock_data"], (
        "akshare not registered in interface.VENDOR_METHODS['get_stock_data']"
    )

    # But yfinance and alpha_vantage must still be there too
    assert "yfinance" in interface.VENDOR_METHODS["get_stock_data"]
    assert "alpha_vantage" in interface.VENDOR_METHODS["get_stock_data"]
