"""
On-demand dependency bootstrap helper.

Installs a missing Python package at runtime via pip, exactly once per process
per import_name.  Designed to be called by adapters that need optional heavy
dependencies (e.g. akshare) without bloating the base install or crashing the
production web service when the dependency is absent.

Thread-safe: uses a module-level lock with double-checked locking so that
concurrent callers never trigger more than one pip invocation for the same
package.

Fail-safe: all subprocess/import errors are translated to DependencyUnavailable;
no other exception type escapes this module.

Stdlib-only: importlib, subprocess, sys, threading, time, logging, types.
"""

import importlib
import logging
import subprocess
import sys
import threading
import time
import types

__all__ = ["DependencyUnavailable", "CHINA_DATA_PINS", "ensure"]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Version pins — single source of truth reused by tests and install scripts.
# Floors (not exact versions): akshare scrapes web endpoints and releases often;
# 1.17.86 is the minimum that fixes a known A-share news KeyError.
# curl_cffi 0.7.0 is the minimum that exposes the API akshare 1.17.86 requires.
# ---------------------------------------------------------------------------
CHINA_DATA_PINS: list[str] = [
    "akshare>=1.17.86",
    "curl_cffi>=0.7.0",
]

# ---------------------------------------------------------------------------
# Module-level state — all guarded by _install_lock
# ---------------------------------------------------------------------------
_install_lock = threading.Lock()

# Names that have already been attempted (and failed) — never retry pip for these.
_failed_installs: set[str] = set()


# ---------------------------------------------------------------------------
# Public exception
# ---------------------------------------------------------------------------

class DependencyUnavailable(RuntimeError):
    """Raised when a required dependency cannot be made importable."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def ensure(
    import_name: str,
    pip_specs: list[str] | None = None,
    *,
    timeout: int = 600,
) -> types.ModuleType:
    """
    Ensure *import_name* is importable, installing it via pip if necessary.

    Parameters
    ----------
    import_name:
        The Python module name to import (e.g. ``"akshare"``).
    pip_specs:
        Pip requirement specs to install.  Defaults to ``CHINA_DATA_PINS``.
    timeout:
        Maximum seconds to wait for the pip subprocess.

    Returns
    -------
    The imported module object.

    Raises
    ------
    DependencyUnavailable
        If installation fails or the module still cannot be imported after
        a successful pip run.  Also raised immediately (without calling pip)
        if a previous install attempt for the same *import_name* already
        failed in this process.
    """
    # --- Fast path: already importable ---
    try:
        return importlib.import_module(import_name)
    except ImportError:
        pass

    # --- Slow path: acquire lock, double-check, then install ---
    with _install_lock:
        # Double-checked locking: another thread may have installed it.
        try:
            return importlib.import_module(import_name)
        except ImportError:
            pass

        # Single-flight: if we already tried (and failed), bail immediately.
        if import_name in _failed_installs:
            raise DependencyUnavailable(
                f"{import_name}: pip install previously failed in this process"
            )

        specs = pip_specs if pip_specs is not None else CHINA_DATA_PINS
        cmd = [sys.executable, "-m", "pip", "install", "--quiet", *specs]

        logger.info("installing %s via %s", specs, sys.executable)
        t0 = time.monotonic()

        try:
            result = subprocess.run(
                cmd,
                timeout=timeout,
                capture_output=True,
                text=True,
            )
        except subprocess.TimeoutExpired as e:
            elapsed = time.monotonic() - t0
            logger.error(
                "pip install timed out after %.1fs for %s", elapsed, import_name
            )
            _failed_installs.add(import_name)
            raise DependencyUnavailable(f"{import_name}: pip install timed out") from e

        elapsed = time.monotonic() - t0
        logger.info(
            "pip install finished in %.1fs (returncode=%d) for %s",
            elapsed,
            result.returncode,
            import_name,
        )

        if result.returncode != 0:
            stderr_tail = (result.stderr or "")[-500:]
            logger.error(
                "pip install failed (returncode=%d) for %s; stderr tail: %s",
                result.returncode,
                import_name,
                stderr_tail,
            )
            _failed_installs.add(import_name)
            raise DependencyUnavailable(f"{import_name}: pip install failed") from None

        # Invalidate import caches so the newly-installed package is visible.
        importlib.invalidate_caches()

        try:
            module = importlib.import_module(import_name)
        except ImportError as e:
            logger.error(
                "pip install succeeded but '%s' still cannot be imported",
                import_name,
            )
            _failed_installs.add(import_name)
            raise DependencyUnavailable(
                f"{import_name}: installed but still not importable"
            ) from e

        return module
