"""
Resume agents and terrariums from .kohakutr session files.

Rebuilds from config, injects saved conversation + scratchpad,
re-attaches session store for continued recording.
"""

import os
from pathlib import Path
from typing import Any

from kohakuterrarium.builtins.inputs import create_builtin_input
from kohakuterrarium.builtins.outputs import create_builtin_output
from kohakuterrarium.core.agent import Agent
from kohakuterrarium.core.conversation import Conversation
from kohakuterrarium.modules.input.base import InputModule
from kohakuterrarium.modules.output.base import OutputModule
from kohakuterrarium.session.history import replay_conversation
from kohakuterrarium.session.migrations import ensure_latest_version
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# Valid IO modes and their module types
IO_MODES = ("cli", "plain", "tui")


def _create_io_modules(
    mode: str,
) -> tuple[InputModule, OutputModule]:
    """Create input and output modules for a given IO mode.

    Returns (input_module, output_module).

    Note: ``cli`` mode is handled by the caller (``cli/resume.py``)
    because the rich CLI lives in the ``builtins.cli_rich`` tier, which
    this module cannot import without creating a cycle (``session/`` is
    below ``builtins/`` in the layering, and ``cli_rich`` reaches up
    into ``studio.identity``).  Pass ``input_module`` / ``output_module``
    keyword arguments to :func:`resume_agent` instead.
    """
    match mode:
        case "plain":
            return create_builtin_input("cli", {}), create_builtin_output("stdout", {})
        case "tui":
            return create_builtin_input("tui", {}), create_builtin_output("tui", {})
        case _:
            raise ValueError(
                f"Unknown IO mode: {mode}. Use one of {IO_MODES} "
                "(``cli`` mode must be constructed by the caller and "
                "passed via ``input_module`` / ``output_module``)."
            )


def _build_conversation(messages: list[dict]) -> Conversation:
    """Build a Conversation from a list of message dicts.

    Each dict has at minimum {role, content}. May also have
    tool_calls, tool_call_id, name, metadata.
    """
    conv = Conversation()
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        kwargs = {}
        if msg.get("tool_calls"):
            kwargs["tool_calls"] = msg["tool_calls"]
        if msg.get("tool_call_id"):
            kwargs["tool_call_id"] = msg["tool_call_id"]
        if msg.get("name"):
            kwargs["name"] = msg["name"]
        if msg.get("metadata"):
            kwargs["metadata"] = msg["metadata"]
        conv.append(role, content, **kwargs)
    return conv


def _load_conversation_with_replay_fallback(
    store: SessionStore, agent_name: str
) -> list[dict] | None:
    """Wave C: prefer the snapshot; replay the event log if it's stale.

    The runtime now keeps the live in-memory conversation snapshot fresh
    at processing end and after compaction. Replay remains the fallback
    for sessions whose saved snapshot is missing or older than the event
    stream.
    """
    snapshot = store.load_conversation(agent_name)
    events = store.get_events(agent_name)
    if not events:
        return snapshot
    last_event_id = 0
    for evt in events:
        eid = evt.get("event_id")
        if isinstance(eid, int) and eid > last_event_id:
            last_event_id = eid
    try:
        cached_up_to = store.state.get(f"{agent_name}:snapshot_event_id")
    except (KeyError, TypeError):
        cached_up_to = None
    if snapshot is not None and isinstance(cached_up_to, int):
        if cached_up_to >= last_event_id:
            return snapshot
    if snapshot is not None and cached_up_to is None:
        return snapshot
    replayed = replay_conversation(events)
    if replayed:
        logger.info(
            "Resume rebuilt conversation via replay",
            agent=agent_name,
            snapshot_event_id=cached_up_to,
            last_event_id=last_event_id,
            messages=len(replayed),
        )
        return replayed
    return snapshot


def _restore_turn_branch_state(agent, store: SessionStore, agent_name: str) -> None:
    """Set turn / branch / parent-path state on the agent from saved events.

    Picks the latest live subtree on resume (parent path = the latest
    branch of every prior turn). This matches ``replay_conversation``
    default selection so the in-memory conversation, the saved
    snapshot, and the agent's branch counters all agree.
    """
    try:
        events = store.get_events(agent_name)
    except Exception as e:
        logger.debug("Failed to read events for turn/branch restore", error=str(e))
        return
    # Walk events: track the most recent live branch of every turn so
    # we can derive both the leaf (turn, branch) and the parent path
    # leading to it.
    latest_by_turn: dict[int, int] = {}
    for evt in events:
        ti = evt.get("turn_index")
        bi = evt.get("branch_id")
        if not isinstance(ti, int) or not isinstance(bi, int):
            continue
        prev = latest_by_turn.get(ti, 0)
        if bi > prev:
            latest_by_turn[ti] = bi
    if not latest_by_turn:
        return
    max_turn = max(latest_by_turn.keys())
    agent._turn_index = max_turn
    agent._branch_id = latest_by_turn[max_turn]
    agent._parent_branch_path = [
        (t, latest_by_turn[t]) for t in sorted(latest_by_turn.keys()) if t < max_turn
    ]
    logger.debug(
        "Turn/branch state restored",
        agent=agent_name,
        turn_index=max_turn,
        branch_id=agent._branch_id,
        parent_path_len=len(agent._parent_branch_path),
    )


def align_agent_name(agent, agent_name: str) -> None:
    """Force ``agent`` to identify as ``agent_name`` after resume.

    All session-store keys are namespaced by the *runtime* agent name
    (e.g. ``crisp-willow:e:42``). When the agent was first started the
    name was a fresh random label; on resume :func:`Agent.from_path`
    rebuilds the agent from the config, which generates a *new* random
    label. Without re-aligning the name, the resumed agent looks up its
    history under one key and writes new events under another — every
    history endpoint then sees 0 events.

    Updates every cached copy of the name that the agent's subsystems
    keep, so subsequent lookups via ``creature.name`` /
    ``agent.config.name`` (used by the chat history route, channel
    routing, trigger ids, etc.) all converge on the saved name.
    """
    if getattr(agent, "config", None) is not None:
        agent.config.name = agent_name
    executor = getattr(agent, "executor", None)
    if executor is not None and hasattr(executor, "_agent_name"):
        executor._agent_name = agent_name
    trigger_manager = getattr(agent, "trigger_manager", None)
    if trigger_manager is not None and hasattr(trigger_manager, "_agent_name"):
        trigger_manager._agent_name = agent_name
    compact_manager = getattr(agent, "compact_manager", None)
    if compact_manager is not None and hasattr(compact_manager, "_agent_name"):
        compact_manager._agent_name = agent_name


def inject_saved_state(agent, store: SessionStore, agent_name: str) -> None:
    """Inject saved conversation, scratchpad, triggers, and resumable
    events from ``store`` into a freshly-rebuilt ``agent``.

    Shared by :func:`resume_agent` (low-tier, builds Agent from config)
    and ``studio.persistence.resume.resume_into_engine`` (Studio,
    builds Creature graph via the engine then injects per-creature).

    Also realigns ``agent.config.name`` (and the executor / trigger /
    compact-manager name caches) to ``agent_name`` so the rebuilt
    agent's *future* writes go to the same store key namespace as the
    saved events we're injecting now.
    """
    align_agent_name(agent, agent_name)
    saved_messages = _load_conversation_with_replay_fallback(store, agent_name)
    if saved_messages:
        agent.controller.conversation = _build_conversation(saved_messages)
        logger.info(
            "Conversation restored", agent=agent_name, messages=len(saved_messages)
        )

    _restore_turn_branch_state(agent, store, agent_name)

    pad_data = store.load_scratchpad(agent_name)
    if pad_data:
        legacy_native_options = pad_data.get("__native_tool_options__")
        if legacy_native_options:
            agent.session.scratchpad.set(
                "__native_tool_options__", legacy_native_options
            )
        visible_count = 0
        for k, v in pad_data.items():
            if k.startswith("__") and k.endswith("__"):
                continue
            agent.session.scratchpad.set(k, v)
            visible_count += 1
        logger.info("Scratchpad restored", agent=agent_name, keys=visible_count)

    native_tool_options = getattr(agent, "native_tool_options", None)
    if native_tool_options is not None:
        try:
            native_tool_options.apply()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "Failed to reapply native tool options",
                agent=agent_name,
                error=str(exc),
            )

    resume_events = store.get_resumable_events(agent_name)
    if resume_events:
        agent._pending_resume_events = resume_events
        logger.info("Resume events loaded", agent=agent_name, count=len(resume_events))

    saved_triggers = store.load_triggers(agent_name)
    if saved_triggers:
        agent._pending_resume_triggers = saved_triggers
        logger.info(
            "Resumable triggers loaded",
            agent=agent_name,
            count=len(saved_triggers),
        )


def _open_store_with_migration(session_path: str | Path) -> SessionStore:
    """Open a session file, auto-migrating older formats upward first.

    Wraps ``ensure_latest_version`` so resume transparently uses the
    newest readable version on disk. If migration raises, the error
    message carries the original v1 path so the user can re-run
    against the preserved file after fixing the cause.
    """
    try:
        resolved = ensure_latest_version(session_path)
    except Exception as exc:
        raise RuntimeError(
            f"Failed to migrate session at {session_path}: {exc}"
        ) from exc
    if str(resolved) != str(session_path):
        logger.info(
            "Session auto-migrated before resume",
            original=str(session_path),
            opened=str(resolved),
        )
    return SessionStore(resolved)


def resume_agent(
    session_path: str | Path,
    pwd_override: str | None = None,
    io_mode: str | None = None,
    llm_override: str | None = None,
    *,
    input_module: InputModule | None = None,
    output_module: OutputModule | None = None,
) -> tuple[Agent, SessionStore]:
    """Resume a standalone agent from a session file.

    Args:
        session_path: Path to the session file.
        pwd_override: Override the working directory (uses saved pwd if None).
        io_mode: Override input/output mode (``"plain"`` or ``"tui"``).
            Pass ``None`` to keep the config's defaults.  ``cli`` mode
            (the rich prompt_toolkit CLI) must be constructed by the
            caller — pass ``input_module`` / ``output_module`` directly.
        llm_override: Override LLM profile (from --llm flag or saved session).
        input_module: Pre-built input module (overrides ``io_mode``).
        output_module: Pre-built output module (overrides ``io_mode``).

    Returns:
        (agent, store) tuple. Caller should run agent.run() then store.close().
    """
    store = _open_store_with_migration(session_path)
    meta = store.load_meta()

    if meta.get("config_type") != "agent":
        raise ValueError(
            f"Session is a {meta.get('config_type')}, not an agent. "
            "Use the legacy resume_terrarium() in terrarium.legacy_resume "
            "instead."
        )

    config_path = meta.get("config_path", "")
    if not config_path:
        raise ValueError("Session has no config_path in metadata")

    pwd = pwd_override or meta.get("pwd", ".")
    if pwd and os.path.isdir(pwd):
        os.chdir(pwd)

    # IO module overrides — explicit instances win over io_mode shortcut.
    io_kwargs: dict[str, Any] = {}
    if input_module is not None or output_module is not None:
        if input_module is not None:
            io_kwargs["input_module"] = input_module
        if output_module is not None:
            io_kwargs["output_module"] = output_module
    elif io_mode:
        inp, out = _create_io_modules(io_mode)
        io_kwargs["input_module"] = inp
        io_kwargs["output_module"] = out

    # Restore LLM profile: CLI override > saved session > default
    effective_llm = llm_override
    if not effective_llm:
        try:
            effective_llm = store.state.get(
                f"{meta.get('agents', ['agent'])[0]}:llm_profile"
            )
        except (KeyError, Exception):
            pass

    # Rebuild agent from config
    agent = Agent.from_path(config_path, llm_override=effective_llm, **io_kwargs)
    agent_name = meta.get("agents", [agent.config.name])[0]

    # Inject every state slot from the store.
    inject_saved_state(agent, store, agent_name)

    # Re-attach session store for continued recording
    store.update_status("running")
    agent.attach_session_store(store)

    logger.info("Agent resumed", agent=agent_name, session=str(session_path))
    return agent, store


def detect_session_type(session_path: str | Path) -> str:
    """Detect whether a session file is an agent or terrarium.

    Returns "agent" or "terrarium". Resolves to the newest version on
    disk so a v1 file with an ``alice.kohakutr.v2`` neighbour reports
    the v2 file's type (they are guaranteed to match today, but the
    abstraction holds for future format changes too).
    """
    try:
        resolved = ensure_latest_version(session_path)
    except Exception:
        resolved = Path(session_path)
    store = SessionStore(resolved)
    try:
        meta = store.load_meta()
        return meta.get("config_type", "agent")
    finally:
        store.close()
