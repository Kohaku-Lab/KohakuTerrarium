"""Attach a :class:`SummaryHook` to every live session store of an engine.

The ``llm`` source uses a separate provider instance built from the summary
model setting, else from the session creature's own model, so a summary call
never shares connection state with the creature's turns. One instance is kept
per store and rebuilt when the model setting or the creature's provider changes.
"""

import asyncio
from typing import Any

from kohakuterrarium.bootstrap.llm import create_llm_from_profile_name
from kohakuterrarium.core.compact import CompactConfig
from kohakuterrarium.studio.identity.session_summary import SummarySettings
from kohakuterrarium.studio.sessions.summary.hook import SummaryHook
from kohakuterrarium.terrarium.store_observers import observe_session_stores
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

_LISTENER_ATTR = "_kt_summary_listener"
_HOOK_ATTR = "_kt_summary_hook"


def creature_agent(engine: Any, store: Any, name: str) -> Any:
    """The live agent named ``name`` in the graph ``store`` backs, or None."""
    for graph_id, held in list(getattr(engine, "_session_stores", {}).items()):
        if held is not store:
            continue
        try:
            creature_ids = engine.get_graph(graph_id).creature_ids
        except KeyError:
            return None
        for creature_id in creature_ids:
            try:
                creature = engine.get_creature(creature_id)
            except KeyError:
                continue
            if creature.name == name:
                return creature.agent
    return None


def summary_llm(agent: Any, settings: SummarySettings) -> Any:
    """A separate provider for the ``llm`` source, or None.

    The summary model when set, else a fresh instance of the agent's model.
    The agent's live provider is never returned: when no separate instance
    can be built, the summary falls back to a non-model source.
    """
    if settings.model:
        return create_llm_from_profile_name(settings.model)
    build = getattr(agent, "_build_compact_llm", None)
    if not callable(build):
        return None
    built = build(CompactConfig())
    return None if built is getattr(agent, "llm", None) else built


def hook_of(store: Any) -> SummaryHook | None:
    return getattr(store, _HOOK_ATTR, None)


def attach(engine: Any, store: Any) -> SummaryHook | None:
    """Attach a hook to ``store`` once; read-only stores get none."""
    if store is None or getattr(store, "_readonly", False):
        return None
    existing = hook_of(store)
    if existing is not None:
        return existing
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None

    providers: dict[tuple, Any] = {}

    def llm_for(name: str, settings: SummarySettings) -> Any:
        agent = creature_agent(engine, store, name)
        key = (name, settings.model, id(getattr(agent, "llm", None)))
        if key not in providers:
            providers.clear()
            providers[key] = summary_llm(agent, settings)
        return providers[key]

    hook = SummaryHook(store, loop=loop, llm_for=llm_for)
    setattr(store, _HOOK_ATTR, hook)
    return hook


def track(engine: Any) -> None:
    """Keep every current and future session store of ``engine`` summarized."""
    if engine is None:
        return
    listener = getattr(engine, _LISTENER_ATTR, None)
    if listener is None:

        def listener(graph_id: str, store: Any) -> None:
            attach(engine, store)

        setattr(engine, _LISTENER_ATTR, listener)
    for store in list(observe_session_stores(engine, listener).values()):
        attach(engine, store)
