"""``act``: run an actions batch on one tab and report statuses plus the page delta.

Status lines (outside the wrapper) carry the action index, kind, ref,
status and a fixed reason; page-derived detail goes inside the wrapper as
``Action N: …`` lines.
"""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING, Any

from kohakuterrarium.browser import results as fmt
from kohakuterrarium.browser.actions import (
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
from kohakuterrarium.browser.observe import Observation, origin_of
from kohakuterrarium.browser.options import BrowserOptions
from kohakuterrarium.browser.refs import RefBook
from kohakuterrarium.browser.secrets import SecretStore
from kohakuterrarium.browser.session.types import BrowserResult, LOST_CRASH, Op
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
        session._nav_event(tab).clear()

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
            before = self._last.get(tab.id)
            fresh = None
            if self._dialogs.pending is None:
                try:
                    before = await self._observe(tab)
                except PageNavigatingError as exc:
                    line = f"Actions not run: {exc}."
                    return self._error_result(tab, op, line, "")
                fresh = before.fresh
            report = await run_batch(parsed, ActContext(self, tab, files, fresh))
            if report.lost:
                self._mark_lost(LOST_CRASH)
                text = fmt.join_sections(
                    format_report(report),
                    "The browser closed unexpectedly; its state was lost and the next "
                    "call starts a fresh browser.",
                )
                meta = self._act_meta(tab, None, report)
                return BrowserResult(self._redact(text), meta, is_error=True)
            return await self._act_result(tab, before, report, op)

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
        self, tab: Tab, before: Observation | None, report: BatchReport, op: Op
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
        delta = await self._delta(tab, before, after)
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
        landed = await self._opened_tab_section(tab, opened)
        obs = after or before
        url = obs.url if obs else tab.page.url
        inside = fmt.join_sections(
            self._title_line(tab, obs.title) if obs else "",
            format_details(report),
            delta,
            events,
        )
        text = fmt.join_sections(
            self._header(tab, url),
            format_report(report),
            self._wrap(inside, tab, origin_of(url)),
            event_notes,
            landed,
            self._notes(op, after),
        )
        stopped = report.stopped
        return BrowserResult(
            self._redact(text, tab),
            self._act_meta(tab, after, report),
            is_error=stopped is not None and stopped.status == FAILED,
        )

    async def _opened_tab_section(self, tab: Tab, opened: list[Tab]) -> str:
        """The compact snapshot of a tab the batch opened that is now active, wrapped."""
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
        rendered = await self._compact(obs)
        self._refs.record(active.id, rendered.fingerprints)
        return fmt.join_sections(
            self._header(active, obs.url),
            self._wrap(
                fmt.join_sections(
                    self._title_line(active, obs.title),
                    f"Tab {active.id} now (interactive elements):",
                    rendered.text or "(no interactive elements)",
                    fmt.format_truncation(rendered.cut_lines, rendered.cut_refs),
                ),
                active,
                obs.origin,
            ),
        )

    async def _delta(
        self, tab: Tab, before: Observation | None, after: Observation | None
    ) -> str:
        """A line diff, or the navigation line plus a compact snapshot after a new document."""
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
            rendered = await self._compact(after)
            self._refs.record(tab.id, rendered.fingerprints)
            return fmt.join_sections(
                nav,
                "Page now (interactive elements):",
                rendered.text or "(no interactive elements)",
                fmt.format_truncation(rendered.cut_lines, rendered.cut_refs),
            )
        diff = await asyncio.to_thread(fmt.diff_snapshots, before.nodes, after.nodes)
        self._refs.record(tab.id, diff.fingerprints)
        nav = fmt.format_navigation(
            redact(before.url, tab),
            redact(after.url, tab),
            redact(before.title, tab),
            redact(after.title, tab),
        )
        return fmt.join_sections(nav, diff.text or "No change in the page snapshot.")
