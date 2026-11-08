"""Publish clean, empty-tail continuation points for ordinary text sessions."""

from copy import deepcopy
from typing import Any

from kohakuterrarium.session.store.records.snapshot import branch_path

POINT_KEY = "resume:clean_checkpoint"
VERSION = 1


def open_point(store: Any) -> None:
    """Consume a previous clean-close point before this writer can modify state."""
    store._resume_point = None
    store._resume_candidate = None
    store._resume_epoch = store._branch_maxima.invalidation_epoch
    if store._readonly:
        return
    point = store.state.get(POINT_KEY)
    if POINT_KEY in store.state:
        del store.state[POINT_KEY]
    if (
        isinstance(point, dict)
        and type(point.get("version")) is int
        and point["version"] == VERSION
    ):
        store._resume_point = point


def text_messages(messages: Any) -> bool:
    """Accept complete text messages with settled, uniquely paired tool calls."""
    if not isinstance(messages, list) or not messages:
        return False
    pending: set[str] = set()
    seen: set[str] = set()
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in {
            "system",
            "user",
            "assistant",
            "tool",
        }:
            return False
        content = message.get("content")
        if content is not None and not isinstance(content, str):
            return False
        metadata = message.get("metadata") or {}
        if (
            not isinstance(metadata, dict)
            or metadata.get("tool_result_phase") == "pending"
        ):
            return False
        if metadata.get("compact_summary") or (
            isinstance(content, str)
            and content.startswith("[Previous context summary (compact round ")
        ):
            return False
        if any(
            key in message
            for key in (
                "reasoning_content",
                "reasoning_details",
                "_kt_anthropic_content",
            )
        ):
            return False
        calls = message.get("tool_calls") or []
        if not isinstance(calls, list):
            return False
        if calls and message["role"] != "assistant":
            return False
        for call in calls:
            if not isinstance(call, dict) or not isinstance(call.get("id"), str):
                return False
            identity = call["id"]
            if not identity or identity in seen:
                return False
            seen.add(identity)
            pending.add(identity)
        if message["role"] == "tool":
            identity = message.get("tool_call_id")
            if not isinstance(identity, str) or identity not in pending:
                return False
            pending.remove(identity)
    return not pending


def stage_point(
    store: Any, namespace: str, messages: list, branch: dict, compact: dict
) -> None:
    """Stage the metadata for a completed, uncompressed, latest text turn."""
    store._resume_candidate = None
    if compact.get("compact_count") != 0 or branch.get("context_cutoff_event_id", 0):
        return
    path = branch_path(branch)
    if not path or not text_messages(messages):
        return
    upper = store._branch_maxima.upper_turn(namespace)
    if upper is None or upper != path[-1][0]:
        return
    store._resume_epoch = store._branch_maxima.invalidation_epoch
    store._resume_candidate = {
        "version": VERSION,
        "namespace": namespace,
        "path": path,
        "event_seq": dict(store._event_seq),
        "watermark": store.global_max_event_id,
        "closed_event_id": store.global_max_event_id,
        "max_turn": upper,
    }


def appended_to_point(store: Any, namespace: str, event: dict) -> None:
    """Retain a candidate across display-only hook timing, not semantic appends."""
    store._resume_point = None
    point = getattr(store, "_resume_candidate", None)
    if point is None:
        return
    turn = event.get("turn_index")
    if (
        point["namespace"] != namespace
        or event.get("type") != "plugin_hook_timing"
        or type(turn) is not int
        or turn > point["max_turn"]
    ):
        store._resume_candidate = None
        return
    point["event_seq"] = dict(store._event_seq)
    point["closed_event_id"] = store.global_max_event_id


def publish_point(store: Any) -> None:
    """Publish a staged point after a successful clean counter commit."""
    point = getattr(store, "_resume_candidate", None)
    if (
        not isinstance(point, dict)
        or point["event_seq"] != store._event_seq
        or store._resume_epoch != store._branch_maxima.invalidation_epoch
    ):
        return
    namespace = point["namespace"]
    if set(store._event_seq) != {namespace}:
        return
    if store.meta.get("config_type") != "agent" or store.meta.get("agents") != [
        namespace
    ]:
        return
    if store.state.get(f"{namespace}:open_text") or store.load_triggers(namespace):
        return
    if store.state.get(f"{namespace}:snapshot_event_id") != point["watermark"]:
        return
    store.state[POINT_KEY] = deepcopy(point)
