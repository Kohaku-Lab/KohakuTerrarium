"""The browser runtime: a policed Chromium that any part of the framework can use.

Public names resolve lazily (PEP 562), so importing this package loads no
submodule and never Playwright; see ``README.md`` for the layer map and the
three ways to use it::

    from kohakuterrarium import browser

    session = await browser.agent_session(context)   # the agent's shared browser
    async with browser.BrowserSession(browser.BrowserOptions()) as own:
        page = await own.read_page("https://example.com/")
    page = await browser.read_page("https://example.com/")  # one shot

Framework modules import the defining submodule instead of this facade.
"""

import importlib
from typing import Any

_EXPORTS = {
    "BROWSER_SERVICE": "kohakuterrarium.browser.access",
    "BrowserAccess": "kohakuterrarium.browser.access",
    "agent_session": "kohakuterrarium.browser.access",
    "browser_access": "kohakuterrarium.browser.access",
    "BrowserOptions": "kohakuterrarium.browser.options",
    "BrowserResult": "kohakuterrarium.browser.session",
    "BrowserSession": "kohakuterrarium.browser.session",
    "PageRead": "kohakuterrarium.browser.session.reading",
    "read_page": "kohakuterrarium.browser.read",
    "BrowserError": "kohakuterrarium.browser.errors",
    "BrowserUnavailableError": "kohakuterrarium.browser.errors",
    "BrowserLaunchError": "kohakuterrarium.browser.errors",
    "BrowserClosedError": "kohakuterrarium.browser.errors",
    "BrowserTimeoutError": "kohakuterrarium.browser.errors",
    "BrowserStateLostError": "kohakuterrarium.browser.errors",
    "NavigationBlockedError": "kohakuterrarium.browser.errors",
    "NavigationError": "kohakuterrarium.browser.errors",
    "PageNavigatingError": "kohakuterrarium.browser.errors",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    module_path = _EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(module_path), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_EXPORTS))
