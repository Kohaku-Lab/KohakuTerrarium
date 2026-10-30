"""Identity-aware accumulation of OpenAI-compatible streamed tool calls."""

import json
from copy import deepcopy
from typing import Any

from kohakuterrarium.llm.openai.helpers import delta_field, tool_call_extra


class ToolCallAccumulator:
    """Collect calls by ID, with index and positional aliases for continuations."""

    def __init__(self) -> None:
        self.calls: dict[int, dict[str, Any]] = {}
        self._ids: dict[str, int] = {}
        self._indexes: dict[int, set[int]] = {}
        self._positions: dict[int, int] = {}
        self._last: int | None = None

    def append(self, delta: Any, position: int, batch_size: int) -> int:
        """Append a delta and return its stable, response-local call slot."""
        call_id = delta_field(delta, "id") or ""
        index = delta_field(delta, "index")
        slot = self._ids.get(call_id) if call_id else None
        if slot is None:
            candidate = self._candidate(index, position, batch_size, call_id)
            if candidate is not None:
                existing_id = self.calls[candidate]["id"]
                if not call_id or not existing_id:
                    slot = candidate
        if slot is None:
            slot = len(self.calls)
            self.calls[slot] = {"id": "", "name": "", "arguments": ""}
        call = self.calls[slot]
        if call_id:
            call["id"] = call_id
            self._ids[call_id] = slot
        if index is not None:
            self._indexes.setdefault(index, set()).add(slot)
        if batch_size > 1:
            self._positions[position] = slot
        self._last = slot
        extra = tool_call_extra(delta)
        if extra:
            _merge_extra(call.setdefault("extra_content", {}), extra)
        function = delta_field(delta, "function")
        if function is not None:
            name = delta_field(function, "name")
            if name:
                call["name"] = name
            arguments = delta_field(function, "arguments")
            if arguments:
                call["arguments"] += arguments
        return slot

    def _candidate(
        self, index: int | None, position: int, batch_size: int, call_id: str
    ) -> int | None:
        slots = (
            self._indexes.get(index, set()) if index is not None else set(self.calls)
        )
        if index is not None and len(slots) <= 1:
            return next(iter(slots), None)
        if batch_size > 1:
            candidate = self._positions.get(position)
            return candidate if candidate in slots else None
        if call_id:
            return None
        unfinished = [slot for slot in slots if not _complete(self.calls[slot])]
        if len(unfinished) > 1:
            raise ValueError(
                "Ambiguous tool-call delta: no unique ID, index or position"
            )
        if unfinished:
            return unfinished[0]
        return self._last if self._last in slots else None


def _complete(call: dict[str, Any]) -> bool:
    try:
        json.loads(call["arguments"])
    except json.JSONDecodeError:
        return False
    return True


def _merge_extra(target: dict[str, Any], extra: dict[str, Any]) -> None:
    for key, value in extra.items():
        if isinstance(target.get(key), dict) and isinstance(value, dict):
            _merge_extra(target[key], value)
        else:
            target[key] = deepcopy(value)
