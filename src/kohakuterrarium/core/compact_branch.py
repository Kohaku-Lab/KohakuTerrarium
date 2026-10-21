"""Branch-aware compaction helpers (kept small for the file-size guard).

These pure helpers turn the agent's branch state into the path a compaction
covers, and compute the event-id range from the event log.
"""

import asyncio
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from kohakuterrarium.core.compact_capture import (
    compute_captured_range,
    compute_replaced_range as compute_replaced_range,
    matching_compact_complete,
    uses_canonical_rounds,
)
from kohakuterrarium.core.compact_splice import (
    is_real_user_message,
    leading_system_count,
)
from kohakuterrarium.session.snapshot import snapshot_tag
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def _coerce_pairs(raw: Any) -> set[tuple[int, int]] | None:
    """Parse a persisted ``[[turn, branch], ...]`` list into a pair set."""
    if not isinstance(raw, (list, tuple)):
        return None
    pairs: set[tuple[int, int]] = set()
    for item in raw:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            try:
                pairs.add((int(item[0]), int(item[1])))
            except (TypeError, ValueError):
                continue
    return pairs or None


def _sorted_pairs(pairs: Sequence[tuple[int, int]] | set[tuple[int, int]]) -> list:
    return sorted([t, b] for (t, b) in pairs)


def compact_path_from_event(evt: Mapping[str, Any]) -> set[tuple[int, int]] | None:
    """Return the recorded branch lineage, or None for pathless legacy data."""
    return _coerce_pairs(evt.get("compact_path"))


def compact_source_path_from_event(
    evt: Mapping[str, Any],
) -> set[tuple[int, int]] | None:
    """Return the recorded input lineage, or None when absent."""
    raw = evt.get("compact_source_path")
    if not isinstance(raw, (list, tuple)) or not raw:
        return None
    pairs = set()
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            return None
        if any(type(value) is not int or value <= 0 for value in item):
            return None
        pairs.add(tuple(item))
    return pairs


@dataclass(frozen=True)
class CompactRule:
    """A summary's replacement coverage and historical input dependencies."""

    replaced_from: int
    replaced_to: int
    path: set[tuple[int, int]] | None
    summary: str
    source_path: set[tuple[int, int]] | None
    source_recorded: bool = False
    result_event_ids: frozenset[int] = frozenset()

    def covers(self, event_id: object, turn_index: object, branch_id: object) -> bool:
        """Whether this replacement swallows one event of its own branch."""
        if not isinstance(event_id, int):
            return False
        if event_id in self.result_event_ids:
            return True
        if not self.replaced_from <= event_id <= self.replaced_to:
            return False
        if self.path is None:
            return True
        if not isinstance(turn_index, int) or not isinstance(branch_id, int):
            return False
        return (turn_index, branch_id) in self.path

    def inputs(self) -> set[tuple[int, int]] | None:
        """Recorded dependencies, or the replacement path as a legacy fallback."""
        return self.source_path if self.source_recorded else self.path

    def compatibility(self, selected_pairs: set[tuple[int, int]]) -> int:
        """Proven ancestry=2, legacy pathless=1, unprovable=0 (a null source
        never borrows ``path``)."""
        if self.source_recorded:
            if self.source_path is None:
                return 0
            return 2 if self.source_path <= selected_pairs else 0
        if self.path is None:
            return 1
        return 2 if self.path.issubset(selected_pairs) else 0

    def priority(self, selected_pairs: set[tuple[int, int]]) -> tuple[int, int]:
        """Rank by compatibility proof, then actual coverage width."""
        span = self.replaced_to - self.replaced_from + 1
        return (self.compatibility(selected_pairs), max(0, span))


def compact_rule_from_event(evt: Mapping[str, Any]) -> CompactRule | None:
    """Decode a persisted summary, without inferring missing dependencies."""
    if evt.get("type") not in ("compact_replace", "compact_complete"):
        return None
    replaced_from = evt.get("replaced_from_event_id")
    replaced_to = evt.get("replaced_to_event_id")
    if not isinstance(replaced_from, int) or not isinstance(replaced_to, int):
        return None
    summary = evt.get("summary") or evt.get("summary_text") or ""
    compact_round = evt.get("round")
    if (
        "compact_source_path" in evt
        and isinstance(compact_round, int)
        and compact_round > 0
    ):
        summary = (
            f"[Previous context summary (compact round {compact_round})]\n\n{summary}"
        )
    return CompactRule(
        replaced_from=replaced_from,
        replaced_to=replaced_to,
        path=compact_path_from_event(evt),
        summary=summary,
        source_path=compact_source_path_from_event(evt),
        source_recorded="compact_source_path" in evt,
        result_event_ids=frozenset(_result_ids(evt)),
    )


def job_origin_pairs(events: list[dict]) -> dict[str, tuple[int, int]]:
    """Map internal job identities to their issuing turns and branches.

    Both tool and sub-agent dispatches issue work. An identity that is reused
    across different turn/branch pairs is ambiguous and is omitted rather
    than resolved to whichever dispatch appeared first.
    """
    seen: dict[str, set[tuple[int, int]]] = {}
    for evt in events:
        if evt.get("type") not in ("tool_call", "subagent_call"):
            continue
        turn = evt.get("turn_index")
        branch = evt.get("branch_id")
        if not isinstance(turn, int) or not isinstance(branch, int):
            continue
        if turn <= 0 or branch <= 0:
            continue
        identities = {
            str(value) for value in (evt.get("call_id"), evt.get("job_id")) if value
        }
        for identity in identities:
            seen.setdefault(identity, set()).add((turn, branch))
    return {
        identity: next(iter(pairs))
        for identity, pairs in seen.items()
        if len(pairs) == 1
    }


def _message_metadata(msg: Any) -> dict:
    metadata = getattr(msg, "metadata", None)
    return metadata if isinstance(metadata, dict) else {}


def _user_message_pairs(
    metadata: dict, origins: Mapping[str, tuple[int, int]] | None
) -> set[tuple[int, int]] | None:
    """Pairs one user message's text depends on, or ``None`` if unknown."""
    turn = metadata.get("turn_index")
    branch = metadata.get("branch_id")
    if not isinstance(turn, int) or not isinstance(branch, int):
        return None
    if turn <= 0 or branch <= 0:
        return None
    # A batch carrying job results without naming them proves no origin.
    if metadata.get("origin_known") is False:
        return None
    pairs = {(turn, branch)}
    for job_id in metadata.get("job_ids") or ():
        origin = (origins or {}).get(str(job_id))
        if origin is None:
            return None
        pairs.add(origin)
    return pairs


def summary_source_path(
    messages: Sequence[Any],
    *,
    origins: Mapping[str, tuple[int, int]] | None = None,
) -> set[tuple[int, int]] | None:
    """Collect dependencies of captured messages and their consumed summaries."""
    deps: set[tuple[int, int]] = set()
    open_turn: set[tuple[int, int]] | None = None
    for msg in messages:
        metadata = _message_metadata(msg)
        if is_summary_message(msg):
            if "compact_source_path" not in metadata:
                return None
            inherited = compact_source_path_from_event(metadata)
            if inherited is None:
                return None
            deps |= inherited
            continue
        role = getattr(msg, "role", None)
        if role == "user":
            pairs = _user_message_pairs(metadata, origins)
            if pairs is None:
                return None
            if metadata.get("kind") != "tool_results":
                open_turn = pairs
            deps |= pairs
            continue
        job_id = metadata.get("job_id")
        if job_id:
            origin = (origins or {}).get(str(job_id))
            if origin is None:
                return None
            deps.add(origin)
        # assistant / tool / folded feedback inherit the turn opened above.
        if open_turn is None:
            return None
        deps |= open_turn
    return deps or None


def is_summary_message(msg: Any) -> bool:
    """Identify both metadata-tagged and legacy compact summaries."""
    return bool(_message_metadata(msg).get("compact_summary")) or (
        getattr(msg, "role", None) == "assistant"
        and isinstance(getattr(msg, "content", None), str)
        and msg.content.startswith("[Previous context summary (compact round ")
    )


def _result_ids(metadata: Mapping[str, Any]) -> set[int]:
    raw = metadata.get("compact_result_event_ids")
    return (
        {value for value in raw if type(value) is int and value > 0}
        if isinstance(raw, (list, tuple))
        else set()
    )


def summary_result_event_ids(messages: Sequence[Any], events: list[dict]) -> set[int]:
    """Collect the identities of final results consumed by captured messages."""
    results: dict[str, set[int]] = {}
    for index, event in enumerate(events):
        if event.get("type") != "tool_result" or type(event.get("event_id")) is not int:
            continue
        if index and {
            k: v for k, v in event.items() if k not in {"event_id", "ts"}
        } == {
            k: v for k, v in events[index - 1].items() if k not in {"event_id", "ts"}
        }:
            continue
        job_id = str(event.get("call_id") or event.get("job_id") or "")
        if job_id:
            results.setdefault(job_id, set()).add(event["event_id"])
    known_ids = set().union(*results.values()) if results else set()
    consumed: set[int] = set()
    for message in messages:
        metadata = _message_metadata(message)
        if is_summary_message(message):
            consumed.update(_result_ids(metadata))
            if "compact_result_event_ids" not in metadata:
                matched = matching_compact_complete(message, events)
                if matched is not None:
                    consumed.update(_result_ids(matched))
            continue
        if metadata.get("tool_result_phase") == "pending":
            continue
        if getattr(message, "role", None) == "tool":
            event_id = metadata.get("result_event_id")
            if event_id in known_ids:
                consumed.add(event_id)
                continue
            job_ids = [metadata.get("job_id")]
        elif (
            metadata.get("kind") == "tool_results"
            and metadata.get("tool_result_phase") == "final"
        ) or (metadata.get("kind") == "injected" and metadata.get("job_ids")):
            job_ids = metadata.get("job_ids") or []
        else:
            continue
        for job_id in job_ids:
            identities = results.get(str(job_id), set())
            if len(identities) == 1:
                consumed.update(identities)
    return consumed


async def capture_compact_metadata(
    store: Any,
    agent_name: str,
    agent: Any,
    messages: Sequence[Any],
    keep_count: int,
    compact_round: int,
    *,
    retained_messages: Sequence[Any] = (),
    retain_latest_user: bool = False,
) -> dict:
    """Capture replacement coverage and summary inputs before generation."""
    messages = deepcopy(messages)
    # The live tail keeps changing while generation awaits the store; freeze
    # both sides of the boundary so coverage describes one consistent split.
    retained_messages = deepcopy(retained_messages)
    captured_agent = SimpleNamespace(
        _turn_index=getattr(agent, "_turn_index", None),
        _branch_id=getattr(agent, "_branch_id", None),
        _parent_branch_path=list(getattr(agent, "_parent_branch_path", None) or []),
    )
    run = getattr(store, "run", None)
    events = (
        await run(read_agent_events, store, agent_name)
        if callable(run)
        else read_agent_events(store, agent_name)
    )
    source = summary_source_path(messages, origins=job_origin_pairs(events))
    metadata = build_compact_metadata(
        captured_agent,
        events,
        keep_count,
        compact_round=compact_round,
        summary="",
        messages_compacted=len(messages),
        source_path=source,
        result_event_ids=summary_result_event_ids(messages, events),
        captured_messages=messages,
        retained_messages=retained_messages,
        retain_latest_user=retain_latest_user,
    )
    metadata["_capture_unproven"] = bool(
        source
        and uses_canonical_rounds(events)
        and "replaced_from_event_id" not in metadata
    )
    return metadata


def live_turn_window(
    messages: Sequence[Any],
    boundary: int,
    preferred_boundary: int,
    keep_count: int,
) -> int:
    """Count retained user turns, including a reattached partial turn."""
    if boundary <= preferred_boundary:
        return keep_count
    retained = sum(1 for msg in messages[boundary:] if is_real_user_message(msg))
    leading = leading_system_count(messages)
    return retained + int(
        any(is_real_user_message(msg) for msg in reversed(messages[leading:boundary]))
    )


def current_path(agent: Any) -> set[tuple[int, int]]:
    """Return the agent's current historical lineage as turn/branch pairs."""
    if agent is None:
        return set()
    path: set[tuple[int, int]] = set()
    raw = getattr(agent, "_parent_branch_path", None)
    if raw:
        for item in raw:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                continue
            try:
                t, b = int(item[0]), int(item[1])
            except (TypeError, ValueError):
                continue
            if t > 0 and b > 0:
                path.add((t, b))
    ti = getattr(agent, "_turn_index", None)
    bi = getattr(agent, "_branch_id", None)
    if isinstance(ti, int) and isinstance(bi, int) and ti > 0 and bi > 0:
        path.add((ti, bi))
    return path


def persist_compacted(
    store: Any,
    agent_name: str,
    agent: Any,
    conversation: Any,
    compact_count: int,
    last_compact_time: float,
    *,
    captured: tuple[list[dict], dict, int] | None = None,
) -> None:
    """Persist a compacted conversation and its matching branch checkpoint."""
    try:
        messages, branch, watermark = captured or (
            conversation.snapshot_messages(preserve_pending_tail=True),
            snapshot_tag(agent),
            max(
                (
                    evt.get("event_id", 0)
                    for evt in read_agent_events(store, agent_name)
                ),
                default=0,
            ),
        )
        saver = getattr(store, "save_branch_snapshot", None)
        if callable(saver):
            saver(
                agent_name,
                messages,
                branch=branch,
                watermark=watermark,
                compact_state={
                    "compact_count": compact_count,
                    "last_compact_time": last_compact_time,
                },
            )
        else:
            store.save_conversation(agent_name, messages)
    except Exception as e:
        logger.warning(
            "Failed to save compacted conversation",
            error=str(e),
            exc_info=True,
        )
    if not callable(getattr(store, "save_branch_snapshot", None)):
        tag_snapshot_branch(store, agent, agent_name)
        persist_compact_state(store, agent_name, compact_count, last_compact_time)


async def persist_compacted_async(
    store: Any,
    agent_name: str,
    agent: Any,
    conversation: Any,
    compact_count: int,
    last_compact_time: float,
) -> None:
    """Persist one captured checkpoint on the store's affinity thread."""
    captured = (
        deepcopy(conversation.snapshot_messages(preserve_pending_tail=True)),
        snapshot_tag(agent),
        max(
            (evt.get("event_id", 0) for evt in read_agent_events(store, agent_name)),
            default=0,
        ),
    )
    run = getattr(store, "run", None)
    if callable(run):
        await run(
            persist_compacted,
            store,
            agent_name,
            agent,
            conversation,
            compact_count,
            last_compact_time,
            captured=captured,
        )
    else:
        await asyncio.to_thread(
            persist_compacted,
            store,
            agent_name,
            agent,
            conversation,
            compact_count,
            last_compact_time,
            captured=captured,
        )


def persist_compact_state(
    store: Any, agent_name: str, compact_count: int, last_compact_time: float
) -> None:
    """Persist compact counters and the watermark for legacy snapshot stores."""
    try:
        store.save_state(agent_name, compact_count=compact_count)
        if not callable(getattr(store, "save_branch_snapshot", None)):
            last_event_id = max(
                (
                    evt["event_id"]
                    for evt in store.get_events(agent_name)
                    if isinstance(evt.get("event_id"), int)
                ),
                default=0,
            )
            store.state[f"{agent_name}:snapshot_event_id"] = last_event_id
        store.state[f"{agent_name}:last_compact_time"] = last_compact_time
    except Exception as e:
        logger.warning(
            "Failed to save compact_count state",
            error=str(e),
            exc_info=True,
        )


def tag_snapshot_branch(store: Any, agent: Any, agent_name: str) -> None:
    """Tag the saved snapshot with the branch it was created on.

    Mirrors ``session/output.py:on_processing_end`` so resume can match the
    target branch before trusting the snapshot. No-op when the agent has not
    started a turn or the store is read-only.
    """
    if agent is None or store is None:
        return
    ti = getattr(agent, "_turn_index", None)
    bi = getattr(agent, "_branch_id", None)
    if not isinstance(ti, int) or ti <= 0 or not isinstance(bi, int) or bi <= 0:
        # persist_compacted rewrote the snapshot above; the agent's branch
        # state is missing/invalid, so clear any stale tag from a prior run
        # rather than leave it pointing at a branch that no longer matches
        # this snapshot (mirrors session/output.py:on_processing_end).
        try:
            store.state.pop(f"{agent_name}:snapshot_branch", None)
        except Exception:  # pragma: no cover - defensive
            pass
        return
    try:
        store.state[f"{agent_name}:snapshot_branch"] = {
            "turn_index": ti,
            "branch_id": bi,
            "parent_branch_path": getattr(agent, "_parent_branch_path", None),
        }
    except Exception:  # pragma: no cover - defensive
        pass


def build_compact_metadata(
    agent: Any,
    events: list[dict],
    keep_count: int,
    *,
    compact_round: int,
    summary: str,
    messages_compacted: int,
    source_path: set[tuple[int, int]] | None = None,
    result_event_ids: set[int] | None = None,
    captured_messages: Sequence[Any] = (),
    retained_messages: Sequence[Any] = (),
    retain_latest_user: bool = False,
) -> dict:
    """Serialize replacement coverage and inputs; explicit null denotes unknown."""
    compact_path = current_path(agent)
    replaced = (
        compute_captured_range(
            events,
            compact_path,
            captured_messages,
            retained_messages,
            retain_latest_user=retain_latest_user,
            keep_count=keep_count,
        )
        if events
        else None
    )
    metadata = {
        "round": compact_round,
        "summary": summary,
        "messages_compacted": messages_compacted,
    }
    metadata["compact_result_event_ids"] = sorted(result_event_ids or ())
    if replaced is not None:
        metadata["replaced_from_event_id"] = replaced[0]
        metadata["replaced_to_event_id"] = replaced[1]
    if compact_path:
        metadata["compact_path"] = _sorted_pairs(compact_path)
    metadata["compact_source_path"] = (
        _sorted_pairs(source_path) if source_path else None
    )
    if agent is not None:
        metadata["turn_index"] = getattr(agent, "_turn_index", None)
        metadata["branch_id"] = getattr(agent, "_branch_id", None)
        metadata["parent_branch_path"] = getattr(agent, "_parent_branch_path", None)
    return metadata


def read_agent_events(store: Any, agent_name: str) -> list[dict]:
    """Read one agent's events, tolerating read failures (compact is best-effort)."""
    if store is None:
        return []
    try:
        return list(store.get_events(agent_name))
    except Exception:  # pragma: no cover - defensive
        return []
