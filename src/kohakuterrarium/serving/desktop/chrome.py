"""Install platform desktop chrome before the native window is first shown."""

import ctypes
import sys
import threading

from kohakuterrarium.utils.logging import get_logger

THEME_SYNC_SCRIPT = """(() => {
    const generation = __GENERATION__;
    if ((window.__ktCaptionGeneration ?? -1) > generation) return;
    window.__ktCaptionGeneration = generation;
    window.__ktCaptionTheme?.disconnect();
    let pending = Promise.resolve();
    const sync = () => {
        const dark = document.documentElement.classList.contains('dark');
        pending = pending.catch(() => {}).then(() =>
            window.pywebview.api.set_desktop_theme(dark, generation));
    };
    const start = () => {
        const observer = new MutationObserver(sync);
        observer.observe(document.documentElement, {
            attributes: true, attributeFilter: ['class']
        });
        window.__ktCaptionTheme = observer;
        sync();
    };
    if (window.pywebview?.api?.set_desktop_theme) start();
    else window.addEventListener('pywebviewready', start, {once: true});
})()"""


def _enable_per_monitor_dpi() -> bool:
    user = ctypes.WinDLL("user32", use_last_error=True)
    set_awareness = user.SetProcessDpiAwarenessContext
    set_awareness.argtypes = [ctypes.c_void_p]
    set_awareness.restype = ctypes.c_int
    user.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    user.AreDpiAwarenessContextsEqual.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    user.AreDpiAwarenessContextsEqual.restype = ctypes.c_int
    desired = ctypes.c_void_p(-4)
    changed = set_awareness(desired)
    error = 0 if changed else ctypes.get_last_error()
    inherited = []
    probe = threading.Thread(
        target=lambda: inherited.append(user.GetThreadDpiAwarenessContext())
    )
    probe.start()
    probe.join()
    effective = bool(user.AreDpiAwarenessContextsEqual(inherited[0], desired))
    if not effective:
        get_logger(__name__).warning(
            "Per-monitor DPI initialization failed (Windows error %s)", error
        )
    return effective


def configure_desktop_chrome(window) -> None:
    if sys.platform != "win32":
        return
    _enable_per_monitor_dpi()
    generation = 0
    generation_lock = threading.Lock()

    def before_load():
        nonlocal generation
        with generation_lock:
            generation += 1

    def before_show():
        from kohakuterrarium.serving.desktop.titlebar import DesktopTitleBar

        window._desktop_titlebar = DesktopTitleBar(
            window.native, min_size=window.min_size
        )

    def set_desktop_theme(dark: bool, document_generation: int) -> bool:
        caption = getattr(window, "_desktop_titlebar", None)
        if (
            not isinstance(dark, bool)
            or type(document_generation) is not int
            or caption is None
        ):
            return False

        def is_current():
            with generation_lock:
                return generation == document_generation

        if not is_current():
            return False
        return caption.set_dark(dark, is_current=is_current)

    def loaded():
        with generation_lock:
            script = THEME_SYNC_SCRIPT.replace("__GENERATION__", str(generation))
        window.evaluate_js(script)

    window.expose(set_desktop_theme)
    window.events.before_show += before_show
    window.events.before_load += before_load
    window.events.loaded += loaded
