"""Platform gating for optional desktop chrome."""

import sys
import threading
from types import SimpleNamespace

import pytest

from kohakuterrarium.serving.desktop import chrome as desktop_chrome


def test_non_windows_does_not_load_winforms_or_touch_window(monkeypatch):
    monkeypatch.setattr(desktop_chrome.sys, "platform", "linux")
    desktop_chrome.configure_desktop_chrome(object())
    assert "kohakuterrarium.serving.desktop.titlebar" not in sys.modules


def test_old_document_bridge_calls_cannot_overwrite_new_document(monkeypatch):
    event_module = pytest.importorskip("webview.event")
    bridge = pytest.importorskip("webview.util").js_bridge_call
    monkeypatch.setattr(desktop_chrome.sys, "platform", "win32")
    monkeypatch.setattr(desktop_chrome, "_enable_per_monitor_dpi", lambda: True)
    queue, applied = [], []
    queued = threading.Event()
    returned = threading.Event()

    def enqueue(dark, is_current):
        queue.append((dark, is_current))
        queued.set()
        return True

    window = SimpleNamespace(
        _functions={},
        _js_api=None,
        _desktop_titlebar=SimpleNamespace(set_dark=enqueue),
        evaluate_js=lambda script: returned.set() if '"late_old"' in script else None,
    )
    window.expose = lambda *functions: window._functions.update(
        {f.__name__: f for f in functions}
    )
    window.events = SimpleNamespace(
        **{
            name: event_module.Event(window, True)
            for name in ("before_show", "before_load", "loaded")
        }
    )
    desktop_chrome.configure_desktop_chrome(window)
    window.events.before_load.set()
    bridge(window, "set_desktop_theme", [False, 1], "old")
    assert queued.wait(2)
    queued.clear()
    window.events.before_load.set()
    bridge(window, "set_desktop_theme", [True, 2], "new")
    assert queued.wait(2)
    for dark, is_current in reversed(queue):
        if is_current():
            applied.append(dark)
    assert applied == [True]
    returned.clear()
    bridge(window, "set_desktop_theme", [False, 1], "late_old")
    assert returned.wait(2)
    assert len(queue) == 2


def test_registers_before_show_without_loading_native_dependencies(monkeypatch):
    monkeypatch.setattr(desktop_chrome.sys, "platform", "win32")
    calls = []
    monkeypatch.setattr(
        desktop_chrome, "_enable_per_monitor_dpi", lambda: calls.append("dpi")
    )

    class Event:
        def __iadd__(self, callback):
            calls.append(callback.__name__)
            return self

    exposed = []
    window = SimpleNamespace(
        events=SimpleNamespace(
            before_show=Event(), before_load=Event(), loaded=Event()
        ),
        expose=lambda *functions: exposed.extend(functions),
    )
    desktop_chrome.configure_desktop_chrome(window)
    assert calls == ["dpi", "before_show", "before_load", "loaded"]
    assert exposed[0](True, 0) is False
    applied = []
    window._desktop_titlebar = SimpleNamespace(
        set_dark=lambda dark, is_current: applied.append(dark) or is_current()
    )
    assert exposed[0]("false", 0) is False
    assert exposed[0](False, "0") is False
    assert exposed[0](False, 0) is True
    assert applied == [False]
    assert "kohakuterrarium.serving.desktop.titlebar" not in sys.modules
