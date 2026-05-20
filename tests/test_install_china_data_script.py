"""
Smoke tests for scripts/install-china-data.sh.

Does NOT execute the script (that would require akshare already installed);
only validates:
1. The script has valid bash syntax (bash -n).
2. The script reads CHINA_DATA_PINS from the Python module (single source of
   truth — not hardcoded specs).
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

_SCRIPT = os.path.join(
    os.path.dirname(__file__), "..", "scripts", "install-china-data.sh"
)
_SCRIPT = os.path.normpath(_SCRIPT)


@pytest.mark.unit
def test_script_exists():
    """The script file must exist at the expected path."""
    assert os.path.isfile(_SCRIPT), f"Script not found at {_SCRIPT!r}"


@pytest.mark.unit
def test_script_syntax_is_valid():
    """bash -n (no-execute syntax check) must exit 0."""
    result = subprocess.run(
        ["bash", "-n", _SCRIPT],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"bash -n reported syntax errors in {_SCRIPT!r}:\n{result.stderr}"
    )


@pytest.mark.unit
def test_script_uses_python_helper_for_pins():
    """The script must reference CHINA_DATA_PINS from the Python module,
    not hardcode version strings.  Grep for the import pattern.
    """
    with open(_SCRIPT) as fh:
        content = fh.read()

    assert "CHINA_DATA_PINS" in content, (
        "Script does not reference CHINA_DATA_PINS from _dep_bootstrap; "
        "version pins may have drifted out of sync."
    )
    assert "_dep_bootstrap" in content, (
        "Script does not import from tradingagents.dataflows._dep_bootstrap; "
        "pins are not read from the single source of truth."
    )


@pytest.mark.unit
def test_script_has_uv_fallback():
    """Script must attempt uv pip install first, falling back to python -m pip."""
    with open(_SCRIPT) as fh:
        content = fh.read()

    assert "command -v uv" in content, (
        "Script does not check for uv availability"
    )
    assert "uv pip install" in content, (
        "Script does not use 'uv pip install' when uv is available"
    )
    assert "python" in content and "pip install" in content, (
        "Script does not have a python -m pip install fallback"
    )
