"""Continue a user turn on an explicitly selected persisted branch.

Idle text continuation reloads the in-memory conversation from the event
log when the requested view differs from the live path, then enqueues the
new user input. Branch selection is persisted so resume restores it.
"""

from typing import Any

from kohakuterrarium.core.agent.history.message_history import (
    apply_prepared_branch_view,
)
from kohakuterrarium.errors import AgentNotRunningError, ConflictError
from kohakuterrarium.session.history.replay import (
    InvalidBranchViewError,
    resolve_branch_view_strict,
)

BRANCH_VIEW_STATE_SUFFIX = "branch_view"


def branch_view_state_key(namespace: str) -> str:
    """Return the session-state key that stores the selected branch view."""
    return f"{namespace}:{BRANCH_VIEW_STATE_SUFFIX}"


def normalize_branch_view(value: Any) -> dict[int, int] | None:
    """Detach positive turn and branch coordinates supplied by a transport."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("branch_view must be a turn-to-branch mapping")
    result: dict[int, int] = {}
    for key, branch in value.items():
        if isinstance(key, str) and key.isascii() and key.isdecimal():
            key = int(key)
        if type(key) is not int or key <= 0 or type(branch) is not int or branch <= 0:
            raise ValueError("branch_view requires positive integer coordinates")
        if key in result:
            raise ValueError("branch_view contains duplicate turn coordinates")
        result[key] = branch
    return result


def encode_branch_view(view: dict[int, int] | None) -> dict[str, int] | None:
    """JSON-safe encoding of a selected branch view."""
    if not view:
        return None
    return {str(turn): branch for turn, branch in sorted(view.items())}


def load_persisted_branch_view(store: Any, namespace: str) -> dict[int, int] | None:
    """Read a previously persisted branch view, or None when absent."""
    if store is None:
        return None
    try:
        raw = store.state.get(branch_view_state_key(namespace))
    except Exception:
        return None
    if not raw:
        return None
    try:
        return normalize_branch_view(raw)
    except ValueError:
        return None


def persist_branch_view(
    store: Any, namespace: str, view: dict[int, int] | None
) -> None:
    """Write the selected view into session state, or drop it when empty."""
    if store is None:
        return
    key = branch_view_state_key(namespace)
    encoded = encode_branch_view(view)
    if encoded is None:
        store.state.pop(key, None)
    else:
        store.state[key] = encoded


def live_branch_path(agent: Any) -> dict[int, int]:
    """Return the agent's current parent path plus the live turn/branch."""
    current: dict[int, int] = {}
    for pair in getattr(agent, "_parent_branch_path", ()) or ():
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            continue
        turn, branch = pair
        if type(turn) is int and turn > 0 and type(branch) is int and branch > 0:
            current[turn] = branch
    turn = getattr(agent, "_turn_index", 0)
    branch = getattr(agent, "_branch_id", 0)
    if type(turn) is int and turn > 0 and type(branch) is int and branch > 0:
        current[turn] = branch
    return current


def selected_branch_view(agent: Any) -> dict[int, int]:
    """Return the persisted or live branch view for history / attach payloads."""
    store = getattr(agent, "session_store", None)
    namespace = getattr(getattr(agent, "config", None), "name", "") or ""
    persisted = load_persisted_branch_view(store, namespace)
    if persisted:
        return persisted
    return live_branch_path(agent)


def _history_mutation_in_flight(agent: Any) -> bool:
    mutation_lock = getattr(agent, "_message_mutation_lock", None)
    return mutation_lock is not None and mutation_lock.locked()


def _running_jobs(agent: Any) -> list[Any]:
    jobs: list[Any] = []
    executor = getattr(agent, "executor", None)
    if executor is not None and hasattr(executor, "get_running_jobs"):
        jobs.extend(executor.get_running_jobs() or [])
    manager = getattr(agent, "subagent_manager", None)
    if manager is not None and hasattr(manager, "get_running_jobs"):
        jobs.extend(manager.get_running_jobs() or [])
    return jobs


def compact_in_flight(agent: Any) -> bool:
    """Whether a compaction round currently owns the conversation."""
    manager = getattr(agent, "compact_manager", None)
    return manager is not None and bool(getattr(manager, "is_compacting", False))


def require_no_active_compact(agent: Any) -> None:
    """Reject history mutation while a compaction round can still splice it."""
    if compact_in_flight(agent):
        raise ConflictError(
            "Cannot change conversation history while compaction is running"
        )


def require_no_running_jobs(agent: Any) -> None:
    """Reject history changes until running jobs and promoted handoffs settle."""
    promoted = any(
        handle.promoted for handle in getattr(agent, "_active_handles", {}).values()
    )
    if _running_jobs(agent) or promoted:
        raise ConflictError(
            "Cannot continue a selected branch while a tool or background job is running"
        )


def _require_history_switch_available(agent: Any) -> None:
    if _history_mutation_in_flight(agent):
        raise ConflictError(
            "Cannot continue a selected branch while history is mutating"
        )
    agent._ensure_history_mutation_idle()
    if getattr(agent, "_running", True) is False:
        raise AgentNotRunningError(
            f"Agent {getattr(getattr(agent, 'config', None), 'name', '')!r} is not running — "
            "start() it before injecting input"
        )
    if getattr(agent, "_paused", False):
        raise ConflictError("Cannot continue a selected branch while paused")
    require_no_running_jobs(agent)


async def continue_on_branch(agent: Any, event: Any, view: dict[int, int]) -> bool:
    """Select a persisted subtree when needed, then enqueue the user event."""
    current = live_branch_path(agent)
    store = getattr(agent, "session_store", None)
    namespace = getattr(getattr(agent, "config", None), "name", "") or ""
    if store is None:
        same = all(current.get(turn) == branch for turn, branch in view.items())
        if not same:
            raise ConflictError(
                "Cannot continue a selected branch without a session store"
            )
        if _history_mutation_in_flight(agent):
            _require_history_switch_available(agent)
        return await agent._process_event(event)
    if getattr(agent, "_running", True) is False or _history_mutation_in_flight(agent):
        _require_history_switch_available(agent)
    if (
        current
        and view == current
        and load_persisted_branch_view(store, namespace) == current
    ):
        persist_branch_view(store, namespace, current)
        return await agent._process_event(event)
    events = list(store.get_events(agent.config.name))
    try:
        resolved = resolve_branch_view_strict(events, view)
    except InvalidBranchViewError as exc:
        raise ValueError(str(exc)) from exc
    same = resolved == current
    if not same:
        _require_history_switch_available(agent)
        prepared = agent._reload_conversation_under_branch_view(
            resolved, events=events, apply=False
        )
        persist_branch_view(store, namespace, resolved)
        apply_prepared_branch_view(agent, prepared)
        return await agent._process_event(event)
    persist_branch_view(store, namespace, resolved)
    return await agent._process_event(event)
