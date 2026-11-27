"""One-shot page reads for callers without a session: launch, read, close."""

from kohakuterrarium.browser.options import BrowserOptions
from kohakuterrarium.browser.policy import ExternalCheck
from kohakuterrarium.browser.protocol import Driver
from kohakuterrarium.browser.session.assembly import BrowserSession
from kohakuterrarium.browser.session.reading import READ_MAX_CHARS, PageRead


async def read_page(
    url: str,
    options: BrowserOptions | None = None,
    *,
    external_check: ExternalCheck | None = None,
    max_chars: int = READ_MAX_CHARS,
    driver: Driver | None = None,
) -> PageRead:
    """Read ``url`` in a browser of its own, closed again before this returns.

    ``options`` default to ``BrowserOptions()``; ``external_check(host, port)``
    is consulted for every connection, as in a session. The browser keeps no
    downloads and nothing outlives the call; errors are those of
    ``BrowserSession.read_page``. ``driver`` replaces Playwright.
    """
    async with BrowserSession(
        options or BrowserOptions(),
        driver=driver,
        external_check=external_check,
    ) as session:
        return await session.read_page(url, max_chars=max_chars)
