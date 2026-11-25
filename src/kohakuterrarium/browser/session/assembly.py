"""``BrowserSession`` assembled from its parts; see ``README.md`` for the part map."""

from pathlib import Path

from kohakuterrarium.browser.downloads import Directory, Downloads, WriteCheck
from kohakuterrarium.browser.driver import PlaywrightDriver
from kohakuterrarium.browser.options import BrowserOptions
from kohakuterrarium.browser.policy import ExternalCheck
from kohakuterrarium.browser.protocol import Driver
from kohakuterrarium.browser.proxy import FilteringProxy
from kohakuterrarium.browser.session.acting import SessionActMixin
from kohakuterrarium.browser.session.capture import SessionCaptureMixin
from kohakuterrarium.browser.session.lifecycle import (
    ProxyFactory,
    SessionLifecycleMixin,
)
from kohakuterrarium.browser.session.navigation import SessionNavigationMixin
from kohakuterrarium.browser.session.observing import SessionObserveMixin
from kohakuterrarium.browser.session.tab_ops import SessionTabsMixin
from kohakuterrarium.browser.session.types import (
    CLOSE_STAGE_TIMEOUT_S,
    CLOSE_TIMEOUT_S,
)
from kohakuterrarium.browser.settle import NAV_GRACE_S


class BrowserSession(
    SessionLifecycleMixin,
    SessionObserveMixin,
    SessionNavigationMixin,
    SessionActMixin,
    SessionCaptureMixin,
    SessionTabsMixin,
):
    """One agent's browser: lazy launch, FIFO lock, bounded calls, terminal close.

    ``downloads_dir`` (a path, or a callable read as each download starts)
    receives page downloads; ``write_check`` judges each download path
    before it is created; ``external_check`` is consulted
    by the network policy for every connection; ``driver`` defaults to
    Playwright; ``close_stage_timeout`` bounds each shutdown stage and
    ``close_timeout`` the whole of ``close``;
    ``settle_grace`` is how long to wait for an action's navigation to start;
    ``proxy_factory`` builds the filtering proxy from the network policy.
    """

    def __init__(
        self,
        options: BrowserOptions,
        *,
        downloads_dir: str | Directory,
        driver: Driver | None = None,
        external_check: ExternalCheck | None = None,
        write_check: WriteCheck | None = None,
        close_stage_timeout: float = CLOSE_STAGE_TIMEOUT_S,
        close_timeout: float = CLOSE_TIMEOUT_S,
        settle_grace: float = NAV_GRACE_S,
        proxy_factory: ProxyFactory = FilteringProxy,
    ) -> None:
        self.options = options
        self.settle_grace = settle_grace
        self.close_timeout = close_timeout
        self._init_observe()
        self._init_lifecycle(
            driver or PlaywrightDriver(),
            external_check,
            close_stage_timeout,
            Downloads(
                downloads_dir if callable(downloads_dir) else Path(downloads_dir),
                write_check=write_check,
            ),
            proxy_factory,
        )
