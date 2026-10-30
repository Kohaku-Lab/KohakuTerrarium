"""Persist completed assistant rounds on the session affinity thread."""

from copy import deepcopy
from typing import Any


def append_assistant_message(store, namespace, message, anchor, stamps):
    """Append a detached canonical message linked to its durable round start."""
    _, event_id = anchor.result()
    return store.append_event(
        namespace,
        "assistant_message",
        {"message": message, "round_start_event_id": event_id},
        turn_index=stamps[0],
        branch_id=stamps[1],
        parent_branch_path=stamps[2],
    )


class AssistantOutputMixin:
    """Queue assistant round boundaries independently of activity capture."""

    def _begin_assistant_round(self) -> None:
        self._recover_open_text()
        self._flush_text_segment()
        self._assistant_recovery_generation = self._open_text.generation
        self._assistant_round_stamps = (
            *self._current_turn_branch(),
            self._current_parent_path(),
        )
        self._assistant_round_anchor = self._append_event_at(
            "assistant_round_start", {}, *self._assistant_round_stamps
        )

    def _finish_assistant_round(self, message: dict[str, Any]) -> None:
        self._recover_open_text()
        self._flush_text_segment()
        anchor = getattr(self, "_assistant_round_anchor", None)
        self._assistant_round_anchor = None
        if anchor is None:
            return
        recorded = self._submit_store_write(
            append_assistant_message,
            self._store,
            self._event_key_prefix,
            deepcopy(message),
            anchor,
            self._assistant_round_stamps,
        )
        if self._open_text.generation > self._assistant_recovery_generation:
            self._open_text.retire(after=recorded)
