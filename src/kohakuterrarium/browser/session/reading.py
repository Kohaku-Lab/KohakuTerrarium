"""Structured page reads: load a URL in a hidden tab and return plain data, not model text.

A read passes the URL check, filtering proxy and network policy ``navigate``
uses and raises the same typed errors. Its tab, and any window that tab
opens, is never an agent tab: the tab list, refs, observations, dialogs and
downloads never see it. Blocked requests are the read's by endpoint (host
and port of a hop of its load) within its run; other ones go to the agent.
``browser/README.md`` says what ``html`` and ``text`` hold and which filters
apply.
"""

import asyncio
import weakref
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from kohakuterrarium.browser.dialogs import DialogManager
from kohakuterrarium.browser.egress import (
    blocked_hops,
    blocked_reason,
    hop_note,
    unreachable,
)
from kohakuterrarium.browser.errors import (
    ActionSpecError,
    BrowserTimeoutError,
    ContextDestroyedError,
    DriverDisconnectedError,
    DriverError,
    DriverTimeoutError,
    NavigationBlockedError,
    NavigationError,
    PageClosedError,
    PageNavigatingError,
)
from kohakuterrarium.browser.options import BrowserOptions
from kohakuterrarium.browser.policy import NetworkPolicy
from kohakuterrarium.browser.protocol import (
    DialogDriver,
    DownloadDriver,
    PageContent,
    PageDriver,
)
from kohakuterrarium.browser.secrets import (
    MIN_REDACT_LENGTH,
    SecretStore,
    value_forms,
)
from kohakuterrarium.browser.session.types import (
    CALL_MARGIN_S,
    NAV_CHAIN_MAX,
    WINDOW_WAIT_S,
    Op,
    Stack,
)
from kohakuterrarium.browser.settle import settle
from kohakuterrarium.browser.tabs import TabRegistry
from kohakuterrarium.errors import ConfigError
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

READ_MAX_CHARS = 1_000_000
READ_MAX_CHARS_LIMIT = 10_000_000


@dataclass(frozen=True)
class PageRead:
    """One page as plain data: where its load ended, status, title, HTML and text.

    ``url`` is the final URL, after redirects and script navigations;
    ``status`` the HTTP status of the response that answered the load
    (``None`` when none did). ``html`` is the serialized DOM after scripts
    ran and ``text`` the body's rendered text; these and ``title`` are cut
    at the read's ``max_chars`` (``html_truncated`` / ``text_truncated``
    say so for the first two). Every
    field is page-chosen, untrusted text with the session's secrets redacted.
    """

    url: str
    status: int | None
    title: str
    html: str
    text: str
    html_truncated: bool = False
    text_truncated: bool = False


class SessionReadMixin:
    """``read_page``; owns the pages reads opened and the driver events routed to them."""

    options: BrowserOptions
    policy: NetworkPolicy
    settle_grace: float
    # owned by SessionLifecycleMixin
    _stack: Stack | None
    _tabs: TabRegistry
    _dialogs: DialogManager
    _expect_page: int
    _late_opens: int
    _arrived: list[PageDriver]
    _refused_pages: set[PageDriver]
    # owned by SessionObserveMixin
    _secrets: SecretStore

    def _init_read(self) -> None:
        self._read_owned: weakref.WeakSet[PageDriver] = weakref.WeakSet()
        self._read_page: PageDriver | None = None
        self._read_nav = asyncio.Event()
        self._read_chain: list[str] = []
        self._read_url = ""
        self._read_mark: tuple[Stack, int] | None = None
        self._read_windows = 0
        self._read_popups = 0
        self._read_arrived = asyncio.Event()
        self._read_downloaded = False
        self._read_undecided: dict[PageDriver, PageDriver | None] = {}

    async def read_page(self, url: str, *, max_chars: int = READ_MAX_CHARS) -> PageRead:
        """Load ``url`` in a temporary tab and return it as a :class:`PageRead`.

        ``max_chars`` bounds ``html`` and ``text`` each. Raises
        ``NavigationBlockedError`` when the policy refuses the URL or a hop
        of its load, ``BrowserTimeoutError`` when the load overruns
        ``nav_timeout_s``, ``NavigationError`` when it fails,
        ``PageNavigatingError`` when the page never stops navigating, and
        ``ActionSpecError`` for a bad argument. The tab is closed on every path.
        """
        self._require_open()
        url = self._read_target(url)
        limit = _limit(max_chars)
        async with self._operation(quiet=True) as op:
            page = await self._open_read_page(op)
            try:
                read = await self._read(page, url, limit)
                await self._await_read_windows()
                return read
            finally:
                await self._end_read(page)

    def _read_target(self, url: Any) -> str:
        if not isinstance(url, str) or not url.strip():
            raise ActionSpecError("url must be a non-empty http or https URL")
        url = url.strip()
        decision = self.policy.check_url(url)
        if not decision:
            raise NavigationBlockedError(
                self._secrets.redact(f"cannot open {url}: {decision.reason}")
            )
        return url

    async def _open_read_page(self, op: Op) -> PageDriver:
        """A new page outside the tab registry; windows that arrive meanwhile are admitted.

        An open that times out or is cancelled keeps running in the
        background; the page it makes is the read's and is closed.
        """
        timeout = self.options.action_timeout_s
        browser = op.stack.browser
        self._expect_page += 1
        opening = asyncio.ensure_future(browser.new_page(timeout))
        try:
            page = await self._bounded(
                lambda: asyncio.shield(opening), timeout, "opening a tab to read in"
            )
        except BaseException:
            self._late_opens += 1
            self._spawn(self._finish_late_read_open(opening, timeout))
            raise
        self._expect_page -= 1
        if page in self._arrived:
            self._arrived.remove(page)
        self._admit_arrivals(None)
        self._read_owned.add(page)
        self._read_page = page
        self._read_nav = asyncio.Event()
        self._read_arrived = asyncio.Event()
        self._read_chain = []
        self._read_windows = self._read_popups = 0
        self._read_downloaded = False
        return page

    async def _finish_late_read_open(
        self, opening: asyncio.Future, timeout: float
    ) -> None:
        """Wait, one more ``timeout``, for a read's page open that gave up; close its page.

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
                self._read_owned.add(own)
                self._spawn(self._close_read_page(own))
            if not self._expect_page:
                arrived, self._arrived = self._arrived, []
                for page in arrived:
                    self._admit_by_page(page)

    async def _read(self, page: PageDriver, url: str, limit: int) -> PageRead:
        stack = self._stack
        mark = stack.proxy.mark()
        self._read_mark = (stack, mark)
        self._read_url = url
        timeout = self.options.nav_timeout_s
        try:
            response = await self._bounded(
                lambda: page.goto(url, timeout), timeout, f"loading {url}"
            )
        except (BrowserTimeoutError, TimeoutError):
            self._raise_read_blocked(url, mark)
            raise BrowserTimeoutError(
                f"loading {url} did not finish within {timeout:g} s"
            ) from None
        except DriverDisconnectedError:
            raise
        except PageClosedError:
            raise NavigationError("the page was closed during the read") from None
        except DriverError as exc:
            self._raise_read_blocked(url, mark)
            raise NavigationError(
                f"loading {url} failed ({exc})", detail=exc.detail
            ) from None
        self._raise_read_blocked(url, mark, response.url)
        if response.download or (self._read_downloaded and response.status is None):
            raise NavigationError(
                f"loading {url} started a download, which a page read does not keep"
            )
        reason = unreachable(stack.proxy.since(mark), response.url, response.blocked)
        if reason:
            raise NavigationError(f"loading {url} failed ({reason})")
        try:
            await settle(page, self._read_nav, grace=self.settle_grace)
        except PageClosedError:
            raise NavigationError("the page was closed during the read") from None
        content = await self._read_content(page, limit)
        title = await self._read_title(page)
        self._raise_read_blocked(url, mark, page.url)
        return self._page_read(page.url, response.status, title, content, limit)

    async def _read_call(
        self, operation: Callable[[], Awaitable[Any]], what: str
    ) -> Any:
        """One bounded page call of a read, its failures as the read's typed errors."""
        timeout = self.options.action_timeout_s
        try:
            return await self._bounded(operation, timeout, what)
        except (DriverDisconnectedError, ContextDestroyedError):
            raise
        except PageClosedError:
            raise NavigationError("the page was closed during the read") from None
        except DriverTimeoutError:
            raise BrowserTimeoutError(f"{what} timed out after {timeout:g} s") from None
        except DriverError as exc:
            raise NavigationError(f"{what} failed ({exc})", detail=exc.detail) from None

    async def _read_content(self, page: PageDriver, limit: int) -> PageContent:
        """The page's content; a navigation mid-read is waited out and read once more."""
        timeout = self.options.action_timeout_s

        async def read() -> PageContent:
            return await self._read_call(
                lambda: page.content(limit, timeout), "reading the page"
            )

        try:
            return await read()
        except ContextDestroyedError:
            pass
        try:
            await self._read_call(
                lambda: page.wait_for_load(timeout), "waiting for the new page"
            )
        except (BrowserTimeoutError, ContextDestroyedError):
            pass
        try:
            return await read()
        except ContextDestroyedError:
            raise PageNavigatingError(
                "the page kept navigating; read it again"
            ) from None

    async def _read_title(self, page: PageDriver) -> str:
        timeout = self.options.action_timeout_s
        try:
            title = await self._read_call(
                lambda: page.title(timeout), "reading the title"
            )
        except (BrowserTimeoutError, ContextDestroyedError, NavigationError):
            return ""
        return title or ""

    def _raise_read_blocked(
        self, target: str, mark: int, landed: str | None = None
    ) -> None:
        """Raise ``NavigationBlockedError`` for a blocked record since ``mark`` on a hop's endpoint."""
        stack = self._stack
        if stack is None:
            return
        urls = [target, *self._read_chain, *([landed] if landed else [])]
        blocked = blocked_hops(stack.proxy.since(mark), urls)
        if not blocked:
            return
        record = blocked[-1]
        raise NavigationBlockedError(
            self._secrets.redact(
                f"loading {target} was blocked by the browser network policy"
                f"{hop_note(record, target)}: {blocked_reason(record, self.policy)}"
            )
        )

    def _page_read(
        self,
        url: str,
        status: int | None,
        title: str,
        content: PageContent,
        limit: int,
    ) -> PageRead:
        passwords = content.passwords

        def redact(text: str) -> str:
            return self._secrets.redact(text, extra=passwords)

        forms = self._secret_forms() + [
            form
            for value in passwords
            if len(value) >= MIN_REDACT_LENGTH
            for form in value_forms(value)
        ]
        html, html_cut = _cut(redact, forms, content.html, content.html_chars, limit)
        text, text_cut = _cut(redact, forms, content.text, content.text_chars, limit)
        title, _ = _cut(redact, forms, title, len(title), limit)
        return PageRead(redact(url), status, title, html, text, html_cut, text_cut)

    def _secret_forms(self) -> list[str]:
        """Every spelling of every secret value currently set and long enough to redact."""
        forms: list[str] = []
        for name in self._secrets.names:
            try:
                forms.extend(value_forms(self._secrets.resolve(name)))
            except ConfigError:
                continue
        return forms

    async def _await_read_windows(self) -> None:
        """Wait, up to ``WINDOW_WAIT_S``, for windows the read page announced, to claim them."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + WINDOW_WAIT_S
        while self._read_popups < self._read_windows and loop.time() < deadline:
            self._read_arrived.clear()
            try:
                await asyncio.wait_for(
                    self._read_arrived.wait(), deadline - loop.time()
                )
            except asyncio.TimeoutError:
                break

    async def _end_read(self, page: PageDriver) -> None:
        """Forget the read and close its page, even if cancelled.

        Blocked records from the read's run whose host and port are those of a
        hop of its load are dropped, whichever page made them; the rest stay
        for the agent.
        """
        started = self._read_mark
        if started is not None and started[0] is self._stack:
            stack, mark = started
            urls = [self._read_url, *self._read_chain, page.url]
            stack.proxy.forget_blocked(blocked_hops(stack.proxy.since(mark), urls))
        self._read_page = None
        self._read_chain = []
        self._read_mark = None
        await asyncio.shield(asyncio.ensure_future(self._close_read_page(page)))

    async def _close_read_page(self, page: PageDriver) -> None:
        timeout = self.options.action_timeout_s
        try:
            await asyncio.wait_for(page.close(timeout), timeout + CALL_MARGIN_S)
        except Exception as exc:
            logger.debug("Closing a read's page failed", error=type(exc).__name__)

    def _claim(self, page: PageDriver) -> None:
        """Take ``page`` for the running read and close it; it never becomes a tab."""
        self._read_owned.add(page)
        self._read_popups += 1
        self._read_arrived.set()
        self._spawn(self._close_read_page(page))

    def _read_claims(self, page: PageDriver) -> bool:
        """Whether an opened ``page`` is the read's to handle; any opened while one runs is.

        Such a page is decided a moment later by its opener (``popup_opened``
        arrives right after): a window of an agent tab becomes a tab, any
        other is the read's and is closed.
        """
        if page in self._read_owned or page in self._read_undecided:
            return True
        if self._read_page is None:
            return False
        if self._tabs.find(page) is not None or page in self._refused_pages:
            return False
        if page in self._arrived:
            return False
        self._read_undecided[page] = None
        loop = asyncio.get_running_loop()
        loop.call_soon(self._read_decide, page, self._stack)
        return True

    def _read_popup(self, opener: PageDriver, page: PageDriver) -> None:
        """Record who opened a window a running read has not decided yet."""
        if page in self._read_undecided:
            self._read_undecided[page] = opener

    def _read_decide(self, page: PageDriver, stack: Stack | None = None) -> None:
        """Hand an undecided window to the tabs when an agent tab opened it, else claim it.

        A window of a browser that was lost (``stack`` no longer current) is
        only forgotten.
        """
        if page not in self._read_undecided:
            return
        opener = self._read_undecided.pop(page)
        if stack is not None and stack is not self._stack:
            return
        if opener is not None and opener not in self._read_owned:
            self._on_page_opened(page)
        else:
            self._claim(page)

    def _read_dialog(self, page: PageDriver, dialog: DialogDriver) -> bool:
        """Dismiss a dialog of a read's page; whether ``page`` was a read's."""
        self._read_decide(page)
        if page not in self._read_owned:
            return False
        self._spawn(dialog.dismiss())
        return True

    def _read_download(self, page: PageDriver, download: DownloadDriver) -> bool:
        """Cancel a download of a read's page; whether ``page`` was a read's."""
        self._read_decide(page)
        if page not in self._read_owned:
            return False
        if page is self._read_page:
            self._read_downloaded = True
        self._spawn(download.cancel())
        return True

    def _read_navigation(self, page: PageDriver, url: str | None) -> bool:
        """Record a main-frame hop of the read's page; whether ``page`` was a read's."""
        self._read_decide(page)
        if page not in self._read_owned:
            return False
        if page is self._read_page:
            self._read_nav.set()
            if url:
                self._read_chain.append(url)
                del self._read_chain[:-NAV_CHAIN_MAX]
        return True

    def _read_window(self, page: PageDriver) -> bool:
        """Count a window the read's page announced; whether ``page`` was a read's."""
        self._read_decide(page)
        if page not in self._read_owned:
            return False
        if page is self._read_page:
            self._read_windows += 1
        return True

    def _read_closed(self, page: PageDriver) -> None:
        self._read_undecided.pop(page, None)
        self._read_owned.discard(page)


def _limit(max_chars: Any) -> int:
    if (
        isinstance(max_chars, bool)
        or not isinstance(max_chars, int)
        or not 1 <= max_chars <= READ_MAX_CHARS_LIMIT
    ):
        raise ActionSpecError(
            f"max_chars must be a whole number from 1 to {READ_MAX_CHARS_LIMIT}"
        )
    return max_chars


def _cut(
    redact: Callable[[str], str],
    forms: list[str],
    raw: str,
    total: int,
    limit: int,
) -> tuple[str, bool]:
    """``raw`` redacted and cut at ``limit``, and whether anything was cut.

    A secret the driver's cut split leaves a prefix at the end; it is dropped.
    """
    cut = total > limit or len(raw) > limit
    text = redact(raw[:limit])
    if cut:
        text = _drop_partial(text, forms)
    if len(text) > limit:
        return text[:limit], True
    return text, cut


def _drop_partial(text: str, forms: list[str]) -> str:
    """``text`` without its longest ending that starts one of ``forms``."""
    longest = 0
    for form in forms:
        for size in range(min(len(form) - 1, len(text)), longest, -1):
            if text.endswith(form[:size]):
                longest = size
                break
    return text[: len(text) - longest] if longest else text
