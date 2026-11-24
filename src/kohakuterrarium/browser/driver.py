"""Playwright implementation of the driver protocol.

The only module that touches Playwright, and only lazily: importing it never
needs Playwright installed. Playwright errors become the ``Driver*`` errors
of ``browser.errors`` with a short reason; call logs never leave this module.
"""

import asyncio
import re
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from kohakuterrarium.browser.errors import (
    BrowserLaunchError,
    BrowserUnavailableError,
    ContextDestroyedError,
    DriverDisconnectedError,
    DriverError,
    DriverTimeoutError,
    NotFileInputError,
    PageClosedError,
)
from kohakuterrarium.browser.launch import Candidate, LaunchPlan, check_event_loop
from kohakuterrarium.browser.protocol import EventSink, NavResponse, ProbeFrames
from kohakuterrarium.browser.proxy import BLOCKED_HEADER
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

INSTALL_HINT = (
    "install it with: pip install 'kohakuterrarium[browser-use]' "
    "and then run: playwright install chromium"
)
CLEANUP_TIMEOUT_S = 10.0
REASON_CHARS = 200
DRAG_STEPS = 8
_TIMEOUT_HINTS = (
    (
        "intercepts pointer events",
        "another element covers it (for example a banner or overlay); close that "
        "first, or click by x/y",
    ),
    ("element is not enabled", "the element is disabled"),
    ("element is not editable", "the element is not editable"),
    ("element is not visible", "the element is not visible"),
    ("element is not stable", "the element keeps moving"),
    ("did not find some options", "no option has that label"),
    (
        "element was detached from the DOM",
        "the element was replaced while acting; take a snapshot and retry",
    ),
)
_CALL_PREFIX = re.compile(r"^(?:Error: )?(?:[A-Za-z]+\.[A-Za-z_]+: )?(?:Error: )?")
_TIMEOUT_MS = re.compile(r"Timeout (\d+)ms exceeded")
_NET_ERROR = re.compile(r"\bnet::ERR_[A-Z_]{1,60}")
_SCROLL_HEIGHT_JS = (
    "() => Math.max(document.documentElement ? document.documentElement.scrollHeight : 0,"
    " document.body ? document.body.scrollHeight : 0)"
)


def load_playwright() -> Any:
    """``playwright.async_api``, or ``BrowserUnavailableError`` saying how to install it."""
    try:
        from playwright import async_api
    except ImportError as exc:
        raise BrowserUnavailableError(
            f"the browser tools need Playwright, which is not installed; {INSTALL_HINT}"
        ) from exc
    return async_api


def first_line(message: str) -> str:
    """The first line of a Playwright error without its ``Locator.click:`` prefix, capped."""
    line = (message or "").strip().split("\n", 1)[0].strip()
    line = _CALL_PREFIX.sub("", line)
    return line[:REASON_CHARS]


def timeout_reason(message: str) -> str:
    """A short reason for a Playwright timeout, read from its call log."""
    match = _TIMEOUT_MS.search(message)
    head = f"timed out after {int(match.group(1)) / 1000:g} s" if match else "timed out"
    hint = next((h for needle, h in _TIMEOUT_HINTS if needle in message), "")
    return f"{head}: {hint}" if hint else head


class _Dialog:
    def __init__(self, dialog: Any, owner: "PlaywrightBrowser") -> None:
        self._dialog = dialog
        self._owner = owner
        self.kind = dialog.type
        self.message = dialog.message
        self.default_value = dialog.default_value

    async def accept(self, text: str | None = None) -> None:
        args = () if text is None else (text,)
        try:
            await self._dialog.accept(*args)
        except self._owner.api.Error as exc:
            raise self._owner.translate(exc) from None

    async def dismiss(self) -> None:
        try:
            await self._dialog.dismiss()
        except self._owner.api.Error as exc:
            raise self._owner.translate(exc) from None


class _Download:
    def __init__(self, download: Any) -> None:
        self._download = download
        self.suggested_filename = download.suggested_filename
        self.url = download.url

    async def save_as(self, path: str) -> None:
        await self._download.save_as(path)

    async def cancel(self) -> None:
        await self._download.cancel()


class PlaywrightPage:
    """One Playwright page behind the ``PageDriver`` protocol."""

    def __init__(self, owner: "PlaywrightBrowser", page: Any) -> None:
        self._owner = owner
        self._page = page

    @property
    def url(self) -> str:
        return self._page.url

    def is_closed(self) -> bool:
        return self._page.is_closed()

    async def _run(self, operation: Callable[[], Awaitable[Any]]) -> Any:
        try:
            return await operation()
        except self._owner.api.Error as exc:
            raise self._owner.translate(exc, self._page) from None
        except asyncio.TimeoutError:
            raise DriverTimeoutError("timed out") from None

    def _ref(self, ref: str) -> Any:
        return self._page.get_by_ref(ref)

    async def title(self, timeout: float) -> str:
        return await self._run(lambda: asyncio.wait_for(self._page.title(), timeout))

    async def goto(self, url: str, timeout: float) -> NavResponse:
        try:
            response = await self._run(
                lambda: self._page.goto(
                    url, timeout=timeout * 1000, wait_until="domcontentloaded"
                )
            )
        except DriverError as exc:
            if "Download is starting" in exc.detail:
                return NavResponse(self._page.url)
            raise
        return self._response(response)

    def _response(self, response: Any) -> NavResponse:
        if response is None:
            return NavResponse(self._page.url)
        blocked = response.headers.get(BLOCKED_HEADER.lower())
        return NavResponse(self._page.url, response.status, blocked)

    async def history(self, step: str, timeout: float) -> NavResponse | None:
        options = {"timeout": timeout * 1000, "wait_until": "domcontentloaded"}
        match step:
            case "back":
                response = await self._run(lambda: self._page.go_back(**options))
            case "forward":
                response = await self._run(lambda: self._page.go_forward(**options))
            case "reload":
                return self._response(
                    await self._run(lambda: self._page.reload(**options))
                )
            case _:
                raise DriverError(f"unknown history step {step!r}")
        return None if response is None else self._response(response)

    async def wait_for_load(self, timeout: float) -> None:
        await self._run(
            lambda: self._page.wait_for_load_state(
                "domcontentloaded", timeout=timeout * 1000
            )
        )

    async def snapshot(self, timeout: float, boxes: bool = False) -> str:
        options: dict[str, Any] = {"mode": "ai", "timeout": timeout * 1000}
        if boxes:
            options["boxes"] = True
        return await self._run(lambda: self._page.aria_snapshot(**options))

    async def probe(self, script: str, timeout: float) -> ProbeFrames:
        frames = list(self._page.frames)
        results = await asyncio.gather(
            *(asyncio.wait_for(frame.evaluate(script), timeout) for frame in frames),
            return_exceptions=True,
        )
        if not self._owner.is_connected():
            raise DriverDisconnectedError("the browser closed unexpectedly")
        good = [r for r in results if not isinstance(r, BaseException)]
        return ProbeFrames(good, len(results) - len(good))

    async def frame_urls(self, refs: Sequence[str], timeout: float) -> dict[str, str]:
        async def one(ref: str) -> str | None:
            handles = await self._ref(ref).element_handles()
            try:
                frame = await handles[0].content_frame() if handles else None
                return frame.url if frame is not None else None
            finally:
                for handle in handles:
                    await handle.dispose()

        results = await asyncio.gather(
            *(asyncio.wait_for(one(ref), timeout) for ref in refs),
            return_exceptions=True,
        )
        if not self._owner.is_connected():
            raise DriverDisconnectedError("the browser closed unexpectedly")
        return {
            ref: url for ref, url in zip(refs, results) if isinstance(url, str) and url
        }

    async def evaluate(
        self,
        expression: str,
        arg: Any = None,
        *,
        ref: str | None = None,
        timeout: float,
    ) -> Any:
        if ref is not None:
            return await self._run(
                lambda: self._ref(ref).evaluate(expression, arg, timeout=timeout * 1000)
            )
        return await self._run(
            lambda: asyncio.wait_for(self._page.evaluate(expression, arg), timeout)
        )

    async def ref_count(self, ref: str, timeout: float) -> int:
        return await self._run(
            lambda: asyncio.wait_for(self._ref(ref).count(), timeout)
        )

    async def click(
        self,
        ref: str,
        *,
        button: str,
        click_count: int,
        modifiers: Sequence[str],
        timeout: float,
    ) -> None:
        await self._run(
            lambda: self._ref(ref).click(
                button=button,
                click_count=click_count,
                modifiers=list(modifiers),
                timeout=timeout * 1000,
            )
        )

    async def click_at(
        self,
        x: float,
        y: float,
        *,
        button: str,
        click_count: int,
        modifiers: Sequence[str],
        timeout: float,
    ) -> None:
        keyboard, mouse = self._page.keyboard, self._page.mouse

        async def click() -> None:
            for key in modifiers:
                await keyboard.down(key)
            try:
                await mouse.click(x, y, button=button, click_count=click_count)
            finally:
                for key in reversed(modifiers):
                    await keyboard.up(key)

        await self._run(lambda: asyncio.wait_for(click(), timeout))

    async def fill(self, ref: str, text: str, timeout: float) -> None:
        await self._run(lambda: self._ref(ref).fill(text, timeout=timeout * 1000))

    async def type_text(self, ref: str, text: str, timeout: float) -> None:
        await self._run(
            lambda: self._ref(ref).press_sequentially(text, timeout=timeout * 1000)
        )

    async def press(self, ref: str | None, key: str, timeout: float) -> None:
        if ref is None:
            await self._run(
                lambda: asyncio.wait_for(self._page.keyboard.press(key), timeout)
            )
        else:
            await self._run(lambda: self._ref(ref).press(key, timeout=timeout * 1000))

    async def select(
        self, ref: str, labels: Sequence[str], timeout: float
    ) -> list[str]:
        return await self._run(
            lambda: self._ref(ref).select_option(
                label=list(labels), timeout=timeout * 1000
            )
        )

    async def set_checked(self, ref: str, checked: bool, timeout: float) -> None:
        await self._run(
            lambda: self._ref(ref).set_checked(checked, timeout=timeout * 1000)
        )

    async def hover(self, ref: str, timeout: float) -> None:
        await self._run(lambda: self._ref(ref).hover(timeout=timeout * 1000))

    async def hover_at(self, x: float, y: float, timeout: float) -> None:
        await self._run(lambda: asyncio.wait_for(self._page.mouse.move(x, y), timeout))

    async def scroll_into_view(self, ref: str, timeout: float) -> None:
        await self._run(
            lambda: self._ref(ref).scroll_into_view_if_needed(timeout=timeout * 1000)
        )

    async def wheel(self, dx: float, dy: float, timeout: float) -> None:
        size = self._page.viewport_size or {"width": 800, "height": 600}
        mouse = self._page.mouse

        async def wheel() -> None:
            await mouse.move(size["width"] / 2, size["height"] / 2)
            await mouse.wheel(dx, dy)

        await self._run(lambda: asyncio.wait_for(wheel(), timeout))

    async def drag(self, ref: str, to_ref: str, timeout: float) -> None:
        await self._run(
            lambda: self._ref(ref).drag_to(self._ref(to_ref), timeout=timeout * 1000)
        )

    async def _point(
        self, target: str | tuple[float, float], timeout: float
    ) -> tuple[float, float]:
        if not isinstance(target, str):
            return target
        locator = self._ref(target)
        await locator.scroll_into_view_if_needed(timeout=timeout * 1000)
        box = await locator.bounding_box(timeout=timeout * 1000)
        if box is None:
            raise DriverError("the element has no box on the page")
        return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2

    async def drag_between(
        self,
        start: str | tuple[float, float],
        end: str | tuple[float, float],
        timeout: float,
    ) -> None:
        mouse = self._page.mouse

        async def drag() -> None:
            sx, sy = await self._point(start, timeout)
            await mouse.move(sx, sy)
            await mouse.down()
            ex, ey = await self._point(end, timeout)
            await mouse.move(ex, ey, steps=DRAG_STEPS)
            await mouse.up()

        await self._run(lambda: asyncio.wait_for(drag(), timeout))

    async def set_input_files(
        self, ref: str, files: Sequence[str], timeout: float
    ) -> None:
        try:
            await self._run(
                lambda: self._ref(ref).set_input_files(
                    list(files), timeout=timeout * 1000
                )
            )
        except DriverError as exc:
            text = exc.detail.lower()
            if "htmlinputelement" in text or "not an input" in text:
                raise NotFileInputError(f"{ref} is not a file input") from None
            raise

    async def choose_files(
        self, ref: str, files: Sequence[str], timeout: float
    ) -> None:
        async def choose() -> None:
            async with self._page.expect_file_chooser(
                timeout=timeout * 1000
            ) as chooser_info:
                await self._ref(ref).click(timeout=timeout * 1000)
            chooser = await chooser_info.value
            await chooser.set_files(list(files), timeout=timeout * 1000)

        await self._run(choose)

    async def wait_for_text(self, text: str, *, gone: bool, timeout: float) -> None:
        state = "hidden" if gone else "visible"
        await self._run(
            lambda: self._page.get_by_text(text).first.wait_for(
                state=state, timeout=timeout * 1000
            )
        )

    async def screenshot(
        self,
        *,
        ref: str | None,
        full_page: bool,
        max_height: int,
        image_type: str,
        quality: int | None,
        timeout: float,
    ) -> bytes:
        options: dict[str, Any] = {
            "type": image_type,
            "scale": "css",
            "timeout": timeout * 1000,
        }
        if quality is not None:
            options["quality"] = quality
        if ref is not None:
            return await self._run(lambda: self._ref(ref).screenshot(**options))
        if full_page:
            options["full_page"] = True
            height = await self._run(
                lambda: asyncio.wait_for(
                    self._page.evaluate(_SCROLL_HEIGHT_JS), timeout
                )
            )
            width = (self._page.viewport_size or {"width": 1280})["width"]
            if isinstance(height, (int, float)) and height > max_height:
                options["clip"] = {"x": 0, "y": 0, "width": width, "height": max_height}
        return await self._run(lambda: self._page.screenshot(**options))

    async def bring_to_front(self, timeout: float) -> None:
        await self._run(lambda: asyncio.wait_for(self._page.bring_to_front(), timeout))

    async def close(self, timeout: float) -> None:
        await self._run(lambda: asyncio.wait_for(self._page.close(), timeout))


class PlaywrightBrowser:
    """A launched Playwright browser and its one context, behind ``BrowserDriver``."""

    def __init__(
        self, api: Any, manager: Any, playwright: Any, browser: Any, context: Any
    ) -> None:
        self.api = api
        self._manager = manager
        self._playwright = playwright
        self._browser = browser
        self._context = context
        self._sink: EventSink | None = None
        self._pages: dict[Any, PlaywrightPage] = {}
        self._closing = False
        self._context_closed = False
        self._watchers: set[asyncio.Task] = set()

    def attach(self, sink: EventSink) -> None:
        """Start delivering events to ``sink`` and wrap the pages already open."""
        self._sink = sink
        self._context.on("page", self._on_page)
        self._context.on("close", self._on_context_close)
        for page in self._context.pages:
            self._on_page(page)

    def _wrap(self, page: Any) -> PlaywrightPage:
        wrapped = self._pages.get(page)
        if wrapped is None:
            wrapped = self._pages[page] = PlaywrightPage(self, page)
        return wrapped

    def _on_page(self, page: Any) -> None:
        if page in self._pages:
            return
        wrapped = self._wrap(page)
        sink = self._sink
        page.on("dialog", lambda d: sink.dialog_opened(wrapped, _Dialog(d, self)))
        page.on("download", lambda d: sink.download_started(wrapped, _Download(d)))
        page.on("close", lambda _: self._on_page_close(page))
        page.on(
            "request",
            lambda r: (
                sink.navigation_started(wrapped, r.url)
                if r.is_navigation_request() and r.frame == page.main_frame
                else None
            ),
        )
        page.on(
            "framenavigated",
            lambda f: (
                sink.navigation_started(wrapped, f.url)
                if f == page.main_frame
                else None
            ),
        )
        sink.page_opened(wrapped)
        task = asyncio.get_running_loop().create_task(
            self._watch_windows(page, wrapped)
        )
        self._watchers.add(task)
        task.add_done_callback(self._watchers.discard)

    async def _watch_windows(self, page: Any, wrapped: PlaywrightPage) -> None:
        """Report ``Page.windowOpen``: a popup is coming before Playwright announces it."""
        try:
            session = await self._context.new_cdp_session(page)
            session.on("Page.windowOpen", lambda _event: self._window_opening(wrapped))
            await session.send("Page.enable")
        except Exception as exc:
            logger.debug("Watching for new windows failed", error=type(exc).__name__)

    def _window_opening(self, wrapped: PlaywrightPage) -> None:
        if self._sink is not None:
            self._sink.window_opening(wrapped)

    def _on_page_close(self, page: Any) -> None:
        wrapped = self._pages.pop(page, None)
        if wrapped is not None and self._sink is not None:
            self._sink.page_closed(wrapped)

    def _on_context_close(self, _context: Any) -> None:
        self._context_closed = True
        if not self._closing and self._sink is not None:
            self._sink.disconnected()

    def translate(self, exc: BaseException, page: Any = None) -> DriverError:
        """The ``Driver*`` error for a Playwright exception."""
        message = str(exc)
        if not self.is_connected():
            return DriverDisconnectedError("the browser closed unexpectedly")
        if page is not None and page.is_closed():
            return PageClosedError("the tab was closed")
        if "Execution context was destroyed" in message:
            return ContextDestroyedError("a navigation interrupted the call")
        detail = first_line(message)
        if isinstance(exc, self.api.TimeoutError):
            return DriverTimeoutError(timeout_reason(message), detail=detail)
        code = _NET_ERROR.search(message)
        return DriverError(code.group(0) if code else "the call failed", detail=detail)

    def pages(self) -> list[PlaywrightPage]:
        return [self._wrap(page) for page in self._context.pages]

    def is_connected(self) -> bool:
        if self._context_closed:
            return False
        return self._browser is None or self._browser.is_connected()

    async def responsive(self, timeout: float) -> bool:
        if not self.is_connected():
            return False
        try:
            await asyncio.wait_for(self._context.cookies(), timeout)
        except (asyncio.TimeoutError, self.api.Error):
            return False
        return True

    async def new_page(self, timeout: float) -> PlaywrightPage:
        try:
            page = await asyncio.wait_for(self._context.new_page(), timeout)
        except self.api.Error as exc:
            raise self.translate(exc) from None
        self._on_page(page)
        return self._wrap(page)

    async def close_context(self) -> None:
        self._closing = True
        await self._context.close()

    async def close_browser(self) -> None:
        self._closing = True
        if self._browser is not None:
            await self._browser.close()

    async def stop(self) -> None:
        self._closing = True
        await self._playwright.stop()

    def kill(self) -> None:
        self._closing = True
        _kill_driver(self._manager)

    async def process_ids(self) -> list[int]:
        """Ids of every browser process (browser first), read over CDP; empty when unavailable."""
        if self._browser is None:
            return []
        session = await self._browser.new_browser_cdp_session()
        try:
            info = await session.send("SystemInfo.getProcessInfo")
        finally:
            await session.detach()
        processes = info.get("processInfo", [])
        ordered = sorted(processes, key=lambda p: p.get("type") != "browser")
        return [int(p["id"]) for p in ordered]


def _kill_driver(manager: Any) -> None:
    connection = getattr(manager, "_connection", None)
    transport = getattr(connection, "_transport", None)
    process = getattr(transport, "_proc", None)
    if process is not None and process.returncode is None:
        try:
            process.kill()
        except (OSError, ProcessLookupError):
            pass


class PlaywrightDriver:
    """Starts Chromium through Playwright per a :class:`LaunchPlan`."""

    async def launch(self, plan: LaunchPlan, sink: EventSink) -> PlaywrightBrowser:
        check_event_loop()
        api = load_playwright()
        manager = api.async_playwright()
        playwright = browser = context = None
        try:
            playwright = await manager.start()
            browser, context = await self._open(api, playwright, plan)
            handle = PlaywrightBrowser(api, manager, playwright, browser, context)
            handle.attach(sink)
            if not context.pages:
                await handle.new_page(plan.timeout_s)
            return handle
        except BaseException:
            await _cleanup(manager, playwright, browser, context)
            raise

    async def _open(
        self, api: Any, playwright: Any, plan: LaunchPlan
    ) -> tuple[Any, Any]:
        failures: list[tuple[Candidate, str]] = []
        for candidate in plan.candidates:
            kwargs = plan.launch_kwargs(candidate)
            try:
                if plan.persistent:
                    context = await playwright.chromium.launch_persistent_context(
                        plan.profile_dir, **kwargs, **plan.context_kwargs()
                    )
                    return None, context
                browser = await playwright.chromium.launch(**kwargs)
            except api.Error as exc:
                failures.append((candidate, first_line(str(exc))))
                continue
            try:
                context = await browser.new_context(**plan.context_kwargs())
            except BaseException:
                await _quietly(browser.close())
                raise
            return browser, context
        raise _launch_failure(failures)


def _launch_failure(failures: list[tuple[Candidate, str]]) -> Exception:
    tried = "; ".join(f"{c.label}: {reason}" for c, reason in failures)
    missing = all(
        "doesn't exist" in reason
        or "not found" in reason.lower()
        or "is not installed" in reason
        for _, reason in failures
    )
    if missing:
        return BrowserUnavailableError(
            f"no usable Chromium was found (tried {tried}); run: playwright install chromium"
        )
    return BrowserLaunchError(f"the browser failed to start (tried {tried})")


async def _quietly(operation: Awaitable[Any]) -> None:
    try:
        await asyncio.wait_for(operation, CLEANUP_TIMEOUT_S)
    except Exception as exc:
        logger.debug("Browser cleanup step failed", error=type(exc).__name__)


async def _cleanup(manager: Any, playwright: Any, browser: Any, context: Any) -> None:
    """Undo a launch that failed or was cancelled part-way; never raises."""
    if context is not None:
        await _quietly(context.close())
    if browser is not None:
        await _quietly(browser.close())
    if playwright is not None:
        await _quietly(playwright.stop())
    else:
        _kill_driver(manager)
