#!/usr/bin/env bash
# Optional manual pre-install of the China data deps.
# Normally the akshare vendor auto-installs them on first A-share/HK use;
# this script is a convenience fallback for CI / fresh environments.
#
# Uses uv pip install when uv is on PATH (uv venvs lack pip by default);
# falls back to python -m pip install otherwise — mirroring the runtime
# logic in tradingagents/dataflows/_dep_bootstrap.py:_install_argv().
#
# PYTHON env var overrides the Python interpreter (default: .venv/bin/python).
set -euo pipefail

PYTHON="${PYTHON:-.venv/bin/python}"

# Read pins from the single source of truth in _dep_bootstrap.
SPECS_PY="$("$PYTHON" -c \
    "from tradingagents.dataflows._dep_bootstrap import CHINA_DATA_PINS; \
     print(' '.join(CHINA_DATA_PINS))")"

if command -v uv >/dev/null 2>&1; then
    # shellcheck disable=SC2086
    exec uv pip install --python "$PYTHON" $SPECS_PY
else
    # shellcheck disable=SC2086
    exec "$PYTHON" -m pip install $SPECS_PY
fi
