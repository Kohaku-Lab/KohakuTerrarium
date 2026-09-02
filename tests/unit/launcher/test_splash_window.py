"""Unit tests for the launcher splash window driver.

Every GUI toolkit pywebview wraps insists on owning the process's main
thread. The original splash ran ``webview.start()`` on a background
thread, which raised immediately — but ``webview.create_window()`` had
already appended the splash to pywebview's process-global window list.
The framework's own ``webview.start()`` (same process, main thread,
right after the launcher hands off) then materialised that stale entry
as a frameless, always-on-top window polling a progress server that was
long gone: "Starting…" with a 0% bar the user could not close.

``run_with_splash`` inverts the threading: the toolkit loop owns the
main thread, the install work runs on a worker, and the window is
unregistered on every exit path. The fake ``webview`` module below
mirrors exactly the surface the driver uses (``windows`` registry,
``create_window``, ``start``, ``Window.destroy``, ``Window.events``).
"""

import http.client
import json
import sys
import threading
import types

import pytest

from kohakuterrarium.launcher import splash_window
from kohakuterrarium.launcher.splash_server import SplashServer

MAIN_THREAD_ERROR = "pywebview must be run on a main thread."


class _FakeEvent:
    def __init__(self) -> None:
        self._event = threading.Event()

    def set(self) -> None:
        self._event.set()

    def is_set(self) -> bool:
        return self._event.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._event.wait(timeout)


class _FakeWindow:
    def __init__(self, module, title: str, url: str | None, kwargs: dict) -> None:
        self.module = module
        self.title = title
        self.url = url
        self.kwargs = kwargs
        self.events = types.SimpleNamespace(shown=_FakeEvent(), closed=_FakeEvent())
        self.destroy_calls = 0
        self.destroyed_after: list[str] = []

    def destroy(self) -> None:
        # pywebview blocks destroy() until the window has been shown and
        # raises once it gives up waiting.
        if not self.events.shown.wait(20):
            raise RuntimeError("Main window failed to start")
        self.destroy_calls += 1
        self.destroyed_after = list(self.module.trace)
        self.module.trace.append("destroy")
        self.module.close(self)


class _FakeWebview(types.ModuleType):
    """Stand-in for the ``webview`` package with pywebview's threading rules."""

    def __init__(self, *, fail_start: str | None = None) -> None:
        super().__init__("webview")
        self.windows: list[_FakeWindow] = []
        self.created: list[_FakeWindow] = []
        self.start_threads: list[str] = []
        self.fail_start = fail_start
        self.trace: list[str] = []
        # Work functions block on this so the worker cannot outrun the
        # main thread; work that finishes first legitimately gets no
        # window at all.
        self.started = threading.Event()
        self._changed = threading.Event()

    def create_window(self, title, url=None, html=None, **kwargs):
        window = _FakeWindow(self, title, url, kwargs)
        self.windows.append(window)
        self.created.append(window)
        return window

    def start(self, func=None, args=None):
        self.start_threads.append(threading.current_thread().name)
        self.started.set()
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError(MAIN_THREAD_ERROR)
        if not self.windows:
            raise RuntimeError("You must create a window first")
        if self.fail_start is not None:
            raise RuntimeError(self.fail_start)
        for window in list(self.windows):
            window.events.shown.set()
        while self.windows:
            self._changed.wait(0.05)
            self._changed.clear()

    def close(self, window: _FakeWindow) -> None:
        if window in self.windows:
            self.windows.remove(window)
        window.events.closed.set()
        self._changed.set()


def _get_progress(endpoint: str) -> dict:
    host_port = endpoint.split("://", 1)[1].split("/", 1)[0]
    host, port = host_port.split(":", 1)
    conn = http.client.HTTPConnection(host, int(port), timeout=2.0)
    try:
        conn.request("GET", "/progress")
        resp = conn.getresponse()
        assert resp.status == 200
        return json.loads(resp.read())
    finally:
        conn.close()


@pytest.fixture
def no_tk(monkeypatch):
    # Tk would open a real window on a desktop test box; the Tk backend
    # is a visual fallback and stays out of the unit tier.
    monkeypatch.setitem(sys.modules, "tkinter", None)


@pytest.fixture
def fake_webview(monkeypatch, no_tk):
    fake = _FakeWebview()
    monkeypatch.setitem(sys.modules, "webview", fake)
    return fake


class TestRunWithSplashPywebview:
    def test_loop_owns_main_thread_and_window_is_torn_down_after_work(
        self, fake_webview
    ):
        seen: dict[str, object] = {}

        def work(server: SplashServer) -> str:
            seen["thread"] = threading.current_thread().name
            server.publish("Extracting", percent=42, message="tree")
            seen["frame"] = _get_progress(server.endpoint)
            seen["page_url"] = server.page_url
            assert fake_webview.started.wait(5)
            fake_webview.trace.append("work-done")
            return "installed"

        assert splash_window.run_with_splash(work) == "installed"

        assert seen["thread"] != "MainThread"
        assert seen["frame"]["phase"] == "Extracting"
        assert seen["frame"]["percent"] == 42.0
        assert fake_webview.start_threads == ["MainThread"]
        window = fake_webview.created[0]
        assert window.title == "KohakuTerrarium"
        # Same-origin page: no CORS / private-network gate between the
        # page and its progress feed.
        assert window.url == seen["page_url"]
        assert window.kwargs["frameless"] is True
        assert window.destroy_calls == 1
        assert window.destroyed_after == ["work-done"]
        assert fake_webview.windows == []

    def test_start_failure_unregisters_window_and_runs_work_once(
        self, monkeypatch, no_tk
    ):
        fake = _FakeWebview(fail_start="You must have either QT or GTK")
        monkeypatch.setitem(sys.modules, "webview", fake)
        calls: list[int] = []

        def work(server: SplashServer) -> int:
            calls.append(1)
            assert fake.started.wait(5)
            return 7

        assert splash_window.run_with_splash(work) == 7
        assert calls == [1]
        assert fake.start_threads == ["MainThread"]
        # The stale entry is what the framework's later start() would
        # resurrect as an unclosable zombie splash.
        assert fake.windows == []

    def test_off_main_thread_never_registers_a_window(self, fake_webview):
        outcome: dict[str, object] = {}

        def run() -> None:
            outcome["value"] = splash_window.run_with_splash(lambda server: "ok")

        thread = threading.Thread(target=run, name="not-main")
        thread.start()
        thread.join(timeout=10)
        assert not thread.is_alive()
        assert outcome["value"] == "ok"
        assert fake_webview.created == []
        assert fake_webview.start_threads == []
        assert fake_webview.windows == []

    def test_work_exception_propagates_and_stops_server(self, fake_webview):
        captured: dict[str, str] = {}

        def work(server: SplashServer) -> None:
            captured["endpoint"] = server.endpoint
            assert fake_webview.started.wait(5)
            raise ValueError("extract failed")

        with pytest.raises(ValueError, match="extract failed"):
            splash_window.run_with_splash(work)

        assert fake_webview.windows == []
        assert fake_webview.created[0].destroy_calls == 1
        with pytest.raises(OSError):
            _get_progress(captured["endpoint"])


class TestRunWithSplashHeadless:
    def test_no_backend_still_runs_work_with_live_server(self, monkeypatch, no_tk):
        monkeypatch.setitem(sys.modules, "webview", None)
        seen: dict[str, object] = {}

        def work(server: SplashServer) -> str:
            server.publish("Ready", percent=100, status="ok")
            seen["frame"] = _get_progress(server.endpoint)
            return "done"

        assert splash_window.run_with_splash(work) == "done"
        assert seen["frame"]["status"] == "ok"
        assert seen["frame"]["percent"] == 100.0
