"""The driver event sink of one launch; stale launches are ignored.

Events of a page a structured read owns go to the read, never to the tabs.
"""

from typing import TYPE_CHECKING

from kohakuterrarium.browser.profiles import Exiting, ProfileClaim
from kohakuterrarium.browser.protocol import DialogDriver, DownloadDriver, PageDriver
from kohakuterrarium.browser.session.types import LAUNCHING, LOST_CRASH, READY

if TYPE_CHECKING:
    from kohakuterrarium.browser.session.assembly import BrowserSession


class LaunchSink:
    """Forwards driver events to the session while its launch is the current one.

    ``claim``, the launch's hold on a persistent profile, is handed the
    launch's processes whether or not the launch is still current.
    """

    def __init__(
        self,
        session: "BrowserSession",
        generation: int,
        claim: ProfileClaim | None = None,
    ) -> None:
        self._session = session
        self._generation = generation
        self._claim = claim

    def _live(self) -> bool:
        s = self._session
        return s._generation == self._generation and s._state in (LAUNCHING, READY)

    def launch_started(self, processes: Exiting) -> None:
        if self._claim is not None:
            self._claim.held_by(processes)

    def page_opened(self, page: PageDriver) -> None:
        if self._live() and not self._session._read_claims(page):
            self._session._on_page_opened(page)

    def page_closed(self, page: PageDriver) -> None:
        if self._live():
            self._session._read_closed(page)
            self._session._on_page_closed(page)

    def dialog_opened(self, page: PageDriver, dialog: DialogDriver) -> None:
        if self._live() and not self._session._read_dialog(page, dialog):
            self._session._on_dialog(page, dialog)

    def download_started(self, page: PageDriver, download: DownloadDriver) -> None:
        if self._live() and not self._session._read_download(page, download):
            self._session._downloads.start(download)

    def navigation_started(self, page: PageDriver, url: str | None = None) -> None:
        if self._live() and not self._session._read_navigation(page, url):
            self._session._on_navigation(page, url)

    def window_opening(self, page: PageDriver) -> None:
        if self._live() and not self._session._read_window(page):
            self._session._on_window_opening(page)

    def popup_opened(self, opener: PageDriver, page: PageDriver) -> None:
        if self._live():
            self._session._read_popup(opener, page)

    def disconnected(self) -> None:
        if self._live() and self._session._state == READY:
            self._session._mark_lost(LOST_CRASH)
            self._session._clean_up_soon()
