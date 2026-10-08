"""Starting points for new creatures and modules.

A starter is a YAML file under ``data/<kind>/<id>.yaml`` holding a label, a
one-line summary, a sort order and a ``form``: the values the kind's code
generator (or, for creatures, the scaffold) renders from. Every starter
renders something that loads and runs as-is; ``blank`` is each kind's default.
"""

from copy import deepcopy
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

import yaml

STARTER_KINDS = (
    "creatures",
    "tools",
    "subagents",
    "triggers",
    "plugins",
    "inputs",
    "outputs",
)
DEFAULT_STARTER = "blank"

_DATA_DIR = Path(__file__).resolve().parent / "data"


class UnknownStarterError(ValueError):
    """No starter of that id exists for the kind."""


@dataclass(frozen=True)
class Starter:
    """One starting point: what it is called, what it does, what it renders from."""

    kind: str
    id: str
    label: str
    summary: str
    order: int = 0
    form: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "id": self.id,
            "label": self.label,
            "summary": self.summary,
            "order": self.order,
        }


@cache
def _load_kind(kind: str) -> tuple[Starter, ...]:
    folder = _DATA_DIR / kind
    if not folder.is_dir():
        return ()
    out: list[Starter] = []
    for path in sorted(folder.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        out.append(
            Starter(
                kind=kind,
                id=path.stem,
                label=str(data.get("label") or path.stem),
                summary=str(data.get("summary") or ""),
                order=int(data.get("order") or 0),
                form=dict(data.get("form") or {}),
            )
        )
    return tuple(sorted(out, key=lambda s: (s.order, s.id)))


def list_starters(kind: str | None = None) -> list[Starter]:
    """Starters of ``kind`` (or of every kind), in display order."""
    if kind is not None and kind not in STARTER_KINDS:
        raise UnknownStarterError(f"unknown kind: {kind!r}")
    kinds = (kind,) if kind else STARTER_KINDS
    return [s for k in kinds for s in _load_kind(k)]


def get_starter(kind: str, starter_id: str | None) -> Starter:
    """The starter ``starter_id`` of ``kind``; None means the default."""
    wanted = starter_id or DEFAULT_STARTER
    for starter in list_starters(kind):
        if starter.id == wanted:
            return starter
    raise UnknownStarterError(f"no {kind} starter {wanted!r}")


def starter_form(kind: str, starter_id: str | None) -> dict:
    """A private copy of a starter's form, safe to extend."""
    return deepcopy(get_starter(kind, starter_id).form)
