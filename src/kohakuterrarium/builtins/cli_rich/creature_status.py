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
    # Unread events are tracked only for unfocused creatures.
    unread: int = 0
    # Canonical profile name is empty when the model cannot be resolved.
    model: str = ""


# Lower values survive first when narrow layouts hide roster slots.
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


def _model_identifier(agent: Any) -> str:
    """Return the canonical LLM identifier with a raw-model fallback."""
    if agent is None:
        return ""
    ident = getattr(agent, "llm_identifier", None)
    if callable(ident):
        try:
            value = ident()
        except Exception:
            value = ""
        if value:
            return value
    return getattr(getattr(agent, "llm", None), "model", "") or ""


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
    """Summarize the most recently added active job."""
    handles = getattr(agent, "_active_handles", None)
    if not handles:
        return ""
    # A stale ordering can only affect one frame of best-effort status text.
    try:
        handle = next(reversed(list(handles.values())))
    except (StopIteration, TypeError):
        return ""
    name = getattr(handle, "name", None) or getattr(handle, "tool_name", "") or "?"
    args = getattr(handle, "args", None) or getattr(handle, "args_preview", "")
    if isinstance(args, dict):
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
    # Pending reply shapes vary across interactive event types.
    for attr in ("prompt", "question", "detail", "message"):
        text = getattr(reply, attr, "")
        if isinstance(text, str) and text:
            return _truncate(f"needs: {text}")
    text = str(reply)
    return _truncate(f"needs: {text}") if text else "needs input"


def derive_status(creature: Any, now: float | None = None) -> CreatureStatus:
    """Derive a read-only roster snapshot from current creature state."""
    now = now if now is not None else time.time()
    cid = getattr(creature, "creature_id", "") or ""
    name = getattr(creature, "name", "") or cid or "creature"
    model = _model_identifier(getattr(creature, "agent", None))

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
            model=model,
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
            model=model,
        )

    replies = _pending_replies(agent)
    if replies:
        return CreatureStatus(
            creature_id=cid,
            name=name,
            state="waiting",
            activity=_pending_reply_summary(replies),
            model=model,
        )

    if _is_processing(agent):
        summary = _running_job_summary(agent)
        if not summary:
            tokens = getattr(agent, "_last_generation_tokens", 0) or 0
            summary = (
                f"Generating response ({tokens}t)" if tokens else "Generating response"
            )
        return CreatureStatus(
            creature_id=cid, name=name, state="working", activity=summary, model=model
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
        model=model,
    )


__all__ = ["CreatureStatus", "StatusState", "STATE_PRIORITY", "derive_status"]
