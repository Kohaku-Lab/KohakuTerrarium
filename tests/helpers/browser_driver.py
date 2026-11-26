"""A scripted implementation of the browser driver protocol, for tests without a browser.

``ScriptedDriver`` launches ``ScriptedBrowser`` objects whose pages answer
from plain attributes (``snapshot_text``, ``title_text``, …). Tests steer a
primitive with ``page.handlers[name]`` (a callable, sync or async, called with
the primitive's keyword arguments), make it raise with ``page.raises[name]``,
or make it hang with ``page.hang.add(name)``; a hung call fails the way
Playwright's do once the browser crashes or is closed. ``alive`` tells whether
the fake browser process would still be running; ``survives_kill`` makes a
forced kill leave it running, and ``kill_delay`` makes the kill take that many
seconds of blocking work (off the event loop, as the protocol requires).
"""

import asyncio
import inspect
import io
import itertools
import random
import struct
import time
from collections.abc import Awaitable
from typing import Any

from PIL import Image

from kohakuterrarium.browser.errors import DriverDisconnectedError, PageClosedError
from kohakuterrarium.browser.launch import LaunchPlan
from kohakuterrarium.browser.observe import frame_owners
from kohakuterrarium.browser.processes import in_thread
from kohakuterrarium.browser.protocol import (
    DOCUMENT_ID_JS,
    FRAME_DOCUMENT_ID_JS,
    FrameView,
    NavResponse,
    PageContent,
    ProbeFrames,
)
from kohakuterrarium.browser.refs import frame_prefix
from kohakuterrarium.browser.settle import QUIET_JS
from kohakuterrarium.browser.snapshot import parse as parse_snapshot

_DOCS = itertools.count(1)
_MISSING = object()


def png_bytes(width: int, height: int, padding: int = 0) -> bytes:
    """A PNG header announcing ``width`` x ``height``, padded to a chosen size."""
    header = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR"
    return (
        header
        + struct.pack(">II", width, height)
        + b"\x08\x06\x00\x00\x00"
        + (b"\x00" * padding)
    )


def jpeg_bytes(width: int, height: int, padding: int = 0) -> bytes:
    """A JPEG start with an SOF0 segment announcing ``width`` x ``height``."""
    sof = b"\xff\xc0" + struct.pack(">HBHH", 11, 8, height, width) + b"\x03\x01\x22\x00"
    return b"\xff\xd8" + sof + b"\x00" * padding


def noise_image(
    width: int, height: int, image_type: str = "png", quality: int | None = None
) -> bytes:
    """A real PNG or JPEG of random pixels, which compresses badly."""
    pixels = random.Random(width * 7919 + height).randbytes(width * height * 3)
    image = Image.frombytes("RGB", (width, height), pixels)
    out = io.BytesIO()
    if image_type == "png":
        image.save(out, "PNG")
    else:
        image.save(out, "JPEG", quality=quality or 75)
    return out.getvalue()


class ScriptedDialog:
    """A dialog whose answer resolves ``answered`` (the call that opened it waits on it)."""

    def __init__(self, kind: str, message: str, default_value: str = "") -> None:
        self.kind = kind
        self.message = message
        self.default_value = default_value
        self.answer: tuple[str, str | None] | None = None
        self.answered = asyncio.get_running_loop().create_future()

    async def accept(self, text: str | None = None) -> None:
        self.answer = ("accept", text)
        if not self.answered.done():
            self.answered.set_result(self.answer)

    async def dismiss(self) -> None:
        self.answer = ("dismiss", None)
        if not self.answered.done():
            self.answered.set_result(self.answer)


class ScriptedDownload:
    """A download whose ``save_as`` writes ``content`` (or raises ``error``)."""

    def __init__(
        self, suggested_filename: str, content: bytes = b"data", error: str = ""
    ) -> None:
        self.suggested_filename = suggested_filename
        self.url = "http://example.test/" + suggested_filename
        self.content = content
        self.error = error
        self.saved_to: str | None = None
        self.cancelled = False

    async def cancel(self) -> None:
        self.cancelled = True

    async def save_as(self, path: str) -> None:
        if self.error:
            raise RuntimeError(self.error)
        with open(path, "wb") as handle:
            handle.write(self.content)
        self.saved_to = path


class ScriptedPage:
    """One fake tab; every protocol primitive is recorded in ``calls``."""

    def __init__(
        self,
        browser: "ScriptedBrowser",
        url: str = "about:blank",
        snapshot: str = "",
        title: str = "",
    ) -> None:
        self.browser = browser
        self._url = url
        self.snapshot_text = snapshot
        self.boxes_text = ""
        self.title_text = title
        self.html_text = ""
        self.body_text = ""
        self.password_values: list[str] = []
        self.doc = next(_DOCS)
        self.probe_results: list[Any] = [{"hidden": [], "visible": [], "passwords": []}]
        self.probe_failed = 0
        self.frame_url_map: dict[str, str] = {}
        self.frame_doc_map: dict[str, Any] = {}
        self.frame_prefixes: dict[str, str] = {}
        self.eval_value: Any = None
        self.history_back: list[str] = []
        self.closed = False
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.typed: list[tuple[str, str]] = []
        self.handlers: dict[str, Any] = {}
        self.raises: dict[str, BaseException] = {}
        self.hang: set[str] = set()

    @property
    def url(self) -> str:
        return self._url

    def is_closed(self) -> bool:
        return self.closed

    def names(self) -> list[str]:
        """The primitive names called so far, in order."""
        return [name for name, _ in self.calls]

    def load(
        self, url: str, snapshot: str | None = None, title: str | None = None
    ) -> None:
        """Simulate a main-document navigation: new document, event fired.

        Fields ``browser.site[url]`` names (``snapshot_text``, ``title_text``,
        ``html_text``, ``body_text``) are set first.
        """
        self.history_back.append(self._url)
        self._url = url
        self.doc = next(_DOCS)
        for name, value in self.browser.site.get(url, {}).items():
            setattr(self, name, value)
        if snapshot is not None:
            self.snapshot_text = snapshot
        if title is not None:
            self.title_text = title
        self.browser.sink.navigation_started(self, url)

    def open_dialog(self, dialog: ScriptedDialog) -> ScriptedDialog:
        self.browser.sink.dialog_opened(self, dialog)
        return dialog

    async def _enter(self, name: str, **kwargs: Any) -> Any:
        self.calls.append((name, kwargs))
        if self.browser.dead:
            raise DriverDisconnectedError("the browser closed unexpectedly")
        if self.closed:
            raise PageClosedError("the tab was closed")
        if name in self.raises:
            raise self.raises[name]
        if name in self.hang:
            await self.browser.hung()
        handler = self.handlers.get(name)
        if handler is None:
            return _MISSING
        result = handler(**kwargs)
        if inspect.isawaitable(result):
            result = await result
        return result

    async def _do(self, name: str, default: Any = None, **kwargs: Any) -> Any:
        result = await self._enter(name, **kwargs)
        return default if result is _MISSING else result

    async def title(self, timeout: float) -> str:
        return await self._do("title", self.title_text, timeout=timeout)

    async def goto(self, url: str, timeout: float) -> NavResponse:
        result = await self._enter("goto", url=url, timeout=timeout)
        if result is _MISSING:
            self.load(url)
            return NavResponse(url, 200)
        return result

    async def history(self, step: str, timeout: float) -> NavResponse | None:
        result = await self._enter("history", step=step, timeout=timeout)
        if result is not _MISSING:
            return result
        if step == "reload":
            self.doc = next(_DOCS)
            return NavResponse(self._url, 200)
        if step == "back" and self.history_back:
            self._url = self.history_back.pop()
            self.doc = next(_DOCS)
            return NavResponse(self._url, 200)
        return None

    async def wait_for_load(self, timeout: float) -> None:
        await self._do("wait_for_load", timeout=timeout)

    async def snapshot(self, timeout: float, boxes: bool = False) -> str:
        default = self.boxes_text if boxes and self.boxes_text else self.snapshot_text
        if boxes:
            return await self._do("snapshot", default, timeout=timeout, boxes=True)
        return await self._do("snapshot", default, timeout=timeout)

    async def probe(self, script: str, timeout: float) -> ProbeFrames:
        result = await self._enter("probe", timeout=timeout)
        if result is _MISSING:
            return ProbeFrames(list(self.probe_results), self.probe_failed)
        return result

    async def frame_views(self, refs, timeout: float) -> dict[str, FrameView]:
        """Views from ``frame_url_map`` (URL) and ``frame_doc_map`` (document id)."""
        known = {**dict.fromkeys(self.frame_doc_map, ""), **self.frame_url_map}
        views = {
            ref: FrameView(url, self.frame_doc_map.get(ref))
            for ref, url in known.items()
            if ref in refs
        }
        return await self._do("frame_views", views, refs=list(refs), timeout=timeout)

    async def evaluate(
        self,
        expression: str,
        arg: Any = None,
        *,
        ref: str | None = None,
        timeout: float,
    ) -> Any:
        if expression == DOCUMENT_ID_JS:
            return await self._do("document_id", self.doc, timeout=timeout)
        if expression == FRAME_DOCUMENT_ID_JS:
            prefix = ref.rsplit("e", 1)[0] if ref else ""
            frame = self.frame_prefixes.get(prefix)
            doc = self.frame_doc_map.get(frame) if frame else None
            return await self._do("frame_document", doc, ref=ref, timeout=timeout)
        if expression == QUIET_JS:
            return await self._do("settle", "quiet", arg=arg, timeout=timeout)
        return await self._do(
            "evaluate", self.eval_value, expression=expression, ref=ref, timeout=timeout
        )

    async def content(self, max_chars: int, timeout: float) -> PageContent:
        html, text = self.html_text, self.body_text
        default = PageContent(
            html[:max_chars],
            text[:max_chars],
            len(html),
            len(text),
            tuple(self.password_values),
        )
        return await self._do("content", default, max_chars=max_chars, timeout=timeout)

    async def ref_count(self, ref: str, timeout: float) -> int:
        present = 1 if f"[ref={ref}]" in self.snapshot_text else 0
        return await self._do("ref_count", present, ref=ref, timeout=timeout)

    async def click(self, ref, *, button, click_count, modifiers, timeout) -> None:
        await self._do(
            "click",
            ref=ref,
            button=button,
            click_count=click_count,
            modifiers=modifiers,
            timeout=timeout,
        )

    async def click_at(self, x, y, *, button, click_count, modifiers, timeout) -> None:
        await self._do(
            "click_at",
            x=x,
            y=y,
            button=button,
            click_count=click_count,
            modifiers=modifiers,
            timeout=timeout,
        )

    async def fill(self, ref: str, text: str, timeout: float) -> None:
        await self._do("fill", ref=ref, text=text, timeout=timeout)

    async def type_text(self, ref: str, text: str, timeout: float) -> None:
        await self._do("type_text", ref=ref, text=text, timeout=timeout)

    def document_url(self, ref: str) -> str:
        """The URL of the document ``ref`` is in: the tab's, or its frame's from ``frame_url_map``."""
        prefix = frame_prefix(ref)
        if not prefix:
            return self._url
        owner = frame_owners(parse_snapshot(self.snapshot_text)).get(prefix)
        return self.frame_url_map.get(owner, "") if owner else ""

    async def type_checked(self, ref, text, allow, *, timeout) -> str:
        """``text`` lands in ``typed`` when ``allow`` accepts the element's document URL.

        A ``type_checked`` handler returns the URL the element is in instead.
        """
        result = await self._enter("type_checked", ref=ref, timeout=timeout)
        url = self.document_url(ref) if result is _MISSING else result
        if allow(url):
            self.typed.append((ref, text))
        return url

    async def press(self, ref, key: str, timeout: float) -> None:
        await self._do("press", ref=ref, key=key, timeout=timeout)

    async def select(self, ref: str, labels, timeout: float) -> list[str]:
        return await self._do(
            "select", list(labels), ref=ref, labels=list(labels), timeout=timeout
        )

    async def set_checked(self, ref: str, checked: bool, timeout: float) -> None:
        await self._do("set_checked", ref=ref, checked=checked, timeout=timeout)

    async def hover(self, ref: str, timeout: float) -> None:
        await self._do("hover", ref=ref, timeout=timeout)

    async def hover_at(self, x: float, y: float, timeout: float) -> None:
        await self._do("hover_at", x=x, y=y, timeout=timeout)

    async def scroll_into_view(self, ref: str, timeout: float) -> None:
        await self._do("scroll_into_view", ref=ref, timeout=timeout)

    async def wheel(self, dx: float, dy: float, timeout: float) -> None:
        await self._do("wheel", dx=dx, dy=dy, timeout=timeout)

    async def drag(self, ref: str, to_ref: str, timeout: float) -> None:
        await self._do("drag", ref=ref, to_ref=to_ref, timeout=timeout)

    async def drag_between(self, start, end, timeout: float) -> None:
        await self._do("drag_between", start=start, end=end, timeout=timeout)

    async def set_input_files(self, ref: str, files, timeout: float) -> None:
        await self._do("set_input_files", ref=ref, files=list(files), timeout=timeout)

    async def choose_files(self, ref: str, files, timeout: float) -> None:
        await self._do("choose_files", ref=ref, files=list(files), timeout=timeout)

    async def wait_for_text(self, text: str, *, gone: bool, timeout: float) -> None:
        await self._do("wait_for_text", text=text, gone=gone, timeout=timeout)

    async def screenshot(
        self, *, ref, full_page, max_height, image_type, quality, timeout
    ) -> bytes:
        default = png_bytes(1280, 800) if image_type == "png" else jpeg_bytes(1280, 800)
        return await self._do(
            "screenshot",
            default,
            ref=ref,
            full_page=full_page,
            max_height=max_height,
            image_type=image_type,
            quality=quality,
            timeout=timeout,
        )

    async def bring_to_front(self, timeout: float) -> None:
        await self._do("bring_to_front", timeout=timeout)

    async def close(self, timeout: float) -> None:
        await self._do("close", timeout=timeout)
        self.mark_closed()

    def mark_closed(self) -> None:
        if not self.closed:
            self.closed = True
            self.browser.sink.page_closed(self)


class ScriptedBrowser:
    """A fake launched browser with staged shutdown that tests can make hang or fail."""

    def __init__(self, driver: "ScriptedDriver", plan: LaunchPlan, sink: Any) -> None:
        self.driver = driver
        self.plan = plan
        self.sink = sink
        self.alive = True
        self.connected = True
        self.dead = False
        self.killed = False
        self.survives_kill = driver.survives_kill
        self.kill_delay = 0.0
        self._killing: asyncio.Future | None = None
        self.unresponsive = False
        self.probes = 0
        self.stages: list[str] = []
        self.hang_stages: set[str] = set()
        self.fail_stages: set[str] = set()
        self.page_list: list[ScriptedPage] = []
        self.site: dict[str, dict[str, Any]] = {}
        self.page_setup = None
        self._hung: list[asyncio.Future] = []
        first = 4000 + 10 * len(driver.browsers)
        self.pids = [first, first + 1, first + 2]

    def start(self) -> None:
        page = ScriptedPage(self)
        self.page_list.append(page)
        self.sink.page_opened(page)

    @property
    def page(self) -> ScriptedPage:
        """The first open page."""
        return self.pages()[0]

    async def hung(self) -> None:
        future = asyncio.get_running_loop().create_future()
        self._hung.append(future)
        await future

    def _release(self, error: BaseException) -> None:
        for future in self._hung:
            if not future.done():
                future.set_exception(error)
        self._hung.clear()

    def pages(self) -> list[ScriptedPage]:
        return [p for p in self.page_list if not p.closed]

    def is_connected(self) -> bool:
        return self.connected

    async def process_ids(self) -> list[int]:
        return list(self.pids) if self.connected else []

    async def responsive(self, timeout: float) -> bool:
        self.probes += 1
        if self.unresponsive:
            await asyncio.sleep(timeout)
            return False
        return self.connected

    async def new_page(self, timeout: float) -> ScriptedPage:
        if self.dead:
            raise DriverDisconnectedError("the browser closed unexpectedly")
        page = ScriptedPage(self)
        if self.page_setup is not None:
            self.page_setup(page)
        self.page_list.append(page)
        self.sink.page_opened(page)
        return page

    def open_popup(
        self,
        url: str,
        snapshot: str = "",
        title: str = "",
        opener: "ScriptedPage | None" = None,
    ) -> ScriptedPage:
        """A page the site opened (``window.open``, ``target=_blank``).

        With an ``opener``, ``popup_opened`` follows ``page_opened`` at once,
        the order Playwright reports them in; without one it is a
        ``noopener`` window.
        """
        page = ScriptedPage(self, url, snapshot, title)
        self.page_list.append(page)
        self.sink.page_opened(page)
        if opener is not None:
            self.sink.popup_opened(opener, page)
        return page

    def crash(self) -> None:
        """The browser process dies: calls fail, the sink hears ``disconnected``."""
        self.alive = False
        self.connected = False
        self.dead = True
        self._release(DriverDisconnectedError("the browser closed unexpectedly"))
        self.sink.disconnected()

    async def _stage(self, name: str) -> None:
        self.stages.append(name)
        if name in self.hang_stages:
            await asyncio.Event().wait()
        if name in self.fail_stages:
            raise RuntimeError(f"{name} failed")

    async def close_context(self) -> None:
        await self._stage("context")
        self.connected = False
        self._release(PageClosedError("the tab was closed"))

    async def close_browser(self) -> None:
        await self._stage("browser")
        self.alive = False

    async def stop(self) -> None:
        await self._stage("playwright")
        self.alive = False

    def kill(self) -> Awaitable[None]:
        """Start the kill now; the process ends once ``kill_delay`` has passed.

        A later call waits for the first kill, as the Playwright driver's does.
        """
        if self._killing is None:
            self.killed = True
            self.connected = False
            self._release(DriverDisconnectedError("the browser closed unexpectedly"))
            if self.kill_delay:
                blocking = in_thread(lambda: time.sleep(self.kill_delay))
                self._killing = asyncio.ensure_future(self._slow_kill(blocking))
            else:
                self._killed()
                self._killing = asyncio.get_running_loop().create_future()
                self._killing.set_result(None)
        return asyncio.shield(self._killing)

    async def _slow_kill(self, blocking: Awaitable[None]) -> None:
        await blocking
        self._killed()

    def _killed(self) -> None:
        """The kill took effect; like Playwright, report the lost browser a moment later."""
        was_alive = self.alive
        self.alive = self.alive and self.survives_kill
        if was_alive and not self.alive:
            asyncio.get_running_loop().call_soon(self.sink.disconnected)

    def exited(self) -> bool:
        return not self.alive


class ScriptedDriver:
    """Launches scripted browsers; ``setup(browser)`` prepares each new one.

    ``fail`` makes launches raise after starting a browser process (which
    the launch then kills); ``fail_before_start`` makes them raise before
    starting any; ``block`` (an ``asyncio.Event``) holds a launch half-way
    until set, with a browser process already alive; ``survives_kill`` is
    given to each new browser. ``cleanup_hang`` (an ``asyncio.Event``) holds
    a failed launch's cleanup, before its kill, until set, as a hung close
    would; ``cleaning`` tells that a cleanup began.
    """

    def __init__(self, setup=None) -> None:
        self.setup = setup
        self.plans: list[LaunchPlan] = []
        self.browsers: list[ScriptedBrowser] = []
        self.fail: BaseException | None = None
        self.fail_before_start: BaseException | None = None
        self.block: asyncio.Event | None = None
        self.survives_kill = False
        self.cleanup_hang: asyncio.Event | None = None
        self.cleaning = False
        self.started = asyncio.Event()

    @property
    def browser(self) -> ScriptedBrowser:
        """The most recently launched browser."""
        return self.browsers[-1]

    def alive(self) -> list[ScriptedBrowser]:
        """Browsers whose process would still be running."""
        return [b for b in self.browsers if b.alive]

    async def launch(self, plan: LaunchPlan, sink: Any) -> ScriptedBrowser:
        self.plans.append(plan)
        if self.fail_before_start is not None:
            raise self.fail_before_start
        browser = ScriptedBrowser(self, plan, sink)
        self.browsers.append(browser)
        sink.launch_started(browser)
        self.started.set()
        try:
            if self.block is not None:
                await self.block.wait()
            if self.fail is not None:
                raise self.fail
        except BaseException:
            self.cleaning = True
            if self.cleanup_hang is not None:
                await self.cleanup_hang.wait()
            await browser.kill()
            raise
        browser.start()
        if self.setup is not None:
            self.setup(browser)
        return browser
