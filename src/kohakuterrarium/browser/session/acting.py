"""``act``: run an actions batch on one tab and report statuses plus the page delta.

Status lines (outside the wrapper) carry the action index, kind, ref,
status and a fixed reason; page-derived detail goes inside the wrapper as
``Action N: …`` lines.
"""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from kohakuterrarium.browser import results as fmt
from kohakuterrarium.browser import snapshot as snap
from kohakuterrarium.browser.actions import (
    DONE,
    FAILED,
    BatchReport,
    format_details,
    format_report,
    parse_actions,
    run_batch,
)
from kohakuterrarium.browser.dialogs import DialogManager
from kohakuterrarium.browser.errors import (
    BrowserTimeoutError,
    DialogOpenedError,
    DriverDisconnectedError,
    DriverError,
    PageClosedError,
    PageNavigatingError,
)
from kohakuterrarium.browser.observe import Observation, blocked_reason, origin_of
from kohakuterrarium.browser.options import BrowserOptions
from kohakuterrarium.browser.refs import RefBook
from kohakuterrarium.browser.secrets import SecretStore
from kohakuterrarium.browser.proxy import EgressRecord
from kohakuterrarium.browser.session.navigation import endpoint_of
from kohakuterrarium.browser.session.types import (
    LOST_CRASH,
    PAGE_TEXT_MAX_CHARS,
    BrowserResult,
    Op,
)
from kohakuterrarium.browser.settle import settle
from kohakuterrarium.browser.tabs import Tab, TabRegistry

if TYPE_CHECKING:
    from kohakuterrarium.browser.session.assembly import BrowserSession


class ActContext:
    """The ``ActionContext`` of one ``act`` call on one tab."""

    def __init__(
        self,
        session: "BrowserSession",
        tab: Tab,
        files: Sequence[str],
        fresh: dict[str, tuple[str, str]] | None,
    ) -> None:
        self.page = tab.page
        self.timeout = session.options.action_timeout_s
        self.files = files
        self._session = session
        self._tab = tab
        self._fresh = fresh
        self._done = 0
        self._seen = session._nav_counts.get(tab.id, 0)
        self._mark = session._stack.proxy.mark()
        self.navigated_at: int | None = None
        session._nav_event(tab).clear()

    def _note_navigation(self) -> None:
        """Remember the done action during which the tab's main frame first navigated."""
        if self.navigated_at is None and self._session._nav_urls.get(self._tab.id):
            self.navigated_at = self._done - 1

    def require_no_dialog(self) -> None:
        self._session._dialogs.require_none()

    def secret(self, name: str) -> str:
        return self._session._secrets.resolve(name)

    async def check_ref(self, ref: str) -> None:
        if self._fresh is None:
            self._fresh = await self._session._fresh_refs(self._tab)
        await self._session._check_ref(self._tab, ref, self._fresh)

    async def call(
        self, operation: Callable[[], Awaitable[Any]], timeout: float
    ) -> Any:
        return await self._session._call(self._tab, operation, timeout, "the action")

    async def pause(self, seconds: float) -> None:
        await self._session._pause(self._tab, seconds)

    def action_done(self) -> None:
        self._fresh = None
        self._done += 1
        self._note_navigation()

    async def settle(self) -> None:
        if self._session._dialogs.pending is not None or self.page.is_closed():
            return
        event = self._session._nav_event(self._tab)
        try:
            await settle(self.page, event, grace=self._session.settle_grace)
        except PageClosedError:
            return
        finally:
            event.clear()
            self._note_navigation()

    def blocked_landing(self) -> str | None:
        """The status reason when the last action navigated the tab to a blocked landing.

        Only proxy records since the previous action count, and only when
        the tab's main frame navigated during this one.
        """
        session = self._session
        stack = session._stack
        if stack is None:
            return None
        count = session._nav_counts.get(self._tab.id, 0)
        mark, self._mark = self._mark, stack.proxy.mark()
        moved, self._seen = count > self._seen, count
        if not moved:
            return None
        blocked = session._blocked_hops([self.page.url], mark, self._tab)
        return session._blocked_reason(blocked[-1]) if blocked else None

    async def answer_dialog(self, accept: bool, text: str | None) -> str:
        dialogs = self._session._dialogs
        record = await dialogs.answer(accept, text)
        error = await dialogs.collect(record.tab_id, self.timeout)
        reason = f"{record.kind} dialog {record.outcome}"
        if isinstance(error, DriverError) and not isinstance(error, TimeoutError):
            reason += f"; the call it interrupted then failed ({error})"
        return reason


class SessionActMixin:
    """``act`` and its delta; stateless beyond the parts it reads."""

    options: BrowserOptions
    # owned by SessionLifecycleMixin
    _tabs: TabRegistry
    _refs: RefBook
    _dialogs: DialogManager
    # owned by SessionObserveMixin
    _secrets: SecretStore
    _last: dict[str, Observation]
    _nav_urls: dict[str, list[str]]
    _nav_counts: dict[str, int]

    async def act(
        self,
        actions: Any,
        *,
        files: Sequence[str] = (),
        tab_id: str | None = None,
    ) -> BrowserResult:
        """Run an ``actions`` batch on one tab; per-action statuses plus the page delta.

        ``files`` are the absolute paths ``upload`` actions index into.
        """
        parsed = parse_actions(actions, files=files, secrets=self._secrets.names)
        async with self._operation() as op:
            if parsed[0].kind != "dialog" and self._dialogs.pending is not None:
                return self._pending_result(op)
            tab = op.tab = self._tabs.get(tab_id)
            mark = self._stack.proxy.mark()
            self._nav_urls[tab.id] = []
            before = self._last.get(tab.id)
            fresh = None
            if self._dialogs.pending is None:
                try:
                    before = await self._observe(tab)
                except PageNavigatingError as exc:
                    line = f"Actions not run: {exc}."
                    return self._error_result(tab, op, line, "")
                fresh = before.fresh
            ctx = ActContext(self, tab, files, fresh)
            report = await run_batch(parsed, ctx)
            if report.lost:
                self._mark_lost(LOST_CRASH)
                text = fmt.join_sections(
                    format_report(report),
                    "The browser closed unexpectedly; its state was lost and the next "
                    "call starts a fresh browser.",
                )
                meta = self._act_meta(tab, None, report)
                return BrowserResult(self._redact(text), meta, is_error=True)
            return await self._act_result(
                tab, before, report, op, mark, ctx.navigated_at
            )

    def _act_meta(
        self, tab: Tab, obs: Observation | None, report: BatchReport
    ) -> dict[str, Any]:
        actions = [
            {
                "index": o.action.index,
                "kind": o.action.kind,
                "ref": o.action.ref,
                "status": o.status,
            }
            for o in report.outcomes
        ]
        return self._meta(tab, obs, actions=actions, ok=report.ok)

    async def _act_result(
        self,
        tab: Tab,
        before: Observation | None,
        report: BatchReport,
        op: Op,
        mark: int,
        navigated_at: int | None = None,
    ) -> BrowserResult:
        after = None
        if self._tabs.find(tab.page) is None:
            op.notes.append(f"Tab {tab.id} was closed by the page.")
        elif self._dialogs.pending is None:
            try:
                after = await self._observe(tab)
            except DialogOpenedError:
                after = None
            except PageNavigatingError:
                op.notes.append(
                    "The page was navigating when the result was read; take a "
                    "snapshot to see the new page."
                )
        blocked: list[EgressRecord] = []
        if self._nav_urls.get(tab.id):
            landed = [after.url] if after is not None else []
            blocked = self._blocked_hops(landed, mark, tab)
        if blocked:
            if report.stopped is None:
                _fail_done(report, navigated_at, self._blocked_reason(blocked[-1]))
            delta = self._blocked_line(tab, before, after, blocked[-1])
        else:
            delta = await self._delta(tab, before, after, op)
        pending = self._dialogs.pending
        if pending is not None:
            delta = fmt.join_sections(
                delta,
                fmt.format_dialogs(
                    [(pending.kind, self._redact(pending.message, tab))], pending=True
                ),
            )
        await self._await_windows(op)
        events, event_notes, opened = await self._events_text(op)
        obs = after or before
        url = obs.url if obs else tab.page.url
        landed_tab = await self._opened_tab_section(tab, opened, op, url)
        inside = fmt.join_sections(
            self._title_line(tab, obs.title) if obs else "",
            format_details(report, lambda detail: self._detail(detail, tab)),
            delta,
            events,
        )
        text = fmt.join_sections(
            self._header(tab, url),
            format_report(report),
            self._wrap(inside, tab, origin_of(url), url),
            event_notes,
            landed_tab,
            self._notes(op, after),
        )
        stopped = report.stopped
        return BrowserResult(
            self._redact(text, tab),
            self._act_meta(tab, after, report),
            is_error=stopped is not None and stopped.status == FAILED,
        )

    def _blocked_reason(self, record: EgressRecord) -> str:
        """The status reason of an action whose page the network policy blocked."""
        return (
            f"it led to {record.host}:{record.port}, which the browser network "
            f"policy blocked ({blocked_reason(record, self.policy)}); the page did "
            "not load"
        )

    def _blocked_line(
        self,
        tab: Tab,
        before: Observation | None,
        after: Observation | None,
        record: EgressRecord,
    ) -> str:
        """``Navigation blocked: <from> → <to>; the page did not load.``

        ``to`` is the chain URL the blocked ``record`` was for, so a browser
        error page that replaced it is not named instead.
        """
        landed = after.url if after else tab.page.url
        chain = [*self._nav_urls.get(tab.id, ()), landed]
        target = next(
            (u for u in chain if endpoint_of(u) == (record.host, record.port)), landed
        )
        to = self._line(target, fmt.LINE_URL, tab)
        if before is None:
            return f"Navigation blocked: {to}; the page did not load."
        start = self._line(before.url, fmt.LINE_URL, tab)
        return f"Navigation blocked: {start} → {to}; the page did not load."

    async def _opened_tab_section(
        self, tab: Tab, opened: list[Tab], op: Op, shown: str
    ) -> str:
        """The page view of a tab the batch opened that is now active, wrapped.

        ``shown`` is the URL on the result's first header line.
        """
        active = self._tabs.active
        if active is None or active is tab or active not in opened:
            return ""
        try:
            await self._settle_tab(active)
            obs = await self._observe(active)
        except (
            DialogOpenedError,
            BrowserTimeoutError,
            DriverError,
            PageNavigatingError,
        ) as exc:
            if isinstance(exc, DriverDisconnectedError):
                raise
            return f"Could not read tab {active.id} yet; take a snapshot of it."
        rendered, full = await self._page_view(obs, op)
        self._refs.record(active.id, rendered.fingerprints, obs.frame_docs)
        return fmt.join_sections(
            self._header(active, obs.url),
            self._wrap(
                fmt.join_sections(
                    self._title_line(active, obs.title),
                    _now(f"Tab {active.id} now", full),
                    rendered.text or _empty(full),
                    fmt.format_truncation(rendered.cut_lines, rendered.cut_refs),
                ),
                active,
                obs.origin,
                shown,
            ),
        )

    async def _delta(
        self,
        tab: Tab,
        before: Observation | None,
        after: Observation | None,
        op: Op,
    ) -> str:
        """A line diff, or the navigation line plus the page view after a new document.

        A diff that was cut or shows fewer lines than it removed gives way to
        the whole page when that fits ``PAGE_TEXT_MAX_CHARS``.
        """
        if after is None:
            return ""
        redact = self._redact
        if before is None or after.doc_id != before.doc_id:
            nav = ""
            if before is not None:
                nav = fmt.format_navigation(
                    redact(before.url, tab),
                    redact(after.url, tab),
                    redact(before.title, tab),
                    redact(after.title, tab),
                ) or (f"Reloaded: {self._line(after.url, fmt.LINE_URL, tab)}")
            rendered, full = await self._page_view(after, op)
            self._refs.record(tab.id, rendered.fingerprints, after.frame_docs)
            return fmt.join_sections(
                nav,
                _now("Page now", full),
                rendered.text or _empty(full),
                fmt.format_truncation(rendered.cut_lines, rendered.cut_refs),
            )
        diff = await asyncio.to_thread(fmt.diff_snapshots, before.nodes, after.nodes)
        page = await self._page_instead(after, diff)
        self._refs.record(tab.id, (page or diff).fingerprints, after.frame_docs)
        nav = fmt.format_navigation(
            redact(before.url, tab),
            redact(after.url, tab),
            redact(before.title, tab),
            redact(after.title, tab),
        )
        if page is not None:
            moved = f" ({diff.moved} moved)" if diff.moved else ""
            summary = (
                f"Page changes: {diff.added} added, {diff.changed} changed{moved}, "
                f"{diff.removed} lines removed. Page now:"
            )
            return fmt.join_sections(nav, summary, page.text or _empty(True))
        return fmt.join_sections(nav, diff.text or "No change in the page snapshot.")

    async def _page_instead(
        self, after: Observation, diff: fmt.SnapshotDiff
    ) -> snap.Rendered | None:
        """The whole page when ``diff`` hides more than it shows and the page fits."""
        shown = len(diff.text.splitlines()) - 1
        if not diff.text or not (diff.truncated or diff.removed > shown):
            return None
        limit = min(PAGE_TEXT_MAX_CHARS, self.options.snapshot_max_chars)
        page = await self._render(after.nodes, max_chars=limit)
        return None if page.cut_lines else page


def _now(lead: str, full: bool) -> str:
    return f"{lead}:" if full else f"{lead} (interactive elements):"


def _empty(full: bool) -> str:
    return "(no accessible content)" if full else "(no interactive elements)"


def _fail_done(report: BatchReport, index: int | None, reason: str) -> None:
    """Turn the done action at ``index`` (else the last done one) into a failure."""
    done = [i for i, o in enumerate(report.outcomes) if o.status == DONE]
    if not done:
        return
    i = index if index in done else done[-1]
    report.outcomes[i] = replace(report.outcomes[i], status=FAILED, reason=reason)
