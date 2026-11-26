"""Typed errors of the browser runtime; every one derives from ``KTError``.

The ``Driver*`` errors are what a driver implementation raises; the session
turns them into the user-facing errors above them. ``str()`` of every one
is fixed wording, ids, numbers and URLs; text a page chose is kept apart in
``detail``.
"""

from collections.abc import Callable

from kohakuterrarium.errors import ConflictError, InvalidRequestError, KTError


class BrowserError(KTError):
    """Base class for browser runtime errors; ``detail`` holds page-derived text."""

    def __init__(self, message: str = "", *, detail: str = "") -> None:
        super().__init__(message)
        self.detail = detail

    def with_text(self, change: Callable[[str], str]) -> "BrowserError":
        """Apply ``change`` to the message and ``detail`` in place; returns ``self``."""
        self.args = (change(str(self)),)
        self.detail = change(self.detail) if self.detail else ""
        return self


class BrowserUnavailableError(BrowserError, RuntimeError):
    """The browser cannot run here: Playwright, Chromium or the event loop is missing,
    or its persistent profile is still held by a browser not known to have exited."""


class BrowserLaunchError(BrowserError, RuntimeError):
    """Launching the browser failed."""


class BrowserClosedError(BrowserError, RuntimeError):
    """The browser session was closed; it never relaunches."""


class BrowserTimeoutError(BrowserError, TimeoutError):
    """A browser operation did not finish within its timeout."""


class BrowserStateLostError(BrowserError, RuntimeError):
    """The browser crashed or disconnected during an operation."""


class NoTabError(BrowserError, InvalidRequestError):
    """The operation needs a tab and there is none, or the tab id is unknown."""


class TabLimitError(BrowserError, ConflictError):
    """Opening a tab was refused because the session already has its limit of tabs open."""


class DialogPendingError(BrowserError, ConflictError):
    """A page dialog is waiting for an answer; only the ``dialog`` action is accepted."""


class NavigationBlockedError(BrowserError, PermissionError):
    """The network policy refused a navigation target."""


class NavigationError(BrowserError, RuntimeError):
    """A navigation failed for a reason other than the network policy."""


class PageNavigatingError(BrowserError, RuntimeError):
    """The page was still loading a new document after a bounded wait and one retry."""


class ActionSpecError(BrowserError, InvalidRequestError):
    """The ``actions`` argument (or another operation argument) is invalid."""


class ActionFailedError(BrowserError):
    """One action could not be carried out; ``str()`` is the reason shown to the model."""


class DialogOpenedError(DialogPendingError):
    """A driver call was interrupted by a dialog that now waits for an answer."""

    def __init__(self, record: object, message: str, *, detail: str = "") -> None:
        super().__init__(message, detail=detail)
        self.record = record


class DriverError(BrowserError):
    """A driver call failed; ``str()`` is a short reason fit to show the model."""


class DriverTimeoutError(DriverError, TimeoutError):
    """The driver gave up waiting (for an element, a load, a script)."""


class DriverDisconnectedError(DriverError):
    """The browser process went away (crash, kill, or close)."""


class PageClosedError(DriverError):
    """The page the call targeted was closed."""


class ContextDestroyedError(DriverError):
    """A navigation destroyed the page's script context mid-call."""


class NotFileInputError(DriverError):
    """The element given to ``set_input_files`` is not a file input."""
