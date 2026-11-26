"""The driver protocol: everything ``BrowserSession`` needs from a browser.

``driver.PlaywrightDriver`` implements it with Playwright; tests implement it
with a scripted fake. Refs are Playwright AI-snapshot refs (``e12``,
``f1e3``). Every ``timeout`` is in seconds and bounds the driver's own wait;
the session adds an outer bound. Failures are the ``Driver*`` errors from
``browser.errors``.
"""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from kohakuterrarium.browser.launch import LaunchPlan
from kohakuterrarium.browser.profiles import Exiting

DOCUMENT_ID_JS = "() => performance.timeOrigin"
FRAME_DOCUMENT_ID_JS = "el => el.ownerDocument.defaultView.performance.timeOrigin"


@dataclass(frozen=True)
class NavResponse:
    """The main-document response of a navigation.

    ``blocked`` is the ``X-KT-Browser-Blocked`` code when the filtering proxy
    answered instead of the site; ``download`` is true when the load turned
    into a download instead of a page.
    """

    url: str
    status: int | None = None
    blocked: str | None = None
    download: bool = False


@dataclass(frozen=True)
class PageContent:
    """The main frame's rendered document, each part cut at the caller's bound.

    ``html`` is the doctype plus ``documentElement.outerHTML``; ``text`` is
    ``document.body.innerText`` (the root's ``textContent`` without a body).
    ``html_chars`` / ``text_chars`` are their full lengths before the cut;
    ``passwords`` holds the main frame's password-field values, current and
    as attributes.
    """

    html: str
    text: str
    html_chars: int
    text_chars: int
    passwords: tuple[str, ...] = ()


@dataclass(frozen=True)
class FrameView:
    """What the frame of an ``iframe`` ref shows now: its URL and document id."""

    url: str
    doc: Any = None


@dataclass(frozen=True)
class ProbeFrames:
    """Per-frame results of a probe script, plus how many frames failed to run it."""

    results: list[Any]
    failed: int = 0


class DialogDriver(Protocol):
    """A JavaScript dialog: ``kind`` is alert, confirm, prompt or beforeunload."""

    kind: str
    message: str
    default_value: str

    async def accept(self, text: str | None = None) -> None: ...

    async def dismiss(self) -> None: ...


class DownloadDriver(Protocol):
    """A download the page started."""

    suggested_filename: str
    url: str

    async def save_as(self, path: str) -> None: ...

    async def cancel(self) -> None:
        """Stop a download the session refuses to keep."""


class PageDriver(Protocol):
    """One tab."""

    @property
    def url(self) -> str: ...

    def is_closed(self) -> bool: ...

    async def title(self, timeout: float) -> str: ...

    async def goto(self, url: str, timeout: float) -> NavResponse: ...

    async def history(self, step: str, timeout: float) -> NavResponse | None:
        """``step`` is ``back``, ``forward`` or ``reload``; ``None`` when there was nowhere to go."""

    async def wait_for_load(self, timeout: float) -> None:
        """Wait for ``domcontentloaded`` of the current main document."""

    async def snapshot(self, timeout: float, boxes: bool = False) -> str:
        """The full-page AI-mode ARIA snapshot text; never scoped.

        With ``boxes`` each element line carries ``[box=x,y,w,h]``: its
        bounding box in viewport CSS pixels.
        """

    async def probe(self, script: str, timeout: float) -> ProbeFrames:
        """Evaluate ``script`` (a function source) in every frame."""

    async def frame_views(
        self, refs: Sequence[str], timeout: float
    ) -> dict[str, FrameView]:
        """What each ``iframe`` ref's frame shows now; refs without a frame are left out.

        ``FrameView.doc`` is the frame's ``DOCUMENT_ID_JS`` value, ``None``
        when it could not be read.
        """

    async def evaluate(
        self,
        expression: str,
        arg: Any = None,
        *,
        ref: str | None = None,
        timeout: float,
    ) -> Any:
        """Run script in the main frame, or against the element of ``ref`` as first argument."""

    async def content(self, max_chars: int, timeout: float) -> PageContent:
        """The main frame's serialized DOM and rendered text, each cut at ``max_chars``."""

    async def ref_count(self, ref: str, timeout: float) -> int: ...

    async def click(
        self,
        ref: str,
        *,
        button: str,
        click_count: int,
        modifiers: Sequence[str],
        timeout: float,
    ) -> None: ...

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
        """Click at CSS-pixel viewport coordinates."""

    async def fill(self, ref: str, text: str, timeout: float) -> None: ...

    async def type_text(self, ref: str, text: str, timeout: float) -> None:
        """Type ``text`` one key at a time into ``ref``."""

    async def press(self, ref: str | None, key: str, timeout: float) -> None: ...

    async def select(
        self, ref: str, labels: Sequence[str], timeout: float
    ) -> list[str]: ...

    async def set_checked(self, ref: str, checked: bool, timeout: float) -> None: ...

    async def hover(self, ref: str, timeout: float) -> None: ...

    async def hover_at(self, x: float, y: float, timeout: float) -> None:
        """Move the mouse to viewport point ``(x, y)``."""

    async def scroll_into_view(self, ref: str, timeout: float) -> None: ...

    async def wheel(self, dx: float, dy: float, timeout: float) -> None: ...

    async def drag(self, ref: str, to_ref: str, timeout: float) -> None: ...

    async def drag_between(
        self,
        start: str | tuple[float, float],
        end: str | tuple[float, float],
        timeout: float,
    ) -> None:
        """Press the mouse at ``start`` and release it at ``end``: a ref's centre or a viewport point."""

    async def set_input_files(
        self, ref: str, files: Sequence[str], timeout: float
    ) -> None:
        """Set files on a file input (or a label for one); ``NotFileInputError`` otherwise."""

    async def type_checked(
        self,
        ref: str,
        text: str,
        allow: Callable[[str], bool],
        *,
        timeout: float,
    ) -> str:
        """Type ``text`` into ``ref`` only when ``allow`` accepts its own document's URL.

        ``ref`` is resolved once; the URL judged and the element typed into
        come from that one resolution, so an element replaced or a document
        navigated in between fails the call instead of receiving ``text``.
        Returns the URL judged (empty when it could not be read).
        """

    async def choose_files(
        self, ref: str, files: Sequence[str], timeout: float
    ) -> None:
        """Click ``ref`` and answer the file chooser it opens with ``files``."""

    async def wait_for_text(self, text: str, *, gone: bool, timeout: float) -> None: ...

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
        """A CSS-scale screenshot; a full page or element is clipped to ``max_height`` CSS px."""

    async def bring_to_front(self, timeout: float) -> None: ...

    async def close(self, timeout: float) -> None: ...


class EventSink(Protocol):
    """Callbacks a driver delivers on the event loop; each must return quickly."""

    def launch_started(self, processes: Exiting) -> None:
        """From now on the launch may start browser processes; ``processes`` tells when all ended.

        Delivered at most once per launch, before its first browser process
        can start; a launch that fails without it started none.
        """

    def page_opened(self, page: PageDriver) -> None: ...

    def page_closed(self, page: PageDriver) -> None: ...

    def dialog_opened(self, page: PageDriver, dialog: DialogDriver) -> None: ...

    def download_started(self, page: PageDriver, download: DownloadDriver) -> None: ...

    def navigation_started(self, page: PageDriver, url: str | None = None) -> None:
        """A main-frame navigation request (or redirect hop) went out to ``url``."""

    def window_opening(self, page: PageDriver) -> None:
        """``page`` asked for a new window; its ``page_opened`` follows later."""

    def popup_opened(self, opener: PageDriver, page: PageDriver) -> None:
        """``page``, just reported by ``page_opened``, was opened by ``opener``.

        Delivered right after that ``page_opened`` and before any other event
        of ``page``; never for a window opened without an opener.
        """

    def disconnected(self) -> None:
        """The browser went away without the session asking."""


class BrowserDriver(Protocol):
    """A launched browser: one context, its pages, and staged shutdown."""

    def pages(self) -> list[PageDriver]: ...

    def is_connected(self) -> bool: ...

    async def responsive(self, timeout: float) -> bool:
        """Whether the browser process answers a round trip within ``timeout``."""

    async def process_ids(self) -> list[int]:
        """Ids of every browser process, the browser process first."""

    async def new_page(self, timeout: float) -> PageDriver: ...

    async def close_context(self) -> None: ...

    async def close_browser(self) -> None: ...

    async def stop(self) -> None:
        """Stop the automation driver process."""

    def kill(self) -> Awaitable[None]:
        """Last resort: start ending every process this browser started, at once.

        Never blocks the event loop; the result resolves once the kill has
        finished (it is bounded), and cancelling a wait on it leaves the kill
        running.
        """

    def exited(self) -> bool:
        """Whether every process of this browser is known to have ended; never waits."""


class Driver(Protocol):
    """Starts browsers."""

    async def launch(self, plan: LaunchPlan, sink: EventSink) -> BrowserDriver:
        """Start a browser per ``plan`` with one open page; clean up fully on failure.

        A failed or cancelled launch ends, by force if need be, the browser
        processes it started; the object it gave ``sink.launch_started``
        reports when they have.
        """
