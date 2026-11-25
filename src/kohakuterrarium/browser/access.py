"""How a tool reaches the agent's shared browser: the service key and its contract.

The ``browser`` plugin publishes a :class:`BrowserAccess` under
``runtime_services[BROWSER_SERVICE]``; any tool reads it through
:func:`browser_access` or :func:`agent_session` without importing the plugin.
"""

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from kohakuterrarium.browser.session import BrowserSession

BROWSER_SERVICE = "browser"


@runtime_checkable
class BrowserAccess(Protocol):
    """Hands out the agent's ``BrowserSession`` for one tool call.

    ``prepare`` applies the publisher's per-call gates (sandbox, host
    resources, closed session) and raises a ``BrowserError`` when this call
    may not use the browser. ``session`` is the current session without any
    gate, ``None`` before the first one exists.
    """

    @property
    def session(self) -> "BrowserSession | None": ...

    async def prepare(self, context: Any) -> "BrowserSession": ...


def browser_access(context: Any) -> BrowserAccess | None:
    """The access point published in ``context.runtime_services``; ``None`` when absent."""
    services = getattr(context, "runtime_services", None) or {}
    access = services.get(BROWSER_SERVICE)
    return access if isinstance(access, BrowserAccess) else None


async def agent_session(context: Any) -> "BrowserSession | None":
    """The agent's session prepared for the call behind ``context`` (a ``ToolContext``).

    ``None`` when no browser is published (the plugin is not loaded); the
    publisher's refusals raise.
    """
    access = browser_access(context)
    if access is None:
        return None
    return await access.prepare(context)
