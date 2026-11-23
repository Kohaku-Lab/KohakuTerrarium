"""The observation pipeline: one page read into a cleaned snapshot tree.

Raw snapshot text (cut at ``RAW_SNAPSHOT_MAX_CHARS``, secrets redacted) →
``snapshot.parse`` → per-frame hidden-text probe → ``hidden.apply_probe`` →
``snapshot.clean``. The CPU-heavy steps run in a worker thread. A probe that
fails or times out never fails the observation; it leaves a note instead.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from kohakuterrarium.browser import snapshot as snap
from kohakuterrarium.browser.errors import (
    BrowserTimeoutError,
    DialogOpenedError,
    DriverDisconnectedError,
    DriverError,
    PageClosedError,
)
from kohakuterrarium.browser.hidden import PROBE_JS, ProbeResult, apply_probe
from kohakuterrarium.browser.policy import NetworkPolicy, parse_ip
from kohakuterrarium.browser.protocol import DOCUMENT_ID_JS, PageDriver, ProbeFrames
from kohakuterrarium.browser.proxy import EgressRecord
from kohakuterrarium.browser.results import format_blocked

Call = Callable[[Callable[[], Awaitable[Any]], float, str], Awaitable[Any]]
Redact = Callable[[str], str]
RAW_SNAPSHOT_MAX_CHARS = 1_000_000


@dataclass(frozen=True)
class Observation:
    """One read of a page.

    ``raw`` is the parse of the snapshot text (refs intact, used to judge
    refs) and ``fresh`` its ref fingerprints; ``nodes`` is the cleaned tree
    that results are rendered from. When the raw text was cut,
    ``cut_from`` is its full length and ``raw_chars`` the length kept.
    """

    doc_id: Any
    url: str
    title: str
    raw: list[snap.Node]
    nodes: list[snap.Node]
    passwords: tuple[str, ...] = ()
    dropped: int = 0
    note: str = ""
    fresh: dict[str, tuple[str, str]] = field(default_factory=dict)
    cut_from: int = 0
    raw_chars: int = 0

    @property
    def origin(self) -> str:
        return origin_of(self.url)


def cap_text(text: str, limit: int) -> tuple[str, int]:
    """``text`` cut at the last line break within ``limit``, and its length if cut."""
    if len(text) <= limit:
        return text, 0
    end = text.rfind("\n", 0, limit + 1)
    return text[: end if end > 0 else limit], len(text)


def _prepare(text: str, limit: int, redact: Redact | None) -> tuple[str, int, int]:
    kept, cut_from = cap_text(text or "", limit)
    return (redact(kept) if redact else kept), cut_from, len(kept)


def _fresh(text: str, limit: int, redact: Redact | None) -> dict[str, tuple[str, str]]:
    return snap.fingerprints(snap.parse(_prepare(text, limit, redact)[0]))


def _process(
    text: str, results: list[Any], limit: int, redact: Redact | None
) -> dict[str, Any]:
    prepared, cut_from, kept = _prepare(text, limit, redact)
    raw = snap.parse(prepared)
    outcome = apply_probe(raw, ProbeResult.merge(results))
    return {
        "raw": raw,
        "nodes": snap.clean(outcome.nodes),
        "passwords": outcome.passwords,
        "dropped": outcome.dropped,
        "fresh": snap.fingerprints(raw),
        "cut_from": cut_from,
        "raw_chars": kept if cut_from else 0,
    }


def origin_of(url: str) -> str:
    """``scheme://host[:port]`` of ``url``, or the URL itself when it has no host."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if not parts.scheme or not parts.netloc:
        return url
    return f"{parts.scheme}://{parts.netloc}"


async def read_document(page: PageDriver, call: Call, timeout: float) -> Any:
    """An id that changes whenever the tab loads a new main document."""
    return await call(
        lambda: page.evaluate(DOCUMENT_ID_JS, timeout=timeout),
        timeout,
        "reading the page",
    )


async def _snapshot_text(page: PageDriver, call: Call, timeout: float) -> str:
    return await call(
        lambda: page.snapshot(timeout), timeout, "taking the page snapshot"
    )


async def read_fresh(
    page: PageDriver,
    call: Call,
    timeout: float,
    *,
    redact: Redact | None = None,
    max_chars: int = RAW_SNAPSHOT_MAX_CHARS,
) -> dict[str, tuple[str, str]]:
    """Ref fingerprints of a fresh full-page snapshot, read the way ``observe`` reads."""
    text = await _snapshot_text(page, call, timeout)
    return await asyncio.to_thread(_fresh, text, max_chars, redact)


async def observe(
    page: PageDriver,
    call: Call,
    timeout: float,
    *,
    redact: Redact | None = None,
    max_chars: int = RAW_SNAPSHOT_MAX_CHARS,
) -> Observation:
    """Read ``page`` through ``call`` (the session's bounded, dialog-aware runner).

    ``redact`` is applied to the raw snapshot text before it is parsed, so
    no later cut can split a secret.
    """
    doc_id = await read_document(page, call, timeout)
    text = await _snapshot_text(page, call, timeout)
    note = ""
    try:
        frames = await call(
            lambda: page.probe(PROBE_JS, timeout), timeout, "checking for hidden text"
        )
        if frames.failed:
            note = (
                f"Hidden-text check failed in {frames.failed} frame"
                f"{'' if frames.failed == 1 else 's'}; visually hidden text there may be shown."
            )
    except (DriverDisconnectedError, PageClosedError, DialogOpenedError):
        raise
    except (DriverError, BrowserTimeoutError) as exc:
        frames = ProbeFrames([])
        note = f"Hidden-text check skipped ({exc}); visually hidden text may be shown."
    parts = await asyncio.to_thread(
        _process, text, list(frames.results), max_chars, redact
    )
    title = await call(lambda: page.title(timeout), timeout, "reading the title")
    return Observation(
        doc_id=doc_id, url=page.url, title=title or "", note=note, **parts
    )


def blocked_reason(record: EgressRecord, policy: NetworkPolicy) -> str:
    """The policy's sentence for a blocked proxy decision, falling back to its code."""
    address = record.address
    if address is None and parse_ip(record.host) is not None:
        address = record.host
    if address is not None:
        decision = policy.check_address(address)
    else:
        decision = policy.check_host(record.host, record.port)
    return decision.reason if not decision.allowed else record.code


def format_blocked_records(records: list[EgressRecord], policy: NetworkPolicy) -> str:
    """``results.format_blocked`` over proxy records."""
    return format_blocked(
        (f"{r.host}:{r.port}", blocked_reason(r, policy)) for r in records
    )
