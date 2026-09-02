"""Splash window driven from the main thread — pywebview primary, Tk fallback.

Every toolkit pywebview wraps insists on owning the process's main
thread: ``webview.start()`` raises when called anywhere else. Running
the install work on the main thread with the splash on a helper thread
therefore never showed a window — and ``webview.create_window()`` had
already put the splash into pywebview's process-global window list. The
framework calls ``webview.start()`` on this same thread once the
launcher hands off, and that call materialises every registered window:
the dead splash came back as a frameless, always-on-top page polling a
progress server that was long gone, with no way for the user to close it.

:func:`run_with_splash` inverts the threading. The caller's ``work``
runs on a worker thread; the main thread hosts the toolkit's event loop
until the work finishes; the window is closed and unregistered before
the function returns, on every exit path. Backends are tried in order:
pywebview, Tk, headless (progress is only logged). Whichever backend
runs, ``work`` executes exactly once and its result or exception is
handed back to the caller.
"""

import threading
from pathlib import Path
from typing import Any, Callable, TypeVar

from kohakuterrarium.launcher.log import get_logger
from kohakuterrarium.launcher.splash_server import SplashServer

_HTML_PATH = Path(__file__).parent / "splash.html"

T = TypeVar("T")


class _Outcome:
    """Slot the worker thread fills in; ``done`` fires once it is final."""

    def __init__(self) -> None:
        self.done = threading.Event()
        self.value: Any = None
        self.error: BaseException | None = None

    def result(self) -> Any:
        if self.error is not None:
            raise self.error
        return self.value


def _run_work(
    work: Callable[[SplashServer], Any], server: SplashServer, outcome: _Outcome
) -> None:
    try:
        outcome.value = work(server)
    except BaseException as e:
        outcome.error = e
    finally:
        outcome.done.set()


def _is_main_thread() -> bool:
    return threading.current_thread() is threading.main_thread()


# ── pywebview backend ───────────────────────────────────────────────


def _unregister(webview_module: Any, window: Any) -> None:
    """Drop ``window`` from pywebview's process-global registry.

    pywebview only forgets a window once its toolkit reports it closed.
    One that never opened (``start()`` failed, or returned before it was
    shown) stays listed and would be materialised by the next ``start()``
    in this process — the framework's main window.
    """
    try:
        registry = webview_module.windows
        while window in registry:
            registry.remove(window)
    except Exception:
        pass


def _destroy_when_done(
    window: Any, outcome: _Outcome, loop_finished: threading.Event
) -> None:
    """Close the splash once the work is over, but only if it opened.

    ``destroy()`` blocks until the window has been shown and raises when
    it gives up; waiting on ``shown`` here instead means a loop that
    never opened (backend failure, user closed it early) releases this
    thread promptly, while a window that opens late still gets closed.
    """
    outcome.done.wait()
    while not window.events.shown.is_set():
        if loop_finished.wait(0.2):
            return
    try:
        window.destroy()
    except Exception as e:
        get_logger().warning("splash: pywebview destroy failed: %s", e)


def _run_pywebview(server: SplashServer, outcome: _Outcome) -> bool:
    if not _is_main_thread():
        return False
    try:
        import webview  # type: ignore
    except ImportError:
        return False

    log = get_logger()
    try:
        window = webview.create_window(
            "KohakuTerrarium",
            url=server.page_url,
            width=420,
            height=260,
            frameless=True,
            easy_drag=True,
            resizable=False,
            on_top=True,
        )
    except Exception as e:
        log.warning("splash: pywebview backend failed: %s", e)
        return False
    loop_finished = threading.Event()
    threading.Thread(
        target=_destroy_when_done,
        args=(window, outcome, loop_finished),
        name="kt-splash-closer",
        daemon=True,
    ).start()
    try:
        webview.start()
    except Exception as e:
        log.warning("splash: pywebview backend failed: %s", e)
        return False
    finally:
        loop_finished.set()
        _unregister(webview, window)
    log.info("splash: pywebview window closed")
    return True


# ── Tk backend ──────────────────────────────────────────────────────


def _run_tk(server: SplashServer, outcome: _Outcome) -> bool:
    if not _is_main_thread():
        return False
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError:
        return False

    log = get_logger()
    try:
        root = tk.Tk()
    except Exception as e:
        log.warning("splash: tk backend failed: %s", e)
        return False
    try:
        root.title("KohakuTerrarium")
        root.geometry("420x180")
        tk.Label(root, text="KohakuTerrarium — setting up", font=("", 12, "bold")).pack(
            pady=(20, 8)
        )
        phase_var = tk.StringVar(value="Starting…")
        tk.Label(root, textvariable=phase_var).pack(pady=(0, 8))
        bar = ttk.Progressbar(root, length=320, mode="determinate", maximum=100)
        bar.pack(pady=(0, 8))
        msg_var = tk.StringVar(value="")
        tk.Label(root, textvariable=msg_var, fg="#888", font=("Menlo", 9)).pack()

        def _poll() -> None:
            frame = server.snapshot()
            phase_var.set(frame.phase or "Starting…")
            bar["value"] = max(0, min(100, frame.percent))
            msg_var.set(frame.message or "")
            if outcome.done.is_set():
                root.destroy()
                return
            root.after(250, _poll)

        root.after(50, _poll)
        root.mainloop()
    except Exception as e:
        log.warning("splash: tk backend failed: %s", e)
        try:
            root.destroy()
        except Exception:
            pass
        return False
    log.info("splash: Tk window closed")
    return True


# ── Public entry ────────────────────────────────────────────────────


def run_with_splash(work: Callable[[SplashServer], T]) -> T:
    """Run ``work`` on a worker thread behind a splash window.

    The main thread hosts the toolkit's event loop for the duration of
    ``work``; the window closes when ``work`` returns or raises, and the
    result (or exception) is handed back here. ``work`` publishes its
    progress through the :class:`SplashServer` it receives.
    """
    server = SplashServer(page_html=_HTML_PATH.read_text(encoding="utf-8")).start()
    outcome = _Outcome()
    worker = threading.Thread(
        target=_run_work,
        args=(work, server, outcome),
        name="kt-launcher-work",
        daemon=True,
    )
    try:
        worker.start()
        shown = False
        for backend in (_run_pywebview, _run_tk):
            # Work that already finished gets no window flashed at it.
            if shown or outcome.done.is_set():
                break
            shown = backend(server, outcome)
        if not shown and not outcome.done.is_set():
            get_logger().info(
                "splash: no UI backend available — progress will be logged only"
            )
        outcome.done.wait()
    finally:
        server.stop()
    return outcome.result()


__all__ = ["run_with_splash"]
