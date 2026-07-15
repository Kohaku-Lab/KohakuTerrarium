"""Agent compact-model helpers.

Agent compaction helpers for overflow recovery and dedicated LLM selection.
"""

import asyncio
from typing import Any

from kohakuterrarium.bootstrap.llm import create_llm_from_profile_name
from kohakuterrarium.core.compact import CompactConfig, CompactManager
from kohakuterrarium.llm.profiles import profile_to_identifier, resolve_controller_llm
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


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
        """Wait out an in-flight compact and retry with the spliced
        conversation instead of dropping tool data."""
        manager = getattr(self, "compact_manager", None)
        if manager is None or not manager.is_compacting:
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

        Falls back to the active provider only when a separate provider
        cannot be constructed.
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
        return self.llm


def restore_compact_state_from_session(
    manager: CompactManager, session_store: Any, agent_name: str
) -> None:
    """Restore the persisted compact count and cooldown timestamp.

    Restoring both values prevents a quick resume from bypassing the cooldown
    while retaining display continuity for the compact count.
    """
    state = getattr(session_store, "state", None)
    if state is None:
        return
    try:
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
