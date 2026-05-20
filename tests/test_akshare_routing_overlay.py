"""
Tests for Phase 2c: AkShare vendor registration, A-share/HK routing overlay,
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

    assert cfg["data_vendors"]["core_stock_apis"] == "akshare"
    # Phase 3: fundamental_data also overlaid
    assert cfg["data_vendors"]["fundamental_data"] == "akshare"
    # Other categories untouched
    assert cfg["data_vendors"]["technical_indicators"] == "yfinance"
    assert cfg["data_vendors"]["news_data"] == "yfinance"


# ---------------------------------------------------------------------------
# 2. overlay is a noop for HK tickers (HK routing deferred to a later phase)
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_overlay_noop_for_hk_until_hk_impl():
    """HK tickers must NOT be routed to akshare yet.

    HK data fetching is not implemented; routing to akshare would cause
    get_stock_data to return an error string that feeds the LLM as price data.
    HK tickers keep using the default yfinance vendor until HK fetching is
    implemented in a later phase.
    """
    import tradingagents.default_config as dc
    from tradingagents.dataflows.akshare_china import apply_china_vendor_overlay

    cfg = copy.deepcopy(dc.DEFAULT_CONFIG)
    apply_china_vendor_overlay(cfg, "0700.HK")

    # Overlay must be a noop — yfinance unchanged for HK
    assert cfg["data_vendors"]["core_stock_apis"] == "yfinance", (
        "HK tickers should NOT be routed to akshare until HK fetching is implemented"
    )
    assert cfg["data_vendors"]["technical_indicators"] == "yfinance"
    assert cfg["data_vendors"]["fundamental_data"] == "yfinance"
    assert cfg["data_vendors"]["news_data"] == "yfinance"


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
    """Shallow copy (like the real call sites) must not corrupt DEFAULT_CONFIG."""
    import tradingagents.default_config as dc
    from tradingagents.dataflows.akshare_china import apply_china_vendor_overlay

    # SHALLOW copy — the shared data_vendors dict is the same object as in
    # DEFAULT_CONFIG, just like cli/main.py and web/app.py do.
    cfg = dc.DEFAULT_CONFIG.copy()
    apply_china_vendor_overlay(cfg, "600519")

    # cfg should see akshare for both overlaid categories
    assert cfg["data_vendors"]["core_stock_apis"] == "akshare"
    assert cfg["data_vendors"]["fundamental_data"] == "akshare"

    # DEFAULT_CONFIG must remain untouched
    assert dc.DEFAULT_CONFIG["data_vendors"]["core_stock_apis"] == "yfinance", (
        "apply_china_vendor_overlay mutated DEFAULT_CONFIG in place — aliasing bug!"
    )
    assert dc.DEFAULT_CONFIG["data_vendors"]["fundamental_data"] == "yfinance", (
        "apply_china_vendor_overlay mutated DEFAULT_CONFIG fundamental_data — aliasing bug!"
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
