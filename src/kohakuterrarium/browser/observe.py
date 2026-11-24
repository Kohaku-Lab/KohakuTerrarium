"""The observation pipeline: one page read into a cleaned snapshot tree.

Raw snapshot text (cut at ``RAW_SNAPSHOT_MAX_CHARS``, secrets redacted) →
``snapshot.parse`` → per-frame hidden-text probe → ``hidden.apply_probe`` →
``snapshot.clean``. An ``iframe`` line whose frame shows another origin than
the page gets ``[origin=...]``. The CPU-heavy steps run in a worker thread. A
probe that fails or times out never fails the observation; it leaves a note
instead, and a failed frame read only leaves the frame lines without origins.
"""

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
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
MAX_FRAME_ORIGINS = 20

_IFRAME_REF = re.compile(r"^[ \t]*- iframe\b[^\n]*?\[ref=([^\]\s]+)\]", re.MULTILINE)


@dataclass(frozen=True)
class Observation:
    """One read of a page.

    ``raw`` is the parse of the snapshot text (refs intact, used to judge
    refs) and ``fresh`` its ref fingerprints; ``nodes`` is the cleaned tree
    that results are rendered from. When the raw text was cut,
    ``cut_from`` is its full length and ``raw_chars`` the length kept.
    ``boxes`` maps refs to ``x,y,w,h`` when the read asked for boxes; the
    trees never carry them.
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
    boxes: dict[str, str] = field(default_factory=dict)

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


def _mark_frames(nodes: list[snap.Node], origins: dict[str, str]) -> list[snap.Node]:
    out = []
    for node in nodes:
        origin = origins.get(node.uid or "") if node.role == "iframe" else None
        attrs = node.attrs + ((("origin", origin),) if origin else ())
        children = _mark_frames(node.children, origins)
        out.append(replace(node, attrs=attrs, children=children))
    return out


def _process(
    text: str,
    results: list[Any],
    limit: int,
    redact: Redact | None,
    origins: dict[str, str] | None = None,
) -> dict[str, Any]:
    prepared, cut_from, kept = _prepare(text, limit, redact)
    raw, boxes = snap.split_boxes(snap.parse(prepared))
    outcome = apply_probe(raw, ProbeResult.merge(results))
    nodes = _mark_frames(outcome.nodes, origins) if origins else outcome.nodes
    return {
        "raw": raw,
        "nodes": snap.clean(nodes),
        "passwords": outcome.passwords,
        "dropped": outcome.dropped,
        "fresh": snap.fingerprints(raw),
        "cut_from": cut_from,
        "raw_chars": kept if cut_from else 0,
        "boxes": boxes,
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


def _web_origin(url: str) -> str | None:
    """``scheme://host[:port]`` of an http(s) URL without credentials, else ``None``."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    host = parts.netloc.rpartition("@")[2]
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https") or not host:
        return None
    return f"{scheme}://{host}"


async def _frame_origins(
    page: PageDriver, call: Call, timeout: float, text: str, redact: Redact | None
) -> dict[str, str]:
    """``{iframe ref: origin}`` for frames showing another origin than the page."""
    refs = list(dict.fromkeys(_IFRAME_REF.findall(text)))[:MAX_FRAME_ORIGINS]
    if not refs:
        return {}
    try:
        urls = await call(
            lambda: page.frame_urls(refs, timeout), timeout, "reading frame addresses"
        )
    except (DriverDisconnectedError, PageClosedError, DialogOpenedError):
        raise
    except (DriverError, BrowserTimeoutError):
        return {}
    page_origin = _web_origin(page.url)
    origins = {}
    for ref, url in urls.items():
        origin = _web_origin(url) if isinstance(url, str) else None
        if origin is not None and origin != page_origin:
            origins[ref] = redact(origin) if redact else origin
    return origins


async def read_document(page: PageDriver, call: Call, timeout: float) -> Any:
    """An id that changes whenever the tab loads a new main document."""
    return await call(
        lambda: page.evaluate(DOCUMENT_ID_JS, timeout=timeout),
        timeout,
        "reading the page",
    )


async def _snapshot_text(
    page: PageDriver, call: Call, timeout: float, boxes: bool = False
) -> str:
    read = (lambda: page.snapshot(timeout, boxes=True)) if boxes else None
    return await call(
        read or (lambda: page.snapshot(timeout)), timeout, "taking the page snapshot"
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
    boxes: bool = False,
) -> Observation:
    """Read ``page`` through ``call`` (the session's bounded, dialog-aware runner).

    ``redact`` is applied to the raw snapshot text before it is parsed, so
    no later cut can split a secret. ``boxes`` also reads each element's
    box into ``Observation.boxes``.
    """
    doc_id = await read_document(page, call, timeout)
    text = await _snapshot_text(page, call, timeout, boxes)
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
    kept, _ = cap_text(text or "", max_chars)
    origins = await _frame_origins(page, call, timeout, kept, redact)
    parts = await asyncio.to_thread(
        _process, text, list(frames.results), max_chars, redact, origins
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
        decision = policy.check_address(address, record.port)
    else:
        decision = policy.check_host(record.host, record.port)
    return decision.reason if not decision.allowed else record.code


def format_blocked_records(records: list[EgressRecord], policy: NetworkPolicy) -> str:
    """``results.format_blocked`` over proxy records."""
    return format_blocked(
        (f"{r.host}:{r.port}", blocked_reason(r, policy)) for r in records
    )
