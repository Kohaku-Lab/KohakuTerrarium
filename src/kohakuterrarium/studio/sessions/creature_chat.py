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
    get_session_store,
    list_session_stores,
)
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.terrarium import TerrariumService
from kohakuterrarium.studio._runtime import as_engine


def _get_agent(engine: Terrarium, session_id: str, creature_id: str) -> Any:
    return find_creature(engine, session_id, creature_id).agent


async def chat(
    service: "TerrariumService",
    session_id: str,
    creature_id: str,
    message: str | list[dict],
) -> AsyncIterator[str]:
    """Inject a message and stream the response.  HTTP fallback only —
    the realtime IO path is the WS attach (Step 11).

    Routes through the ``TerrariumService`` Protocol's ``chat`` rather
    than resolving the creature on the host engine directly — a
    worker-hosted creature isn't in the host engine's ``find_creature``
    table, so ``service.chat`` (which routes by the creature's home
    node) is the only path that reaches it. This mirrors the production
    HTTP route ``api/routes/sessions_v2/creatures_chat.py``.
    """
    async for chunk in service.chat(creature_id, message):
        yield chunk


async def regenerate(
    service: "TerrariumService",
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

    CF-11: route through ``service.regenerate`` so worker-hosted
    creatures (lab-host / cluster sessions) don't 404 on host-engine
    ``find_creature``. Standalone services implement the same protocol
    method on top of their host engine, so the path collapses to a
    direct call there.
    """
    await service.regenerate(
        creature_id, turn_index=turn_index, branch_view=branch_view
    )


async def edit_message(
    service: "TerrariumService",
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

    CF-11: route via ``service.edit_message`` so the worker hosting
    the creature receives the RPC. The local host engine doesn't know
    about worker-hosted creatures, so the legacy ``as_engine`` path
    raised ``KeyError`` in lab-host mode.
    """
    return await service.edit_message(
        creature_id,
        msg_idx,
        content,
        turn_index=turn_index,
        user_position=user_position,
        branch_view=branch_view,
    )


async def rewind(
    service: "TerrariumService", session_id: str, creature_id: str, msg_idx: int
) -> None:
    """Drop messages from ``msg_idx`` onward without re-running.

    CF-11: route via ``service.rewind`` so worker-hosted creatures
    aren't looked up against the host engine.
    """
    await service.rewind(creature_id, msg_idx)


def history(
    service: "TerrariumService", session_id: str, creature_id: str
) -> dict[str, Any]:
    """Return the conversation + event log for a creature OR channel.

    The frontend reuses this single endpoint for both per-creature
    chat tabs and per-channel views (``ch:<name>``); the latter never
    map to a creature, so we shape a channel-history payload from the
    session store instead of 404ing.  See plan §6 / api-audit row 2.2.
    """
    engine = as_engine(service)
    if creature_id.startswith("ch:"):
        return _channel_history(engine, session_id, creature_id[3:])

    creature = find_creature(engine, session_id, creature_id)
    agent = creature.agent

    # Currently-in-flight job ids — promoted background sub-agents stay
    # in ``_direct_job_meta`` until their final completion clears them,
    # so they're the canonical "still running" set. Without this hint
    # ``normalize_resumable_events`` would synthesize an interrupted
    # ``subagent_result`` for the live bg sub-agent and the UI would
    # flash "interrupted" until the real result event arrived.
    live_job_ids: set[str] = set(getattr(agent, "_direct_job_meta", {}).keys())

    events: list[dict] = []
    if hasattr(agent, "session_store") and agent.session_store:
        try:
            events = agent.session_store.get_resumable_events(
                creature.name, live_job_ids=live_job_ids
            )
        except Exception:
            events = []

    if not events:
        # Fallback to lifecycle-attached store if any. ``engine`` here is
        # already the concrete engine hosting this creature, so resolve
        # the graph by a direct local walk — no service round-trip.
        sid = next(
            (g.graph_id for g in engine.list_graphs() if creature_id in g.creature_ids),
            session_id,
        )
        store = get_session_store(sid)
        if store is not None:
            try:
                events = store.get_resumable_events(
                    creature.name, live_job_ids=live_job_ids
                )
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
    if store is None:
        # Walk every active store as a last resort; useful when the
        # session id is the legacy "_" wildcard or when the studio
        # bookkeeping disagrees with the engine after a fork. Pick the
        # first active store that actually holds this channel.
        for candidate in list_session_stores():
            try:
                if candidate.get_channel_messages(channel):
                    store = candidate
                    break
            except Exception:
                continue

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


def branches(
    service: "TerrariumService", session_id: str, creature_id: str
) -> dict[str, Any]:
    """Return per-turn branch metadata for the navigator UI."""
    engine = as_engine(service)
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
