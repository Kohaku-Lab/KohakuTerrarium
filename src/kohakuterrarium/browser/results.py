"""Text the browser tools return: header line, untrusted wrapper, post-action delta."""

import html
import re
import secrets
from bisect import bisect_left
from collections import defaultdict, deque
from collections.abc import Iterable
from dataclasses import dataclass

from kohakuterrarium.browser.dialogs import DIALOG_KINDS
from kohakuterrarium.browser.snapshot import Node, is_interactive, line_of, normalize

HEADER_MAX = 160
HEADER_URL = 64
LINE_URL = 120
LINE_TEXT = 80
MAX_LISTED = 5
DIFF_MAX_LINES = 40
DIFF_MAX_CHARS = 3000
WRAPPER_TAG = "page_content"

_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_GAP = r"[\s­​-‏ - ⁠-⁤﻿]*"
_TAG_START = re.compile(
    r"<(?=(?:"
    + _GAP
    + r"/)*"
    + _GAP
    + _GAP.join("page")
    + r"(?:[_\-]|"
    + _GAP[:-1]
    + r")*"
    + _GAP.join("content")
    + ")",
    re.IGNORECASE,
)


def one_line(text: str) -> str:
    """``text`` on one line: control characters become spaces, runs of spaces collapse."""
    return normalize(_CONTROL.sub(" ", text or ""))


def middle(text: str, limit: int) -> str:
    """Shorten ``text`` to ``limit`` characters by replacing its middle with ``…``."""
    if len(text) <= limit:
        return text
    head = (limit - 1) // 2
    return text[:head] + "…" + text[len(text) - (limit - 1 - head) :]


def format_header(tab_id: str, url: str, dialog: str | None = None) -> str:
    """One line naming the tab, URL and pending dialog kind, at most ``HEADER_MAX``.

    The header sits outside the untrusted wrapper, so it carries no
    page-chosen text: a dialog outside ``DIALOG_KINDS`` is shown as ``dialog``.
    """
    parts = [f"[{one_line(tab_id)}] {middle(one_line(url), HEADER_URL)}"]
    if dialog:
        kind = dialog if dialog in DIALOG_KINDS else "dialog"
        parts.append(f"dialog pending: {kind}")
    return middle(" | ".join(parts), HEADER_MAX)


def format_title(title: str) -> str:
    """The page title as the first line inside the untrusted wrapper."""
    text = middle(one_line(title), LINE_TEXT)
    return f'Title: "{text}"' if text else ""


def new_token() -> str:
    """A fresh random id for one session's ``page_content`` wrappers."""
    return secrets.token_hex(8)


def wrap_untrusted(text: str, origin: str | None, token: str) -> str:
    """Wrap page-derived text in a ``page_content`` element the page cannot close.

    ``origin`` becomes the ``origin`` attribute; ``None`` leaves it out.

    Inside the content, the session token is removed and every ``<`` that
    starts something resembling the wrapper tag (any case; whitespace, line
    breaks, zero-width or format characters, ``_``/``-`` anywhere between the
    letters) becomes ``&lt;``.
    """
    body = (
        re.sub(re.escape(token), "[id]", text, flags=re.IGNORECASE) if token else text
    )
    body = _TAG_START.sub("&lt;", body)
    attr = "" if origin is None else f'origin="{html.escape(origin, quote=True)}" '
    return (
        f'<{WRAPPER_TAG} {attr}id="{token}" trust="untrusted">\n'
        f"{body}\n</{WRAPPER_TAG}>"
    )


@dataclass(frozen=True)
class _Unit:
    key: tuple | None
    group: tuple
    lines: tuple[str, ...]
    depth: int
    role: str
    ref: str | None
    fingerprint: tuple[str, str]
    anchor: int | None


def _units(nodes: list[Node]) -> list[_Unit]:
    """Flatten a tree into diff units in document order.

    A unit is one element line plus its folded leaf lines. ``key`` is the
    element's identity (its parsed ref); ``group`` is its nearest identified
    ancestor and its depth, both as they were before wrapper lines were
    collapsed, so a wrapper that folds or unfolds moves nothing.
    """
    units: list[_Unit] = []
    seen: dict[str, int] = {}
    wrappers: dict[str, tuple] = {}

    def identify(uid: str) -> tuple:
        seen[uid] = seen.get(uid, 0) + 1
        return (uid, seen[uid])

    def visit(
        siblings: list[Node], depth: int, anchor: int | None, owner, level: int
    ) -> None:
        for node in siblings:
            parent, place = owner, level + node.lifted
            if node.lifted and node.via:
                if node.via not in wrappers:
                    wrappers[node.via] = identify(node.via)
                parent = wrappers[node.via]
            folded = ("prop", "text") if is_interactive(node) else ("prop",)
            leaves = [c for c in node.children if c.kind in folded and not c.children]
            lines = (line_of(node),) + tuple(line_of(c) for c in leaves)
            uid = node.uid or node.ref
            key = identify(uid) if uid else None
            ref = node.ref
            units.append(
                _Unit(
                    key,
                    (parent, place),
                    lines,
                    depth,
                    node.role,
                    ref,
                    node.fingerprint,
                    anchor,
                )
            )
            index = len(units) - 1
            inner = anchor if not (ref or node.name) else index
            rest = [c for c in node.children if not any(c is leaf for leaf in leaves)]
            visit(rest, depth + 1, inner, key or parent, place + 1)

    visit(nodes, 0, None, None, 0)
    return units


@dataclass(frozen=True)
class SnapshotDiff:
    """The post-action snapshot delta as text, with its counts.

    ``removed`` counts lines; ``added`` and ``changed`` count elements, and
    ``moved`` the changed ones that moved among their siblings or to another
    parent. ``text`` is empty when nothing changed. ``fingerprints`` maps each
    ref shown in ``text`` to its ``(role, name)``, ready for ``RefBook.record``.
    """

    text: str
    added: int
    changed: int
    removed: int
    truncated: bool
    fingerprints: dict[str, tuple[str, str]]
    moved: int = 0


def _in_order(values: list[int]) -> set[int]:
    """Positions of one longest increasing subsequence of ``values``, in O(n log n)."""
    tails: list[int] = []
    tail_at: list[int] = []
    before = [-1] * len(values)
    for i, value in enumerate(values):
        k = bisect_left(tails, value)
        if k == len(tails):
            tails.append(value)
            tail_at.append(i)
        else:
            tails[k] = value
            tail_at[k] = i
        before[i] = tail_at[k - 1] if k else -1
    kept: set[int] = set()
    i = tail_at[-1] if tail_at else -1
    while i >= 0:
        kept.add(i)
        i = before[i]
    return kept


def _moves(old: list[_Unit], new: list[_Unit]) -> set[int]:
    """New units that kept their key but changed parent or order among their siblings.

    Within each sibling group, the units outside one longest run that kept
    its relative order count as moved, so a single move marks one unit.
    """
    old_at = {u.key: (i, u.group) for i, u in enumerate(old) if u.key is not None}
    moved: set[int] = set()
    groups: dict[tuple, list[tuple[int, int]]] = defaultdict(list)
    for j, unit in enumerate(new):
        seen = old_at.get(unit.key) if unit.key is not None else None
        if seen is None:
            continue
        if seen[1] != unit.group:
            moved.add(j)
        else:
            groups[unit.group].append((seen[0], j))
    for pairs in groups.values():
        kept = _in_order([i for i, _ in pairs])
        moved.update(j for n, (_, j) in enumerate(pairs) if n not in kept)
    return moved


def _classify(old: list[_Unit], new: list[_Unit]) -> tuple[dict[int, str], int]:
    """Mark new units ``+`` / ``~`` and count removed lines, in linear time.

    Identified units match by key. Within each group, unidentified units
    first match on identical lines; the leftovers pair up in order when their
    roles agree (``~``), and the rest are added or removed.
    """
    marks: dict[int, str] = {}
    removed = 0
    old_keys = {u.key: i for i, u in enumerate(old) if u.key is not None}
    new_keys = {u.key for u in new if u.key is not None}
    old_groups: dict[tuple, list[int]] = defaultdict(list)
    new_groups: dict[tuple, list[int]] = defaultdict(list)
    for i, unit in enumerate(old):
        if unit.key is None:
            old_groups[unit.group].append(i)
        elif unit.key not in new_keys:
            removed += len(unit.lines)
    for j, unit in enumerate(new):
        if unit.key is None:
            new_groups[unit.group].append(j)
            continue
        i = old_keys.get(unit.key)
        if i is None:
            marks[j] = "+"
        elif old[i].lines != unit.lines:
            marks[j] = "~"
    for group, news in new_groups.items():
        pool: dict[tuple[str, ...], deque[int]] = defaultdict(deque)
        for i in old_groups.pop(group, []):
            pool[old[i].lines].append(i)
        unmatched = []
        for j in news:
            if pool[new[j].lines]:
                pool[new[j].lines].popleft()
            else:
                unmatched.append(j)
        leftover = sorted(i for queue in pool.values() for i in queue)
        pairs = [
            (i, j) for i, j in zip(leftover, unmatched) if old[i].role == new[j].role
        ]
        paired_old = {i for i, _ in pairs}
        paired_new = {j for _, j in pairs}
        marks.update({j: "~" for j in paired_new})
        marks.update({j: "+" for j in unmatched if j not in paired_new})
        removed += sum(len(old[i].lines) for i in leftover if i not in paired_old)
    for olds in old_groups.values():
        removed += sum(len(old[i].lines) for i in olds)
    return marks, removed


def diff_snapshots(
    before: list[Node],
    after: list[Node],
    *,
    max_lines: int = DIFF_MAX_LINES,
    max_chars: int = DIFF_MAX_CHARS,
) -> SnapshotDiff:
    """Lines added and changed between two cleaned snapshots, plus a removed-line count.

    An element whose ref stayed but whose line or value changed is ``~``
    changed. Each group starts with an ``@`` line naming the nearest ref'd or
    named ancestor. Past ``max_lines`` / ``max_chars`` the text ends with a
    "page changed substantially" line instead of more entries.
    """
    old, new = _units(before), _units(after)
    marks, removed = _classify(old, new)
    moved = _moves(old, new)
    for j in moved:
        marks.setdefault(j, "~")
    added = sum(1 for m in marks.values() if m == "+")
    changed = len(marks) - added
    if not marks and not removed:
        return SnapshotDiff("", 0, 0, 0, False, {})
    body: list[str] = []
    shown_refs: dict[str, tuple[str, str]] = {}
    used = -1
    shown = 0
    truncated = False
    context: int | None = -1
    base = 0
    for j in sorted(marks):
        unit = new[j]
        entry: list[str] = []
        entry_units = [unit]
        if unit.anchor not in marks and unit.anchor != context:
            context = unit.anchor
            base = unit.depth
            if unit.anchor is not None:
                entry.append("@ " + new[unit.anchor].lines[0].removesuffix(":"))
                entry_units.append(new[unit.anchor])
        pad = "  " * max(0, unit.depth - base)
        tail = " (moved)" if j in moved else ""
        entry.append(f"{marks[j]} {pad}- {unit.lines[0]}{tail}")
        entry.extend(f"  {pad}  - {line}" for line in unit.lines[1:])
        cost = sum(len(line) + 1 for line in entry)
        if len(body) + len(entry) > max_lines or used + cost > max_chars:
            truncated = True
            break
        body.extend(entry)
        shown_refs.update({u.ref: u.fingerprint for u in entry_units if u.ref})
        used += cost
        shown += 1
    summary = (
        f"Page changes (+ added, ~ changed, @ context): {added} added, "
        f"{changed} changed{f' ({len(moved)} moved)' if moved else ''}, "
        f"{removed} lines removed"
    )
    lines = [summary, *body]
    if truncated:
        lines.append(
            f"… page changed substantially ({len(marks) - shown} more changes not shown);"
            " take a snapshot."
        )
    return SnapshotDiff(
        "\n".join(lines),
        added,
        changed,
        removed,
        truncated,
        shown_refs,
        len(moved),
    )


def format_navigation(
    before_url: str, after_url: str, before_title: str, after_title: str
) -> str:
    """A line for a URL or title change; empty when neither changed."""
    titles = f'"{middle(one_line(before_title), LINE_TEXT)}" → "{middle(one_line(after_title), LINE_TEXT)}"'
    if before_url != after_url:
        line = f"Navigated: {middle(one_line(before_url), LINE_URL)} → {middle(one_line(after_url), LINE_URL)}"
        return f"{line} | title {titles}" if before_title != after_title else line
    return f"Title changed: {titles}" if before_title != after_title else ""


def format_tabs_opened(tabs: Iterable[tuple[str, str]]) -> str:
    """One line per tab an action opened: ``(tab_id, url)``."""
    return "\n".join(
        f"Tab opened: {tab} {middle(one_line(url), LINE_URL)}" for tab, url in tabs
    )


def format_dialogs(dialogs: Iterable[tuple[str, ...]], pending: bool = False) -> str:
    """Lines for dialogs: ``(kind, message)`` or ``(kind, message, outcome)``."""
    lines = []
    for kind, message, *rest in dialogs:
        quoted = f'{kind} "{middle(one_line(message), LINE_TEXT)}"'
        if pending:
            lines.append(f"Dialog pending: {quoted} (answer it with the dialog action)")
        else:
            lines.append(f"Dialog: {quoted} ({rest[0] if rest else 'accepted'})")
    return "\n".join(lines)


def format_downloads(downloads: Iterable[tuple[str, str]]) -> str:
    """One line per finished download: ``(suggested filename, saved path)``."""
    return "\n".join(
        f"Downloaded: {middle(one_line(name), LINE_TEXT)} → {path}"
        for name, path in downloads
    )


def format_blocked(blocked: Iterable[tuple[str, str]]) -> str:
    """Blocked requests ``(url, reason)``, listing at most ``MAX_LISTED``."""
    items = list(blocked)
    if not items:
        return ""
    noun = "request" if len(items) == 1 else "requests"
    lines = [f"Blocked {len(items)} {noun}:"]
    lines.extend(
        f"  {middle(one_line(url), LINE_URL)} ({one_line(reason)})"
        for url, reason in items[:MAX_LISTED]
    )
    if len(items) > MAX_LISTED:
        lines.append(f"  … and {len(items) - MAX_LISTED} more")
    return "\n".join(lines)


def format_hidden_dropped(count: int) -> str:
    """How many visually hidden text blocks were removed; empty for zero."""
    if count <= 0:
        return ""
    return f"Dropped {count} visually hidden text block{'' if count == 1 else 's'}."


def format_truncation(cut_lines: int, cut_refs: int) -> str:
    """The line ending a snapshot cut by ``max_chars``; empty when nothing was cut."""
    if cut_lines <= 0:
        return ""
    return (
        f"… truncated: {cut_lines} more lines ({cut_refs} refs)"
        " — narrow with ref / depth / query."
    )


def join_sections(*parts: str) -> str:
    """Join the non-empty parts with newlines."""
    return "\n".join(part for part in parts if part)
