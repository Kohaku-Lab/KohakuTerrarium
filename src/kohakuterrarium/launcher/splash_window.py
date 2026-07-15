"""Open a borderless splash with pywebview or a Tk fallback.

Both backends run off the caller's thread and consume progress from a local
:class:`SplashServer`. When no graphical backend is available, callers still
receive the server and can follow the same publishing path headlessly.
"""

import threading
from pathlib import Path

from kohakuterrarium.launcher.log import get_logger
from kohakuterrarium.launcher.splash_server import SplashServer

_HTML_PATH = Path(__file__).parent / "splash.html"


def _render_html(endpoint: str) -> str:
    """Inject the progress endpoint before the template's polling script."""
    template = _HTML_PATH.read_text(encoding="utf-8")
    # The assignment must precede the polling script that reads it.
    inject = f'<script>window.SPLASH_ENDPOINT = "{endpoint}";</script>'
    needle = "<script>"
    if needle not in template:
        return template
    head, tail = template.split(needle, 1)
    return f"{head}{inject}{needle}{tail}"


def _try_pywebview(server: SplashServer) -> bool:
    """Start a pywebview splash when the optional backend is available."""
    try:
        import webview  # type: ignore
    except ImportError:
        return False

    html = _render_html(server.endpoint)
    # The window is created asynchronously, so the server's close callback
    # needs this shared container to reach it.
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
        # Destroying the splash releases pywebview's global event loop before
        # the main application creates another window.
        if not window_box:
            return
        try:
            window_box[0].destroy()
        except Exception as e:  # pragma: no cover - backend-specific
            get_logger().warning("splash: pywebview destroy failed: %s", e)

    server.register_close_callback(_close)
    return True


def _try_tk(server: SplashServer) -> bool:
    """Start a Tk splash when the standard GUI backend is available."""
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
        # Explicit teardown covers exits that occur before a terminal frame
        # reaches Tk's polling loop.
        if not root_box:
            return
        try:
            root_box[0].after(0, root_box[0].destroy)
        except Exception:  # pragma: no cover - root may already be gone
            pass

    server.register_close_callback(_close)
    return True


def open_splash() -> SplashServer:
    """Start progress serving, open the first available UI, and return it.

    The progress server is returned even without a graphical backend so
    callers can publish through one consistent interface.
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
