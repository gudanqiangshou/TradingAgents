"""
Tests for tradingagents/dataflows/_dep_bootstrap.py  (includes uv-pip fallback)

All tests are unit tests: zero real pip installs, zero real network calls.
subprocess.run and importlib.import_module are mocked throughout.

Patching strategy: patch within the module under test's namespace, i.e.
"tradingagents.dataflows._dep_bootstrap.importlib" and
"tradingagents.dataflows._dep_bootstrap.subprocess".

Fixture `reset_bootstrap` clears module-level global state between tests
so tests are fully independent.
"""

import importlib as _real_importlib
import subprocess as _real_subprocess
import sys
import threading
import time
import types
import unittest.mock as mock
from unittest.mock import MagicMock, patch

import pytest

# Module under test path
_MOD_PATH = "tradingagents.dataflows._dep_bootstrap"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fake_module(name: str = "fakepkg") -> types.ModuleType:
    """Return a real ModuleType so isinstance checks work."""
    return types.ModuleType(name)


def _import_bootstrap():
    """Import (or re-import) the module under test. Called AFTER fixture cleanup."""
    return _real_importlib.import_module(_MOD_PATH)


# ---------------------------------------------------------------------------
# Fixture: reset module global state between tests
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_bootstrap():
    """
    Remove _dep_bootstrap from sys.modules before and after each test so that
    module-level globals (lock, attempted set) are reset to fresh objects.
    """
    for key in list(sys.modules.keys()):
        if "_dep_bootstrap" in key:
            del sys.modules[key]
    yield
    for key in list(sys.modules.keys()):
        if "_dep_bootstrap" in key:
            del sys.modules[key]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_returns_module_without_pip_when_already_importable():
    """
    If the package is already importable, ensure() must return it immediately
    and must NEVER call subprocess.run.
    """
    # First, import the module under test so it's in sys.modules
    mod = _import_bootstrap()

    fake_pkg = _make_fake_module("somepkg")

    mock_importlib = MagicMock(wraps=_real_importlib)
    mock_importlib.import_module.return_value = fake_pkg

    with patch(f"{_MOD_PATH}.importlib", mock_importlib), \
         patch(f"{_MOD_PATH}.subprocess") as mock_subprocess:

        result = mod.ensure("somepkg")

    assert result is fake_pkg
    mock_subprocess.run.assert_not_called()


@pytest.mark.unit
def test_installs_then_imports_on_missing():
    """
    When import_module raises ImportError on first call but succeeds after
    subprocess.run (returncode 0), ensure() should:
    - call subprocess.run exactly once
    - pass argv starting with [sys.executable, "-m", "pip", "install", "--quiet"]
    - include all CHINA_DATA_PINS specs in that argv
    - return the post-install module object
    """
    mod = _import_bootstrap()

    fake_pkg = _make_fake_module("akshare")
    # pip_ran tracks whether subprocess.run has been called.
    # import_module raises ImportError until pip has run, then returns the module.
    state = {"pip_ran": False}

    def side_effect_import(name, *args, **kwargs):
        if not state["pip_ran"]:
            raise ImportError(f"No module named '{name}'")
        return fake_pkg

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stderr = ""

    def run_side_effect(*args, **kwargs):
        state["pip_ran"] = True
        return mock_proc

    mock_importlib = MagicMock(wraps=_real_importlib)
    mock_importlib.import_module.side_effect = side_effect_import
    mock_importlib.invalidate_caches = MagicMock()

    mock_subprocess = MagicMock()
    mock_subprocess.run.side_effect = run_side_effect
    mock_subprocess.TimeoutExpired = _real_subprocess.TimeoutExpired

    with patch(f"{_MOD_PATH}.importlib", mock_importlib), \
         patch(f"{_MOD_PATH}.subprocess", mock_subprocess), \
         patch(f"{_MOD_PATH}.shutil.which", return_value=None):

        result = mod.ensure("akshare")

    assert result is fake_pkg
    mock_subprocess.run.assert_called_once()
    mock_importlib.invalidate_caches.assert_called_once()

    argv = mock_subprocess.run.call_args[0][0]
    assert argv[0] == sys.executable
    assert argv[1:4] == ["-m", "pip", "install"]
    assert "--quiet" in argv

    for spec in mod.CHINA_DATA_PINS:
        assert spec in argv, f"Expected '{spec}' in pip argv but got: {argv}"


@pytest.mark.unit
def test_custom_pip_specs_used():
    """
    When pip_specs is provided explicitly, that list (not CHINA_DATA_PINS) is used.
    """
    mod = _import_bootstrap()

    fake_pkg = _make_fake_module("foo")
    state = {"pip_ran": False}

    def side_effect_import(name, *args, **kwargs):
        if not state["pip_ran"]:
            raise ImportError(f"No module named '{name}'")
        return fake_pkg

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stderr = ""

    def run_side_effect(*args, **kwargs):
        state["pip_ran"] = True
        return mock_proc

    mock_importlib = MagicMock(wraps=_real_importlib)
    mock_importlib.import_module.side_effect = side_effect_import
    mock_importlib.invalidate_caches = MagicMock()

    mock_subprocess = MagicMock()
    mock_subprocess.run.side_effect = run_side_effect
    mock_subprocess.TimeoutExpired = _real_subprocess.TimeoutExpired

    custom_specs = ["foo==1.2"]

    with patch(f"{_MOD_PATH}.importlib", mock_importlib), \
         patch(f"{_MOD_PATH}.subprocess", mock_subprocess):

        result = mod.ensure("foo", pip_specs=custom_specs)

    assert result is fake_pkg
    argv = mock_subprocess.run.call_args[0][0]
    assert "foo==1.2" in argv

    for spec in mod.CHINA_DATA_PINS:
        assert spec not in argv, (
            f"CHINA_DATA_PINS spec '{spec}' should not be in argv when custom specs given"
        )


@pytest.mark.unit
def test_pip_failure_raises_DependencyUnavailable_not_crash():
    """
    When subprocess.run returns returncode != 0, ensure() must raise
    DependencyUnavailable (not propagate a subprocess exception or sys.exit).
    """
    mod = _import_bootstrap()

    mock_importlib = MagicMock(wraps=_real_importlib)
    mock_importlib.import_module.side_effect = ImportError("no module")

    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.stderr = "ERROR: some pip error"

    mock_subprocess = MagicMock()
    mock_subprocess.run.return_value = mock_proc
    mock_subprocess.TimeoutExpired = _real_subprocess.TimeoutExpired

    with patch(f"{_MOD_PATH}.importlib", mock_importlib), \
         patch(f"{_MOD_PATH}.subprocess", mock_subprocess):

        with pytest.raises(mod.DependencyUnavailable):
            mod.ensure("badpkg")


@pytest.mark.unit
def test_timeout_raises_DependencyUnavailable():
    """
    When subprocess.run raises subprocess.TimeoutExpired, ensure() must catch
    it and raise DependencyUnavailable (not let TimeoutExpired propagate).
    """
    mod = _import_bootstrap()

    mock_importlib = MagicMock(wraps=_real_importlib)
    mock_importlib.import_module.side_effect = ImportError("no module")

    mock_subprocess = MagicMock()
    mock_subprocess.TimeoutExpired = _real_subprocess.TimeoutExpired
    mock_subprocess.run.side_effect = _real_subprocess.TimeoutExpired(
        cmd="pip", timeout=600
    )

    with patch(f"{_MOD_PATH}.importlib", mock_importlib), \
         patch(f"{_MOD_PATH}.subprocess", mock_subprocess):

        with pytest.raises(mod.DependencyUnavailable):
            mod.ensure("slowpkg")


@pytest.mark.unit
def test_single_flight_no_repeat_pip_after_failure():
    """
    After a failed install for an import_name, subsequent ensure() calls for
    the SAME name must raise DependencyUnavailable WITHOUT calling subprocess.run
    again (call_count must remain 1).
    """
    mod = _import_bootstrap()

    mock_importlib = MagicMock(wraps=_real_importlib)
    mock_importlib.import_module.side_effect = ImportError("no module")

    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.stderr = "pip error"

    mock_subprocess = MagicMock()
    mock_subprocess.run.return_value = mock_proc
    mock_subprocess.TimeoutExpired = _real_subprocess.TimeoutExpired

    with patch(f"{_MOD_PATH}.importlib", mock_importlib), \
         patch(f"{_MOD_PATH}.subprocess", mock_subprocess):

        # First call: pip runs and fails
        with pytest.raises(mod.DependencyUnavailable):
            mod.ensure("mypkg")

        assert mock_subprocess.run.call_count == 1, (
            "pip should have been called once on first failure"
        )

        # Second call: must NOT call pip again
        with pytest.raises(mod.DependencyUnavailable):
            mod.ensure("mypkg")

        assert mock_subprocess.run.call_count == 1, (
            "pip must NOT be called again after first failure for same import_name"
        )


@pytest.mark.unit
def test_thread_safe_single_install():
    """
    When multiple threads call ensure() concurrently for the same missing
    package, subprocess.run must be called AT MOST ONCE (double-checked locking).
    """
    mod = _import_bootstrap()

    n_threads = 5
    fake_pkg = _make_fake_module("pkgx")
    install_completed = threading.Event()

    # import_module side effect: raises ImportError until install_completed,
    # then returns fake_pkg.
    # Note: this side_effect is called under the patched importlib mock so it
    # operates on the name string.
    _import_lock = threading.Lock()

    def side_effect_import(name, *args, **kwargs):
        with _import_lock:
            if not install_completed.is_set():
                raise ImportError(f"No module named '{name}'")
            return fake_pkg

    def slow_install(*args, **kwargs):
        time.sleep(0.05)
        install_completed.set()
        proc = MagicMock()
        proc.returncode = 0
        proc.stderr = ""
        return proc

    mock_importlib = MagicMock(wraps=_real_importlib)
    mock_importlib.import_module.side_effect = side_effect_import
    mock_importlib.invalidate_caches = MagicMock()

    mock_subprocess = MagicMock()
    mock_subprocess.run.side_effect = slow_install
    mock_subprocess.TimeoutExpired = _real_subprocess.TimeoutExpired

    results = []
    errors = []

    def worker():
        try:
            r = mod.ensure("pkgx")
            results.append(r)
        except Exception as e:
            errors.append(e)

    with patch(f"{_MOD_PATH}.importlib", mock_importlib), \
         patch(f"{_MOD_PATH}.subprocess", mock_subprocess):

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

    assert mock_subprocess.run.call_count <= 1, (
        f"subprocess.run called {mock_subprocess.run.call_count} times — "
        "double-checked locking broken"
    )
    assert not errors, f"Unexpected errors from threads: {errors}"
    assert len(results) == n_threads, (
        f"Expected {n_threads} successful results, got {len(results)}"
    )
    for r in results:
        assert r is fake_pkg


@pytest.mark.unit
def test_pip_succeeds_but_import_still_fails():
    """
    pip returns returncode==0 (broken/namespace wheel) but importlib.import_module
    STILL raises ImportError after install.  ensure() must:
    - raise DependencyUnavailable (not raw ImportError)
    - call subprocess.run exactly once
    - record the name in single-flight so a second call does NOT re-run pip
    """
    mod = _import_bootstrap()

    mock_importlib = MagicMock(wraps=_real_importlib)
    # import_module always raises ImportError — even after pip "succeeds"
    mock_importlib.import_module.side_effect = ImportError("No module named 'brokenpkg'")
    mock_importlib.invalidate_caches = MagicMock()

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stderr = ""

    mock_subprocess = MagicMock()
    mock_subprocess.run.return_value = mock_proc
    mock_subprocess.TimeoutExpired = _real_subprocess.TimeoutExpired

    with patch(f"{_MOD_PATH}.importlib", mock_importlib), \
         patch(f"{_MOD_PATH}.subprocess", mock_subprocess):

        # First call: pip runs (returncode 0) but import still fails
        with pytest.raises(mod.DependencyUnavailable):
            mod.ensure("brokenpkg")

        assert mock_subprocess.run.call_count == 1, (
            "subprocess.run should have been called exactly once on first attempt"
        )

        # Second call: single-flight must block re-running pip
        with pytest.raises(mod.DependencyUnavailable):
            mod.ensure("brokenpkg")

        assert mock_subprocess.run.call_count == 1, (
            "subprocess.run must NOT be called again after post-install ImportError "
            "(single-flight set should have recorded the failure)"
        )


# ---------------------------------------------------------------------------
# uv-pip fallback tests
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_uv_install_argv_when_uv_available():
    """When uv is on PATH, _install_argv returns uv pip install with --python <exe>."""
    import shutil as _real_shutil

    mod = _import_bootstrap()

    fake_pkg = _make_fake_module("akshare")
    state = {"pip_ran": False}

    def side_effect_import(name, *args, **kwargs):
        if not state["pip_ran"]:
            raise ImportError(f"No module named '{name}'")
        return fake_pkg

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stderr = ""

    def run_side_effect(*args, **kwargs):
        state["pip_ran"] = True
        return mock_proc

    mock_importlib = MagicMock(wraps=_real_importlib)
    mock_importlib.import_module.side_effect = side_effect_import
    mock_importlib.invalidate_caches = MagicMock()

    mock_subprocess = MagicMock()
    mock_subprocess.run.side_effect = run_side_effect
    mock_subprocess.TimeoutExpired = _real_subprocess.TimeoutExpired

    with patch(f"{_MOD_PATH}.importlib", mock_importlib), \
         patch(f"{_MOD_PATH}.subprocess", mock_subprocess), \
         patch(f"{_MOD_PATH}.shutil.which", return_value="/usr/local/bin/uv"):

        result = mod.ensure("akshare")

    assert result is fake_pkg
    mock_subprocess.run.assert_called_once()

    argv = mock_subprocess.run.call_args[0][0]
    assert argv[0] == "uv", f"Expected 'uv' as first element, got: {argv[0]}"
    assert argv[1:3] == ["pip", "install"], f"Expected ['pip', 'install'], got: {argv[1:3]}"
    assert "--python" in argv
    python_idx = argv.index("--python")
    assert argv[python_idx + 1] == sys.executable

    for spec in mod.CHINA_DATA_PINS:
        assert spec in argv, f"Expected '{spec}' in argv but got: {argv}"


@pytest.mark.unit
def test_pip_install_argv_when_uv_absent():
    """When uv is NOT on PATH, _install_argv returns python -m pip install --quiet (existing behavior)."""
    mod = _import_bootstrap()

    fake_pkg = _make_fake_module("akshare")
    state = {"pip_ran": False}

    def side_effect_import(name, *args, **kwargs):
        if not state["pip_ran"]:
            raise ImportError(f"No module named '{name}'")
        return fake_pkg

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stderr = ""

    def run_side_effect(*args, **kwargs):
        state["pip_ran"] = True
        return mock_proc

    mock_importlib = MagicMock(wraps=_real_importlib)
    mock_importlib.import_module.side_effect = side_effect_import
    mock_importlib.invalidate_caches = MagicMock()

    mock_subprocess = MagicMock()
    mock_subprocess.run.side_effect = run_side_effect
    mock_subprocess.TimeoutExpired = _real_subprocess.TimeoutExpired

    with patch(f"{_MOD_PATH}.importlib", mock_importlib), \
         patch(f"{_MOD_PATH}.subprocess", mock_subprocess), \
         patch(f"{_MOD_PATH}.shutil.which", return_value=None):

        result = mod.ensure("akshare")

    assert result is fake_pkg
    mock_subprocess.run.assert_called_once()

    argv = mock_subprocess.run.call_args[0][0]
    assert argv[0] == sys.executable, f"Expected sys.executable as first element, got: {argv[0]}"
    assert argv[1:4] == ["-m", "pip", "install"], f"Expected ['-m', 'pip', 'install'], got: {argv[1:4]}"
    assert "--quiet" in argv

    for spec in mod.CHINA_DATA_PINS:
        assert spec in argv, f"Expected '{spec}' in argv but got: {argv}"


# ---------------------------------------------------------------------------
# Fix 3: new catch-all tests
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_runtime_error_during_import_translates_to_dependency_unavailable():
    """A post-install import that raises RuntimeError (not ImportError) must be
    caught and translated to DependencyUnavailable, not propagate raw.

    Context: the fast-path (before the lock) only catches ImportError — if the
    module was never installed it can't raise RuntimeError there.  The post-install
    retry path catches all exceptions via 'except Exception'.
    """
    mod = _import_bootstrap()

    # Fast path: first import raises ImportError (module missing — normal)
    # Post-install retry: raises RuntimeError (simulates a broken/misconfigured package)
    state = {"pip_ran": False}

    def side_effect_import(name, *args, **kwargs):
        if not state["pip_ran"]:
            raise ImportError(f"No module named '{name}'")
        # After pip "succeeds", the module raises RuntimeError on import
        raise RuntimeError("boom — package misconfigured")

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stderr = ""

    def run_side_effect(*args, **kwargs):
        state["pip_ran"] = True
        return mock_proc

    mock_importlib = MagicMock(wraps=_real_importlib)
    mock_importlib.import_module.side_effect = side_effect_import
    mock_importlib.invalidate_caches = MagicMock()

    mock_subprocess = MagicMock()
    mock_subprocess.run.side_effect = run_side_effect
    mock_subprocess.TimeoutExpired = _real_subprocess.TimeoutExpired

    with patch(f"{_MOD_PATH}.importlib", mock_importlib), \
         patch(f"{_MOD_PATH}.subprocess", mock_subprocess):

        with pytest.raises(mod.DependencyUnavailable):
            mod.ensure("brokenpkg")


@pytest.mark.unit
def test_file_not_found_during_install_translates_to_dependency_unavailable():
    """FileNotFoundError when launching subprocess (installer binary gone after
    which() check) must be caught and translated to DependencyUnavailable.
    """
    mod = _import_bootstrap()

    mock_importlib = MagicMock(wraps=_real_importlib)
    mock_importlib.import_module.side_effect = ImportError("no module")

    mock_subprocess = MagicMock()
    mock_subprocess.TimeoutExpired = _real_subprocess.TimeoutExpired
    mock_subprocess.run.side_effect = FileNotFoundError("uv missing")

    with patch(f"{_MOD_PATH}.importlib", mock_importlib), \
         patch(f"{_MOD_PATH}.subprocess", mock_subprocess):

        with pytest.raises(mod.DependencyUnavailable):
            mod.ensure("mypkg")

    # Must have recorded the failure so it won't retry
    assert mock_subprocess.run.call_count == 1


@pytest.mark.unit
def test_os_error_during_install_translates_to_dependency_unavailable():
    """Generic OSError during subprocess launch → DependencyUnavailable."""
    mod = _import_bootstrap()

    mock_importlib = MagicMock(wraps=_real_importlib)
    mock_importlib.import_module.side_effect = ImportError("no module")

    mock_subprocess = MagicMock()
    mock_subprocess.TimeoutExpired = _real_subprocess.TimeoutExpired
    mock_subprocess.run.side_effect = OSError("permission denied")

    with patch(f"{_MOD_PATH}.importlib", mock_importlib), \
         patch(f"{_MOD_PATH}.subprocess", mock_subprocess):

        with pytest.raises(mod.DependencyUnavailable):
            mod.ensure("mypkg")


# ---------------------------------------------------------------------------
# Fix 4: fast-path non-ImportError translates to DependencyUnavailable
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_fast_path_non_import_error_translates_to_dependency_unavailable():
    """If import raises RuntimeError (not ImportError) in the fast-path,
    ensure() must raise DependencyUnavailable, not RuntimeError.
    subprocess.run must NOT be called (we never reach the install step).
    """
    mod = _import_bootstrap()

    mock_importlib = MagicMock(wraps=_real_importlib)
    # Fast path: import raises RuntimeError (not ImportError)
    mock_importlib.import_module.side_effect = RuntimeError("boom — bad module")

    mock_subprocess = MagicMock()
    mock_subprocess.TimeoutExpired = _real_subprocess.TimeoutExpired

    with patch(f"{_MOD_PATH}.importlib", mock_importlib), \
         patch(f"{_MOD_PATH}.subprocess", mock_subprocess):

        with pytest.raises(mod.DependencyUnavailable) as exc_info:
            mod.ensure("akshare")

    # Must raise DependencyUnavailable (not RuntimeError)
    assert isinstance(exc_info.value, mod.DependencyUnavailable), (
        f"Expected DependencyUnavailable, got {type(exc_info.value)}"
    )
    # subprocess.run must NOT have been called — we never reached install
    mock_subprocess.run.assert_not_called(), (
        "subprocess.run was called — fast-path should have raised before install"
    )


# ---------------------------------------------------------------------------
# audit-v3: lock-inner double-check non-ImportError translates to
# DependencyUnavailable (Critical 1 — third symmetric path)
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_lock_inner_non_import_error_translates_to_dependency_unavailable():
    """Lock-inner double-check: first import raises ImportError (expected; forces
    lock acquisition), then the second import attempt (inside the lock) raises
    RuntimeError.  ensure() must:
    - raise DependencyUnavailable (NOT RuntimeError)
    - NOT call subprocess.run (we exit before reaching the install step)
    """
    mod = _import_bootstrap()

    call_count = {"n": 0}

    def side_effect_import(name, *args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # Fast path: normal ImportError (module not installed yet)
            raise ImportError(f"No module named '{name}'")
        # Lock-inner retry: unexpected non-ImportError (e.g. broken sys.modules entry)
        raise RuntimeError("inner boom")

    mock_importlib = MagicMock(wraps=_real_importlib)
    mock_importlib.import_module.side_effect = side_effect_import

    mock_subprocess = MagicMock()
    mock_subprocess.TimeoutExpired = _real_subprocess.TimeoutExpired

    with patch(f"{_MOD_PATH}.importlib", mock_importlib), \
         patch(f"{_MOD_PATH}.subprocess", mock_subprocess):

        with pytest.raises(mod.DependencyUnavailable):
            mod.ensure("brokenpkg")

    # subprocess.run must NOT have been called — we exited before install
    mock_subprocess.run.assert_not_called()
