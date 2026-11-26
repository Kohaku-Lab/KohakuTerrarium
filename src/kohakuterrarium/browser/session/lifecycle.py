"""Session lifecycle: state machine, lock, launch, staged teardown, idle close, crashes.

Also the bounded call runners every other part goes through.
"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from kohakuterrarium.browser.dialogs import DialogManager
from kohakuterrarium.browser.downloads import Downloads
from kohakuterrarium.browser.errors import (
    BrowserClosedError,
    BrowserError,
    BrowserLaunchError,
    BrowserStateLostError,
    BrowserTimeoutError,
    DialogOpenedError,
    DriverDisconnectedError,
    NoTabError,
    PageClosedError,
)
from kohakuterrarium.browser.launch import plan_launch
from kohakuterrarium.browser.observe import Observation
from kohakuterrarium.browser.options import BrowserOptions
from kohakuterrarium.browser.policy import ExternalCheck, NetworkPolicy
from kohakuterrarium.browser.profiles import claim_profile, forget_if_released
from kohakuterrarium.browser.protocol import (
    BrowserDriver,
    DialogDriver,
    Driver,
    PageDriver,
)
from kohakuterrarium.browser.proxy import EgressRecord, FilteringProxy
from kohakuterrarium.browser.refs import RefBook
from kohakuterrarium.browser.session.sink import LaunchSink
from kohakuterrarium.browser.session.types import (
    CALL_MARGIN_S,
    CLOSED,
    DETAIL_MAX_CHARS,
    LAUNCHING,
    LOST_CRASH,
    LOST_HUNG,
    MAX_TABS,
    NAV_CHAIN_MAX,
    NEW,
    PROBE_TIMEOUT_S,
    READY,
    WINDOW_WAIT_S,
    Op,
    Stack,
)
from kohakuterrarium.browser.tabs import Tab, TabRegistry
from kohakuterrarium.browser.tasks import discard
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

LOST_MESSAGE = (
    "the browser closed unexpectedly during the call; its state was lost and the "
    "next call starts a fresh browser"
)
ProxyFactory = Callable[[NetworkPolicy], FilteringProxy]


class SessionLifecycleMixin:
    """Owns the state machine, the lock, the launch stack and the per-browser books."""

    options: BrowserOptions
    close_timeout: float
    # owned by SessionObserveMixin
    _last: dict[str, Observation]
    _blocked: list[EgressRecord]
    _redact: Callable[..., str]

    def _init_lifecycle(
        self,
        driver: Driver,
        external_check: ExternalCheck | None,
        stage_timeout: float,
        downloads: Downloads,
        proxy_factory: ProxyFactory,
    ) -> None:
        self._driver = driver
        self.bundled_only = False
        self._proxy_factory = proxy_factory
        self._external_check = external_check
        self._stage_timeout = stage_timeout
        self._lock = asyncio.Lock()
        self._state = NEW
        self._generation = 0
        self._stack: Stack | None = None
        self._stale: list[Stack] = []
        self._lost: str | None = None
        self._launch_task: asyncio.Task | None = None
        self._idle_task: asyncio.Task | None = None
        self._close_task: asyncio.Task | None = None
        self._close_deadline = 0.0
        self._live_stacks: set[Stack] = set()
        self._cleanup_task: asyncio.Task | None = None
        self._teardowns: set[asyncio.Task] = set()
        self._kills: set[asyncio.Future] = set()
        self._background: set[asyncio.Task] = set()
        self._expect_page = 0
        self._late_opens = 0
        self._max_tabs = MAX_TABS
        self._refused_tabs = 0
        self._windows_due = 0
        self._windows_early = 0
        self._window_arrived = asyncio.Event()
        self._refused_pages: set[PageDriver] = set()
        self._arrived: list[PageDriver] = []
        self._nav_urls: dict[str, list[str]] = {}
        self._nav_counts: dict[str, int] = {}
        self._tabs = TabRegistry()
        self._refs = RefBook()
        self._dialogs = DialogManager()
        self._downloads = downloads
        self._nav: dict[str, asyncio.Event] = {}

    @property
    def state(self) -> str:
        """``new``, ``launching``, ``ready`` or ``closed``."""
        return self._state

    @property
    def policy(self) -> NetworkPolicy:
        """The network policy this session's proxy enforces."""
        return self.options.network_policy(self._external_check)

    async def process_ids(self) -> list[int]:
        """Ids of the running browser's processes, browser process first.

        Empty when no browser is running or it does not answer within the
        action timeout; never launches one.
        """
        stack = self._stack
        if stack is None or stack.browser is None:
            return []
        try:
            return await asyncio.wait_for(
                stack.browser.process_ids(), self.options.action_timeout_s
            )
        except Exception as exc:
            logger.debug("Reading browser process ids failed", error=str(exc))
            return []

    async def drop_connections(self) -> None:
        """Cut the running browser's open proxy connections; new ones are judged afresh."""
        stack = self._stack
        if stack is not None:
            await stack.proxy.drop_connections()

    async def require_bundled(self, required: bool) -> None:
        """Launch only the bundled Chromium from now on while ``required``.

        A running browser whose launch could have used another one is closed;
        the next call starts a bundled one.
        """
        self.bundled_only = required
        stack = self._stack
        if required and stack is not None and not stack.bundled_only:
            await self.close_browser()

    async def rejudge_connections(self) -> int:
        """Cut the open proxy connections the policy now refuses; how many were cut."""
        stack = self._stack
        return 0 if stack is None else await stack.proxy.rejudge()

    def _require_open(self) -> None:
        if self._state == CLOSED:
            raise BrowserClosedError("the browser session is closed")

    @asynccontextmanager
    async def _operation(
        self, *, launch: bool = True, quiet: bool = False
    ) -> AsyncIterator[Op]:
        """Hold the lock for one operation, launching first unless ``launch`` is false."""
        self._require_open()
        op = Op(downloads_mark=self._downloads.started, quiet=quiet)
        async with self._lock:
            self._require_open()
            self._cancel_idle()
            try:
                if launch:
                    await self._ensure_ready(op)
                op.stack = self._stack
                yield op
            except asyncio.CancelledError:
                if op.tab is not None:
                    self._refs.reset(op.tab.id)
                raise
            except Exception as exc:
                if isinstance(exc, TimeoutError):
                    await self._drop_if_unresponsive(op)
                raise self._operation_error(exc, op) from None
            finally:
                self._schedule_idle()

    async def _drop_if_unresponsive(self, op: Op) -> None:
        """After a timeout, kill the browser and mark it lost when it no longer answers."""
        stack = op.stack
        if stack is None or stack is not self._stack or stack.browser is None:
            return
        if await stack.browser.responsive(PROBE_TIMEOUT_S):
            return
        if self._stack is not stack or self._state == CLOSED:
            return
        logger.warning("The browser stopped responding; killing it")
        self._mark_lost(LOST_HUNG)
        await self._kill(stack.browser)

    def _operation_error(self, exc: Exception, op: Op) -> Exception:
        """The error an operation raises for ``exc``, redacted when it is a browser error."""
        lost = op.stack is not None and self._stack is not op.stack
        if isinstance(exc, DriverDisconnectedError) and not lost:
            self._mark_lost(LOST_CRASH)
            lost = True
        if lost and not isinstance(exc, (BrowserStateLostError, BrowserClosedError)):
            if self._state == CLOSED:
                exc = BrowserClosedError(
                    "the browser session was closed during the call"
                )
            else:
                exc = BrowserStateLostError(LOST_MESSAGE)
        elif isinstance(exc, PageClosedError):
            exc = NoTabError("the tab was closed during the call")
        if isinstance(exc, BrowserError):
            exc.with_text(self._redact)
            exc.detail = exc.detail[:DETAIL_MAX_CHARS]
        return exc

    async def _ensure_ready(self, op: Op) -> None:
        if self._state == READY and self._stack.browser.is_connected():
            self._note_lost(op)
            return
        if self._state == READY:
            self._mark_lost(LOST_CRASH)
        await self._teardown_stale()
        self._require_open()
        self._generation += 1
        self._state = LAUNCHING
        task = self._launch_task = asyncio.create_task(self._launch(self._generation))
        try:
            stack = await task
        except asyncio.CancelledError:
            if self._state == CLOSED and not _cancel_requested():
                raise BrowserClosedError("the browser session was closed") from None
            raise
        finally:
            self._launch_task = None
            if self._state == LAUNCHING and not (task.done() and _succeeded(task)):
                self._state = NEW
        if self._state == CLOSED:
            await self._teardown(stack)
            raise BrowserClosedError("the browser session was closed")
        self._stack = stack
        self._state = READY
        op.launched = True
        self._note_lost(op)

    def _note_lost(self, op: Op) -> None:
        """Tell ``op`` once that the previous browser's state was lost."""
        if self._lost and not op.quiet:
            op.notes.append(
                f"Browser state was lost ({self._lost}); a fresh browser was started, so "
                "earlier tabs, refs, logins and page state are gone."
            )
            self._lost = None

    async def _launch(self, generation: int) -> Stack:
        claim = None
        if self.options.profile_dir is not None:
            claim = await claim_profile(self.options.profile_dir)
        try:
            stack = Stack(self._proxy_factory(self.policy), generation, claim=claim)
        except BaseException:
            if claim is not None:
                claim.drop()
            raise
        self._live_stacks.add(stack)
        plan = None
        self._expect_page += 1
        try:
            await stack.proxy.start()
            stack.bundled_only = self.bundled_only
            plan = plan_launch(
                self.options, stack.proxy.url, bundled_only=stack.bundled_only
            )
            stack.browser = await asyncio.wait_for(
                self._driver.launch(plan, LaunchSink(self, generation, claim)),
                plan.timeout_s + CALL_MARGIN_S,
            )
            if claim is not None:
                claim.held_by(stack.browser)
            arrived, self._arrived = self._arrived, []
            for page in dict.fromkeys([*arrived, *stack.browser.pages()]):
                self._admit(page, opened_by_page=False)
            if not len(self._tabs):
                timeout = self.options.action_timeout_s
                page = await self._bounded(
                    lambda: stack.browser.new_page(timeout), timeout, "opening a tab"
                )
                self._admit_arrivals(page)
        except asyncio.TimeoutError:
            await self._teardown_shielded(stack)
            limit = plan.timeout_s if plan else 0
            raise BrowserLaunchError(
                f"starting the browser took longer than {limit:g} s"
            ) from None
        except BaseException:
            await self._teardown_shielded(stack)
            raise
        finally:
            self._expect_page -= 1
            self._arrived = []
        return stack

    async def _stage(self, operation: Callable[[], Awaitable[Any]], name: str) -> bool:
        try:
            await asyncio.wait_for(operation(), self._stage_timeout)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("Browser close stage failed", stage=name, error=str(exc))
            return False
        return True

    async def _teardown(self, stack: Stack) -> None:
        """Close context, browser, Playwright, proxy; each stage bounded on its own."""
        if stack.torn_down:
            return
        stack.torn_down = True
        browser = stack.browser
        if browser is not None:
            clean = await self._stage(browser.close_context, "context")
            clean = await self._stage(browser.close_browser, "browser") and clean
            clean = await self._stage(browser.stop, "playwright") and clean
            if not clean:
                await self._kill(browser)
        await self._stage(stack.proxy.close, "proxy")
        self._live_stacks.discard(stack)
        if stack.claim is not None:
            if stack.claim.browser is None:
                stack.claim.drop()
            else:
                forget_if_released(stack.claim)

    async def _teardown_shielded(self, stack: Stack) -> None:
        """Tear ``stack`` down to the end even if the caller is cancelled; ``close`` waits for it."""
        task = asyncio.create_task(self._teardown(stack))
        self._teardowns.add(task)
        task.add_done_callback(self._teardowns.discard)
        await asyncio.shield(task)

    async def _teardown_stale(self) -> None:
        stale, self._stale = self._stale, []
        for stack in stale:
            await self._teardown_shielded(stack)

    def _clean_up_soon(self) -> None:
        """Tear down a crashed browser's leftovers without waiting for the next call."""
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._teardown_stale())

    def _reset_browser_state(self) -> None:
        self._tabs.clear()
        self._refs.reset()
        self._dialogs.clear()
        self._downloads.clear()
        self._nav.clear()
        self._nav_urls.clear()
        self._nav_counts.clear()
        self._last.clear()
        self._blocked.clear()
        self._refused_pages.clear()
        self._arrived = []
        self._refused_tabs = 0
        self._windows_due = 0
        self._windows_early = 0

    def _mark_lost(self, reason: str | None) -> None:
        """Drop the current browser (kept for teardown) and every book about it."""
        stack, self._stack = self._stack, None
        if stack is not None:
            self._stale.append(stack)
        if self._state == READY:
            self._state = NEW
        self._lost = reason
        self._reset_browser_state()
        self._cancel_idle()

    def _schedule_idle(self) -> None:
        self._cancel_idle()
        if self._state == READY:
            self._idle_task = asyncio.create_task(
                self._idle_close(self.options.idle_close_s)
            )

    def _cancel_idle(self) -> None:
        task, self._idle_task = self._idle_task, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    async def _idle_close(self, delay: float) -> None:
        await asyncio.sleep(delay)
        async with self._lock:
            if self._state != READY or self._idle_task is not asyncio.current_task():
                return
            self._idle_task = None
            self._mark_lost(f"it was closed after {delay:g} s without use")
            await self._teardown_stale()

    async def close(self) -> None:
        """Close for good: mark closed, then context, browser, Playwright, proxy. Idempotent.

        Returns within ``close_timeout`` of the first call whatever the stages
        do; past it every browser still running is killed and the rest of the
        teardown carries on in the background.
        """
        loop = asyncio.get_running_loop()
        if self._close_task is None:
            self._state = CLOSED
            self._close_deadline = loop.time() + self.close_timeout
            self._close_task = asyncio.create_task(self._close())
        task = self._close_task
        remaining = max(0.0, self._close_deadline - loop.time())
        done, _ = await asyncio.wait({task}, timeout=remaining)
        if done:
            task.result()
            return
        logger.warning("Browser close overran its bound; killing what is left")
        self._kill_live_stacks()

    def _kill_live_stacks(self) -> None:
        for stack in list(self._live_stacks):
            if stack.browser is not None:
                self._start_kill(stack.browser)

    def _start_kill(self, browser: BrowserDriver) -> asyncio.Future:
        """Start killing ``browser``, kept until it ends; a failure is logged."""
        try:
            killing = asyncio.ensure_future(browser.kill())
        except Exception as exc:
            logger.warning("Killing the browser failed", error=str(exc))
            killing = asyncio.get_running_loop().create_future()
            killing.set_result(None)
        self._kills.add(killing)
        killing.add_done_callback(self._kill_ended)
        return killing

    def _kill_ended(self, killing: asyncio.Future) -> None:
        self._kills.discard(killing)
        if not killing.cancelled() and killing.exception() is not None:
            logger.warning("Killing the browser failed", error=str(killing.exception()))

    async def _kill(self, browser: BrowserDriver) -> None:
        """Kill ``browser`` and wait for the end; cancelling the wait leaves the kill running."""
        await asyncio.wait({self._start_kill(browser)})

    async def _close(self) -> None:
        self._cancel_idle()
        stacks: list[Stack] = []
        task = self._launch_task
        if task is not None:
            task.cancel()
            await asyncio.wait([task], timeout=self._stage_timeout * 5)
            if task.done() and _succeeded(task):
                stacks.append(task.result())
        if self._stack is not None:
            stacks.append(self._stack)
            self._stack = None
        stacks.extend(self._stale)
        self._stale = []
        self._reset_browser_state()
        for stack in stacks:
            await self._teardown(stack)
        cleanup = self._cleanup_task
        if cleanup is not None:
            await asyncio.wait([cleanup], timeout=self._stage_timeout * 5)
        while self._teardowns:
            await asyncio.wait(list(self._teardowns), timeout=self._stage_timeout * 5)
            self._teardowns = {t for t in self._teardowns if not t.done()}

    def _on_page_opened(self, page: PageDriver) -> None:
        known = self._tabs.find(page) is not None or page in self._refused_pages
        if known or page in self._arrived:
            return
        if self._expect_page:
            self._arrived.append(page)
            return
        self._admit_by_page(page)

    def _admit(self, page: PageDriver, *, opened_by_page: bool) -> Tab | None:
        """The tab of ``page``; ``None`` after closing it when ``MAX_TABS`` are open."""
        tab = self._tabs.find(page)
        if tab is not None or page in self._refused_pages:
            return tab
        if len(self._tabs) >= self._max_tabs:
            self._refused_tabs += 1
            self._refused_pages.add(page)
            self._spawn(page.close(self.options.action_timeout_s))
            return None
        return self._tabs.add(page, opened_by_page=opened_by_page)

    def _admit_by_page(self, page: PageDriver) -> Tab | None:
        """Admit a page the site opened, matching it with an announced window."""
        if self._windows_due:
            self._windows_due -= 1
            self._window_arrived.set()
        else:
            self._windows_early += 1
        return self._admit(page, opened_by_page=True)

    def _admit_arrivals(self, own: PageDriver | None) -> Tab | None:
        """Admit the pages that opened while the session opened ``own``.

        ``own`` (already allowed by the cap) is admitted first and stays
        active; the others count as opened by the page, and stay buffered
        while a timed-out open may still claim one of them.
        """
        arrived, self._arrived = self._arrived, []
        tab = None if own is None else self._tabs.add(own)
        others = [page for page in arrived if page is not own]
        if self._late_opens:
            self._arrived = others
            others = []
        for page in others:
            self._admit_by_page(page)
        if tab is not None:
            self._tabs.select(tab.id)
        return tab

    def _spawn(self, coroutine: Awaitable[Any]) -> None:
        """Run ``coroutine`` in the background; its failure is logged, never raised."""
        task = asyncio.create_task(coroutine)
        self._background.add(task)
        task.add_done_callback(self._background_done)

    def _background_done(self, task: asyncio.Task) -> None:
        self._background.discard(task)
        if not task.cancelled() and task.exception() is not None:
            error = type(task.exception()).__name__
            logger.debug("Background browser call failed", error=error)

    def _on_page_closed(self, page: PageDriver) -> None:
        if page in self._arrived:
            self._arrived.remove(page)
        tab = self._tabs.remove(page)
        if tab is not None:
            self._forget_tab(tab)

    def _forget_tab(self, tab: Tab) -> None:
        self._refs.reset(tab.id)
        self._dialogs.forget_tab(tab.id)
        self._nav.pop(tab.id, None)
        self._last.pop(tab.id, None)

    def _on_dialog(self, page: PageDriver, dialog: DialogDriver) -> None:
        tab = self._tabs.find(page)
        if tab is None and page not in self._refused_pages:
            if page in self._arrived:
                self._arrived.remove(page)
            tab = self._admit_by_page(page)
        if tab is None:
            self._spawn(dialog.dismiss())
            return
        self._dialogs.opened(tab.id, dialog)

    def _on_window_opening(self, page: PageDriver) -> None:
        """A known tab asked for a new window; ``page_opened`` for it usually comes later.

        A page that registered first, with no window due, is matched here instead.
        """
        if self._tabs.find(page) is None:
            return
        if self._windows_early:
            self._windows_early -= 1
        else:
            self._windows_due += 1

    async def _await_windows(self, op: Op) -> None:
        """Wait, up to ``WINDOW_WAIT_S``, for announced windows to register as tabs."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + WINDOW_WAIT_S
        while self._windows_due and loop.time() < deadline:
            self._window_arrived.clear()
            try:
                await asyncio.wait_for(
                    self._window_arrived.wait(), deadline - loop.time()
                )
            except asyncio.TimeoutError:
                break
        if self._windows_due:
            self._windows_due = 0
            op.notes.append(
                f"A new tab was requested but did not open within {WINDOW_WAIT_S:g} s; "
                "list tabs to check."
            )

    def _on_navigation(self, page: PageDriver, url: str | None) -> None:
        tab = self._tabs.find(page)
        if tab is None:
            return
        self._nav_event(tab).set()
        self._nav_counts[tab.id] = self._nav_counts.get(tab.id, 0) + 1
        if url:
            chain = self._nav_urls.setdefault(tab.id, [])
            chain.append(url)
            del chain[:-NAV_CHAIN_MAX]

    def _nav_event(self, tab: Tab) -> asyncio.Event:
        event = self._nav.get(tab.id)
        if event is None:
            event = self._nav[tab.id] = asyncio.Event()
        return event

    async def _bounded(
        self, operation: Callable[[], Awaitable[Any]], timeout: float, what: str
    ) -> Any:
        """A browser-level call bounded by ``timeout`` plus a margin."""
        try:
            return await asyncio.wait_for(operation(), timeout + CALL_MARGIN_S)
        except asyncio.TimeoutError:
            raise BrowserTimeoutError(f"{what} timed out after {timeout:g} s") from None

    async def _call(
        self,
        tab: Tab,
        operation: Callable[[], Awaitable[Any]],
        timeout: float,
        what: str,
    ) -> Any:
        """Run a page call bounded by ``timeout``; a dialog it opens parks it and raises."""
        call = asyncio.create_task(_awaited(operation))
        waiter = asyncio.create_task(self._dialogs.wait_opened(tab.id))
        try:
            done, _ = await asyncio.wait(
                {call, waiter},
                timeout=timeout + CALL_MARGIN_S,
                return_when=asyncio.FIRST_COMPLETED,
            )
        except asyncio.CancelledError:
            discard(call)
            raise
        finally:
            waiter.cancel()
        if call in done:
            return call.result()
        if waiter in done:
            record = waiter.result()
            self._dialogs.park(tab.id, call)
            raise _dialog_opened(record)
        discard(call)
        raise BrowserTimeoutError(f"{what} timed out after {timeout:g} s")

    async def _pause(self, tab: Tab, seconds: float) -> None:
        """Sleep ``seconds``; a dialog opening on ``tab`` ends it with ``DialogOpenedError``."""
        sleeper = asyncio.create_task(asyncio.sleep(seconds))
        waiter = asyncio.create_task(self._dialogs.wait_opened(tab.id))
        try:
            done, _ = await asyncio.wait(
                {sleeper, waiter}, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            sleeper.cancel()
            waiter.cancel()
        if waiter in done:
            raise _dialog_opened(waiter.result())


async def _awaited(operation: Callable[[], Awaitable[Any]]) -> Any:
    return await operation()


def _dialog_opened(record: Any) -> DialogOpenedError:
    return DialogOpenedError(
        record,
        f"a {record.describe()} opened; answer it with the dialog action",
        detail=record.message,
    )


def _succeeded(task: asyncio.Task) -> bool:
    return not task.cancelled() and task.exception() is None


def _cancel_requested() -> bool:
    task = asyncio.current_task()
    cancelling = getattr(task, "cancelling", None)
    return bool(cancelling and cancelling())
