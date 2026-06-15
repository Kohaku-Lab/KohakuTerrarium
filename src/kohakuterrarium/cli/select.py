"""Runnable enumeration + picker navigation state (UI-free, testable).

This is the data layer behind the ``kt-cli`` / ``kt-tui`` startup agent
picker.  It deliberately imports **no** UI toolkit (prompt_toolkit /
textual) so the grouping, filtering, and cursor logic can be unit-tested
in isolation; the two renderers (:mod:`.select_cli`, :mod:`.select_tui`)
sit on top of it.

The single discovery entry point is :func:`enumerate_runnables`, which
projects :func:`kohakuterrarium.studio.catalog.packages_scan.scan_catalog`
into ``RunnableGroup``s grouped by source (``"local"`` first, then
installed packages alphabetically).  Each :class:`Runnable` carries a
portable ``ref`` (``@pkg/...`` where possible, else an absolute path)
that feeds straight into ``resolve_any_path`` / ``run_agent_cli``.
"""

from dataclasses import dataclass, field

from kohakuterrarium.packages.locations import get_package_root, packages_dir
from kohakuterrarium.packages.walk import list_packages
from kohakuterrarium.studio.catalog.packages_scan import scan_catalog, to_ref

LOCAL_SOURCE = "local"


@dataclass(frozen=True)
class Runnable:
    """A single selectable creature or terrarium.

    ``ref`` is what gets handed to ``run_agent_cli`` — a ``@pkg/...``
    reference when the config lives inside an installed package, else an
    absolute filesystem path.
    """

    ref: str
    name: str
    kind: str  # "creature" | "terrarium"
    source: str  # package name or "local"
    description: str = ""
    model: str = ""
    members: tuple[str, ...] = ()  # terrarium-only: member creature names


@dataclass(frozen=True)
class RunnableGroup:
    """All runnables originating from one source (a package or ``local``)."""

    source: str
    creatures: tuple[Runnable, ...] = ()
    terrariums: tuple[Runnable, ...] = ()

    def items(self) -> list[Runnable]:
        """Flat list, creatures before terrariums (display order)."""
        return [*self.creatures, *self.terrariums]


def _package_roots() -> dict[str, str]:
    """``{resolved_pkg_root_str: pkg_name}`` for installed packages.

    Mirrors the private ``packages_scan._build_package_root_map`` using
    only the public ``list_packages`` / ``get_package_root`` surface so
    we do not depend on a private symbol.
    """
    roots: dict[str, str] = {}
    if not packages_dir().exists():
        return roots
    for pkg in list_packages():
        root = get_package_root(pkg["name"])
        if root is not None:
            roots[str(root.resolve())] = pkg["name"]
    return roots


def _clean_description(text: str) -> str:
    """Collapse a (possibly multiline) description to one trimmed line."""
    return " ".join(text.split())


def _sorted_sources(sources: list[str]) -> list[str]:
    """``local`` first, then every other source case-insensitively a→z."""
    others = sorted((s for s in sources if s != LOCAL_SOURCE), key=str.lower)
    return ([LOCAL_SOURCE] if LOCAL_SOURCE in sources else []) + others


def enumerate_runnables() -> list[RunnableGroup]:
    """Group every visible creature + terrarium by source for the picker.

    Built on ``scan_catalog()`` (installed packages + ``cwd``), with each
    entry's absolute path projected back to a portable ``ref`` via
    ``to_ref``.  Empty-name entries are already filtered by the scanner.
    """
    roots = _package_roots()
    by_source: dict[str, list[Runnable]] = {}
    for entry in scan_catalog():
        source = entry.source or LOCAL_SOURCE
        runnable = Runnable(
            ref=to_ref(entry.path, roots),
            name=entry.name,
            kind=entry.type,
            source=source,
            description=_clean_description(entry.description or ""),
            model=entry.model or "",
            members=tuple(entry.creatures),
        )
        by_source.setdefault(source, []).append(runnable)

    groups: list[RunnableGroup] = []
    for source in _sorted_sources(list(by_source)):
        items = by_source[source]
        creatures = tuple(
            sorted(
                (r for r in items if r.kind == "creature"), key=lambda r: r.name.lower()
            )
        )
        terrariums = tuple(
            sorted(
                (r for r in items if r.kind == "terrarium"),
                key=lambda r: r.name.lower(),
            )
        )
        groups.append(
            RunnableGroup(source=source, creatures=creatures, terrariums=terrariums)
        )
    return groups


def _matches(runnable: Runnable, needle: str) -> bool:
    """Case-insensitive substring match over name / description / source."""
    return (
        needle in runnable.name.lower()
        or needle in runnable.description.lower()
        or needle in runnable.source.lower()
    )


@dataclass
class Row:
    """One rendered line in the picker: a group header or a runnable."""

    kind: str  # "header" | "entry"
    source: str
    runnable: Runnable | None = None


def flatten_rows(
    groups: list[RunnableGroup], expanded: set[str], flt: str
) -> list[Row]:
    """Project groups → a flat list of visible rows.

    With a non-empty ``flt`` filter, only matching entries show and their
    groups are force-expanded (collapse state is ignored while filtering);
    groups with no match are dropped entirely.  With no filter, a group
    contributes a header plus — if in ``expanded`` — its entries.
    """
    needle = flt.strip().lower()
    rows: list[Row] = []
    for group in groups:
        items = group.items()
        if needle:
            items = [r for r in items if _matches(r, needle)]
            if not items:
                continue
            rows.append(Row("header", group.source))
            rows.extend(Row("entry", group.source, r) for r in items)
        else:
            rows.append(Row("header", group.source))
            if group.source in expanded:
                rows.extend(Row("entry", group.source, r) for r in items)
    return rows


@dataclass
class NavState:
    """Cursor + expand/collapse + filter state over a set of groups.

    Pure: every method mutates in-memory state and re-derives ``rows``.
    The renderers call :meth:`move`, :meth:`collapse`, :meth:`expand`,
    :meth:`toggle`, :meth:`add_char`, :meth:`backspace`, and read
    :attr:`rows` / :meth:`current` to draw.
    """

    groups: list[RunnableGroup]
    filter: str = ""
    expanded: set[str] = field(default_factory=set)
    cursor: int = 0
    rows: list[Row] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Start with every group expanded — the catalog is usually small
        # enough that showing everything up front beats forcing the user
        # to expand each package.
        if not self.expanded:
            self.expanded = {g.source for g in self.groups}
        self._refresh()
        self.cursor = self._first_entry_index()

    # ── derivation ──

    def _refresh(self) -> None:
        self.rows = flatten_rows(self.groups, self.expanded, self.filter)
        self._clamp()

    def _clamp(self) -> None:
        if not self.rows:
            self.cursor = 0
            return
        self.cursor = max(0, min(self.cursor, len(self.rows) - 1))

    def _first_entry_index(self) -> int:
        for i, row in enumerate(self.rows):
            if row.kind == "entry":
                return i
        return 0

    # ── queries ──

    def current(self) -> Row | None:
        if not self.rows:
            return None
        return self.rows[max(0, min(self.cursor, len(self.rows) - 1))]

    def is_empty(self) -> bool:
        return not self.rows

    # ── cursor ──

    def move(self, delta: int) -> None:
        if not self.rows:
            return
        self.cursor = max(0, min(len(self.rows) - 1, self.cursor + delta))

    # ── expand / collapse ──

    def _header_source_for_cursor(self) -> str | None:
        row = self.current()
        if row is None:
            return None
        return row.source

    def expand(self) -> None:
        """Expand the group under the cursor (no-op while filtering)."""
        if self.filter.strip():
            return
        source = self._header_source_for_cursor()
        if source is not None:
            self.expanded.add(source)
            self._refresh()

    def collapse(self) -> None:
        """Collapse the group under the cursor; from an entry, jump to its header."""
        if self.filter.strip():
            return
        row = self.current()
        if row is None:
            return
        if row.kind == "entry":
            self._move_cursor_to_header(row.source)
            return
        self.expanded.discard(row.source)
        self._refresh()
        self._move_cursor_to_header(row.source)

    def _move_cursor_to_header(self, source: str) -> None:
        for i, row in enumerate(self.rows):
            if row.kind == "header" and row.source == source:
                self.cursor = i
                return

    def toggle(self) -> str | None:
        """Enter/space: select an entry (return its ref) or toggle a header.

        Returns the selected ``ref`` when the cursor is on a runnable,
        else ``None`` (a header was toggled, or nothing is selectable).
        """
        row = self.current()
        if row is None:
            return None
        if row.kind == "entry" and row.runnable is not None:
            return row.runnable.ref
        # Header: toggle expansion (ignored while filtering — everything
        # is already expanded then).
        if not self.filter.strip():
            if row.source in self.expanded:
                self.expanded.discard(row.source)
            else:
                self.expanded.add(row.source)
            self._refresh()
            self._move_cursor_to_header(row.source)
        return None

    # ── filter ──

    def add_char(self, char: str) -> None:
        self.filter += char
        self._refresh()
        self.cursor = self._first_entry_index()

    def backspace(self) -> None:
        if self.filter:
            self.filter = self.filter[:-1]
            self._refresh()
            self.cursor = self._first_entry_index()
