"""Shared values of the ``BrowserSession`` parts: states, limits, the result type."""

from dataclasses import dataclass, field
from typing import Any

from kohakuterrarium.browser.proxy import FilteringProxy
from kohakuterrarium.browser.protocol import BrowserDriver
from kohakuterrarium.browser.tabs import Tab

NEW, LAUNCHING, READY, CLOSED = "new", "launching", "ready", "closed"
CALL_MARGIN_S = 0.5
CLOSE_STAGE_TIMEOUT_S = 5.0
CLOSE_TIMEOUT_S = 20.0
PROBE_TIMEOUT_S = 1.0
COMPACT_MAX_CHARS = 4000
PAGE_TEXT_MAX_CHARS = 8000
FULL_PAGE_MAX_HEIGHT = 8000
EVAL_MAX_CHARS = 10000
DETAIL_MAX_CHARS = 200
DOWNLOAD_WAIT_S = 2.0
WINDOW_WAIT_S = 3.0
JPEG_QUALITIES = (75, 50, 30)
HISTORY_STEPS = ("back", "forward", "reload")
LOST_CRASH = "the browser crashed or was closed from outside"
LOST_HUNG = "the browser stopped responding"
MAX_TABS = 20
NAV_CHAIN_MAX = 20


@dataclass(frozen=True)
class BrowserResult:
    """What one session operation returns to its tool.

    ``text`` carries page-chosen text only inside ``page_content`` wrappers.
    ``meta`` carries tab ids, a capped redacted URL, the pending dialog kind,
    action kinds and statuses; never page text, typed text or secret values.
    """

    text: str
    meta: dict[str, Any] = field(default_factory=dict)
    image: bytes | None = None
    mime_type: str | None = None
    is_error: bool = False


@dataclass(eq=False)
class Stack:
    """The resources of one launch: its proxy and, once started, its browser.

    ``bundled_only`` is whether its launch plan named the bundled Chromium alone.
    """

    proxy: FilteringProxy
    generation: int
    browser: BrowserDriver | None = None
    torn_down: bool = False
    bundled_only: bool = False


@dataclass(eq=False)
class Op:
    """One running operation: its tab, notes for its result, and what it started from.

    ``stack`` is the browser the operation runs on, ``launched`` whether
    the operation launched it, ``downloads_mark`` the download count when
    it began.
    """

    tab: Tab | None = None
    notes: list[str] = field(default_factory=list)
    stack: Stack | None = None
    launched: bool = False
    downloads_mark: int = 0
