"""JavaScript dialog handling: auto-accepted kinds, pending dialogs, and parked calls.

An ``alert`` or ``beforeunload`` is accepted at once and reported later. A
``confirm`` or ``prompt`` stays pending until the ``dialog`` action answers
it; the driver call that opened it is parked meanwhile and collected after
the answer.
"""

import asyncio
from dataclasses import dataclass, field

from kohakuterrarium.browser.errors import ActionFailedError, DialogPendingError
from kohakuterrarium.browser.protocol import DialogDriver
from kohakuterrarium.browser.tasks import discard
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

DIALOG_KINDS = frozenset({"alert", "confirm", "prompt", "beforeunload"})
AUTO_ACCEPT = frozenset({"alert", "beforeunload"})
ANSWER_TIMEOUT_S = 5.0


@dataclass(eq=False)
class DialogRecord:
    """One dialog the page opened and what became of it.

    ``kind`` is one of ``DIALOG_KINDS`` or ``dialog``; ``message``
    is page text and is shown only inside the untrusted wrapper.
    """

    tab_id: str
    kind: str
    message: str
    default_value: str = ""
    outcome: str | None = None
    handle: DialogDriver | None = field(default=None, repr=False)

    def describe(self) -> str:
        """``confirm dialog on t2``: no page text, safe for headers and errors."""
        kind = "" if self.kind == "dialog" else f"{self.kind} "
        return f"{kind}dialog on {self.tab_id}"


def _kind(value: object) -> str:
    return value if value in DIALOG_KINDS else "dialog"


class DialogManager:
    """Every dialog of one browser: reports, pending ones, and the calls they parked."""

    def __init__(self, answer_timeout: float = ANSWER_TIMEOUT_S) -> None:
        self.answer_timeout = answer_timeout
        self._pending: list[DialogRecord] = []
        self._reports: list[DialogRecord] = []
        self._events: dict[str, asyncio.Event] = {}
        self._parked: dict[str, asyncio.Future] = {}
        self._tasks: set[asyncio.Task] = set()

    @property
    def pending(self) -> DialogRecord | None:
        """The oldest unanswered dialog, or ``None``."""
        return self._pending[0] if self._pending else None

    def opened(self, tab_id: str, dialog: DialogDriver) -> DialogRecord:
        """Record a dialog the driver reported; accept it at once when it is auto-accepted."""
        record = DialogRecord(
            tab_id, _kind(dialog.kind), dialog.message or "", dialog.default_value or ""
        )
        record.handle = dialog
        if record.kind in AUTO_ACCEPT:
            record.outcome = "accepted automatically"
            self._reports.append(record)
            self._spawn(dialog)
            return record
        self._pending.append(record)
        self._event(tab_id).set()
        return record

    def require_none(self) -> None:
        """Raise ``DialogPendingError`` while a dialog waits for an answer."""
        record = self.pending
        if record is not None:
            raise DialogPendingError(
                f"a {record.describe()} is waiting for an answer; answer it with the "
                "dialog action of browser_act before doing anything else",
                detail=record.message,
            )

    async def wait_opened(self, tab_id: str) -> DialogRecord:
        """Return once a dialog is pending on ``tab_id``."""
        await self._event(tab_id).wait()
        return next(r for r in self._pending if r.tab_id == tab_id)

    def park(self, tab_id: str, call: asyncio.Future) -> None:
        """Keep the driver call the dialog interrupted until the dialog is answered."""
        previous = self._parked.pop(tab_id, None)
        if previous is not None:
            discard(previous)
        self._parked[tab_id] = call

    async def answer(self, accept: bool, text: str | None) -> DialogRecord:
        """Accept or dismiss the oldest pending dialog.

        Raises ``DialogPendingError`` when none is pending and
        ``ActionFailedError`` when the answer fails; the dialog then stays
        pending.
        """
        record = self.pending
        if record is None:
            raise DialogPendingError("no dialog is open, so there is nothing to answer")
        handle = record.handle
        answer = handle.accept(text) if accept else handle.dismiss()
        try:
            await asyncio.wait_for(answer, self.answer_timeout)
        except asyncio.TimeoutError:
            raise ActionFailedError(
                f"answering the {record.kind} dialog failed",
                detail=f"no answer within {self.answer_timeout:g} s",
            ) from None
        except Exception as exc:
            raise ActionFailedError(
                f"answering the {record.kind} dialog failed",
                detail=getattr(exc, "detail", "") or str(exc),
            ) from None
        record.outcome = "accepted" if accept else "dismissed"
        if record in self._pending:
            self._pending.remove(record)
        if not any(r.tab_id == record.tab_id for r in self._pending):
            self._event(record.tab_id).clear()
        return record

    async def collect(self, tab_id: str, timeout: float) -> BaseException | None:
        """Wait for the call parked on ``tab_id``; return its error, if it raised one.

        A dialog opening on the tab meanwhile ends the wait at once and the
        call stays parked behind that dialog.
        """
        call = self._parked.pop(tab_id, None)
        if call is None:
            return None
        opened = asyncio.create_task(self.wait_opened(tab_id))
        try:
            done, _ = await asyncio.wait(
                {call, opened}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
            )
        except asyncio.CancelledError:
            discard(call)
            raise
        finally:
            opened.cancel()
        if call in done:
            return None if call.cancelled() else call.exception()
        if opened in done:
            self._parked[tab_id] = call
            return None
        discard(call)
        return asyncio.TimeoutError()

    def drain_reports(self) -> list[tuple[str, str, str]]:
        """``(kind, message, outcome)`` of auto-accepted dialogs since the last call."""
        reports = [(r.kind, r.message, r.outcome or "") for r in self._reports]
        self._reports.clear()
        return reports

    def forget_tab(self, tab_id: str) -> None:
        """Drop pending dialogs and parked calls of a closed tab."""
        self._pending = [r for r in self._pending if r.tab_id != tab_id]
        self._events.pop(tab_id, None)
        call = self._parked.pop(tab_id, None)
        if call is not None:
            discard(call)

    def clear(self) -> None:
        """Forget everything; the browser behind these dialogs is gone."""
        for call in self._parked.values():
            discard(call)
        for task in self._tasks:
            task.cancel()
        self._parked.clear()
        self._pending.clear()
        self._reports.clear()
        self._events.clear()

    def _event(self, tab_id: str) -> asyncio.Event:
        event = self._events.get(tab_id)
        if event is None:
            event = self._events[tab_id] = asyncio.Event()
        return event

    def _spawn(self, dialog: DialogDriver) -> None:
        task = asyncio.create_task(self._accept(dialog))
        self._tasks.add(task)
        task.add_done_callback(self._finished)

    async def _accept(self, dialog: DialogDriver) -> None:
        await asyncio.wait_for(dialog.accept(), self.answer_timeout)

    def _finished(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            error = type(task.exception()).__name__
            logger.debug("Auto-accepting a dialog failed", error=error)
