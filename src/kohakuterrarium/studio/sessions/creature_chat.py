"""Provide creature chat mutations, history, and branch metadata."""

from typing import Any, AsyncIterator

from kohakuterrarium.session.raw_history import UserMessageSelector
from kohakuterrarium.studio.sessions.lifecycle import find_creature
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.terrarium import TerrariumService


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
    request_id: str | None = None,
    target: UserMessageSelector | None = None,
) -> dict[str, Any]:
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
    kwargs = {
        "turn_index": turn_index,
        "branch_view": branch_view,
        "request_id": request_id,
    }
    if target is not None:
        kwargs["target"] = target
    return await service.regenerate(creature_id, **kwargs)


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
    request_id: str | None = None,
    target: UserMessageSelector | None = None,
) -> dict[str, Any]:
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
    kwargs = {
        "turn_index": turn_index,
        "user_position": user_position,
        "branch_view": branch_view,
        "request_id": request_id,
    }
    if target is not None:
        kwargs["target"] = target
    return await service.edit_message(creature_id, msg_idx, content, **kwargs)


async def rewind(
    service: "TerrariumService", session_id: str, creature_id: str, msg_idx: int
) -> None:
    """Drop messages from ``msg_idx`` onward without re-running.

    CF-11: route via ``service.rewind`` so worker-hosted creatures
    aren't looked up against the host engine.
    """
    await service.rewind(creature_id, msg_idx)


async def history(
    service: "TerrariumService", session_id: str, creature_id: str
) -> dict[str, Any]:
    """Return the conversation + event log for a creature OR channel.

    The frontend reuses this single endpoint for both per-creature
    chat tabs and per-channel views (``ch:<name>``); the latter never
    map to a creature, so we shape a channel-history payload from the
    session store instead of 404ing.  See plan §6 / api-audit row 2.2.
    """
    if creature_id.startswith("ch:"):
        return await channel_history(service, session_id, creature_id[3:])
    return await service.chat_history(creature_id)


async def channel_history(
    service: "TerrariumService", session_id: str, channel: str
) -> dict[str, Any]:
    """Build a channel-history payload through the service boundary."""
    try:
        messages = await service.channel_history(session_id, channel)
    except KeyError:
        messages = []
    events = [
        {
            "type": "channel_message",
            "channel": channel,
            "sender": message.get("sender", ""),
            "content": message.get("content", ""),
            "ts": message.get("timestamp", message.get("ts", 0)),
        }
        for message in messages
    ]
    return {
        "creature_id": f"ch:{channel}",
        "session_id": session_id,
        "messages": [],
        "events": events,
        "is_processing": False,
    }


async def branches(
    service: "TerrariumService", session_id: str, creature_id: str
) -> list[dict[str, Any]]:
    """Return the authoritative per-turn branch metadata from the runtime."""
    return await service.chat_branches(creature_id)
