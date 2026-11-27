"""``BrowserSession``: one lazily launched browser shared by the browser tools."""

from kohakuterrarium.browser.session.assembly import BrowserSession
from kohakuterrarium.browser.session.types import BrowserResult

__all__ = ["BrowserResult", "BrowserSession"]
