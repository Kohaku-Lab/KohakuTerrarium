"""A page read for framework tools, under every gate the ``browser`` plugin applies.

For a tool with a ``ToolContext`` (``web_fetch`` and the like). The read runs
in a browser of its own, launched and closed by the call, and is judged as the
plugin judges its own calls whether or not the plugin is loaded on the creature.
"""

from typing import Any

from kohakuterrarium.browser.errors import BrowserUnavailableError
from kohakuterrarium.browser.options import BrowserOptions
from kohakuterrarium.browser.protocol import Driver
from kohakuterrarium.browser.session import BrowserSession
from kohakuterrarium.browser.session.reading import READ_MAX_CHARS, PageRead
from kohakuterrarium.builtins.plugins.browser.gates import (
    CONFINEMENT_REFUSAL,
    SandboxLink,
    bundled_only,
    host_resource_refusal,
)


async def read_page_for(
    context: Any,
    url: str,
    options: BrowserOptions | None = None,
    *,
    max_chars: int = READ_MAX_CHARS,
    driver: Driver | None = None,
) -> PageRead:
    """Read ``url`` for the tool call behind ``context``, in a browser closed before returning.

    Before anything launches, ``BrowserUnavailableError`` refuses the read
    when the sandbox requires OS confinement, or when ``options`` (default
    ``BrowserOptions()``) ask for host resources the creature may not use; such
    a creature gets the bundled Chromium only. Every connection is judged by
    the sandbox's network rules as they are at that moment. Downloads the page
    starts are cancelled, so nothing is written. Other errors and the result
    are those of ``BrowserSession.read_page``; ``driver`` replaces Playwright.
    """
    options = options or BrowserOptions()
    link = SandboxLink()
    link.bind(getattr(context, "runtime_services", None))
    if link.requires_confinement:
        raise BrowserUnavailableError(CONFINEMENT_REFUSAL)
    refusal = host_resource_refusal(options, context)
    if refusal is not None:
        raise BrowserUnavailableError(refusal)
    async with BrowserSession(
        options, driver=driver, external_check=link.check
    ) as session:
        await session.require_bundled(bundled_only(context))
        return await session.read_page(url, max_chars=max_chars)
