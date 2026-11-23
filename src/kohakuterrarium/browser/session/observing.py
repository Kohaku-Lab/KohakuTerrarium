"""Reading pages, judging refs, and assembling result text for the session.

Text outside a ``page_content`` wrapper is fixed wording, tab ids, refs,
numbers and URLs (one line, redacted, then capped); everything the page
chose goes through :meth:`SessionObserveMixin._wrap`.
"""

import asyncio
from typing import Any

from kohakuterrarium.browser import results as fmt
from kohakuterrarium.browser import snapshot as snap
from kohakuterrarium.browser.dialogs import DialogManager
from kohakuterrarium.browser.downloads import Downloads
from kohakuterrarium.browser.errors import ActionFailedError, NoTabError
from kohakuterrarium.browser.observe import (
    Observation,
    format_blocked_records,
    observe,
    origin_of,
    read_document,
    read_fresh,
)
from kohakuterrarium.browser.options import BrowserOptions
from kohakuterrarium.browser.policy import NetworkPolicy
from kohakuterrarium.browser.proxy import EgressRecord
from kohakuterrarium.browser.refs import RefBook
from kohakuterrarium.browser.secrets import SecretStore
from kohakuterrarium.browser.session.types import (
    COMPACT_MAX_CHARS,
    DOWNLOAD_WAIT_S,
    BrowserResult,
    Op,
    Stack,
)
from kohakuterrarium.browser.tabs import Tab, TabRegistry


class SessionObserveMixin:
    """Owns the last observation per tab, blocked-request buffer, secrets and wrapper token."""

    options: BrowserOptions
    policy: NetworkPolicy
    # owned by SessionLifecycleMixin
    _tabs: TabRegistry
    _refs: RefBook
    _dialogs: DialogManager
    _downloads: Downloads
    _stack: Stack | None
    _max_tabs: int
    _refused_tabs: int

    def _init_observe(self) -> None:
        self._secrets = SecretStore(self.options.secrets)
        self._token = fmt.new_token()
        self._last: dict[str, Observation] = {}
        self._blocked: list[EgressRecord] = []

    @property
    def token(self) -> str:
        """The id this session's ``page_content`` wrappers carry."""
        return self._token

    def _runner(self, tab: Tab):
        async def run(operation, timeout, what):
            return await self._call(tab, operation, timeout, what)

        return run

    async def _observe(self, tab: Tab) -> Observation:
        obs = await observe(
            tab.page,
            self._runner(tab),
            self.options.action_timeout_s,
            redact=self._secrets.redact,
        )
        self._sync_doc(tab, obs.doc_id)
        self._last[tab.id] = obs
        return obs

    def _sync_doc(self, tab: Tab, doc_id: Any) -> None:
        """Forget the tab's refs when it now shows a different main document."""
        if doc_id != tab.doc_id:
            self._refs.reset(tab.id)
            tab.doc_id = doc_id

    async def _fresh_refs(self, tab: Tab) -> dict[str, tuple[str, str]]:
        """Fingerprints of a fresh full snapshot, for judging refs at this moment."""
        timeout = self.options.action_timeout_s
        runner = self._runner(tab)
        self._sync_doc(tab, await read_document(tab.page, runner, timeout))
        return await read_fresh(tab.page, runner, timeout, redact=self._secrets.redact)

    async def _check_ref(
        self, tab: Tab, ref: str, fresh: dict[str, tuple[str, str]]
    ) -> None:
        """Raise ``ActionFailedError`` unless ``ref`` is known, unchanged and on the page."""
        verdict = self._refs.judge(tab.id, ref, fresh)
        if not verdict.ok:
            raise ActionFailedError(verdict.message, detail=verdict.detail)
        timeout = self.options.action_timeout_s
        count = await self._call(
            tab, lambda: tab.page.ref_count(ref, timeout), timeout, f"finding {ref}"
        )
        if not count:
            raise ActionFailedError(
                f"ref {ref} is no longer on the page; take a snapshot to get current refs"
            )

    async def _render(self, nodes: list[snap.Node], **options: Any) -> snap.Rendered:
        return await asyncio.to_thread(lambda: snap.render(nodes, **options))

    def _redact(self, text: str, tab: Tab | None = None) -> str:
        obs = self._last.get(tab.id) if tab is not None else None
        return self._secrets.redact(text, extra=obs.passwords if obs else ())

    def _line(self, text: str, limit: int, tab: Tab | None = None) -> str:
        """``text`` redacted, then flattened to one line, then capped at ``limit``."""
        return fmt.middle(fmt.one_line(self._redact(text, tab)), limit)

    def _header(self, tab: Tab, url: str) -> str:
        pending = self._dialogs.pending
        return fmt.format_header(
            tab.id, self._redact(url, tab), pending.kind if pending else None
        )

    def _title_line(self, tab: Tab, title: str) -> str:
        return fmt.format_title(self._redact(title, tab))

    def _wrap(self, text: str, tab: Tab | None, origin: str) -> str:
        if not text:
            return ""
        return fmt.wrap_untrusted(
            self._redact(text, tab), self._redact(origin, tab), self._token
        )

    def _take_blocked(self) -> list[EgressRecord]:
        self._buffer_blocked()
        records, self._blocked = self._blocked, []
        return records

    def _buffer_blocked(self) -> None:
        if self._stack is not None:
            self._blocked.extend(self._stack.proxy.drain_blocked())

    async def _events_text(self, op: Op | None) -> tuple[str, str, list[Tab]]:
        """Page-derived event lines (for the wrapper), safe notes, and tabs opened.

        Waits briefly for downloads only when ``op`` started one.
        """
        if op is not None and self._downloads.started > op.downloads_mark:
            await self._downloads.wait(DOWNLOAD_WAIT_S)
        finished, failed = self._downloads.drain()
        opened = self._tabs.drain_opened()
        in_progress = self._downloads.in_progress()
        redact = self._redact
        page = [
            fmt.format_tabs_opened((t.id, redact(t.page.url)) for t in opened),
            fmt.format_dialogs(
                (kind, redact(message), outcome)
                for kind, message, outcome in self._dialogs.drain_reports()
            ),
            fmt.format_downloads((redact(n), redact(p)) for n, p in finished),
            "\n".join(
                f"Download failed: {self._line(n, fmt.LINE_TEXT)} "
                f"({self._line(r, fmt.LINE_TEXT)})"
                for n, r in failed
            ),
            "\n".join(
                f"Download in progress: {self._line(n, fmt.LINE_TEXT)}"
                for n in in_progress
            ),
            format_blocked_records(self._take_blocked(), self.policy),
        ]
        return fmt.join_sections(*page), self._event_notes(opened, in_progress), opened

    def _event_notes(self, opened: list[Tab], in_progress: list[str]) -> str:
        notes = []
        active = self._tabs.active
        if opened and active is not None:
            notes.append(f"Active tab is now {active.id}.")
        if in_progress:
            count = len(in_progress)
            verb = "is" if count == 1 else "are"
            notes.append(
                f"{count} download{'' if count == 1 else 's'} {verb} still in progress."
            )
        if self._refused_tabs:
            count, self._refused_tabs = self._refused_tabs, 0
            notes.append(
                f"Closed {count} tab{'' if count == 1 else 's'} the page opened beyond "
                f"the limit of {self._max_tabs} open tabs."
            )
        return fmt.join_sections(*notes)

    def _meta(self, tab: Tab, obs: Observation | None, **extra: Any) -> dict[str, Any]:
        pending = self._dialogs.pending
        url = obs.url if obs is not None else tab.page.url
        meta: dict[str, Any] = {
            "tab": tab.id,
            "url": self._line(url, fmt.LINE_URL, tab),
            "dialog": pending.kind if pending else None,
        }
        meta.update(extra)
        return meta

    def _notes(self, op: Op, obs: Observation | None) -> str:
        notes = list(op.notes)
        if obs is not None:
            notes.append(fmt.format_hidden_dropped(obs.dropped))
            notes.append(obs.note)
            if obs.cut_from:
                notes.append(
                    f"The page snapshot was cut at {obs.raw_chars} of {obs.cut_from} "
                    "characters; the rest of the page is not shown."
                )
        return fmt.join_sections(*notes)

    async def _page_result(
        self, tab: Tab, obs: Observation, op: Op, rendered: snap.Rendered
    ) -> BrowserResult:
        """Header, wrapped title, snapshot and events, notes; records the refs it shows."""
        self._refs.record(tab.id, rendered.fingerprints)
        body = fmt.join_sections(
            self._title_line(tab, obs.title),
            rendered.text or "(no accessible content)",
            fmt.format_truncation(rendered.cut_lines, rendered.cut_refs),
        )
        events, event_notes, _ = await self._events_text(op)
        text = fmt.join_sections(
            self._header(tab, obs.url),
            self._wrap(fmt.join_sections(body, events), tab, obs.origin),
            event_notes,
            self._notes(op, obs),
        )
        return BrowserResult(self._redact(text, tab), self._meta(tab, obs))

    async def _compact(self, obs: Observation) -> snap.Rendered:
        """Interactive elements only, capped well below ``snapshot_max_chars``."""
        limit = min(COMPACT_MAX_CHARS, self.options.snapshot_max_chars // 4)
        return await self._render(obs.nodes, interactive=True, max_chars=limit)

    def _dialog_result(self, tab: Tab, op: Op, *, error: bool = False) -> BrowserResult:
        """The result of a call a dialog interrupted, or (``error``) refused while one waits."""
        pending = self._dialogs.pending
        lead = ""
        if error:
            lead = (
                f"A {pending.describe()} is waiting for an answer; answer it with the "
                "dialog action of browser_act before doing anything else."
            )
        text = fmt.join_sections(
            self._header(tab, tab.page.url),
            lead,
            self._wrap(
                fmt.format_dialogs(
                    [(pending.kind, self._redact(pending.message, tab))], pending=True
                ),
                tab,
                origin_of(tab.page.url),
            ),
            self._notes(op, None),
        )
        return BrowserResult(
            self._redact(text, tab), self._meta(tab, None), is_error=error
        )

    def _pending_result(self, op: Op) -> BrowserResult:
        """The error result of a call made while a dialog waits for an answer."""
        try:
            tab = self._tabs.get(self._dialogs.pending.tab_id)
        except NoTabError:
            tab = self._tabs.get()
        op.tab = tab
        return self._dialog_result(tab, op, error=True)

    def _error_result(self, tab: Tab, op: Op, line: str, detail: str) -> BrowserResult:
        """A failure the page caused: a safe line, the page's detail wrapped."""
        url = tab.page.url
        text = fmt.join_sections(
            self._header(tab, url),
            line,
            self._wrap(detail, tab, origin_of(url)),
            self._notes(op, None),
        )
        return BrowserResult(
            self._redact(text, tab), self._meta(tab, None), is_error=True
        )
