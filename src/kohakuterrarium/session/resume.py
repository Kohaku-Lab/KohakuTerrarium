"""Rebuild agents from session files and restore their persisted runtime state."""

import os
from pathlib import Path
from typing import Any

from kohakuterrarium.builtins.inputs import create_builtin_input
from kohakuterrarium.builtins.outputs import create_builtin_output
from kohakuterrarium.core.agent import Agent
from kohakuterrarium.core.agent_selection import restore_selections
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
    replay_conversation,
    resolve_selected_branches,
)
from kohakuterrarium.session.migrations import (
    ensure_latest_version,
    latest_readable_version,
)
from kohakuterrarium.session.readonly import read_session_meta
from kohakuterrarium.session.resume_branch import (
    backfill_turn_metadata,
    fresh_snapshot_watermark,
    replayed_messages_for,
    snapshot_has_turn_metadata,
    snapshot_mismatches_branch,
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


def _load_conversation_with_replay_fallback(
    store: SessionStore,
    agent_name: str,
    events: list[dict] | None = None,
) -> list[dict] | None:
    """Load the conversation snapshot and replay events when it is stale.

    Post-snapshot events are appended when branch ancestry is unchanged; new
    branch forks require a full replay to preserve coherent selection.
    ``events`` (when given) is the caller's single raw read of the event
    log, shared across the resume helpers so one scan serves them all.
    """
    snapshot = store.load_conversation(agent_name)
    if (
        events is None
        and fresh_snapshot_watermark(store, agent_name, snapshot) is not None
    ):
        # O(1) counter check: the event log holds nothing past the snapshot.
        return snapshot
    if events is None:
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
            if snapshot_has_turn_metadata(snapshot):
                return snapshot
            logger.info(
                "Legacy snapshot lacks turn metadata — backfill",
                agent=agent_name,
            )
            return backfill_turn_metadata(snapshot, events)
        # Compaction exists only in the snapshot, so replay just its normalized tail.
        tail = [
            evt
            for evt in events
            if isinstance(evt.get("event_id"), int) and evt["event_id"] > cached_up_to
        ]
        # A new post-snapshot branch can supersede earlier turns; appending it to
        # the snapshot would retain incompatible history.
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
            appended = replay_conversation(
                normalize_resumable_events(tail), include_metadata=True
            )
            # A legacy snapshot portion must be backfilled too, otherwise the
            # resumed conversation becomes a mix of metadata-less (snapshot)
            # and metadata-bearing (tail) user messages. Backfill uses only
            # events up to the snapshot watermark so the tail's user turns do
            # not shift the mapping.
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
            return list(base) + appended
        logger.info(
            "Post-snapshot tail contains branch forks — full replay",
            agent=agent_name,
            snapshot_event_id=cached_up_to,
        )
    if snapshot is not None and cached_up_to is None:
        if snapshot_has_turn_metadata(snapshot):
            return snapshot
        logger.info(
            "Legacy snapshot lacks turn metadata — backfill",
            agent=agent_name,
        )
        return backfill_turn_metadata(snapshot, events)
    replayed = replay_conversation(
        normalize_resumable_events(events), include_metadata=True
    )
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
    selected = resolve_selected_branches(events_list, parent_paths, None)
    if not selected:
        return
    max_turn = max(selected.keys())
    agent._turn_index = max_turn
    agent._branch_id = selected[max_turn]
    agent._parent_branch_path = [
        (t, selected[t]) for t in sorted(selected.keys()) if t < max_turn
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
    """Restore identity, conversation, branch state, scratchpad, and triggers.

    Future writes remain in the saved namespace, and interrupted events are
    queued for the rebuilt agent's resume flow.
    """
    align_agent_name(agent, agent_name)
    # ONE raw read of the event log feeds every consumer below (conversation
    # replay, branch restore, mismatch replay) — not the old 3-4 scans.
    events = store.get_events(agent_name)
    saved_messages = _load_conversation_with_replay_fallback(store, agent_name, events)
    if saved_messages:
        agent.controller.conversation = _build_conversation(saved_messages)
        _apply_restore_elision(agent)
        logger.info(
            "Conversation restored", agent=agent_name, messages=len(saved_messages)
        )

    _restore_turn_branch_state(agent, store, agent_name, events)

    # A snapshot saved on a DIFFERENT branch (a sibling path) is stale for
    # the restored target branch: discard it and rebuild via the branch-aware
    # replay so the restored conversation matches the branch the agent is on.
    if snapshot_mismatches_branch(store, agent, agent_name):
        logger.info(
            "Snapshot belongs to another branch — replaying target branch",
            agent=agent_name,
        )
        replayed = replayed_messages_for(store, agent_name, events)
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

    # Same dedupe→normalize pipeline as SessionStore.get_resumable_events,
    # computed from the shared read instead of a second full table scan.
    resume_events = normalize_resumable_events(dedupe_adjacent_duplicate_events(events))
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

    # Re-apply the persisted model / plugin selections (best-effort;
    # stale profiles/plugins degrade to the config defaults). The store
    # is passed explicitly because attach_session_store runs after this.
    try:
        restore_selections(agent, store)
    except Exception:  # pragma: no cover - resume continues without selections
        logger.warning(
            "selection restore failed",
            agent=agent_name,
            exc_info=True,
        )


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


def _open_store_with_migration(
    session_path: str | Path, *, writer_lock: bool = False
) -> SessionStore:
    """Open a session file, auto-migrating older formats upward first.

    Migration resolves the newest readable file while preserving the original
    path in failures. Live resumes may request a writer lock; preview consumers
    remain lock-free.
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
    return SessionStore(resolved, writer_lock=writer_lock)


def preflight_legacy_workspace(
    session_path: str | Path,
    pwd_override: str | None = None,
) -> str:
    """Resolve a legacy workspace without migration or writer acquisition."""
    path = latest_readable_version(session_path)
    meta = read_session_meta(path)
    dirty_state = meta.get("workspace_resume_state")
    if isinstance(dirty_state, dict) and dirty_state.get("status") == "partial_dirty":
        raise SessionNotResumableError(
            "Session has an incomplete workspace rollback and must be repaired"
        )
    saved_pwd = meta.get("pwd")
    pwd = pwd_override or saved_pwd
    if not (pwd and os.path.isdir(pwd)):
        source = "override" if pwd_override else "saved"
        raise SessionNotResumableError(
            f"The {source} working directory is missing or invalid: {pwd!r}. "
            "Choose a replacement directory or open the session history."
        )
    return str(Path(pwd).resolve())


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
    meta = store.load_meta()

    # Missing type metadata follows detection's agent default for partial mirrors.
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

    # Pass workspace explicitly; process-wide directory changes race other sessions.
    saved_pwd = meta.get("pwd")
    pwd = pwd_override or saved_pwd
    if not (pwd and os.path.isdir(pwd)):
        source = "override" if pwd_override else "saved"
        raise SessionNotResumableError(
            f"The {source} working directory is missing or invalid: {pwd!r}. "
            "Choose a replacement directory or open the session history."
        )

    # Explicit module instances take precedence over the mode shortcut.
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

    # Resolution order is caller override, saved profile, then provider default.
    effective_llm = llm
    if not effective_llm:
        try:
            effective_llm = store.state.get(
                f"{meta.get('agents', ['agent'])[0]}:llm_profile"
            )
        except (KeyError, Exception):
            pass

    # Embedded snapshots support inline-spawn and cross-node resume.
    agent = _rebuild_agent(
        config_path=config_path,
        config_snapshot=config_snapshot,
        llm=effective_llm,
        io_kwargs=io_kwargs,
        pwd=pwd,
    )
    agent_name = meta.get("agents", [agent.config.name])[0]

    inject_saved_state(agent, store, agent_name)

    # Continued turns append to the same session file.
    if mark_conversation_open:
        _mark_conversation_open(store)
        store.update_status("running")
    agent.attach_session_store(store)

    logger.info("Agent resumed", agent=agent_name, session=str(session_path))
    return agent, store


def detect_session_type(session_path: str | Path) -> str:
    """Detect whether a session file is an agent or terrarium.

    Resolve migrations first so detection reflects the newest readable file.
    Missing type metadata defaults to ``"agent"``.
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
        store.close(update_status=False)
