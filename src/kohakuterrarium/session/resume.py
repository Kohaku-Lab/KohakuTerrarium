"""Rebuild agents from session files and restore their persisted runtime state."""

import os
from pathlib import Path
from typing import Any

from kohakuterrarium.builtins.inputs import create_builtin_input
from kohakuterrarium.builtins.outputs import create_builtin_output
from kohakuterrarium.core.agent import Agent
from kohakuterrarium.core.agent_selection import restore_selections
from kohakuterrarium.core.config_serde import unpack_agent_config
from kohakuterrarium.core.conversation import Conversation
from kohakuterrarium.core.conversation_elide import (
    elide_stale_tool_results,
    estimate_tokens,
)
from kohakuterrarium.errors import SessionNotResumableError
from kohakuterrarium.modules.input.base import InputModule
from kohakuterrarium.modules.output.base import OutputModule
from kohakuterrarium.packages.resolve import resolve_any_path
from kohakuterrarium.session.history import (
    _index_parent_paths,
    _resolve_selected_branches,
    normalize_resumable_events,
    replay_conversation,
    select_live_event_ids,
)
from kohakuterrarium.session.migrations import (
    ensure_latest_version,
    latest_readable_version,
)
from kohakuterrarium.session.readonly import read_session_meta
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


def _build_conversation(messages: list[dict]) -> Conversation:
    """Build a conversation from persisted message dictionaries.

    Tool-call identifiers, names, and metadata are retained when present.
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
    # Preserve a trailing in-flight call while removing stale orphaned fragments.
    conv.prune_orphan_tool_pairs(preserve_pending_tail=True)
    return conv


def _snapshot_has_turn_metadata(snapshot: list[dict]) -> bool:
    """Return whether the snapshot carries user turn metadata.

    Edit/regenerate targeting resolves the edited turn through message
    metadata; legacy snapshots saved without it force content matching,
    which is ambiguous when a turn's wording repeats. Such snapshots are
    backfilled from the event log so targeting stays deterministic.
    """
    return any(
        m.get("role") == "user"
        and isinstance(m.get("metadata"), dict)
        and m["metadata"].get("turn_index") is not None
        for m in snapshot
    )


def _backfill_turn_metadata(snapshot: list[dict], events: list[dict]) -> list[dict]:
    """Backfill user-turn metadata onto a legacy metadata-less snapshot.

    The snapshot is the canonical persisted state (compaction exists only
    there), so it is trusted verbatim: a full replay would resurrect the
    pre-compact history and drop snapshot-only in-flight messages. Turn
    identity is recovered from the live ``user_message`` events so edit
    targeting stays deterministic.
    """
    live_ids = set(select_live_event_ids(events))
    meta_by_pos: list[dict] = []
    for evt in events:
        if evt.get("type") not in ("user_message", "user_input"):
            continue
        if isinstance(evt.get("event_id"), int) and evt["event_id"] not in live_ids:
            continue
        ti = evt.get("turn_index")
        bi = evt.get("branch_id")
        if isinstance(ti, int) and isinstance(bi, int):
            meta_by_pos.append({"turn_index": ti, "branch_id": bi})
    out: list[dict] = []
    user_pos = 0
    for msg in snapshot:
        m = dict(msg)
        if m.get("role") == "user" and user_pos < len(meta_by_pos):
            meta = dict(m.get("metadata") or {})
            meta.setdefault("turn_index", meta_by_pos[user_pos]["turn_index"])
            meta.setdefault("branch_id", meta_by_pos[user_pos]["branch_id"])
            m["metadata"] = meta
            user_pos += 1
        out.append(m)
    return out


def _load_conversation_with_replay_fallback(
    store: SessionStore, agent_name: str
) -> list[dict] | None:
    """Load the conversation snapshot and replay events when it is stale.

    Post-snapshot events are appended when branch ancestry is unchanged; new
    branch forks require a full replay to preserve coherent selection.
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
            if _snapshot_has_turn_metadata(snapshot):
                return snapshot
            logger.info(
                "Legacy snapshot lacks turn metadata — backfill",
                agent=agent_name,
            )
            return _backfill_turn_metadata(snapshot, events)
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
            logger.info(
                "Resume appended post-snapshot tail",
                agent=agent_name,
                snapshot_event_id=cached_up_to,
                last_event_id=last_event_id,
                appended=len(appended),
            )
            return list(snapshot) + appended
        logger.info(
            "Post-snapshot tail contains branch forks — full replay",
            agent=agent_name,
            snapshot_event_id=cached_up_to,
        )
    if snapshot is not None and cached_up_to is None:
        if _snapshot_has_turn_metadata(snapshot):
            return snapshot
        logger.info(
            "Legacy snapshot lacks turn metadata — backfill",
            agent=agent_name,
        )
        return _backfill_turn_metadata(snapshot, events)
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
        logger.warning(
            "Failed to read events for turn/branch restore",
            error=str(e),
            exc_info=True,
        )
        return
    # Use replay's path-aware selector so restored branch ancestry actually existed.
    events_list = list(events)
    parent_paths = _index_parent_paths(events_list)
    selected = _resolve_selected_branches(events_list, parent_paths, None)
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


def inject_saved_state(agent, store: SessionStore, agent_name: str) -> None:
    """Restore identity, conversation, branch state, scratchpad, and triggers.

    Future writes remain in the saved namespace, and interrupted events are
    queued for the rebuilt agent's resume flow.
    """
    align_agent_name(agent, agent_name)
    saved_messages = _load_conversation_with_replay_fallback(store, agent_name)
    if saved_messages:
        agent.controller.conversation = _build_conversation(saved_messages)
        # Rebuilds restore tool outputs elided during live turns, so re-apply
        # elision when the estimated prompt is already past the compact
        # threshold (prevents the first resumed LLM call from overflowing).
        # Elision is a compact companion: it only fires under real pressure.
        controller = agent.controller
        config = getattr(controller, "config", None)
        if config is not None and getattr(config, "elide_tool_results", False):
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
        except Exception as exc:  # pragma: no cover - resume continues without options
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
