"""Rebuild agents from session files and restore their persisted runtime state."""

import os
from pathlib import Path
from typing import Any, NamedTuple

from kohakuterrarium.builtins.inputs import create_builtin_input
from kohakuterrarium.builtins.outputs import create_builtin_output
from kohakuterrarium.core.agent import Agent
from kohakuterrarium.core.agent_compact import stage_resume_compact_state
from kohakuterrarium.core.agent_message_history import replay_with_settled_tools
from kohakuterrarium.core.agent_selection import restore_selections
from kohakuterrarium.core.branch_continuation import load_persisted_branch_view
from kohakuterrarium.core.config_serde import unpack_agent_config
from kohakuterrarium.core.conversation_elide import (
    elide_stale_tool_results,
    estimate_tokens,
)
from kohakuterrarium.errors import SessionNotResumableError
from kohakuterrarium.modules.input.base import InputModule
from kohakuterrarium.modules.output.base import OutputModule
from kohakuterrarium.packages.resolve import resolve_any_path
from kohakuterrarium.session.history import (
    dedupe_adjacent_duplicate_events,
    index_parent_paths,
    normalize_resumable_events,
    resolve_selected_branches,
)
from kohakuterrarium.session.resume_open import (
    # Re-exported for callers that still import it from this module.
    detect_session_type as detect_session_type,  # noqa: F401
    open_store_with_migration as _open_store_with_migration,
    preflight_legacy_workspace,
)
from kohakuterrarium.session.resume_branch import (
    backfill_turn_metadata,
    prepare_saved_snapshot,
    replayed_messages_for,
    snapshot_has_turn_metadata,
    snapshot_mismatches_branch,
    snapshot_needs_settled_tool_replay,
)
from kohakuterrarium.session.resume_build import (
    build_conversation as _build_conversation,
)
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

IO_MODES = ("cli", "plain", "tui")


def _mark_conversation_open(store: SessionStore) -> None:
    """Persist the UI lifecycle marker when the store supports it."""
    setter = getattr(store, "set_conversation_open", None)
    if callable(setter):
        setter(True)


def _create_io_modules(
    mode: str,
) -> tuple[InputModule, OutputModule]:
    """Create input and output modules for a given IO mode.

    Rich ``cli`` modules must be supplied by the caller because importing their
    higher-level dependencies here would create a package cycle.
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


_UNPREPARED = object()


class _LoadedConversation(NamedTuple):
    """Conversation messages and the source of a complete branch replay."""

    messages: list[dict] | None
    full_replay: bool = False
    branch_view: dict[int, int] | None = None


def _load_conversation_with_replay_fallback(
    store: SessionStore,
    agent_name: str,
    events: list[dict] | None = None,
    prepared: dict | None | object = _UNPREPARED,
) -> list[dict] | None:
    """Load a snapshot, append its tail, or fully replay a changed branch.

    Supplied ``events`` reuse the caller's raw read; omitted events are read here.
    """
    return _load_conversation_result(store, agent_name, events, prepared).messages


def _load_conversation_result(
    store: SessionStore,
    agent_name: str,
    events: list[dict] | None = None,
    prepared: dict | None | object = _UNPREPARED,
) -> _LoadedConversation:
    """Load messages with full-replay provenance for this restoration."""
    snapshot = store.load_conversation(agent_name)
    if events is None:
        events = store.get_events(agent_name)
    if prepared is _UNPREPARED:
        prepared = prepare_saved_snapshot(store, agent_name, events)
    if prepared:
        return _LoadedConversation(prepared["messages"])
    try:
        if store.state.get(f"{agent_name}:branch_snapshots"):
            branch_view = (
                load_persisted_branch_view(store, agent_name) if events else None
            )
            replayed = replayed_messages_for(
                store, agent_name, events, branch_view=branch_view
            )
            return _LoadedConversation(replayed, bool(replayed), branch_view)
    except (KeyError, TypeError, AttributeError):
        pass
    if not events:
        return _LoadedConversation(snapshot)
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
            if snapshot_has_turn_metadata(snapshot):
                return _LoadedConversation(snapshot)
            logger.info(
                "Legacy snapshot lacks turn metadata — backfill", agent=agent_name
            )
            return _LoadedConversation(backfill_turn_metadata(snapshot, events))
        # Compaction exists only in the snapshot, so replay just its normalized tail.
        tail = [
            evt
            for evt in events
            if isinstance(evt.get("event_id"), int) and evt["event_id"] > cached_up_to
        ]
        # New post-snapshot forks require full replay rather than tail appending.
        pre_pairs = {
            (evt.get("turn_index"), evt.get("branch_id"))
            for evt in events
            if isinstance(evt.get("event_id"), int) and evt["event_id"] <= cached_up_to
        }
        tail_has_forks = any(
            isinstance(evt.get("branch_id"), int)
            and evt["branch_id"] > 1
            and (evt.get("turn_index"), evt["branch_id"]) not in pre_pairs
            for evt in tail
        )
        if not tail_has_forks:
            appended = replay_with_settled_tools(
                tail,
                branch_view=load_persisted_branch_view(store, agent_name),
                include_metadata=True,
                resumable=True,
            )
            # Backfill legacy snapshot metadata from pre-watermark events only.
            base = snapshot
            if not snapshot_has_turn_metadata(snapshot):
                pre_events = [
                    evt
                    for evt in events
                    if isinstance(evt.get("event_id"), int)
                    and evt["event_id"] <= cached_up_to
                ]
                base = backfill_turn_metadata(snapshot, pre_events)
            logger.info(
                "Resume appended post-snapshot tail",
                agent=agent_name,
                snapshot_event_id=cached_up_to,
                last_event_id=last_event_id,
                appended=len(appended),
            )
            return _LoadedConversation(list(base) + appended)
        logger.info(
            "Post-snapshot tail contains branch forks — full replay",
            agent=agent_name,
            snapshot_event_id=cached_up_to,
        )
    if snapshot is not None and cached_up_to is None:
        if snapshot_has_turn_metadata(snapshot):
            return _LoadedConversation(snapshot)
        logger.info("Legacy snapshot lacks turn metadata — backfill", agent=agent_name)
        return _LoadedConversation(backfill_turn_metadata(snapshot, events))
    branch_view = load_persisted_branch_view(store, agent_name)
    replayed = replay_with_settled_tools(
        events, branch_view=branch_view, include_metadata=True, resumable=True
    )
    if replayed:
        logger.info(
            "Resume rebuilt conversation via replay",
            agent=agent_name,
            snapshot_event_id=cached_up_to,
            last_event_id=last_event_id,
            messages=len(replayed),
        )
        return _LoadedConversation(replayed, True, branch_view)
    return _LoadedConversation(snapshot)


def _restore_turn_branch_state(
    agent,
    store: SessionStore,
    agent_name: str,
    events: list[dict] | None = None,
) -> None:
    """Set turn / branch / parent-path state on the agent from saved events.

    Picks the latest live subtree on resume (parent path = the latest
    branch of every prior turn). This matches ``replay_conversation``
    default selection so the in-memory conversation, the saved
    snapshot, and the agent's branch counters all agree. ``events`` is
    the caller's shared raw read; omit it to read the store here.
    """
    if events is None:
        try:
            events = store.get_events(agent_name)
        except Exception as e:
            logger.warning(
                "Failed to read events for turn/branch restore",
                error=str(e),
                exc_info=True,
            )
            return
    # Use replay's path-aware selector so restored branch ancestry actually existed.
    events_list = list(events)
    parent_paths = index_parent_paths(events_list)
    persisted = load_persisted_branch_view(store, agent_name)
    selected = resolve_selected_branches(events_list, parent_paths, persisted)
    if not selected:
        return
    max_turn = max(selected.keys())
    agent._turn_index = max_turn
    agent._branch_id = selected[max_turn]
    agent._parent_branch_path = [
        (t, selected[t])
        for t in sorted(selected.keys())
        if 0 < t < max_turn and selected[t] > 0
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

    Rebuilding can generate a different runtime name, which would split reads and
    writes across namespaces. All subsystem name caches are aligned to the saved
    namespace.
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


def _apply_restore_elision(agent: Any) -> None:
    """Re-apply tool-result elision after restoring/rebuilding a conversation.

    Rebuilds restore tool outputs elided during live turns, so re-apply
    elision when the estimated prompt is already past the compact threshold
    (prevents the first resumed LLM call from overflowing). Elision is a
    compact companion: it only fires under real pressure.
    """
    controller = getattr(agent, "controller", None)
    if controller is None:
        return
    config = getattr(controller, "config", None)
    if config is None or not getattr(config, "elide_tool_results", False):
        return
    compact = getattr(agent, "compact_manager", None)
    compact_max = (
        compact.config.max_tokens
        if compact is not None
        and compact.config.enabled
        and getattr(compact.config, "max_tokens", 0)
        else 0
    )
    if compact_max and estimate_tokens(controller.conversation) >= int(
        compact_max * compact.config.threshold
    ):
        elide_stale_tool_results(controller.conversation)


def _reapply_options(agent: Any, agent_name: str, attribute: str) -> None:
    options = getattr(agent, attribute, None)
    if options is None:
        return
    try:
        options.apply()
    except Exception as exc:  # pragma: no cover - resume continues without options
        message = f"Failed to reapply {attribute.replace('_', ' ')}"
        logger.warning(message, agent=agent_name, error=str(exc))


def inject_saved_state(agent, store: SessionStore, agent_name: str) -> None:
    """Restore identity, conversation, branch state, scratchpad, and pending work."""
    align_agent_name(agent, agent_name)
    # One raw event read serves every restoration step.
    events = store.get_events(agent_name)
    prepared = prepare_saved_snapshot(store, agent_name, events)
    agent._context_cutoff_event_id = (
        prepared.get("context_cutoff_event_id", 0) if prepared is not None else 0
    )
    loaded = _load_conversation_result(store, agent_name, events, prepared)
    saved_messages = loaded.messages
    installed = None
    if saved_messages is not None:
        installed = _build_conversation(saved_messages)
        agent.controller.conversation = installed
        _apply_restore_elision(agent)
        logger.info(
            "Conversation restored", agent=agent_name, messages=len(saved_messages)
        )

    _restore_turn_branch_state(agent, store, agent_name, events)

    # Repair a mismatched snapshot or a missing completed ancestor tool result.
    persisted = load_persisted_branch_view(store, agent_name)
    if prepared is None and (
        snapshot_mismatches_branch(store, agent, agent_name)
        or snapshot_needs_settled_tool_replay(
            events, persisted, snapshot=saved_messages
        )
    ):
        replay_view = load_persisted_branch_view(store, agent_name) if events else None
        already_installed = (
            loaded.full_replay
            and agent.controller.conversation is installed
            and loaded.branch_view == replay_view
        )
        if not already_installed:
            logger.info("Replaying target branch for resume", agent=agent_name)
            replayed = replayed_messages_for(
                store, agent_name, events, branch_view=replay_view
            )
            if replayed:
                agent.controller.conversation = _build_conversation(replayed)
                _apply_restore_elision(agent)

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

    _reapply_options(agent, agent_name, "native_tool_options")
    _reapply_options(agent, agent_name, "tool_options")

    # Match SessionStore.get_resumable_events using the shared raw read.
    resume_events = normalize_resumable_events(dedupe_adjacent_duplicate_events(events))
    if resume_events:
        agent._pending_resume_events = resume_events
        logger.info("Resume events loaded", agent=agent_name, count=len(resume_events))

    saved_triggers = store.load_triggers(agent_name)
    if saved_triggers:
        agent._pending_resume_triggers = saved_triggers
        logger.info(
            "Resumable triggers loaded", agent=agent_name, count=len(saved_triggers)
        )

    # Restore persisted model/plugin selections before the store is attached.
    try:
        restore_selections(agent, store)
    except Exception:  # pragma: no cover - resume continues without selections
        logger.warning("selection restore failed", agent=agent_name, exc_info=True)

    stage_resume_compact_state(agent, store, agent_name, prepared)


def _rebuild_agent(
    *,
    config_path: str,
    config_snapshot: dict[str, Any],
    llm: Any,
    io_kwargs: dict[str, Any],
    pwd: str | None = None,
) -> Agent:
    """Build the ``Agent`` from saved meta.

    Prefer a resolvable config path, then fall back to the embedded snapshot for
    cross-node or inline-spawn sessions. Non-strict construction lets users open
    saved history even when the original model profile is unavailable.
    """
    if config_path:
        try:
            path_obj = resolve_any_path(config_path)
        except (FileNotFoundError, ValueError):
            path_obj = None
        if path_obj is not None and path_obj.exists():
            return Agent.from_path(
                str(path_obj), llm=llm, pwd=pwd, strict=False, **io_kwargs
            )
    if not config_snapshot:
        # Without a snapshot, callers must deploy the original config before retrying.
        raise FileNotFoundError(
            f"Agent config folder not found at {config_path!r} and the "
            "session has no config_snapshot to rebuild from"
        )
    cfg = unpack_agent_config(config_snapshot)
    return Agent(cfg, llm=llm, pwd=pwd, strict=False, **io_kwargs)


def resume_agent(
    session_path: str | Path,
    pwd_override: str | None = None,
    io_mode: str | None = None,
    llm: Any = None,
    *,
    input_module: InputModule | None = None,
    output_module: OutputModule | None = None,
    mark_conversation_open: bool = True,
) -> tuple[Agent, SessionStore]:
    """Resume a standalone agent and return it with its writable store.

    Explicit input or output modules override ``io_mode``. The caller owns the
    resumed agent loop and must close the returned store.
    """
    pwd_override = preflight_legacy_workspace(session_path, pwd_override)
    store = _open_store_with_migration(session_path, writer_lock=True)
    try:
        return _resume_agent_from_open_store(
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
        # Any post-open failure must release the writer lock before propagating.
        try:
            store.close(update_status=False)
        except Exception:
            logger.warning(
                "resume_agent: closing store after failed resume failed",
                exc_info=True,
            )
        raise


def _rebuild_resumed_agent(
    store: SessionStore,
    session_path: str | Path,
    *,
    pwd_override: str | None,
    io_mode: str | None,
    llm: Any,
    input_module: InputModule | None,
    output_module: OutputModule | None,
) -> tuple[Agent, str]:
    """Rebuild an agent and return its persisted namespace before state injection."""
    meta = store.load_meta()

    config_type = meta.get("config_type")
    if config_type not in (None, "", "agent"):
        raise ValueError(
            f"Session config_type is {config_type!r}, not 'agent'. "
            "Resume the saved file via "
            "`Terrarium.resume(path)` / `engine.adopt_session(path)` "
            "(see kohakuterrarium.terrarium.resume.resume_into_engine) "
            "which dispatches between the agent and terrarium rebuild "
            "paths."
        )

    config_path = meta.get("config_path", "")
    config_snapshot = meta.get("config_snapshot") or {}
    if not config_path and not config_snapshot:
        raise ValueError("Session has no config_path or config_snapshot in metadata")

    saved_pwd = meta.get("pwd")
    pwd = pwd_override or saved_pwd
    if not (pwd and os.path.isdir(pwd)):
        source = "override" if pwd_override else "saved"
        raise SessionNotResumableError(
            f"The {source} working directory is missing or invalid: {pwd!r}. "
            "Choose a replacement directory or open the session history."
        )

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

    effective_llm = llm
    if not effective_llm:
        try:
            effective_llm = store.state.get(
                f"{meta.get('agents', ['agent'])[0]}:llm_profile"
            )
        except (KeyError, Exception):
            pass

    agent = _rebuild_agent(
        config_path=config_path,
        config_snapshot=config_snapshot,
        llm=effective_llm,
        io_kwargs=io_kwargs,
        pwd=pwd,
    )
    agent_name = meta.get("agents", [agent.config.name])[0]
    return agent, agent_name


def _attach_resumed_agent(
    agent: Agent,
    store: SessionStore,
    session_path: str | Path,
    agent_name: str,
    *,
    mark_conversation_open: bool,
) -> tuple[Agent, SessionStore]:
    """Mark the conversation open and attach the store for continued turns."""
    if mark_conversation_open:
        _mark_conversation_open(store)
        store.update_status("running")
    agent.attach_session_store(store)

    logger.info("Agent resumed", agent=agent_name, session=str(session_path))
    return agent, store


def _resume_agent_from_open_store(
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
    """Rebuild and rehydrate an agent from an already-open session store."""
    agent, agent_name = _rebuild_resumed_agent(
        store,
        session_path,
        pwd_override=pwd_override,
        io_mode=io_mode,
        llm=llm,
        input_module=input_module,
        output_module=output_module,
    )
    inject_saved_state(agent, store, agent_name)

    return _attach_resumed_agent(
        agent,
        store,
        session_path,
        agent_name,
        mark_conversation_open=mark_conversation_open,
    )
