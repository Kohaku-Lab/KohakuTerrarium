"""Per-creature chat: HTTP fallback chat + regenerate + edit + rewind +
history + branches.

Replaces ``KohakuManager.agent_chat / agent_get_history /
terrarium_chat`` and the legacy ``routes/agents.py`` regen/edit/rewind/
branches handlers + ``routes/terrarium.py:terrarium_history`` body.
"""

from typing import Any, AsyncIterator

from kohakuterrarium.session.history import collect_branch_metadata
from kohakuterrarium.studio.sessions.lifecycle import (
    find_creature,
    find_session_for_creature,
    get_session_store,
)
from kohakuterrarium.terrarium.engine import Terrarium


def _get_agent(engine: Terrarium, session_id: str, creature_id: str) -> Any:
    return find_creature(engine, session_id, creature_id).agent


async def chat(
    engine: Terrarium,
    session_id: str,
    creature_id: str,
    message: str | list[dict],
) -> AsyncIterator[str]:
    """Inject a message and stream the response.  HTTP fallback only —
    the realtime IO path is the WS attach (Step 11)."""
    creature = find_creature(engine, session_id, creature_id)
    async for chunk in creature.chat(message):
        yield chunk


async def regenerate(
    engine: Terrarium,
    session_id: str,
    creature_id: str,
    *,
    turn_index: int | None = None,
    branch_view: dict[int, int] | None = None,
) -> None:
    """Regenerate an assistant response.

    ``turn_index=None`` regenerates the conversation tail (legacy
    behaviour). A specific ``turn_index`` opens a new branch under
    that turn — used when the user clicks "retry" on a non-tail
    message in the chat UI; without this parameter the click silently
    targeted the tail no matter where the user clicked.

    ``branch_view`` lets the caller retry on a NON-LATEST branch.
    Without it, the agent's in-memory conversation reflects whichever
    branch it last ran, and a retry click on an older branch in the
    UI would silently target the wrong message.
    """
    agent = _get_agent(engine, session_id, creature_id)
    await agent.regenerate_last_response(
        turn_index=turn_index,
        branch_view=branch_view,
    )


async def edit_message(
    engine: Terrarium,
    session_id: str,
    creature_id: str,
    msg_idx: int,
    content: str,
    *,
    turn_index: int | None = None,
    user_position: int | None = None,
    branch_view: dict[int, int] | None = None,
) -> bool:
    """Edit a user message at ``msg_idx`` and re-run from there.

    ``branch_view`` lets the caller edit a message on a NON-LATEST
    branch — the agent reloads its in-memory conversation from
    events under the chosen view before truncating + rerunning so
    the resolution lands on the message the user actually clicked.
    """
    agent = _get_agent(engine, session_id, creature_id)
    return await agent.edit_and_rerun(
        msg_idx,
        content,
        turn_index=turn_index,
        user_position=user_position,
        branch_view=branch_view,
    )


async def rewind(
    engine: Terrarium, session_id: str, creature_id: str, msg_idx: int
) -> None:
    """Drop messages from ``msg_idx`` onward without re-running."""
    agent = _get_agent(engine, session_id, creature_id)
    await agent.rewind_to(msg_idx)


def history(engine: Terrarium, session_id: str, creature_id: str) -> dict[str, Any]:
    """Return the conversation + event log for a creature OR channel.

    The frontend reuses this single endpoint for both per-creature
    chat tabs and per-channel views (``ch:<name>``); the latter never
    map to a creature, so we shape a channel-history payload from the
    session store instead of 404ing.  See plan §6 / api-audit row 2.2.
    """
    if creature_id.startswith("ch:"):
        return _channel_history(engine, session_id, creature_id[3:])

    creature = find_creature(engine, session_id, creature_id)
    agent = creature.agent

    events: list[dict] = []
    if hasattr(agent, "session_store") and agent.session_store:
        try:
            events = agent.session_store.get_resumable_events(creature.name)
        except Exception:
            events = []

    if not events:
        # Fallback to lifecycle-attached store if any.
        sid = find_session_for_creature(engine, creature_id) or session_id
        store = get_session_store(sid)
        if store is not None:
            try:
                events = store.get_resumable_events(creature.name)
            except Exception:
                events = []

    return {
        "creature_id": creature_id,
        "session_id": session_id,
        "messages": agent.conversation_history,
        "events": events,
        "is_processing": bool(getattr(agent, "_processing_task", None)),
    }


def _channel_history(
    engine: Terrarium, session_id: str, channel: str
) -> dict[str, Any]:
    """Build a channel-history payload from the attached session store.

    Mirrors the legacy ``terrarium_history`` body for channel targets:
    each persisted message becomes a ``channel_message`` event so the
    frontend's chat replay logic can render them inside the channel
    tab.  Returns an empty event list when no store is attached or the
    channel has no recorded messages — the frontend tolerates that.
    """
    store = get_session_store(session_id)
    if store is None and session_id != "_":
        # Walk every active store as a last resort; useful when the
        # session id is the legacy "_" wildcard or when the studio
        # bookkeeping disagrees with the engine after a fork.
        for candidate in []:  # placeholder; explicit scan below
            store = candidate
            break

    events: list[dict] = []
    if store is not None:
        try:
            messages = store.get_channel_messages(channel) or []
        except Exception:
            messages = []
        for m in messages:
            events.append(
                {
                    "type": "channel_message",
                    "channel": channel,
                    "sender": m.get("sender", ""),
                    "content": m.get("content", ""),
                    "ts": m.get("ts", 0),
                }
            )

    return {
        "creature_id": f"ch:{channel}",
        "session_id": session_id,
        "messages": [],
        "events": events,
        "is_processing": False,
    }


def branches(engine: Terrarium, session_id: str, creature_id: str) -> dict[str, Any]:
    """Return per-turn branch metadata for the navigator UI."""
    payload = history(engine, session_id, creature_id)
    meta = collect_branch_metadata(payload["events"])
    turns = [
        {
            "turn_index": ti,
            "branches": info["branches"],
            "latest_branch": info["latest_branch"],
        }
        for ti, info in sorted(meta.items())
    ]
    return {"creature_id": creature_id, "turns": turns}
