"""Tab operations: list, new, select, close, and closing the browser itself."""

import asyncio

from kohakuterrarium.browser import results as fmt
from kohakuterrarium.browser.dialogs import DialogManager
from kohakuterrarium.browser.errors import (
    BrowserTimeoutError,
    PageClosedError,
    TabLimitError,
)
from kohakuterrarium.browser.options import BrowserOptions
from kohakuterrarium.browser.protocol import PageDriver
from kohakuterrarium.browser.session.types import (
    CALL_MARGIN_S,
    READY,
    BrowserResult,
    Stack,
)
from kohakuterrarium.browser.tabs import NO_TAB_HINT, Tab, TabRegistry
from kohakuterrarium.browser.tasks import discard

TITLES_ORIGIN = "tab titles"


class SessionTabsMixin:
    """``tabs_list`` / ``tab_new`` / ``tab_select`` / ``tab_close`` / ``close_browser``."""

    options: BrowserOptions
    # owned by SessionLifecycleMixin
    _tabs: TabRegistry
    _dialogs: DialogManager
    _stack: Stack | None
    _stale: list[Stack]
    _state: str
    _expect_page: int
    _late_opens: int
    _max_tabs: int
    _arrived: list[PageDriver]
    _refused_pages: set[PageDriver]

    async def _open_tab(self) -> Tab:
        """A new active tab; ``TabLimitError`` when ``MAX_TABS`` are already open."""
        if len(self._tabs) >= self._max_tabs:
            raise TabLimitError(
                f"the limit of {self._max_tabs} open tabs is reached; close a tab "
                "with browser_tabs close first"
            )
        timeout = self.options.action_timeout_s
        browser = self._stack.browser
        self._expect_page += 1
        opening = asyncio.ensure_future(browser.new_page(timeout))
        try:
            page = await self._bounded(
                lambda: asyncio.shield(opening), timeout, "opening a tab"
            )
        except BrowserTimeoutError:
            self._late_opens += 1
            self._spawn(self._finish_late_open(opening, timeout))
            raise
        except BaseException:
            discard(opening)
            self._expect_page -= 1
            self._admit_arrivals(None)
            raise
        self._expect_page -= 1
        return self._admit_arrivals(page)

    async def _finish_late_open(self, opening: asyncio.Future, timeout: float) -> None:
        """Wait, one more ``timeout``, for a tab open that timed out; close the tab it made.

        Pages that arrived meanwhile are admitted as opened by the site once
        no other open is waiting for its own page.
        """
        own = None
        try:
            own = await asyncio.wait_for(opening, timeout + CALL_MARGIN_S)
        except Exception:
            own = None
        finally:
            self._expect_page -= 1
            self._late_opens -= 1
            if own is not None:
                if own in self._arrived:
                    self._arrived.remove(own)
                self._refused_pages.add(own)
                self._spawn(own.close(timeout))
            if not self._expect_page:
                arrived, self._arrived = self._arrived, []
                for page in arrived:
                    self._admit_by_page(page)

    async def tabs_list(self) -> BrowserResult:
        """The open tabs, active one marked; never launches the browser."""
        async with self._operation(launch=False):
            if self._state != READY:
                return BrowserResult(
                    "No browser is running; it starts on the first browser call.",
                    {"tabs": [], "active": None},
                )
            return await self._tabs_result([])

    async def _titles(self, tabs: list[Tab]) -> list[str]:
        """Every tab's title, read concurrently under one timeout; empty where unread."""
        timeout = self.options.action_timeout_s
        tasks = [asyncio.create_task(tab.page.title(timeout)) for tab in tabs]
        if tasks:
            await asyncio.wait(tasks, timeout=timeout + CALL_MARGIN_S)
        titles = []
        for task in tasks:
            if not task.done():
                discard(task)
                titles.append("")
            elif task.cancelled() or task.exception() is not None:
                titles.append("")
            else:
                titles.append(task.result() or "")
        return titles

    async def _tabs_result(
        self, lead: list[str], notes: list[str] | None = None
    ) -> BrowserResult:
        active = self._tabs.active
        tabs = list(self._tabs)
        titles = await self._titles(tabs)
        lines, named, meta_tabs = [], [], []
        for tab, title in zip(tabs, titles):
            marker = "*" if tab is active else " "
            lines.append(f"{marker} {self._header(tab, tab.page.url)}")
            line = self._line(title, fmt.LINE_TEXT, tab)
            if line:
                named.append(f'{tab.id}: "{line}"')
            meta_tabs.append(
                {"tab": tab.id, "url": self._line(tab.page.url, fmt.LINE_URL, tab)}
            )
        if active is None:
            summary = f"No tab is open; {NO_TAB_HINT}."
        else:
            summary = f"Tabs ({len(lines)}, active {active.id}):"
        events, event_notes, _ = await self._events_text(None)
        text = fmt.join_sections(
            *lead,
            summary,
            *lines,
            self._wrap(fmt.join_sections(*named, events), None, TITLES_ORIGIN),
            event_notes,
            *(notes or []),
        )
        meta = {"tabs": meta_tabs, "active": active.id if active else None}
        return BrowserResult(self._redact(text), meta)

    async def tab_new(self, url: str | None = None) -> BrowserResult:
        """Open a tab and make it active; load ``url`` in it when given.

        Right after a launch, the launch's own blank tab is used instead.
        """
        if url is not None:
            url = self._check_url(url)
        async with self._operation() as op:
            if self._dialogs.pending is not None:
                return self._pending_result(op)
            if op.launched and len(self._tabs) == 1:
                tab = self._tabs.active
            else:
                tab = await self._open_tab()
            op.tab = tab
            if url is None:
                return await self._tabs_result([], op.notes)
            return await self._go(op, tab, url, None)

    async def tab_select(self, tab_id: str) -> BrowserResult:
        """Make ``tab_id`` the active tab."""
        async with self._operation(launch=False) as op:
            tab = op.tab = self._tabs.select(tab_id)
            timeout = self.options.action_timeout_s
            await self._bounded(
                lambda: tab.page.bring_to_front(timeout), timeout, "showing the tab"
            )
            return await self._tabs_result([f"Selected {tab.id}."])

    async def tab_close(self, tab_id: str | None = None) -> BrowserResult:
        """Close ``tab_id`` (default: the active one); the last active other tab takes over."""
        async with self._operation(launch=False):
            tab = self._tabs.get(tab_id)
            timeout = self.options.action_timeout_s
            try:
                await self._bounded(
                    lambda: tab.page.close(timeout), timeout, "closing the tab"
                )
            except PageClosedError:
                pass
            if self._tabs.remove(tab.page) is not None:
                self._forget_tab(tab)
            return await self._tabs_result([f"Closed {tab.id}."])

    async def close_browser(self) -> BrowserResult:
        """Close the browser now; the session stays open and the next call relaunches."""
        async with self._operation(launch=False):
            running = self._state == READY or bool(self._stale)
            self._mark_lost(None)
            await self._teardown_stale()
            message = (
                "Closed the browser; the next browser call starts a fresh one."
                if running
                else "No browser was running."
            )
            return BrowserResult(message, {"closed": running})
