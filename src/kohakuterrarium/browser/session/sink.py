"""The driver event sink of one launch; stale launches are ignored."""

from typing import TYPE_CHECKING

from kohakuterrarium.browser.protocol import DialogDriver, DownloadDriver, PageDriver
from kohakuterrarium.browser.session.types import LAUNCHING, LOST_CRASH, READY

if TYPE_CHECKING:
    from kohakuterrarium.browser.session.assembly import BrowserSession


class LaunchSink:
    """Forwards driver events to the session while its launch is the current one."""

    def __init__(self, session: "BrowserSession", generation: int) -> None:
        self._session = session
        self._generation = generation

    def _live(self) -> bool:
        s = self._session
        return s._generation == self._generation and s._state in (LAUNCHING, READY)

    def page_opened(self, page: PageDriver) -> None:
        if self._live():
            self._session._on_page_opened(page)

    def page_closed(self, page: PageDriver) -> None:
        if self._live():
            self._session._on_page_closed(page)

    def dialog_opened(self, page: PageDriver, dialog: DialogDriver) -> None:
        if self._live():
            self._session._on_dialog(page, dialog)

    def download_started(self, page: PageDriver, download: DownloadDriver) -> None:
        if self._live():
            self._session._downloads.start(download)

    def navigation_started(self, page: PageDriver, url: str | None = None) -> None:
        if self._live():
            self._session._on_navigation(page, url)

    def window_opening(self, page: PageDriver) -> None:
        if self._live():
            self._session._on_window_opening(page)

    def disconnected(self) -> None:
        if self._live() and self._session._state == READY:
            self._session._mark_lost(LOST_CRASH)
            self._session._clean_up_soon()
