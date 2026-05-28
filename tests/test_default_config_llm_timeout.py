"""Tests for the Codex I3 fix: llm_timeout default + env-var override.

The analysis_runner's watchdog only fires BETWEEN langgraph chunks, so a
single hung LLM call could block indefinitely. Wiring a per-request
timeout into the default config + provider kwargs ensures the inner-layer
guard exists.
"""
from __future__ import annotations

import importlib
import os
from unittest.mock import patch

import pytest


def _reload_default_config():
    """Force a fresh import so the module-level dict picks up env changes.

    `DEFAULT_CONFIG` is built at import time from `_apply_env_overrides`,
    so we must reimport after monkeypatching env vars.
    """
    import tradingagents.default_config as cfg
    return importlib.reload(cfg)


def test_default_llm_timeout_is_300s(monkeypatch):
    """Default = 300s (5 min) — generous enough for reasoning models."""
    monkeypatch.delenv("TRADINGAGENTS_LLM_TIMEOUT", raising=False)
    cfg = _reload_default_config()
    assert cfg.DEFAULT_CONFIG["llm_timeout"] == 300


def test_llm_timeout_env_var_override(monkeypatch):
    """TRADINGAGENTS_LLM_TIMEOUT env var overrides the default."""
    monkeypatch.setenv("TRADINGAGENTS_LLM_TIMEOUT", "120")
    cfg = _reload_default_config()
    assert cfg.DEFAULT_CONFIG["llm_timeout"] == 120


def test_llm_timeout_env_var_coerced_to_int(monkeypatch):
    """The override coercer turns the string env value into int."""
    monkeypatch.setenv("TRADINGAGENTS_LLM_TIMEOUT", "45")
    cfg = _reload_default_config()
    assert cfg.DEFAULT_CONFIG["llm_timeout"] == 45
    assert isinstance(cfg.DEFAULT_CONFIG["llm_timeout"], int)


def test_provider_kwargs_includes_timeout(monkeypatch):
    """_get_provider_kwargs forwards llm_timeout under the kwarg name `timeout`
    so the LLM client constructor receives it as a LangChain `timeout=`.
    """
    monkeypatch.delenv("TRADINGAGENTS_LLM_TIMEOUT", raising=False)
    # Build a graph instance enough to call _get_provider_kwargs without
    # actually hitting the network. We just exercise the method directly.
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    # Use a fake `self` that only has the .config attribute the method reads.
    class _Fake:
        config = {"llm_provider": "openai", "llm_timeout": 90}

    kwargs = TradingAgentsGraph._get_provider_kwargs(_Fake())
    assert kwargs.get("timeout") == 90


def test_provider_kwargs_skips_timeout_when_none(monkeypatch):
    """When llm_timeout is explicitly None, no `timeout` kwarg is sent —
    the LLM client falls back to its own default."""
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    class _Fake:
        config = {"llm_provider": "openai", "llm_timeout": None}

    kwargs = TradingAgentsGraph._get_provider_kwargs(_Fake())
    assert "timeout" not in kwargs
