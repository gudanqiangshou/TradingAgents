"""Regression: an explicit TRADINGAGENTS_LLM_BACKEND_URL must survive the
interactive CLI's per-provider endpoint selection.

Root cause of the field bug: select_llm_provider() returns a hardcoded
"https://api.anthropic.com/" for Anthropic; run_analysis wrote that into
config["backend_url"], clobbering the env override. A custom-proxy key
(sk-acw-*) then hit official api.anthropic.com -> 401 invalid x-api-key.
"""

from __future__ import annotations

import cli.main as m
from cli.main import build_analysis_config

PROXY = "https://api.aicodewith.com"
OFFICIAL = "https://api.anthropic.com/"


def _selections(backend_url: str) -> dict:
    return {
        "research_depth": 1,
        "shallow_thinker": "claude-sonnet-4-6",
        "deep_thinker": "claude-sonnet-4-6",
        "backend_url": backend_url,
        "llm_provider": "anthropic",
        "output_language": "English",
    }


def test_env_backend_url_overrides_cli_menu(monkeypatch):
    """Explicit env override must outrank the menu's hardcoded endpoint."""
    monkeypatch.setenv("TRADINGAGENTS_LLM_BACKEND_URL", PROXY)
    # In real runs default_config's env overlay puts the proxy into
    # DEFAULT_CONFIG at import; emulate that so the helper has it to keep.
    monkeypatch.setitem(m.DEFAULT_CONFIG, "backend_url", PROXY)

    cfg = build_analysis_config(_selections(OFFICIAL), checkpoint=False)

    assert cfg["backend_url"] == PROXY


def test_cli_menu_used_when_env_absent(monkeypatch):
    """Without the env override, the interactive selection is honored."""
    monkeypatch.delenv("TRADINGAGENTS_LLM_BACKEND_URL", raising=False)

    cfg = build_analysis_config(_selections(OFFICIAL), checkpoint=False)

    assert cfg["backend_url"] == OFFICIAL
