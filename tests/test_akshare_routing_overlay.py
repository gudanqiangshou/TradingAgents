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

    # All four categories overlaid for A_SHARE
    assert cfg["data_vendors"]["core_stock_apis"] == "akshare"
    assert cfg["data_vendors"]["fundamental_data"] == "akshare"
    assert cfg["data_vendors"]["news_data"] == "akshare"
    assert cfg["data_vendors"]["technical_indicators"] == "akshare"
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

    # cfg should see akshare for all four overlaid categories
    assert cfg["data_vendors"]["core_stock_apis"] == "akshare"
    assert cfg["data_vendors"]["fundamental_data"] == "akshare"
    assert cfg["data_vendors"]["news_data"] == "akshare"
    assert cfg["data_vendors"]["technical_indicators"] == "akshare"

    # DEFAULT_CONFIG must remain untouched for all four overlaid keys
    assert dc.DEFAULT_CONFIG["data_vendors"]["core_stock_apis"] == "yfinance", (
        "apply_china_vendor_overlay mutated DEFAULT_CONFIG in place — aliasing bug!"
    )
    assert dc.DEFAULT_CONFIG["data_vendors"]["fundamental_data"] == "yfinance", (
        "apply_china_vendor_overlay mutated DEFAULT_CONFIG fundamental_data — aliasing bug!"
    )
    assert dc.DEFAULT_CONFIG["data_vendors"]["news_data"] == "yfinance", (
        "apply_china_vendor_overlay mutated DEFAULT_CONFIG news_data — aliasing bug!"
    )
    assert dc.DEFAULT_CONFIG["data_vendors"]["technical_indicators"] == "yfinance", (
        "apply_china_vendor_overlay mutated DEFAULT_CONFIG technical_indicators — aliasing bug!"
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


# ---------------------------------------------------------------------------
# 7. overlay always-reset: stale HK tool_vendors cleared for subsequent US run
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_overlay_resets_stale_tool_vendors_from_prior_hk_run(monkeypatch):
    """Simulate the exact production pollution scenario.

    A prior HK run left tool_vendors={"get_stock_data":"akshare",...} in
    _config.  A subsequent US ticker (AAPL) call must see tool_vendors={} after
    apply_china_vendor_overlay, not the stale HK values.
    """
    import copy
    import tradingagents.default_config as dc
    import tradingagents.dataflows.config as cfg_mod
    from tradingagents.dataflows.akshare_china import apply_china_vendor_overlay

    # Build a config that simulates what set_config (merge) would have left after
    # a prior HK run: tool_vendors still has akshare overrides.
    polluted = copy.deepcopy(dc.DEFAULT_CONFIG)
    polluted["tool_vendors"] = {"get_stock_data": "akshare", "get_fundamentals": "akshare"}

    # Now run overlay for a US ticker using the polluted config
    apply_china_vendor_overlay(polluted, "AAPL")

    # tool_vendors must be completely cleared (not merged — replaced)
    assert polluted["tool_vendors"] == {}, (
        f"Expected empty tool_vendors after US overlay, got: {polluted['tool_vendors']}"
    )

    # data_vendors must be reset to yfinance defaults (not any akshare leftovers)
    assert polluted["data_vendors"]["core_stock_apis"] == "yfinance"
    assert polluted["data_vendors"]["fundamental_data"] == "yfinance"
    assert polluted["data_vendors"]["news_data"] == "yfinance"
    assert polluted["data_vendors"]["technical_indicators"] == "yfinance"


@pytest.mark.unit
def test_overlay_resets_stale_data_vendors_from_prior_a_share_run(monkeypatch):
    """Inverse scenario: prior A-share run left data_vendors full of 'akshare'.

    After apply_china_vendor_overlay for an HK ticker, data_vendors must be
    reset to yfinance defaults (HK overlay only touches tool_vendors).
    """
    import copy
    import tradingagents.default_config as dc
    from tradingagents.dataflows.akshare_china import apply_china_vendor_overlay

    # Polluted config left by a prior A-share run
    polluted = copy.deepcopy(dc.DEFAULT_CONFIG)
    polluted["data_vendors"] = {
        "core_stock_apis": "akshare",
        "fundamental_data": "akshare",
        "news_data": "akshare",
        "technical_indicators": "akshare",
    }
    polluted["tool_vendors"] = {}

    # Now run overlay for an HK ticker
    apply_china_vendor_overlay(polluted, "0700.HK")

    # data_vendors must be reset to yfinance (HK overlay must not inherit A-share pollution)
    assert polluted["data_vendors"]["core_stock_apis"] == "yfinance", (
        "HK overlay must reset data_vendors to yfinance defaults; A-share pollution persisted"
    )
    assert polluted["data_vendors"]["fundamental_data"] == "yfinance"
    assert polluted["data_vendors"]["news_data"] == "yfinance"
    assert polluted["data_vendors"]["technical_indicators"] == "yfinance"

    # HK tool_vendors must still have the two HK overrides
    assert polluted["tool_vendors"]["get_stock_data"] == "akshare"
    assert polluted["tool_vendors"]["get_fundamentals"] == "akshare"


@pytest.mark.unit
def test_overlay_is_idempotent():
    """Calling apply_china_vendor_overlay twice for the same ticker yields the same result."""
    import copy
    import tradingagents.default_config as dc
    from tradingagents.dataflows.akshare_china import apply_china_vendor_overlay

    for ticker in ("600519", "0700.HK", "AAPL", "BTC-USD"):
        cfg_a = copy.deepcopy(dc.DEFAULT_CONFIG)
        apply_china_vendor_overlay(cfg_a, ticker)

        cfg_b = copy.deepcopy(dc.DEFAULT_CONFIG)
        apply_china_vendor_overlay(cfg_b, ticker)
        apply_china_vendor_overlay(cfg_b, ticker)  # second call

        assert cfg_a["data_vendors"] == cfg_b["data_vendors"], (
            f"data_vendors not idempotent for ticker={ticker!r}"
        )
        assert cfg_a["tool_vendors"] == cfg_b["tool_vendors"], (
            f"tool_vendors not idempotent for ticker={ticker!r}"
        )
