"""Native webview backend detection (Linux needs GTK/WebKit2 or Qt WebEngine)."""

import sys
from types import SimpleNamespace

import pytest

from kohakuterrarium.serving.desktop import webview_probe as probe


@pytest.fixture
def linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_windows_and_macos_always_have_a_window(monkeypatch, platform):
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(probe, "gtk_ready", lambda: pytest.fail("probed GTK"))
    assert probe.native_window_available() is True
    assert probe.describe() == "native window available"


def test_linux_without_gtk_or_qt_falls_back_and_names_the_packages(monkeypatch, linux):
    monkeypatch.setattr(probe, "gtk_ready", lambda: False)
    monkeypatch.setattr(probe, "qt_ready", lambda: False)

    assert probe.native_window_available() is False
    text = probe.describe()
    assert text.startswith("browser fallback")
    assert "gir1.2-webkit2-4.1" in text and "pywebview[qt]" in text


@pytest.mark.parametrize("gtk,qt", [(True, False), (False, True)])
def test_either_backend_is_enough(monkeypatch, linux, gtk, qt):
    monkeypatch.setattr(probe, "gtk_ready", lambda: gtk)
    monkeypatch.setattr(probe, "qt_ready", lambda: qt)
    assert probe.native_window_available() is True


def _fake_modules(monkeypatch, importable):
    def import_module(name):
        if name in importable:
            return importable[name]
        raise ImportError(name)

    monkeypatch.setattr(probe.importlib, "import_module", import_module)


class _Gi:
    def __init__(self, versions):
        self.versions = versions
        self.required = []

    def require_version(self, namespace, version):
        if self.versions.get(namespace) != version and namespace != "Gtk":
            raise ValueError(f"{namespace} {version} unavailable")
        self.required.append((namespace, version))


def test_gtk_needs_gi_gtk3_and_a_webkit2_version(monkeypatch):
    _fake_modules(monkeypatch, {"gi": _Gi({"WebKit2": "4.0"})})
    assert probe.gtk_ready() is True
    _fake_modules(monkeypatch, {"gi": _Gi({"WebKit2": "4.1"})})
    assert probe.gtk_ready() is True
    _fake_modules(monkeypatch, {"gi": _Gi({})})
    assert probe.gtk_ready() is False
    _fake_modules(monkeypatch, {})
    assert probe.gtk_ready() is False


def test_qt_needs_qtpy_and_a_web_engine(monkeypatch):
    _fake_modules(monkeypatch, {"qtpy": SimpleNamespace()})
    assert probe.qt_ready() is False
    _fake_modules(
        monkeypatch,
        {"qtpy": SimpleNamespace(), "PySide6.QtWebEngineWidgets": SimpleNamespace()},
    )
    assert probe.qt_ready() is True
    _fake_modules(monkeypatch, {"PyQt6.QtWebEngineWidgets": SimpleNamespace()})
    assert probe.qt_ready() is False
