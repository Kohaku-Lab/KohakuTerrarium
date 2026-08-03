"""Agent LLM-profile switching + canonical-identifier helpers.

Provide model switching and canonical LLM identifiers for agents.
"""

from typing import Any

from kohakuterrarium.bootstrap.llm import create_llm_from_profile_name
from kohakuterrarium.core.agent_selection import persist_model_selection
from kohakuterrarium.llm.profiles import profile_to_identifier, resolve_controller_llm
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class AgentModelMixin:
    """Switch an agent's model and report its canonical identifier."""

    # These annotations describe state supplied by ``Agent.__init__``. A stub
    # for ``_build_compact_llm`` would shadow ``AgentCompactMixin`` through MRO.
    llm: Any
    controller: Any
    compact_manager: Any
    output_router: Any
    config: Any
    _llm_selector: str | None
    _llm_identifier: str

    def switch_model(self, profile_name: str) -> str:
        """Switch the LLM provider to a different model profile.

        Args:
            profile_name: Canonical selector — ``provider/name[@group=option,...]``.
                Bare names are also accepted but will fail on ambiguity
                if the name exists under multiple providers.

        Returns:
            The canonical ``provider/name[@variations]`` identifier —
            the same string the pickers emit, so a round-trip
            ``switch_model(picker_output) == picker_output`` is safe.
        """
        profile = resolve_controller_llm({}, llm=profile_name)
        if profile is None:
            raise ValueError(f"Model profile not found: {profile_name}")
        new_llm = create_llm_from_profile_name(profile_name)
        identifier = profile_to_identifier(profile)

        self._llm_selector = profile_name
        self._llm_identifier = identifier
        self.llm = new_llm
        self.controller.llm = new_llm
        # Fresh provider needs the same emergency-drop sync the boot
        # provider got — without it a drop after a model switch leaves
        # the controller holding the original oversized conversation.
        drop_sync = getattr(self, "_on_provider_emergency_drop", None)
        if drop_sync is not None and hasattr(new_llm, "on_emergency_drop"):
            new_llm.on_emergency_drop(drop_sync)
        # Sub-agents resolve their LLM from the manager's ``llm`` at spawn
        # time — ``resolve_llm(self.subagent_manager.llm, config)`` for
        # task sub-agents, and ``llm=self.llm`` directly for interactive
        # ones. Without this propagation a sub-agent dispatched AFTER a
        # model switch still runs on the model the agent booted with.
        subagent_manager = getattr(self, "subagent_manager", None)
        if subagent_manager is not None:
            subagent_manager.llm = new_llm
        if self.compact_manager:
            compact_llm = self._build_compact_llm(self.compact_manager.config)
            self.compact_manager._llm = compact_llm
            context_source = compact_llm if compact_llm is not self.llm else new_llm
            new_max = getattr(context_source, "_profile_max_context", 0)
            if new_max:
                self.compact_manager.config.max_tokens = new_max
            self._wire_overflow_rescue()

        model_name = getattr(new_llm, "model", profile_name)
        logger.info(
            "Model switched",
            agent_name=self.config.name,
            profile=profile_name,
            identifier=identifier,
            model=model_name,
        )

        # Persist the selection so a resume can restore it (best-effort;
        # a detached agent has no store and simply skips).
        try:
            persist_model_selection(self, profile_name)
        except Exception:  # pragma: no cover - persistence must not break switching
            logger.warning(
                "model selection persist failed",
                agent_name=self.config.name,
                exc_info=True,
            )

        # ``llm_name`` in the session_info metadata now carries the full
        # ``provider/name@variations`` form so every display surface can
        # show it verbatim.
        new_max = getattr(new_llm, "_profile_max_context", 0)
        compact_at = 0
        if self.compact_manager and new_max:
            compact_at = int(new_max * self.compact_manager.config.threshold)
        self.output_router.notify_activity(
            "session_info",
            f"Model switched to {identifier}",
            metadata={
                "model": model_name,
                "llm_name": identifier,
                "agent_name": self.config.name,
                "session_id": getattr(self, "_session_id", ""),
                "max_context": new_max,
                "compact_threshold": compact_at,
            },
        )
        return identifier

    def llm_identifier(self) -> str:
        """Return the canonical ``provider/name[@variations]`` for the
        currently-bound LLM profile.

        Populated on every :meth:`switch_model` call. On startup the
        agent goes through the bootstrap path instead, so first access
        resolves from config and caches the result — the banner,
        ``/model`` command, and frontend pill all see the identifier
        the user can paste back into ``/model``.
        """
        if self._llm_identifier:
            return self._llm_identifier
        controller_data: dict[str, Any] = {
            "llm": self._llm_selector or self.config.llm_profile or None,
            "model": self.config.model,
            "provider": self.config.provider,
            "variation_selections": dict(self.config.variation_selections or {}),
        }
        controller_data = {k: v for k, v in controller_data.items() if v}
        profile = resolve_controller_llm(controller_data)
        if profile is None:
            self._llm_identifier = getattr(self.llm, "model", "")
        else:
            self._llm_identifier = profile_to_identifier(profile)
        return self._llm_identifier
