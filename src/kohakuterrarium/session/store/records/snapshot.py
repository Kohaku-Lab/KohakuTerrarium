"""Store independent conversation checkpoints for diverging branch lineages."""

import math
from copy import deepcopy
from typing import Any


def _path(raw: Any) -> list[list[int]] | None:
    if not isinstance(raw, (list, tuple)):
        return None
    out = []
    for pair in raw:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            return None
        if any(type(value) is not int or value <= 0 for value in pair):
            return None
        if out and pair[0] <= out[-1][0]:
            return None
        out.append(list(pair))
    return out


def branch_path(tag: Any) -> list[list[int]] | None:
    """Decode a snapshot's complete parent lineage and current turn."""
    if not isinstance(tag, dict):
        return None
    parents = _path(tag.get("parent_branch_path") or [])
    return (
        _path([*parents, [tag.get("turn_index"), tag.get("branch_id")]])
        if parents is not None
        else None
    )


def snapshot_tag(agent: Any) -> dict:
    """Capture the branch identity of a live conversation."""
    return {
        "turn_index": getattr(agent, "_turn_index", None),
        "branch_id": getattr(agent, "_branch_id", None),
        "parent_branch_path": deepcopy(getattr(agent, "_parent_branch_path", None)),
        "context_cutoff_event_id": getattr(agent, "_context_cutoff_event_id", 0),
    }


def compact_state(manager: Any) -> dict:
    """Capture the compact counters attached to a conversation."""
    return {
        "compact_count": getattr(manager, "_compact_count", 0),
        "last_compact_time": getattr(manager, "_last_compact_time", 0.0),
    }


def load_records(store: Any, agent: str) -> list[dict]:
    """Return detached, structurally valid branch checkpoints."""
    try:
        raw = getattr(store, "state", {}).get(f"{agent}:branch_snapshots", [])
    except (KeyError, TypeError, AttributeError):
        return []
    if not isinstance(raw, list):
        return []
    records = []
    for record in raw:
        if not isinstance(record, dict) or not _path(record.get("path")):
            continue
        watermark, messages, state = (
            record.get("watermark"),
            record.get("messages"),
            record.get("compact_state"),
        )
        if (
            type(watermark) is not int
            or watermark < 0
            or not isinstance(messages, list)
        ):
            continue
        if not all(
            isinstance(msg, dict)
            and msg.get("role") in {"system", "user", "assistant", "tool"}
            and "content" in msg
            for msg in messages
        ):
            continue
        if (
            not isinstance(state, dict)
            or type(state.get("compact_count")) is not int
            or state["compact_count"] < 0
        ):
            continue
        cutoff = record.get("context_cutoff_event_id", 0)
        if type(cutoff) is not int or not 0 <= cutoff <= watermark:
            continue
        timestamp = state.get("last_compact_time")
        if (
            type(timestamp) not in (int, float)
            or not math.isfinite(timestamp)
            or timestamp < 0
        ):
            continue
        records.append(deepcopy(record))
    return records


def load_branch_snapshot(
    store: Any, agent: str, branch_view: dict[int, int]
) -> dict | None:
    """Select the deepest checkpoint on an explicitly resolved lineage."""
    target = _path(sorted(branch_view.items()))
    if not target:
        return None
    candidates = [
        record
        for record in load_records(store, agent)
        if target[: len(record["path"])] == record["path"]
    ]
    return max(
        candidates,
        key=lambda record: (len(record["path"]), record["watermark"]),
        default=None,
    )


def save_branch_snapshot(
    store: Any,
    agent: str,
    messages: list[dict],
    *,
    branch: dict | None,
    watermark: int,
    compact_state: dict | None = None,
) -> None:
    """Write one coherent checkpoint and maintain the legacy latest mirrors."""
    path = branch_path(branch)
    if path and type(watermark) is int and watermark >= 0:
        records = [
            record
            for record in load_records(store, agent)
            if record["path"][: min(len(path), len(record["path"]))]
            != path[: min(len(path), len(record["path"]))]
        ]
        state = compact_state or {"compact_count": 0, "last_compact_time": 0.0}
        records.append(
            {
                "path": path,
                "messages": deepcopy(messages),
                "watermark": watermark,
                "compact_state": deepcopy(state),
                "context_cutoff_event_id": branch.get("context_cutoff_event_id", 0),
            }
        )
        store.state[f"{agent}:branch_snapshots"] = records
        store.state[f"{agent}:snapshot_branch"] = deepcopy(branch)
        store.state[f"{agent}:compact_count"] = state["compact_count"]
        store.state[f"{agent}:last_compact_time"] = state["last_compact_time"]
    else:
        store.state[f"{agent}:branch_snapshots"] = []
        store.state.pop(f"{agent}:snapshot_branch", None)
    store.conversation[agent] = deepcopy(messages)
    store.state[f"{agent}:snapshot_event_id"] = watermark


def save_agent_snapshot(
    store: Any, namespace: str, agent: Any, messages: list[dict]
) -> None:
    """Persist a quiescent history mutation without discarding sibling checkpoints."""
    saver = getattr(store, "save_branch_snapshot", None)
    if not callable(saver):
        store.save_conversation(namespace, messages)
        return
    watermark = store.max_event_id(namespace)
    if not any(message["role"] != "system" for message in messages):
        agent._context_cutoff_event_id = watermark
    state = (
        compact_state(getattr(agent, "compact_manager", None))
        if any(message["role"] != "system" for message in messages)
        else {"compact_count": 0, "last_compact_time": 0.0}
    )
    saver(
        namespace,
        messages,
        branch=snapshot_tag(agent),
        watermark=watermark,
        compact_state=state,
    )
