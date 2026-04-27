"""Agent compact-model helpers.

Split out of :mod:`agent` to keep the main orchestrator file below the
repository file-size guard while keeping compaction-specific LLM logic in one
place.
"""

from typing import Any

from kohakuterrarium.bootstrap.llm import create_llm_from_profile_name
from kohakuterrarium.core.compact import CompactConfig
from kohakuterrarium.llm.profiles import profile_to_identifier, resolve_controller_llm
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class AgentCompactMixin:
    """Mixin providing compact-LLM construction helpers."""

    llm: Any
    config: Any
    _llm_override: str | None

    def _build_compact_llm(self, compact_cfg: CompactConfig) -> Any:
        """Build an isolated LLM instance for compaction.

        Falls back to the active provider only when a separate provider
        cannot be constructed.
        """
        profile_name = (
            compact_cfg.compact_model or self._llm_override or self.config.llm_profile
        )
        if not profile_name:
            controller_data: dict[str, Any] = {
                "llm": self.config.llm_profile or None,
                "model": self.config.model or None,
                "provider": self.config.provider or None,
                "variation_selections": dict(self.config.variation_selections or {}),
            }
            controller_data = {k: v for k, v in controller_data.items() if v}
            profile = resolve_controller_llm(
                controller_data, llm_override=self._llm_override
            )
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
