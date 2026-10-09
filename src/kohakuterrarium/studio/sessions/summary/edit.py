"""User actions on a session summary: write it by hand, or regenerate it now.

Both work on a live store (through its hook's model) and on a saved store
(through the summary-model setting, else the heuristic).
"""

import asyncio
from pathlib import Path
from typing import Any

from kohakuterrarium.studio.identity.session_summary import load_settings
from kohakuterrarium.studio.persistence.session_index import (
    get_session_index_default,
)
from kohakuterrarium.studio.persistence.session_index.hooks import push_index_update
from kohakuterrarium.studio.persistence.session_index.exchange import primary_agent
from kohakuterrarium.studio.sessions.summary.record import (
    USER,
    clear_summary,
    read_summary,
    write_summary,
)
from kohakuterrarium.studio.sessions.summary.sources import one_line
from kohakuterrarium.studio.sessions.summary.tracking import hook_of, summary_llm
from kohakuterrarium.studio.sessions.summary.writer import (
    MANUAL,
    count_turns,
    summarize,
)


async def reindex(store: Any) -> None:
    """Push ``store``'s row into its session directory's listing index now."""
    index = await asyncio.to_thread(get_session_index_default, Path(store.path).parent)
    await store.run(push_index_update, store, index)


def _apply_title(store: Any, title: str) -> dict:
    store.meta["name"] = title
    return {"title": title}


async def set_title(store: Any, title: str) -> dict:
    """Store the session's display name in ``meta["name"]``; empty clears it."""
    return await store.run(_apply_title, store, " ".join((title or "").split()))


def _apply_user_text(store: Any, text: str) -> dict:
    if not text:
        clear_summary(store)
        return {}
    return write_summary(store, text, USER, count_turns(store))


async def set_user_summary(store: Any, text: str) -> dict:
    """Store ``text`` as a ``user`` summary; empty text hands it back to automatic."""
    return await store.run(_apply_user_text, store, one_line(text or ""))


async def regenerate(store: Any) -> dict:
    """Write a fresh summary from the configured source, replacing any summary."""
    settings = load_settings()
    hook = hook_of(store)
    if hook is not None:
        meta = await store.run(store.load_meta)
        agent = primary_agent(meta)

        def factory() -> Any:
            return hook.llm_for(agent, settings)

    else:

        def factory() -> Any:
            return summary_llm(None, settings)

    record = await summarize(store, settings, llm_factory=factory, reason=MANUAL)
    if record is None:
        return read_summary(await store.run(store.load_meta))
    return record
