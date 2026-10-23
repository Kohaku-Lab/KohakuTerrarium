"""Agent compact-model helpers.

Agent compaction helpers for overflow recovery and dedicated LLM selection.
"""

import asyncio
from dataclasses import dataclass
from typing import Any
from weakref import ReferenceType, ref

from kohakuterrarium.bootstrap.llm import create_llm_from_profile_name
from kohakuterrarium.core.agent_message_history import (
    replay_with_settled_tools,
    snapshot_needs_settled_tool_replay,
)
from kohakuterrarium.core.branch_continuation import (
    live_branch_path,
    load_persisted_branch_view,
)
from kohakuterrarium.core.compact import CompactConfig, CompactManager
from kohakuterrarium.core.snapshot_history import (
    prepare_snapshot,
    replayed_compact_state,
)
from kohakuterrarium.llm.base import fork_llm
from kohakuterrarium.llm.profiles import profile_to_identifier, resolve_controller_llm
from kohakuterrarium.session.snapshot import load_branch_snapshot
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class _ResumeCompactState:
    """Compact metadata for one restored conversation and manager lifetime."""

    store: ReferenceType
    namespace: str
    conversation: ReferenceType
    path: dict[int, int]
    persisted_view: dict[int, int] | None
    context_cutoff: int
    count: int
    cooldown: float
    manager: ReferenceType | None = None


def _matching_compact_snapshot(agent: Any, store: Any, namespace: str) -> dict | None:
    """Return the current checkpoint when its messages match the live prefix."""
    record = load_branch_snapshot(store, namespace, live_branch_path(agent))
    if record is None or record["watermark"] > store.max_event_id(namespace):
        return None
    messages = agent.controller.conversation.snapshot_messages(
        preserve_pending_tail=True
    )
    return record if record["messages"] == messages[: len(record["messages"])] else None


def stage_resume_compact_state(
    agent: Any, store: Any, namespace: str, prepared: dict | None
) -> None:
    """Stage selected compact metadata after installing the restored conversation."""
    agent._resume_compact_state = None
    conversation = agent.controller.conversation
    try:
        if prepared is not None:
            compact = prepared["compact_state"]
        elif f"{namespace}:branch_snapshots" in store.state:
            matched = _matching_compact_snapshot(agent, store, namespace)
            compact = (
                matched["compact_state"]
                if matched is not None
                else replayed_compact_state(
                    [
                        {"role": message.role, "content": message.content}
                        for message in conversation.get_messages()
                    ]
                )
            )
        else:
            compact = {
                "compact_count": store.state.get(f"{namespace}:compact_count", 0),
                "last_compact_time": store.state.get(
                    f"{namespace}:last_compact_time", 0.0
                ),
            }
        agent._resume_compact_state = _ResumeCompactState(
            ref(store),
            namespace,
            ref(conversation),
            live_branch_path(agent),
            load_persisted_branch_view(store, namespace),
            getattr(agent, "_context_cutoff_event_id", 0),
            int(compact["compact_count"]),
            float(compact["last_compact_time"]),
        )
    except (KeyError, TypeError, ValueError):
        return
    manager = getattr(agent, "compact_manager", None)
    if manager is not None:
        restore_resume_compact_state(manager, store, namespace)


def restore_resume_compact_state(
    manager: CompactManager, store: Any, namespace: str
) -> bool:
    """Seed a restored manager once, or retain its newer live compact state."""
    agent = getattr(manager, "_agent", None)
    saved = getattr(agent, "_resume_compact_state", None)
    if saved is None:
        return False
    source = saved.store()
    path = live_branch_path(agent)
    if (
        source is None
        or store is not None
        and source is not store
        or saved.namespace != namespace
        or saved.conversation() is not agent.controller.conversation
        or saved.persisted_view != load_persisted_branch_view(source, namespace)
        or saved.context_cutoff != getattr(agent, "_context_cutoff_event_id", 0)
        or any(path.get(turn) != branch for turn, branch in saved.path.items())
        or saved.manager is None
        and path != saved.path
        or saved.manager is not None
        and saved.manager() is not manager
    ):
        agent._resume_compact_state = None
        return False
    if saved.manager is None:
        manager._compact_count = saved.count
        manager._last_compact_time = saved.cooldown
        saved.manager = ref(manager)
    return True


class AgentCompactMixin:
    """Mixin providing compact-LLM construction helpers."""

    llm: Any
    config: Any
    _llm_selector: str | None

    def _wire_overflow_rescue(self) -> None:
        """Point the active provider's overflow hook at the compact manager.

        The rescue is best-effort: an injected provider only needs the
        ``LLMProvider`` protocol, which doesn't guarantee writable
        attributes (``__slots__`` classes reject the assignment)."""
        if getattr(self, "compact_manager", None) is None:
            return
        try:
            self.llm._overflow_rescue = self._compact_overflow_rescue
        except (AttributeError, TypeError):
            logger.debug(
                "Provider does not accept the overflow-rescue hook; skipping",
                provider=type(self.llm).__name__,
            )

    async def _compact_overflow_rescue(self) -> list[dict[str, Any]] | None:
        """Compact (starting a round if none is running) and retry with the
        spliced conversation instead of dropping tool data."""
        manager = getattr(self, "compact_manager", None)
        if manager is None:
            return None
        # An overflowing request carries no usage, so usage-driven auto-compact
        # never starts; the rescue itself must.
        if not manager.is_compacting:
            # Like auto-compact, the rescue honours compact.enabled=false.
            if not manager.config.enabled or not manager.trigger_compact():
                return None
        # When the compactor fell back to the ACTIVE provider, an
        # overflow inside the summarization call fires this hook from
        # within the compact task itself — waiting would deadlock on
        # our own task. Let the emergency drop handle it instead.
        if getattr(manager, "_compact_task", None) is asyncio.current_task():
            return None
        await manager.wait_for_current()
        controller = getattr(self, "controller", None)
        if controller is None:
            return None
        return controller.conversation.to_messages()

    def _build_compact_llm(self, compact_cfg: CompactConfig) -> Any:
        """Build an isolated LLM instance for compaction.

        Falls back to a fork of the active provider when a separate provider
        cannot be constructed, so compaction never shares its transport session.
        """
        profile_name = (
            compact_cfg.compact_model or self._llm_selector or self.config.llm_profile
        )
        if not profile_name:
            controller_data: dict[str, Any] = {
                "llm": self.config.llm_profile or None,
                "model": self.config.model or None,
                "provider": self.config.provider or None,
                "variation_selections": dict(self.config.variation_selections or {}),
            }
            controller_data = {k: v for k, v in controller_data.items() if v}
            profile = resolve_controller_llm(controller_data, llm=self._llm_selector)
            if profile is not None:
                profile_name = profile_to_identifier(profile)
        if profile_name:
            try:
                return create_llm_from_profile_name(profile_name)
            except Exception as e:
                logger.warning(
                    "Failed to build dedicated compact LLM; falling back to active provider",
                    agent_name=self.config.name,
                    profile=profile_name,
                    error=str(e),
                    exc_info=True,
                )
        return fork_llm(self.llm)


def restore_compact_state_from_session(
    manager: CompactManager, session_store: Any, agent_name: str
) -> None:
    """Restore the persisted compact count and cooldown timestamp.

    Restoring both values prevents a quick resume from bypassing the cooldown
    while retaining display continuity for the compact count.
    """
    if restore_resume_compact_state(manager, session_store, agent_name):
        return
    state = getattr(session_store, "state", None)
    if state is None:
        return
    try:
        if f"{agent_name}:branch_snapshots" in state:
            agent = manager._agent
            record = _matching_compact_snapshot(agent, session_store, agent_name)
            if record is None:
                record = prepare_snapshot(
                    session_store,
                    agent_name,
                    session_store.get_events(agent_name),
                    live_branch_path(agent),
                    replay=replay_with_settled_tools,
                    needs_replay=snapshot_needs_settled_tool_replay,
                )
            compact = (
                record["compact_state"]
                if record is not None
                else replayed_compact_state(
                    agent.controller.conversation.snapshot_messages(
                        preserve_pending_tail=True
                    )
                )
            )
            manager._compact_count = compact["compact_count"]
            manager._last_compact_time = compact["last_compact_time"]
            return
        saved_count = state.get(f"{agent_name}:compact_count")
        if saved_count is not None:
            manager._compact_count = int(saved_count)
            logger.info(
                "Compact count restored",
                compact_count=manager._compact_count,
            )
        saved_ts = state.get(f"{agent_name}:last_compact_time")
        if saved_ts is not None:
            manager._last_compact_time = float(saved_ts)
    except (KeyError, TypeError, ValueError):
        pass
