"""Wave G — read-side token-usage helpers for :class:`SessionStore`.

Implements the Q2-locked behaviour from
``plans/session-system/implementation-plan.md`` §2.4 / §7: no aggregation
by default, opt-in roll-up across sub-agents / attached agents, plus a
flat enumeration of every controller loop in the session tree for
consumers that want to display or sum tokens themselves.

This module is the implementation heart of Wave G. Both
``SessionStore.token_usage`` and ``SessionStore.token_usage_all_loops``
are thin facades over the helpers below. Nothing here mutates the store
— every function is a pure read.
"""

from typing import TYPE_CHECKING, Any

from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.session.store import SessionStore

logger = get_logger(__name__)


# ─── Key helpers ──────────────────────────────────────────────────────


def _decode_key(key_bytes: Any) -> str:
    """Decode a KVault key (bytes or str) to a native string."""
    if isinstance(key_bytes, bytes):
        return key_bytes.decode("utf-8", errors="replace")
    return str(key_bytes)


# ─── Counter shape ────────────────────────────────────────────────────


def _empty_usage() -> dict[str, int]:
    """Zero-initialised token counter dict, the canonical return shape."""
    return {
        "total_tokens": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cached_tokens": 0,
    }


def _state_usage_to_shape(raw: dict[str, Any]) -> dict[str, int]:
    """Normalise a ``state[<agent>:token_usage]`` row to the public shape.

    ``SessionOutput`` writes ``total_input_tokens`` / ``total_output_tokens``
    / ``total_cached_tokens`` (and a transient ``last_prompt_tokens``). We
    surface them under the concise names the read-side API uses.
    """
    prompt = int(raw.get("total_input_tokens", 0) or 0)
    completion = int(raw.get("total_output_tokens", 0) or 0)
    cached = int(raw.get("total_cached_tokens", 0) or 0)
    # Total is the sum of prompt + completion — providers already fold
    # cached into prompt (it's a discount signal, not an extra bucket).
    total = prompt + completion
    return {
        "total_tokens": total,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "cached_tokens": cached,
    }


def _own_usage_for_namespace(store: "SessionStore", namespace: str) -> dict[str, int]:
    """Load the controller-loop counters for a single event-namespace prefix.

    Returns the zero shape when the namespace has no persisted state yet
    (e.g., a sub-agent that emitted a ``subagent_result`` event but never
    had its own ``token_usage`` emitted through ``SessionOutput``).
    """
    try:
        raw = store.state.get(f"{namespace}:token_usage")
    except (KeyError, TypeError):
        raw = None
    if isinstance(raw, dict):
        return _state_usage_to_shape(raw)
    return _empty_usage()


# ─── Sub-agent enumeration ────────────────────────────────────────────


def _iter_subagent_runs(store: "SessionStore", parent: str) -> list[tuple[str, int]]:
    """Return every ``(name, run)`` for sub-agents spawned by ``parent``.

    Uses the authoritative ``subagents`` KVault table so ordering is
    stable across restarts. Sub-agents whose metadata is missing are
    silently ignored — they'll still surface through the event-walk
    code path below when tokens are needed.
    """
    runs: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    prefix = f"{parent}:"
    for key_bytes in store.subagents.keys():
        key = _decode_key(key_bytes)
        if not key.startswith(prefix) or not key.endswith(":meta"):
            continue
        body = key[: -len(":meta")]
        parts = body.rsplit(":", 2)
        if len(parts) != 3:
            continue
        _p, name, run_str = parts
        try:
            run = int(run_str)
        except ValueError:
            continue
        entry = (name, run)
        if entry in seen:
            continue
        seen.add(entry)
        runs.append(entry)
    runs.sort(key=lambda pair: (pair[0], pair[1]))
    return runs


def _subagent_tokens_from_events(
    events: list[dict],
) -> dict[str, list[dict[str, int]]]:
    """Group per-sub-agent-name ``subagent_result`` token fields in order.

    Each returned list is ordered by event appearance, which matches
    run index for well-behaved ``SubAgentManager`` usage: ``next_subagent_run``
    hands out monotonically increasing indices, and events are appended
    in completion order. See note in module docstring.
    """
    per_name: dict[str, list[dict[str, int]]] = {}
    for evt in events:
        if evt.get("type") != "subagent_result":
            continue
        name = evt.get("name")
        if not isinstance(name, str) or not name:
            continue
        per_name.setdefault(name, []).append(
            {
                "total_tokens": int(evt.get("total_tokens", 0) or 0),
                "prompt_tokens": int(evt.get("prompt_tokens", 0) or 0),
                "completion_tokens": int(evt.get("completion_tokens", 0) or 0),
                "cached_tokens": int(evt.get("cached_tokens", 0) or 0),
            }
        )
    return per_name


def _subagent_usage_map(
    store: "SessionStore", parent: str
) -> dict[str, dict[str, int]]:
    """Return ``{<parent>:subagent:<name>:<run>: usage}`` for one parent.

    Combines the authoritative run list from the ``subagents`` table
    with the per-event token fields captured in parent's event stream.
    Events past the recorded run count (or runs with no matching event)
    fall back to the zero shape. This keeps enumeration stable even
    when metadata and events are slightly out of sync on disk.
    """
    results: dict[str, dict[str, int]] = {}
    runs = _iter_subagent_runs(store, parent)
    events = store.get_events(parent)
    per_name = _subagent_tokens_from_events(events)
    # Track consumed positions per name so unrecorded extra events can
    # still be surfaced after the known runs.
    consumed: dict[str, int] = {}
    for name, run in runs:
        path = f"{parent}:subagent:{name}:{run}"
        tokens_list = per_name.get(name, [])
        idx = consumed.get(name, 0)
        if idx < len(tokens_list):
            results[path] = tokens_list[idx]
            consumed[name] = idx + 1
        else:
            results[path] = _empty_usage()
    # Surface any events that had no matching meta row (defensive — the
    # plugin/attach flow may emit results without going through
    # ``SubAgentManager``).
    for name, token_rows in per_name.items():
        start = consumed.get(name, 0)
        for extra_idx in range(start, len(token_rows)):
            path = f"{parent}:subagent:{name}:{extra_idx}"
            if path in results:
                continue
            results[path] = token_rows[extra_idx]
    return results


# ─── Attached-agent enumeration (Wave F) ──────────────────────────────


def _attached_usage_map(store: "SessionStore") -> dict[str, dict[str, int]]:
    """Return ``{<host>:attached:<role>:<seq>: usage}`` for every attached agent.

    Delegates to :meth:`SessionStore.discover_attached_agents` so
    hot-plugged / plugin-spawned creatures (whose namespaces never land
    in ``meta["agents"]``) are picked up. Flushes the events cache
    first — Wave F events land via a flush-interval-buffered KVault and
    may be invisible to discovery otherwise.
    """
    results: dict[str, dict[str, int]] = {}
    try:
        store.events.flush_cache()
    except Exception as e:  # pragma: no cover — defensive
        logger.debug("events.flush_cache failed", error=str(e), exc_info=True)
    try:
        attached = store.discover_attached_agents()
    except Exception as e:  # pragma: no cover — defensive
        logger.debug("discover_attached_agents failed", error=str(e), exc_info=True)
        attached = []
    for entry in attached:
        namespace = entry.get("namespace")
        if not isinstance(namespace, str) or not namespace:
            continue
        results[namespace] = _own_usage_for_namespace(store, namespace)
    return results


# ─── Per-turn breakdown ───────────────────────────────────────────────


def _by_turn_from_rollup(store: "SessionStore", agent: str) -> list[dict[str, int]]:
    """Return per-turn counters from the ``turn_rollup`` table.

    Empty list when Wave B's rollup emitter hasn't fired for this
    agent yet — the caller then falls back to event-walking.
    """
    try:
        rows = store.list_turn_rollups(agent)
    except Exception as e:  # pragma: no cover — defensive
        logger.debug("list_turn_rollups failed", error=str(e), exc_info=True)
        return []
    out: list[dict[str, int]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        out.append(
            {
                "turn_index": int(row.get("turn_index", 0) or 0),
                "prompt": int(row.get("prompt_tokens", 0) or 0),
                "completion": int(row.get("completion_tokens", 0) or 0),
                "cached": int(row.get("cached_tokens", 0) or 0),
            }
        )
    return out


def _by_turn_from_events(store: "SessionStore", agent: str) -> list[dict[str, int]]:
    """Group ``token_usage`` events by ``turn_index`` (fallback path).

    Used when the rollup table has nothing for ``agent``; Wave B
    documented this as acceptable because the emitter is not yet wired
    on every code path. Events without a ``turn_index`` land under turn
    ``0`` — consumers should treat ``0`` as "pre-conversation / unknown"
    per Q6 in the plan.
    """
    buckets: dict[int, dict[str, int]] = {}
    for evt in store.get_events(agent):
        if evt.get("type") != "token_usage":
            continue
        turn = int(evt.get("turn_index", 0) or 0)
        slot = buckets.setdefault(
            turn,
            {"turn_index": turn, "prompt": 0, "completion": 0, "cached": 0},
        )
        slot["prompt"] += int(evt.get("prompt_tokens", 0) or 0)
        slot["completion"] += int(evt.get("completion_tokens", 0) or 0)
        slot["cached"] += int(evt.get("cached_tokens", 0) or 0)
    return [buckets[t] for t in sorted(buckets.keys())]


# ─── Public helpers (imported by SessionStore) ────────────────────────


def token_usage(
    store: "SessionStore",
    agent: str | None,
    *,
    include_subagents: bool = False,
    include_attached: bool = False,
    by_turn: bool = False,
) -> dict[str, Any]:
    """Return token counters for ``agent`` — Wave G primary read API.

    See :meth:`SessionStore.token_usage` for the public docstring. A
    missing ``agent`` raises :class:`ValueError` (Q2: "no silent main
    pick"). Sub-dicts are only present when the matching flag is set,
    and are always dicts (never ``None``) when the flag is on — callers
    can rely on ``result["attached"]`` being iterable regardless of
    whether any attached agents exist.
    """
    if agent is None:
        raise ValueError(
            "token_usage(agent=...) requires an explicit agent name; "
            "no silent 'main' pick. Pass the host agent's name, or use "
            "token_usage_all_loops() for a tree-wide enumeration."
        )

    result: dict[str, Any] = {"agent": agent}
    result.update(_own_usage_for_namespace(store, agent))

    if include_subagents:
        result["subagents"] = _subagent_usage_map(store, agent)
    if include_attached:
        result["attached"] = _attached_usage_map(store)
    if by_turn:
        rollup_rows = _by_turn_from_rollup(store, agent)
        if rollup_rows:
            result["by_turn"] = rollup_rows
        else:
            result["by_turn"] = _by_turn_from_events(store, agent)
    return result


def token_usage_all_loops(
    store: "SessionStore",
) -> list[tuple[str, dict[str, int]]]:
    """Flat enumeration of every controller loop (main + sub + attached).

    See :meth:`SessionStore.token_usage_all_loops` for the public
    docstring. Order:

    1. Main agents (``meta["agents"]`` union with
       ``discover_agents_from_events``).
    2. Sub-agents per main agent (sorted by name then run).
    3. Attached agents (discover order — Wave F guarantees first-seen).
    """
    result: list[tuple[str, dict[str, int]]] = []

    try:
        meta = store.load_meta()
    except Exception as e:  # pragma: no cover — defensive
        logger.debug("load_meta failed in all_loops", error=str(e), exc_info=True)
        meta = {}

    mains: list[str] = []
    seen_mains: set[str] = set()
    raw_agents = meta.get("agents") if isinstance(meta, dict) else None
    if isinstance(raw_agents, list):
        for name in raw_agents:
            if isinstance(name, str) and name and name not in seen_mains:
                mains.append(name)
                seen_mains.add(name)

    # ``discover_agents_from_events`` already skips attached namespaces.
    try:
        extra_mains = store.discover_agents_from_events()
    except Exception as e:  # pragma: no cover — defensive
        logger.debug(
            "discover_agents_from_events failed in all_loops",
            error=str(e),
            exc_info=True,
        )
        extra_mains = []
    for name in extra_mains:
        if name not in seen_mains:
            mains.append(name)
            seen_mains.add(name)

    for name in mains:
        result.append((name, _own_usage_for_namespace(store, name)))
        subagents = _subagent_usage_map(store, name)
        for path in sorted(subagents.keys()):
            result.append((path, subagents[path]))

    attached = _attached_usage_map(store)
    # Preserve discovery order (which matches ``discover_attached_agents``).
    try:
        attached_entries = store.discover_attached_agents()
    except Exception as e:  # pragma: no cover — defensive
        logger.debug(
            "discover_attached_agents failed in all_loops",
            error=str(e),
            exc_info=True,
        )
        attached_entries = []
    for entry in attached_entries:
        ns = entry.get("namespace")
        if isinstance(ns, str) and ns in attached:
            result.append((ns, attached[ns]))

    return result
