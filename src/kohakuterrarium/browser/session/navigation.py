"""Navigation and snapshots: ``navigate`` (url / back / forward / reload) and ``snapshot``.

A navigation counts as blocked only when the session's own proxy recorded a
blocked decision during it for a host on its redirect chain; response
headers never decide it.
"""

from urllib.parse import urlsplit

from kohakuterrarium.browser import snapshot as snap
from kohakuterrarium.browser.errors import (
    ActionFailedError,
    ActionSpecError,
    BrowserTimeoutError,
    DialogOpenedError,
    DriverDisconnectedError,
    DriverError,
    NavigationBlockedError,
    NavigationError,
    PageClosedError,
    PageNavigatingError,
)
from kohakuterrarium.browser.observe import blocked_reason
from kohakuterrarium.browser.options import BrowserOptions
from kohakuterrarium.browser.policy import WEB_SCHEMES, NetworkPolicy, normalize_host
from kohakuterrarium.browser.protocol import NavResponse
from kohakuterrarium.browser.proxy import EgressRecord
from kohakuterrarium.browser.session.types import (
    HISTORY_STEPS,
    BrowserResult,
    Op,
    Stack,
)
from kohakuterrarium.browser.settle import settle
from kohakuterrarium.browser.tabs import Tab, TabRegistry

_NONE_LEFT = {"back": "earlier", "forward": "later"}
_UNREACHABLE = {"upstream": "connect to", "resolve_failed": "resolve"}


class SessionNavigationMixin:
    """``navigate`` and ``snapshot``; stateless beyond the parts it reads."""

    options: BrowserOptions
    policy: NetworkPolicy
    settle_grace: float
    # owned by SessionLifecycleMixin
    _tabs: TabRegistry
    _stack: Stack | None
    _nav_urls: dict[str, list[str]]
    # owned by SessionObserveMixin
    _blocked: list[EgressRecord]

    async def navigate(
        self,
        url: str | None = None,
        *,
        action: str | None = None,
        tab_id: str | None = None,
    ) -> BrowserResult:
        """Open ``url`` or go ``back`` / ``forward`` / ``reload``; header plus compact snapshot.

        A failure the page or network caused is an ``is_error`` result; a URL
        the network policy refuses raises ``NavigationBlockedError``.
        """
        if (url is None) == (action is None):
            raise ActionSpecError("give either url or action (back, forward or reload)")
        if action is not None and action not in HISTORY_STEPS:
            raise ActionSpecError(f"action must be one of {', '.join(HISTORY_STEPS)}")
        if url is not None:
            url = self._check_url(url)
        async with self._operation() as op:
            if self._dialogs.pending is not None:
                return self._pending_result(op)
            if url is not None and tab_id is None and self._tabs.active is None:
                tab = await self._open_tab()
            else:
                tab = self._tabs.get(tab_id)
            op.tab = tab
            return await self._go(op, tab, url, action)

    async def _go(
        self, op: Op, tab: Tab, url: str | None, action: str | None
    ) -> BrowserResult:
        """Load ``url`` (or run the history ``action``) in ``tab`` and read the result."""
        target = url or f"{action} in {tab.id}"
        timeout = self.options.nav_timeout_s
        self._buffer_blocked()
        mark = self._stack.proxy.mark()
        self._nav_event(tab).clear()
        self._nav_urls[tab.id] = []
        try:
            if url is not None:
                response = await self._call(
                    tab, lambda: tab.page.goto(url, timeout), timeout, f"loading {url}"
                )
            else:
                response = await self._call(
                    tab, lambda: tab.page.history(action, timeout), timeout, target
                )
        except DialogOpenedError:
            return self._dialog_result(tab, op)
        except (BrowserTimeoutError, TimeoutError):
            self._raise_if_blocked(target, mark, tab, None)
            raise BrowserTimeoutError(
                f"loading {target} did not finish within {timeout:g} s; take a "
                "snapshot to see how far it got"
            ) from None
        except (DriverDisconnectedError, PageClosedError):
            raise
        except DriverError as exc:
            self._raise_if_blocked(target, mark, tab, None)
            line = f"Navigation failed: loading {target} failed ({exc})."
            return self._error_result(tab, op, line, exc.detail)
        if response is None:
            raise NavigationError(
                f"there is no {_NONE_LEFT[action]} page in this tab's history"
            )
        self._raise_if_blocked(target, mark, tab, response)
        unreachable = self._unreachable(mark, response)
        if unreachable:
            line = f"Navigation failed: loading {target} failed ({unreachable})."
            return self._error_result(tab, op, line, "")
        await self._settle_tab(tab)
        try:
            obs = await self._observe(tab)
        except DialogOpenedError:
            return self._dialog_result(tab, op)
        except PageNavigatingError as exc:
            return self._error_result(tab, op, f"Page not read: {exc}.", "")
        return await self._page_result(tab, obs, op, await self._compact(obs))

    def _check_url(self, url: str) -> str:
        """The stripped URL, or ``NavigationBlockedError`` when the policy refuses it outright."""
        if not isinstance(url, str) or not url.strip():
            raise ActionSpecError("url must be a non-empty http or https URL")
        url = url.strip()
        decision = self.policy.check_url(url)
        if not decision:
            raise NavigationBlockedError(
                self._redact(f"cannot open {url}: {decision.reason}")
            )
        return url

    def _chain(
        self, target: str, tab: Tab, response: NavResponse | None
    ) -> set[tuple[str, int]]:
        """``(host, port)`` of each URL the main frame went to: target, redirect hops, final."""
        urls = [target, *self._nav_urls.get(tab.id, ())]
        if response is not None:
            urls.append(response.url)
        return {hop for hop in map(endpoint_of, urls) if hop[0]}

    def _raise_if_blocked(
        self, target: str, mark: int, tab: Tab, response: NavResponse | None
    ) -> None:
        """Raise ``NavigationBlockedError`` when the proxy blocked a hop of this navigation.

        Only records whose host and port match a URL on the chain count; the
        rest stay buffered for the usual blocked-requests report.
        """
        chain = self._chain(target, tab, response)
        blocked = [
            r
            for r in self._stack.proxy.since(mark)
            if r.blocked and (r.host, r.port) in chain
        ]
        if not blocked:
            return
        self._buffer_blocked()
        self._blocked = [r for r in self._blocked if not any(r is b for b in blocked)]
        record = blocked[-1]
        start = endpoint_of(target)
        if (record.host, record.port) == start:
            hop = ""
        elif record.host == start[0]:
            hop = f" (it led to {record.host}:{record.port})"
        else:
            hop = f" (it led to {record.host})"
        raise NavigationBlockedError(
            self._redact(
                f"loading {target} was blocked by the browser network policy{hop}: "
                f"{blocked_reason(record, self.policy)}"
            )
        )

    def _unreachable(self, mark: int, response: NavResponse) -> str:
        """Why the proxy could not reach the final host, when it recorded so; else empty."""
        host, port = endpoint_of(response.url)
        for record in self._stack.proxy.since(mark):
            if (record.host, record.port) == (host, port) and (
                record.code == response.blocked
            ):
                verb = _UNREACHABLE.get(record.code)
                if verb:
                    return f"could not {verb} {host}"
        return ""

    async def _settle_tab(self, tab: Tab) -> None:
        event = self._nav_event(tab)
        try:
            await settle(tab.page, event, grace=self.settle_grace)
        finally:
            event.clear()

    async def snapshot(
        self,
        *,
        ref: str | None = None,
        depth: int | None = None,
        query: str | None = None,
        interactive: bool = False,
        boxes: bool = False,
        tab_id: str | None = None,
    ) -> BrowserResult:
        """The current page as a filtered snapshot capped at ``snapshot_max_chars``.

        ``boxes`` adds each element's ``[box=x,y,w,h]`` in viewport CSS pixels.
        """
        if depth is not None and (
            isinstance(depth, bool) or not isinstance(depth, int) or depth < 1
        ):
            raise ActionSpecError("depth must be a whole number of at least 1")
        async with self._operation() as op:
            if self._dialogs.pending is not None:
                return self._pending_result(op)
            tab = op.tab = self._tabs.get(tab_id)
            try:
                obs = await self._observe(tab, boxes)
            except PageNavigatingError as exc:
                return self._error_result(tab, op, f"Snapshot not taken: {exc}.", "")
            try:
                rendered = await self._render(
                    snap.add_boxes(obs.nodes, obs.boxes),
                    ref=ref,
                    depth=depth,
                    query=query,
                    interactive=interactive,
                    max_chars=self.options.snapshot_max_chars,
                )
            except snap.UnknownRefError:
                raise ActionFailedError(
                    f"ref {ref} is not on the page now; take a snapshot without ref "
                    "to see current refs"
                ) from None
            if not rendered.text and query:
                rendered = snap.Rendered(f"(nothing matches {query!r})", 0, 0, 0, {})
            return await self._page_result(tab, obs, op, rendered)


def host_of(url: str) -> str:
    """The host of ``url`` normalised the way the proxy records it; empty when none."""
    return endpoint_of(url)[0]


def endpoint_of(url: str) -> tuple[str, int]:
    """``(host, port)`` of ``url`` as the proxy records them; ``("", 0)`` when none."""
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        port = parts.port or WEB_SCHEMES.get(parts.scheme.lower(), 0)
        return (normalize_host(host), port) if host else ("", 0)
    except ValueError:
        return ("", 0)
