"""Project membership: which project every session folder belongs to.

Candidate roots are the saved projects plus every distinct session folder. A
folder is its own *found* project only when no saved project and no other
distinct folder on the same node contains it. A session belongs to the
deepest project whose folder is its folder or an ancestor of it on the same
node; a session with no folder is unassigned.
"""

import os
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from kohakuterrarium.studio.persistence.projects.paths import (
    LOCAL_NODE,
    ancestors,
    default_name,
    normalize_node,
    normalize_path,
    project_id,
    public_node,
)

UNASSIGNED = "none"

Location = tuple[str, str]


@dataclass
class _Counts:
    session_count: int = 0
    running_count: int = 0
    last_active: str = ""

    def add_sessions(self, count: int, last_active: str) -> None:
        self.session_count += count
        if last_active > self.last_active:
            self.last_active = last_active

    def public_counts(self) -> dict[str, Any]:
        return {
            "session_count": self.session_count,
            "running_count": self.running_count,
            "last_active": self.last_active or None,
        }


@dataclass
class ProjectRoot(_Counts):
    """One project (saved or found) with its session tallies."""

    id: str = ""
    node: str = LOCAL_NODE
    key: str = ""
    path: str = ""
    record: dict[str, Any] | None = field(default=None)

    @property
    def saved(self) -> bool:
        return self.record is not None

    @property
    def name(self) -> str:
        raw = (self.record or {}).get("name")
        custom = raw.strip() if isinstance(raw, str) else ""
        return custom or default_name(self.path, self.node)

    def to_public(self) -> dict[str, Any]:
        """The API project object."""
        record = self.record or {}
        return {
            "id": self.id,
            "path": self.path,
            "node": public_node(self.node),
            "name": self.name,
            "pinned": bool(record.get("pinned")),
            "hidden": bool(record.get("hidden")),
            "saved": self.saved,
            "exists": os.path.isdir(self.path) if self.node == LOCAL_NODE else None,
            **self.public_counts(),
        }

    def ref(self) -> dict[str, str]:
        """The short ``{"id", "name"}`` form attached to session rows."""
        return {"id": self.id, "name": self.name}


def sort_projects(projects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pinned first, then most recently active, then by name."""
    ordered = sorted(projects, key=lambda p: (p["name"].casefold(), p["id"]))
    ordered.sort(key=lambda p: p["last_active"] or "", reverse=True)
    ordered.sort(key=lambda p: not p["pinned"])
    return ordered


class ProjectView:
    """Projects and session membership computed from one snapshot.

    ``groups`` maps each raw ``(node_id, pwd)`` pair of the session index to
    ``(session_count, latest last_active)``; ``live`` lists the raw
    ``(node_id, pwd)`` of every running session.
    """

    def __init__(
        self,
        records: Iterable[dict[str, Any]],
        groups: Mapping[Location, tuple[int, str]],
        live: Iterable[tuple[str | None, str | None]] = (),
    ) -> None:
        self._roots: dict[Location, ProjectRoot] = {}
        self._by_id: dict[str, ProjectRoot] = {}
        self._resolved: dict[Location, ProjectRoot | None] = {}
        self._locations: dict[str | None, set[Location]] = defaultdict(set)
        self.unassigned = _Counts()
        live_pairs = [(str(n or ""), str(p or "")) for n, p in live]
        for record in records:
            self._add_saved(record)
        self._add_found(sorted(groups) + live_pairs)
        for raw, (count, last_active) in groups.items():
            root = self.resolve(*raw)
            (root or self.unassigned).add_sessions(int(count), str(last_active))
            self._locations[root.id if root else None].add(raw)
        for raw in live_pairs:
            root = self.resolve(*raw)
            (root or self.unassigned).running_count += 1

    def _add_saved(self, record: dict[str, Any]) -> None:
        node = normalize_node(record.get("node"))
        path, key = normalize_path(record["path"], node)
        if (node, key) in self._roots:
            return
        root = ProjectRoot(
            id=project_id(node, key), node=node, key=key, path=path, record=record
        )
        self._roots[(node, key)] = root
        self._by_id[root.id] = root

    def _add_found(self, raw_pairs: list[Location]) -> None:
        saved = set(self._roots)
        distinct: dict[Location, str] = {}
        for raw_node, raw_pwd in raw_pairs:
            node = normalize_node(raw_node)
            path, key = normalize_path(raw_pwd, node)
            if key:
                distinct.setdefault((node, key), path)
        for (node, key), path in distinct.items():
            if (node, key) in saved:
                continue
            if any(
                (node, parent) in saved or (node, parent) in distinct
                for parent in ancestors(key, node)
            ):
                continue
            root = ProjectRoot(id=project_id(node, key), node=node, key=key, path=path)
            self._roots[(node, key)] = root
            self._by_id[root.id] = root

    def resolve(self, raw_node: str | None, raw_pwd: str | None) -> ProjectRoot | None:
        """The deepest project containing a raw ``(node_id, pwd)``, or ``None``."""
        raw = (str(raw_node or ""), str(raw_pwd or ""))
        if raw in self._resolved:
            return self._resolved[raw]
        node = normalize_node(raw[0])
        _, key = normalize_path(raw[1], node)
        found = None
        if key:
            for candidate in [key, *ancestors(key, node)]:
                found = self._roots.get((node, candidate))
                if found is not None:
                    break
        self._resolved[raw] = found
        return found

    def get(self, project_id_: str) -> ProjectRoot | None:
        return self._by_id.get(project_id_)

    def ref_for(self, raw_node: str | None, raw_pwd: str | None) -> Any:
        """``{"id", "name"}`` of a session's project, or ``None``."""
        root = self.resolve(raw_node, raw_pwd)
        return root.ref() if root is not None else None

    def locations_for(self, project: str) -> set[Location]:
        """Raw index ``(node_id, pwd)`` pairs of a project, or of ``"none"``."""
        if project == UNASSIGNED:
            return set(self._locations.get(None, set()))
        return set(self._locations.get(project, set()))

    def projects(self, *, include_hidden: bool = False) -> list[dict[str, Any]]:
        """Every project as API objects, sorted for display."""
        rows = [root.to_public() for root in self._roots.values()]
        if not include_hidden:
            rows = [row for row in rows if not row["hidden"]]
        return sort_projects(rows)


__all__ = ["UNASSIGNED", "ProjectRoot", "ProjectView", "sort_projects"]
