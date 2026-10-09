"""Decide whether a session's summary is due and write it.

``summarize`` runs one refresh against a store: it counts the turn, checks the
refresh rule (first turn, every N turns, every compaction, manual), composes
text from the configured source with fallbacks, and stores it. Store calls go
through ``store.run`` so they land on the store's affinity thread.
"""

from collections.abc import Callable
from typing import Any

from kohakuterrarium.studio.identity.session_summary import (
    COMPACTION,
    HEURISTIC,
    LLM,
    OFF,
    SummarySettings,
)
from kohakuterrarium.studio.persistence.session_index.exchange import primary_agent
from kohakuterrarium.studio.sessions.summary.record import (
    USER,
    read_summary,
    should_replace,
    write_summary,
)
from kohakuterrarium.studio.sessions.summary.sources import (
    compaction_text,
    heuristic_text,
    llm_text,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

TURN = "turn"
COMPACTED = "compaction"
MANUAL = "manual"
TURNS_KEY = "summary:turns"


def is_due(current: dict, turns: int, every: int, reason: str) -> bool:
    if reason != TURN:
        return True
    if not current:
        return turns >= 1
    return turns - int(current.get("at_turn") or 0) >= every


def count_turns(store: Any, add: int = 0) -> int:
    """The primary agent's finished-turn counter, after adding ``add`` (requires blocking).

    It only grows, so compaction shrinking the conversation never resets it.
    """
    try:
        turns = int(store.state.get(TURNS_KEY) or 0)
    except (KeyError, TypeError, ValueError):
        turns = 0
    if add:
        turns += add
        store.state[TURNS_KEY] = turns
    return turns


async def compose(
    configured: str,
    messages: list | None,
    compaction: str,
    llm_factory: Callable[[], Any],
) -> tuple[str, str]:
    """``(text, source)`` from the configured source, falling back toward heuristic."""
    if configured == LLM:
        try:
            llm = llm_factory()
            text = await llm_text(llm, messages, compaction) if llm else ""
        except Exception as exc:  # noqa: BLE001 - a failed call falls back
            logger.warning("summary llm call failed", error=str(exc))
            text = ""
        if text:
            return text, LLM
    if configured in (LLM, COMPACTION) and compaction:
        text = compaction_text(compaction)
        if text:
            return text, COMPACTION
    return heuristic_text(messages), HEURISTIC


async def summarize(
    store: Any,
    settings: SummarySettings,
    *,
    llm_factory: Callable[[], Any],
    reason: str = TURN,
    agent: str | None = None,
    compaction: str = "",
    new_turns: int = 0,
) -> dict | None:
    """Refresh ``store``'s summary if due; returns the written record or None.

    ``agent`` is the agent whose event fired; only the primary agent's turns
    count, ``new_turns`` of them finished since the last call. ``MANUAL``
    regenerates even over a user summary or with the source ``off``.
    """
    meta = await store.run(store.load_meta)
    primary = primary_agent(meta)
    if not primary or (agent is not None and agent != primary):
        return None
    turns = await store.run(count_turns, store, new_turns)
    current = read_summary(meta)
    if settings.source == OFF and reason != MANUAL:
        return None
    if current.get("source") == USER and reason != MANUAL:
        return None
    if not is_due(current, turns, settings.every_n_turns, reason):
        return None
    messages = await store.run(store.load_conversation, primary)
    configured = LLM if settings.source == OFF else settings.source
    if configured == LLM and not current:
        quick = heuristic_text(messages)
        if quick:
            current = await store.run(write_summary, store, quick, HEURISTIC, turns)
    text, source = await compose(configured, messages, compaction, llm_factory)
    if not text:
        return None
    if reason != MANUAL and not should_replace(current, source, configured):
        return None
    return await store.run(write_summary, store, text, source, turns)
