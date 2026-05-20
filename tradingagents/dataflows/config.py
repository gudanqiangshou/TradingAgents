from copy import deepcopy
from typing import Any, Dict, Optional

import tradingagents.default_config as default_config

# Use default config but allow it to be overridden
_config: Optional[Dict] = None


def initialize_config():
    """Initialize the configuration with default values."""
    global _config
    if _config is None:
        _config = deepcopy(default_config.DEFAULT_CONFIG)


def set_config(config: Dict):
    """Update the configuration with custom values.

    Dict-valued keys (e.g. ``data_vendors``) are merged one level deep so a
    partial update like ``{"data_vendors": {"core_stock_apis": "alpha_vantage"}}``
    keeps the other nested keys from the default; scalar keys are replaced.
    """
    global _config
    initialize_config()
    incoming = deepcopy(config)
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(_config.get(key), dict):
            _config[key].update(value)
        else:
            _config[key] = value


def replace_section(key: str, value: Any) -> None:
    """Fully replace a top-level section in the global ``_config``.

    Unlike :func:`set_config`, which *merges* dict-valued keys one level deep,
    this function deep-copies *value* and assigns it directly to
    ``_config[key]``, completely replacing whatever was there.

    Use sparingly — intended for callers that need REPLACE semantics on nested
    dicts (e.g. per-run routing overlays such as
    :func:`tradingagents.dataflows.akshare_china.apply_china_vendor_overlay`)
    where the full desired state must be written through to the global config
    to defeat :func:`set_config`'s merge-only behaviour.
    """
    global _config
    initialize_config()
    _config[key] = deepcopy(value)


def get_config() -> Dict:
    """Get the current configuration."""
    if _config is None:
        initialize_config()
    return deepcopy(_config)


# Initialize with default config
initialize_config()
