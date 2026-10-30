"""Whether this machine can open a native webview window.

Windows and macOS always can. On Linux pywebview needs GTK with WebKit2GTK, or Qt
with WebEngine, which pip cannot provide; without them ``kt app`` falls back to the
browser and ``kt doctor`` names the packages to install.
"""

import importlib
import sys

INSTALL_HINT = (
    "No native webview backend found. Install GTK with WebKit2GTK "
    "(Debian/Ubuntu: `sudo apt install python3-gi gir1.2-webkit2-4.1`; "
    "Fedora: `sudo dnf install python3-gobject webkit2gtk4.1`) "
    "or Qt WebEngine (`pip install pywebview[qt]`)."
)

_QT_ENGINES = (
    "PyQt6.QtWebEngineWidgets",
    "PyQt5.QtWebEngineWidgets",
    "PySide6.QtWebEngineWidgets",
    "PySide2.QtWebEngineWidgets",
)


def _importable(name: str) -> bool:
    try:
        importlib.import_module(name)
    except Exception:
        return False
    return True


def gtk_ready() -> bool:
    try:
        gi = importlib.import_module("gi")
    except Exception:
        return False
    try:
        gi.require_version("Gtk", "3.0")
    except (ValueError, AttributeError):
        return False
    for version in ("4.1", "4.0"):
        try:
            gi.require_version("WebKit2", version)
            return True
        except (ValueError, AttributeError):
            continue
    return False


def qt_ready() -> bool:
    return _importable("qtpy") and any(_importable(name) for name in _QT_ENGINES)


def native_window_available() -> bool:
    """Whether pywebview has a backend to draw a window with."""
    if not sys.platform.startswith("linux"):
        return True
    return gtk_ready() or qt_ready()


def describe() -> str:
    """One line for ``kt doctor``."""
    if native_window_available():
        return "native window available"
    return f"browser fallback. {INSTALL_HINT}"
