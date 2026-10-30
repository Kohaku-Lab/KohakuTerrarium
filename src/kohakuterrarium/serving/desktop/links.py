"""Open links outside the desktop window and keep the window on the app origin.

The embedded webview has no back button and, on some platforms, loads a new-window
request into the app's own view. The frontend therefore hands external links to
``open_external``; if a page still escapes, :class:`AppLinks` opens it in the
system browser and brings the window back to the app.
"""

import webbrowser
from urllib.parse import urlsplit

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

OPENABLE_SCHEMES = frozenset({"http", "https", "mailto"})
_WEB_SCHEMES = frozenset({"http", "https"})


def is_openable(url: object) -> bool:
    """Return whether the system may be asked to open ``url``."""
    return isinstance(url, str) and urlsplit(url).scheme.lower() in OPENABLE_SCHEMES


def _origin(url: str) -> tuple[str, str]:
    parts = urlsplit(url)
    return parts.scheme.lower(), parts.netloc.lower()


def is_foreign(current: str | None, home: str | None) -> bool:
    """Return whether ``current`` is a web page outside the app at ``home``."""
    if not current or not home:
        return False
    scheme, _ = _origin(current)
    return scheme in _WEB_SCHEMES and _origin(current) != _origin(home)


def open_external(url: str) -> bool:
    """Open an http(s) or mailto link in the system browser; refuse other schemes."""
    if not is_openable(url):
        logger.warning("refused to open non-web link", scheme=urlsplit(str(url)).scheme)
        return False
    try:
        return bool(webbrowser.open(url))
    except webbrowser.Error:
        return False


class AppLinks:
    """Expose link opening to the page and recover when the window leaves the app."""

    def __init__(self, window):
        self._window = window
        self._home: str | None = None

    def attach(self) -> None:
        self._window.expose(open_external)
        self._window.events.loaded += self._on_loaded

    def set_home(self, url: str) -> None:
        self._home = url

    def _on_loaded(self) -> None:
        try:
            current = self._window.get_current_url()
        except Exception:
            return
        if not is_foreign(current, self._home):
            return
        open_external(current)
        self._return_to_app()

    def _return_to_app(self) -> None:
        try:
            went_back = self._window.evaluate_js(
                "history.length > 1 ? (history.back(), true) : false"
            )
        except Exception:
            went_back = False
        if went_back is not True and self._home:
            self._window.load_url(self._home)
