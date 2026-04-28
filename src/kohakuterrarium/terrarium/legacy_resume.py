"""Legacy ``kt resume <terrarium>`` flow — TerrariumRuntime-based.

This module hosts the legacy ``resume_terrarium`` function that
rebuilds a :class:`TerrariumRuntime` instance from a saved
``.kohakutr`` file.  It used to live in
:mod:`kohakuterrarium.session.resume`, but that home created a real
import cycle:

    session.resume -> terrarium.runtime
                   -> terrarium.__init__  (eager engine import)
                   -> terrarium.engine
                   -> terrarium.resume
                   -> session.resume      (mid-import — boom)

By relocating the only code path that touches :class:`TerrariumRuntime`
into ``terrarium/`` itself, ``session.resume`` can stop importing
the runtime altogether.  The new (engine-backed) terrarium-resume
path lives in :mod:`kohakuterrarium.terrarium.resume` and is used
by :func:`Terrarium.resume` / :func:`Terrarium.adopt_session`.

This legacy path is still wired up to ``kt resume`` because the
CLI's TUI flow drives the runtime directly.
"""

import os
from pathlib import Path

from kohakuterrarium.session.resume import (
    _load_conversation_with_replay_fallback,
    _open_store_with_migration,
)
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.terrarium.config import load_terrarium_config
from kohakuterrarium.terrarium.runtime import TerrariumRuntime
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def resume_terrarium(
    session_path: str | Path,
    pwd_override: str | None = None,
    io_mode: str | None = None,
) -> tuple[TerrariumRuntime, SessionStore]:
    """Resume a terrarium from a session file (legacy CLI flow).

    Args:
        session_path: Path to the session file.
        pwd_override: Override the working directory (uses saved pwd if None).
        io_mode: Override root agent input/output mode ("cli", "plain",
            "tui", or None for config default).

    Returns:
        (runtime, store) tuple. Caller should run runtime.run() then
        store.close(). The runtime will auto-inject conversations via
        attach_session_store.
    """
    store = _open_store_with_migration(session_path)
    meta = store.load_meta()

    if meta.get("config_type") != "terrarium":
        raise ValueError(
            f"Session is a {meta.get('config_type')}, not a terrarium. "
            "Use resume_agent() instead."
        )

    config_path = meta.get("config_path", "")
    if not config_path:
        raise ValueError("Session has no config_path in metadata")

    pwd = pwd_override or meta.get("pwd", ".")
    if pwd and os.path.isdir(pwd):
        os.chdir(pwd)

    config = load_terrarium_config(config_path)
    if io_mode and config.root:
        config.root.config_data["input"] = {
            "type": io_mode if io_mode != "cli" else "cli"
        }
        config.root.config_data["output"] = {
            "type": io_mode if io_mode != "cli" else "stdout",
            "controller_direct": True,
        }
    runtime = TerrariumRuntime(config)

    agents = meta.get("agents", [])
    resume_data: dict[str, dict] = {}
    resume_events: dict[str, list] = {}
    resume_triggers: dict[str, list] = {}
    for name in agents:
        resume_data[name] = {
            "conversation": _load_conversation_with_replay_fallback(store, name),
            "scratchpad": store.load_scratchpad(name),
        }
        events = store.get_resumable_events(name)
        if events:
            resume_events[name] = events
        triggers = store.load_triggers(name)
        if triggers:
            resume_triggers[name] = triggers

    runtime._pending_session_store = store
    runtime._pending_resume_data = resume_data
    runtime._pending_resume_triggers = resume_triggers
    runtime._pending_resume_events = resume_events

    store.update_status("running")

    logger.info(
        "Terrarium resume prepared",
        terrarium=config.name,
        agents=agents,
        session=str(session_path),
    )
    return runtime, store
