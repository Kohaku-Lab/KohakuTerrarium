"""Window-free Windows DPI initialization regressions."""

import subprocess
import sys

import pytest


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DPI APIs")
def test_splash_configures_dpi_before_toolkit_and_main_reuses_it():
    code = """
import ctypes
import time
from kohakuterrarium.launcher import splash_window
from kohakuterrarium.serving.desktop.chrome import _enable_per_monitor_dpi
user = ctypes.WinDLL('user32', use_last_error=True)
user.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
user.AreDpiAwarenessContextsEqual.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
def backend(server, outcome):
    user.SetProcessDPIAware()
    assert user.AreDpiAwarenessContextsEqual(user.GetThreadDpiAwarenessContext(), ctypes.c_void_p(-4))
    server.publish('configured')
    return True
splash_window._run_pywebview = backend
def work(server):
    while server.snapshot().phase != 'configured':
        time.sleep(.01)
splash_window.run_with_splash(work)
assert _enable_per_monitor_dpi() is True
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=15
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DPI APIs")
def test_locked_system_dpi_is_reported_instead_of_claiming_success():
    code = """
import ctypes
from kohakuterrarium.serving.desktop.chrome import _enable_per_monitor_dpi
ctypes.WinDLL('user32').SetProcessDPIAware()
ctypes.WinDLL('user32').SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
from kohakuterrarium.utils.logging import enable_stderr_logging
enable_stderr_logging()
assert _enable_per_monitor_dpi() is False
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stderr
    assert "DPI" in result.stderr and "5" in result.stderr
