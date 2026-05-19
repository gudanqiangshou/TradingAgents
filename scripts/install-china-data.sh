#!/usr/bin/env bash
# Optional manual pre-install of the China data deps. Normally the akshare
# vendor auto-installs them on first A-share/HK use; this is just a fallback.
set -euo pipefail
exec "${PYTHON:-.venv/bin/python}" -c "import sys,subprocess; from tradingagents.dataflows._dep_bootstrap import CHINA_DATA_PINS; raise SystemExit(subprocess.run([sys.executable,'-m','pip','install',*CHINA_DATA_PINS]).returncode)"
