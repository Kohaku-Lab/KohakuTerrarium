"""Keyed redraws of an inline ``<ui>`` block: nodes are updated in place, not rebuilt.

``ui_map`` draws a block by calling ``slot.add(spec)`` the way it would call ``Node.add``.
A :class:`Slot` keeps the nodes it added last time, in order. On the next pass a node at the
same position with the same kind is updated with the new props (so a focused control keeps
its focus and caret while the numbers around it change); from the first child that differs,
the rest of that container is drawn anew. Kinds fed by ``append`` (charts) are always new.
"""

import dataclasses
from typing import Any

from kohakutui.ui import Node

# kinds whose content arrives through append: an update could not reset it
FRESH_KINDS = frozenset({"chart"})


@dataclasses.dataclass
class _Drawn:
    node: Node
    kind: str
    children: list["_Drawn"] = dataclasses.field(default_factory=list)


def _props(spec: Any) -> dict[str, Any]:
    return {
        f.name: getattr(spec, f.name)
        for f in dataclasses.fields(spec)
        if f.name != "children"
    }


class Slot:
    """A node to draw into, with what was drawn into it on the previous pass."""

    def __init__(self, node: Node, ui: Any, previous: list[_Drawn] | None = None):
        self.node = node
        self.ui = ui
        self._previous = previous or []
        self._drawn: list[_Drawn] = []
        self._slots: list["Slot"] = []
        self._diverged = False

    def add(self, spec: Any) -> "Slot":
        """Draws ``spec`` as the next child: the previous node when it still fits."""
        index = len(self._drawn)
        old = None if self._diverged else self._take(index)
        if old is not None and old.kind == spec.kind and spec.kind not in FRESH_KINDS:
            old.node.update(**_props(spec))
            drawn = _Drawn(old.node, spec.kind)
            slot = Slot(old.node, self.ui, old.children)
        else:
            if old is not None:
                self._diverge(index)
            drawn = _Drawn(self.node.add(spec), spec.kind)
            slot = Slot(drawn.node, self.ui)
        drawn.children = slot._drawn
        self._drawn.append(drawn)
        self._slots.append(slot)
        return slot

    def append(self, data: Any) -> None:
        self.node.append(data)

    def finish(self) -> None:
        """Removes what the previous pass drew beyond this one, here and in every child."""
        for slot in self._slots:
            slot.finish()
        self._diverge(len(self._drawn))

    def _take(self, index: int) -> _Drawn | None:
        return self._previous[index] if index < len(self._previous) else None

    def _diverge(self, index: int) -> None:
        # everything from here on is drawn anew, after the nodes kept so far
        for old in self._previous[index:]:
            old.node.remove()
        self._previous = self._previous[:index]
        self._diverged = True

    @property
    def drawn(self) -> list[_Drawn]:
        return self._drawn


__all__ = ["FRESH_KINDS", "Slot"]
