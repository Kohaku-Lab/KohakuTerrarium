"""Derive a per-creature status snapshot from a ``Creature`` runtime.

The roster widget renders one slot per status. The derivation is a
pure function (no globals, no side effects) so the roster can be
rendered at any time without ordering constraints, and the function
itself is easy to unit-test.

State priorities (highest first — used by the roster's compression
algorithm at narrow widths):

    waiting > working > failed > stopped > idle

Notes on the source of each signal:

- ``working`` — ``creature.agent._processing_task`` exists and isn't
  done (the canonical "controller turn in flight" handle used by
  :meth:`Agent.interrupt` at ``core/agent.py:566``).
- ``waiting`` — ``creature.agent.output_router._pending_replies``
  is non-empty (the interactive-bus pending-reply map from
  ``modules/output/router.py:98``). Covers ``ask_user`` tools and
  the ``permgate`` plugin's held-tool prompts.
- ``failed`` — ``creature._last_turn_failed`` flag (default False
  on legacy creatures; set by future error-capture hook).
- ``stopped`` — ``not creature.is_running``.
- ``idle`` — none of the above.

The activity string is best-effort and never longer than ~40 chars.
The roster truncates further to fit slot width.
"""

import time
from dataclasses import dataclass
from typing import Any, Literal

StatusState = Literal["working", "idle", "waiting", "failed", "stopped"]


@dataclass(frozen=True)
class CreatureStatus:
    """Pure snapshot of one creature's state for the roster."""

    creature_id: str
    name: str
    state: StatusState
    activity: str
    duration_seconds: int = 0
    # Phase H — number of events since the user last focused this
    # creature. Rendered as ``●N`` next to non-focused creatures with
    # unread > 0. Populated by the app from
    # ``LiveRegionState.unread_since_focus``; default 0 keeps the
    # field optional for callers that don't track unread.
    unread: int = 0


# Priority order for the roster compression algorithm — lower number
# wins when slots have to be hidden.
STATE_PRIORITY: dict[StatusState, int] = {
    "waiting": 0,
    "working": 1,
    "failed": 2,
    "stopped": 3,
    "idle": 4,
}


_ACTIVITY_MAX = 40


def _truncate(text: str, limit: int = _ACTIVITY_MAX) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _format_duration(seconds: int) -> str:
    """Compact duration: ``5s`` / ``2m`` / ``1h 14m`` / ``3d``."""
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        return f"{h}h {m}m" if m else f"{h}h"
    return f"{seconds // 86400}d"


def _is_processing(agent: Any) -> bool:
    """``True`` if the controller turn is in flight."""
    task = getattr(agent, "_processing_task", None)
    return task is not None and not task.done()


def _pending_replies(agent: Any) -> list[Any]:
    """The interactive-bus pending-reply list (may be empty)."""
    router = getattr(agent, "output_router", None)
    if router is None:
        return []
    pending = getattr(router, "_pending_replies", None)
    if not pending:
        return []
    return list(pending.values())


def _last_event_time(agent: Any) -> float | None:
    """When the controller last emitted an event (best-effort)."""
    return getattr(agent, "_last_activity_ts", None)


def _running_job_summary(agent: Any) -> str:
    """Short string describing the current direct job, if any.

    Reads ``agent._active_handles`` — the dict the interrupt path
    walks at ``core/agent.py:571``. Picks the most-recent handle's
    name + first arg preview.
    """
    handles = getattr(agent, "_active_handles", None)
    if not handles:
        return ""
    # ``_active_handles`` is dict-like; the last-added value tends to
    # be the visible job. Don't rely on ordering for correctness; if
    # the iteration surfaces a stale entry the worst case is an
    # outdated string for one frame.
    try:
        handle = next(reversed(list(handles.values())))
    except (StopIteration, TypeError):
        return ""
    name = getattr(handle, "name", None) or getattr(handle, "tool_name", "") or "?"
    args = getattr(handle, "args", None) or getattr(handle, "args_preview", "")
    if isinstance(args, dict):
        # Pick the first stringable value.
        for value in args.values():
            if isinstance(value, str) and value:
                return _truncate(f"{name}: {value}")
            if value is not None:
                return _truncate(f"{name}: {value!r}")
    if isinstance(args, str) and args:
        return _truncate(f"{name}: {args}")
    return _truncate(name)


def _pending_reply_summary(replies: list[Any]) -> str:
    """Short prompt/question text from the first pending reply."""
    if not replies:
        return "needs input"
    reply = replies[0]
    # The pending entry is whatever ``router.emit_and_wait`` stashed;
    # try the common fields without crashing on shape drift.
    for attr in ("prompt", "question", "detail", "message"):
        text = getattr(reply, attr, "")
        if isinstance(text, str) and text:
            return _truncate(f"needs: {text}")
    text = str(reply)
    return _truncate(f"needs: {text}") if text else "needs input"


def derive_status(creature: Any, now: float | None = None) -> CreatureStatus:
    """Return a fresh :class:`CreatureStatus` for ``creature``.

    Pure function. Does NOT touch ``creature`` mutable state. Safe to
    call from any thread (it only reads attributes); the roster
    invokes it per render tick.
    """
    now = now if now is not None else time.time()
    cid = getattr(creature, "creature_id", "") or ""
    name = getattr(creature, "name", "") or cid or "creature"

    if not getattr(creature, "is_running", False):
        last = _last_event_time(getattr(creature, "agent", None))
        duration = int(max(0.0, now - last)) if last else 0
        return CreatureStatus(
            creature_id=cid,
            name=name,
            state="stopped",
            activity=(
                f"stopped {_format_duration(duration)} ago" if duration else "stopped"
            ),
            duration_seconds=duration,
        )

    agent = getattr(creature, "agent", None)
    if agent is None:
        return CreatureStatus(creature_id=cid, name=name, state="idle", activity="idle")

    if getattr(creature, "_last_turn_failed", False):
        return CreatureStatus(
            creature_id=cid,
            name=name,
            state="failed",
            activity=_truncate(getattr(creature, "_last_turn_error", "failed")),
        )

    replies = _pending_replies(agent)
    if replies:
        return CreatureStatus(
            creature_id=cid,
            name=name,
            state="waiting",
            activity=_pending_reply_summary(replies),
        )

    if _is_processing(agent):
        summary = _running_job_summary(agent)
        if not summary:
            tokens = getattr(agent, "_last_generation_tokens", 0) or 0
            summary = (
                f"Generating response ({tokens}t)" if tokens else "Generating response"
            )
        return CreatureStatus(
            creature_id=cid, name=name, state="working", activity=summary
        )

    last = _last_event_time(agent)
    duration = int(max(0.0, now - last)) if last else 0
    activity = f"idle {_format_duration(duration)}" if duration else "idle"
    return CreatureStatus(
        creature_id=cid,
        name=name,
        state="idle",
        activity=activity,
        duration_seconds=duration,
    )


__all__ = ["CreatureStatus", "StatusState", "STATE_PRIORITY", "derive_status"]
