"""Async resume flow: heavy store work on the store's affinity thread.

Mirrors :mod:`kohakuterrarium.session.resume` stage for stage. The injection
body — the single raw event scan plus the dedupe/replay CPU work — runs as
one dispatched call so a large session cannot freeze the caller's event loop.
The agent being resumed is not started yet, so no other coroutine touches it
while the affinity thread mutates it.
"""

import asyncio
from pathlib import Path
from typing import Any

from kohakuterrarium.core.agent import Agent
from kohakuterrarium.modules.input.base import InputModule
from kohakuterrarium.modules.output.base import OutputModule
from kohakuterrarium.session.resume import (
    _attach_resumed_agent,
    _rebuild_resumed_agent,
    inject_saved_state,
)
from kohakuterrarium.session.resume_open import (
    open_store_with_migration as _open_store_with_migration,
    preflight_legacy_workspace,
)
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.session.store_open import _finish_cleanup, open_owned_store
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


async def inject_saved_state_async(agent, store: SessionStore, agent_name: str) -> None:
    """Restore saved state on the store's affinity thread.

    Same work and ordering as :func:`inject_saved_state`; the whole body —
    including the event scan and conversation replay — leaves the caller's
    event loop.
    """
    await store.run(inject_saved_state, agent, store, agent_name)


async def _resume_agent_from_open_store_async(
    store: SessionStore,
    session_path: str | Path,
    *,
    pwd_override: str | None,
    io_mode: str | None,
    llm: Any,
    input_module: InputModule | None,
    output_module: OutputModule | None,
    mark_conversation_open: bool,
) -> tuple[Agent, SessionStore]:
    """Rebuild and rehydrate an agent, injecting state off the event loop."""
    agent, agent_name = _rebuild_resumed_agent(
        store,
        session_path,
        pwd_override=pwd_override,
        io_mode=io_mode,
        llm=llm,
        input_module=input_module,
        output_module=output_module,
    )

    await inject_saved_state_async(agent, store, agent_name)

    # Attachment can recover an interrupted text segment, including another
    # event scan. Keep it on the same worker as state injection; the agent
    # has not started and cannot observe partially attached state.
    attachment = asyncio.create_task(
        store.run(
            _attach_resumed_agent,
            agent,
            store,
            session_path,
            agent_name,
            mark_conversation_open=mark_conversation_open,
        )
    )
    try:
        return await asyncio.shield(attachment)
    except asyncio.CancelledError:
        # Finish all recovery submissions before closing the store.
        try:
            await _finish_cleanup(attachment)
        except Exception:
            logger.warning(
                "Attachment failed during resume cancellation", exc_info=True
            )
        raise


async def resume_agent_async(
    session_path: str | Path,
    pwd_override: str | None = None,
    io_mode: str | None = None,
    llm: Any = None,
    *,
    input_module: InputModule | None = None,
    output_module: OutputModule | None = None,
    mark_conversation_open: bool = True,
) -> tuple[Agent, SessionStore]:
    """Async twin of :func:`resume_agent`; the caller owns the store.

    Preflight and store opening are blocking file work (legacy migration
    may copy the session file) and run in a worker thread; any post-open
    failure releases the writer lock off-loop as well.
    """
    pwd_override = await asyncio.to_thread(
        preflight_legacy_workspace, session_path, pwd_override
    )
    store = await open_owned_store(
        _open_store_with_migration, session_path, writer_lock=True
    )
    try:
        return await _resume_agent_from_open_store_async(
            store,
            session_path,
            pwd_override=pwd_override,
            io_mode=io_mode,
            llm=llm,
            input_module=input_module,
            output_module=output_module,
            mark_conversation_open=mark_conversation_open,
        )
    except BaseException:
        # Any post-open failure must release the writer lock before
        # propagating; close checkpoints WAL and must stay off the loop.
        try:
            await asyncio.to_thread(store.close, update_status=False)
        except Exception:
            logger.warning(
                "resume_agent_async: closing store after failed resume failed",
                exc_info=True,
            )
        raise
