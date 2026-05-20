"""Borderless splash window — pywebview primary, Tk fallback.

Both backends:

1. Spin up the :class:`SplashServer` so the window has a progress feed.
2. Inject ``window.SPLASH_ENDPOINT`` into the HTML / Tk title so the
   page knows where to poll.
3. Run in a background thread so the caller can drive ``publish()``
   from the main thread (where the actual install work happens).

If neither backend is available (headless Linux box, no pywebview,
no Tk), :func:`open_splash` returns a :class:`SplashServer` only and
the caller logs progress to stderr.
"""

import threading
from pathlib import Path

from kohakuterrarium.launcher.log import get_logger
from kohakuterrarium.launcher.splash_server import SplashServer

_HTML_PATH = Path(__file__).parent / "splash.html"


def _render_html(endpoint: str) -> str:
    template = _HTML_PATH.read_text(encoding="utf-8")
    # The page reads window.SPLASH_ENDPOINT — inject by appending a
    # tiny inline script BEFORE the existing polling script tag.
    inject = f'<script>window.SPLASH_ENDPOINT = "{endpoint}";</script>'
    needle = "<script>"
    if needle not in template:
        return template
    head, tail = template.split(needle, 1)
    return f"{head}{inject}{needle}{tail}"


def _try_pywebview(server: SplashServer) -> bool:
    try:
        import webview  # type: ignore
    except ImportError:
        return False

    html = _render_html(server.endpoint)
    # The splash window object is created on the background thread; the
    # box lets the main-thread ``stop()`` reach it.
    window_box: list = []

    def _run():
        try:
            window = webview.create_window(
                "KohakuTerrarium",
                html=html,
                width=420,
                height=260,
                frameless=True,
                easy_drag=True,
                resizable=False,
                on_top=True,
            )
            window_box.append(window)
            webview.start()
        except Exception as e:  # pragma: no cover - backend-specific
            get_logger().warning("splash: pywebview backend failed: %s", e)

    t = threading.Thread(target=_run, name="kt-splash-pywebview", daemon=True)
    t.start()

    def _close() -> None:
        # ``webview.start`` blocks the background thread until the last
        # window closes, so destroying the splash here also lets the
        # background thread exit and frees pywebview's global event
        # loop for the framework's main UI window later.
        if not window_box:
            return
        try:
            window_box[0].destroy()
        except Exception as e:  # pragma: no cover - backend-specific
            get_logger().warning("splash: pywebview destroy failed: %s", e)

    server.register_close_callback(_close)
    return True


def _try_tk(server: SplashServer) -> bool:
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError:
        return False

    root_box: list = []

    def _run():
        try:
            root = tk.Tk()
            root_box.append(root)
            root.title("KohakuTerrarium")
            root.geometry("420x180")
            tk.Label(
                root, text="KohakuTerrarium — setting up", font=("", 12, "bold")
            ).pack(pady=(20, 8))
            phase_var = tk.StringVar(value="Starting…")
            tk.Label(root, textvariable=phase_var).pack(pady=(0, 8))
            bar = ttk.Progressbar(root, length=320, mode="determinate", maximum=100)
            bar.pack(pady=(0, 8))
            msg_var = tk.StringVar(value="")
            tk.Label(root, textvariable=msg_var, fg="#888", font=("Menlo", 9)).pack()

            def _poll():
                f = server.snapshot()
                phase_var.set(f.phase or "Starting…")
                bar["value"] = max(0, min(100, f.percent))
                msg_var.set(f.message or "")
                if f.status in ("ok", "failed"):
                    root.after(800, root.destroy)
                    return
                root.after(250, _poll)

            root.after(50, _poll)
            root.mainloop()
        except Exception as e:  # pragma: no cover - backend-specific
            get_logger().warning("splash: tk backend failed: %s", e)

    t = threading.Thread(target=_run, name="kt-splash-tk", daemon=True)
    t.start()

    def _close() -> None:
        # Tk's in-loop self-destroy only fires on a terminal status; if
        # ``stop()`` is called before then (early exit, exception in
        # the install loop), the window would linger.
        if not root_box:
            return
        try:
            root_box[0].after(0, root_box[0].destroy)
        except Exception:  # pragma: no cover - root may already be gone
            pass

    server.register_close_callback(_close)
    return True


def open_splash() -> SplashServer:
    """Start the progress server and (if possible) a splash window.

    Returns the server unconditionally — callers always publish frames
    even when no UI backend is available, so the same code path runs
    headless (logs only).
    """
    server = SplashServer().start()
    if _try_pywebview(server):
        get_logger().info("splash: opened pywebview window")
        return server
    if _try_tk(server):
        get_logger().info("splash: opened Tk window (pywebview unavailable)")
        return server
    get_logger().info("splash: no UI backend available — progress will be logged only")
    return server


__all__ = ["open_splash"]
