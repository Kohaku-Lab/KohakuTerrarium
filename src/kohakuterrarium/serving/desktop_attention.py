"""Capability-gated native attention for the pywebview desktop shell."""

from __future__ import annotations

import ctypes
import sys
from typing import Any, Callable


def _native(window: Any) -> Any:
    return getattr(window, "native", None)


def _handle(window: Any) -> int | None:
    value = getattr(_native(window), "Handle", None)
    if value is None:
        return None
    try:
        if callable(getattr(value, "ToInt64", None)):
            value = value.ToInt64()
        elif callable(getattr(value, "ToInt32", None)):
            value = value.ToInt32()
        handle = int(value)
        return handle or None
    except (TypeError, ValueError, OverflowError):
        return None


def _windows_available(window: Any) -> bool:
    if sys.platform != "win32":
        return False
    try:
        return bool(_handle(window) and ctypes.windll.user32.FlashWindowEx)
    except Exception:
        return False


def _mac_available() -> bool:
    if sys.platform != "darwin":
        return False
    try:
        import AppKit  # noqa: F401
        from PyObjCTools import AppHelper  # noqa: F401

        return True
    except Exception:
        return False


def _linux_available(window: Any) -> bool:
    return sys.platform.startswith("linux") and callable(
        getattr(_native(window), "set_urgency_hint", None)
    )


def get_desktop_capabilities(window: Any) -> dict[str, bool | int | str]:
    return {
        "surface": "desktop",
        "protocol": 1,
        "nativeAttention": (
            _windows_available(window) or _mac_available() or _linux_available(window)
        ),
    }


def _active(window: Any) -> bool:
    if sys.platform == "win32":
        handle = _handle(window)
        return bool(handle and ctypes.windll.user32.GetForegroundWindow() == handle)
    if sys.platform == "darwin":
        from AppKit import NSApp

        app = NSApp()
        return bool(app and app.isActive())
    native = _native(window)
    for name in ("is_active", "has_focus"):
        value = getattr(native, name, None)
        if value is not None:
            return bool(value() if callable(value) else value)
    return False


def _request_windows(window: Any) -> bool:
    handle = _handle(window)
    if not handle:
        return False

    class FLASHWINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", ctypes.c_uint),
            ("hwnd", ctypes.c_void_p),
            ("dwFlags", ctypes.c_uint),
            ("uCount", ctypes.c_uint),
            ("dwTimeout", ctypes.c_uint),
        ]

    info = FLASHWINFO(ctypes.sizeof(FLASHWINFO), handle, 0x3, 3, 0)
    return bool(ctypes.windll.user32.FlashWindowEx(ctypes.byref(info)))


def _dispatch_macos(callback: Callable[[], None]) -> bool:
    try:
        from PyObjCTools import AppHelper

        AppHelper.callAfter(callback)
        return True
    except Exception:
        return False


def _request_macos() -> bool:
    def request() -> None:
        from AppKit import NSApp, NSApplication

        app = NSApp() or NSApplication.sharedApplication()
        app.requestUserAttention_(0)

    return _dispatch_macos(request)


def _dispatch_linux(callback: Callable[[], None]) -> bool:
    try:
        from gi.repository import GLib

        GLib.idle_add(callback)
        return True
    except Exception:
        return False


def _request_linux(window: Any) -> bool:
    setter = getattr(_native(window), "set_urgency_hint", None)
    if not callable(setter):
        return False
    return _dispatch_linux(lambda: setter(True))


def request_desktop_attention(window: Any) -> bool:
    try:
        if _active(window):
            return False
        if sys.platform == "win32":
            return _request_windows(window)
        if sys.platform == "darwin":
            return _request_macos()
        if sys.platform.startswith("linux"):
            return _request_linux(window)
        return False
    except Exception:
        return False


def expose_desktop_attention(window: Any) -> None:
    def get_capabilities() -> dict[str, bool | int | str]:
        try:
            return get_desktop_capabilities(window)
        except Exception:
            return {"surface": "desktop", "protocol": 1, "nativeAttention": False}

    get_capabilities.__name__ = "get_desktop_capabilities"

    def request_attention() -> bool:
        try:
            return request_desktop_attention(window)
        except Exception:
            return False

    request_attention.__name__ = "request_desktop_attention"
    window.expose(get_capabilities, request_attention)
