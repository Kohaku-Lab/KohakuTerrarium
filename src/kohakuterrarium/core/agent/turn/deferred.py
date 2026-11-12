"""Persist and recover branch-scoped deferred job feedback."""

from typing import Any

from kohakuterrarium.core.events import create_tool_complete_event
from kohakuterrarium.llm.message import content_parts_to_dicts


def deferred_key(namespace: str) -> str:
    """Return the session marker for deferred completions."""
    return f"{namespace}:has_deferred_results"


def record_deferred(
    store: Any,
    namespace: str,
    event: Any,
    origin: dict[int, int],
    text_mode: bool = True,
) -> None:
    """Persist one deferred completion exactly once under its calling branch."""
    pairs = sorted(origin.items())
    if not pairs:
        return
    dedupe_key = f"{namespace}:deferred_job_ids"
    try:
        state_jobs = store.state.get(dedupe_key, [])
    except (AttributeError, TypeError):
        state_jobs = []
    recorded_jobs = set(state_jobs) if isinstance(state_jobs, list) else set()
    if event.job_id in recorded_jobs:
        return
    turn, branch = pairs[-1]
    content = (
        content_parts_to_dicts(event.content)
        if isinstance(event.content, list)
        else event.content
    )
    context = event.context or {}
    recorded_jobs.add(event.job_id)
    store.state[dedupe_key] = sorted(recorded_jobs)
    store.state[deferred_key(namespace)] = True
    store.append_event(
        namespace,
        "deferred_job_result",
        {
            "job_id": event.job_id,
            "text_mode": text_mode,
            "tool_call_id": context.get("tool_call_id"),
            "content": content,
            "context": {
                key: context[key]
                for key in (
                    "exit_code",
                    "error",
                    "interrupted",
                    "cancelled",
                    "final_state",
                )
                if key in context
            },
        },
        turn_index=turn,
        branch_id=branch,
        parent_branch_path=pairs[:-1],
    )
    store._flush_events_cache()


def restore_deferred(events: list[dict], branch_view: dict[int, int], cutoff: int = 0):
    """Restore undelivered deferred completions from the selected history."""
    delivered = set()
    for event in events:
        if event.get("type") != "deferred_job_feedback":
            continue
        lineage = dict(event.get("parent_branch_path") or [])
        lineage[event.get("turn_index")] = event.get("branch_id")
        if all(branch_view.get(turn) == branch for turn, branch in lineage.items()):
            delivered.update(event.get("job_ids") or [])
    restored = []
    seen = set()
    for event in events:
        if event.get("type") != "deferred_job_result":
            continue
        job_id = event.get("job_id")
        event_id = event.get("event_id", 0)
        if not job_id or job_id in delivered or job_id in seen or event_id <= cutoff:
            continue
        origin = dict(event.get("parent_branch_path") or [])
        turn, branch = event.get("turn_index"), event.get("branch_id")
        if type(turn) is not int or type(branch) is not int:
            continue
        origin[turn] = branch
        if not all(branch_view.get(t) == b for t, b in origin.items()):
            continue
        seen.add(job_id)
        restored.append(
            (
                create_tool_complete_event(
                    job_id, event.get("content") or "", **(event.get("context") or {})
                ),
                origin,
            )
        )
    return restored
