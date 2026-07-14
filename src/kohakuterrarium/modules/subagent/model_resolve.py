"""Sub-agent LLM resolution.

A sub-agent's ``model`` may name a full profile (the same
``provider/name[@group=option,...]`` selector top-level agents switch
to) or a legacy same-provider raw model id. This module resolves the
former through the shared profile system — so a sub-agent can genuinely
run on a different provider/model than its parent — and keeps the raw
``with_model`` override as the fallback for the latter.
"""

from kohakuterrarium.errors import LLMNotConfiguredError
from kohakuterrarium.bootstrap.llm import _create_from_profile
from kohakuterrarium.llm.base import LLMProvider
from kohakuterrarium.llm.profiles import get_profile
from kohakuterrarium.utils.logging import get_logger
from kohakuterrarium.modules.subagent.config import SubAgentConfig

logger = get_logger(__name__)


# Sentinel model values that mean "use the parent's LLM, don't switch
# to a different model". These never reach profile resolution or the
# provider's ``with_model`` (which would treat them as literal model
# ids and trip provider-side validation).
_INHERIT_PARENT_SENTINELS: frozenset[str] = frozenset(
    {"subagent-default", "subagent_default", "default", "inherit", "parent"}
)


def resolve_subagent_llm(
    parent_llm: LLMProvider, config: SubAgentConfig
) -> LLMProvider:
    """Return the sub-agent's provider, resolving its ``model`` selector.

    Resolution order:

    * Empty / sentinel ``model`` — inherit the parent LLM unchanged.
    * A selector that resolves through the profile system — build that
      profile's provider (a real cross-provider switch, exactly like
      ``AgentModelMixin.switch_model``).
    * An unresolved ``@variations`` selector — raise
      :class:`LLMNotConfiguredError`. The ``@`` unambiguously marks a
      profile reference, so a miss is a real config error, never a
      silent inherit.
    * Any other unresolved value (a bare id or a raw ``provider/model``
      id, e.g. an OpenRouter / LiteLLM model) — the legacy same-provider
      override via ``parent_llm.with_model``; a failure there logs and
      inherits the parent so the sub-agent still runs.
    """
    name = (config.model or "").strip()
    if not name or name.lower() in _INHERIT_PARENT_SENTINELS:
        return parent_llm

    # Single, warning-free resolution: ``get_profile`` returns ``None`` for
    # an unknown selector without the ``resolve_controller_llm`` "profile
    # not found" log a legit raw model id would otherwise trip.
    profile = get_profile(name)
    if profile is not None:
        return _create_from_profile(profile)

    if "@" in name:
        raise LLMNotConfiguredError(
            f"Sub-agent {config.name!r} requested model {name!r}, which did not "
            "resolve to a known LLM profile."
        )

    try:
        return parent_llm.with_model(name)
    except Exception as exc:
        logger.warning(
            "Sub-agent raw model override failed; inheriting parent LLM",
            subagent_name=config.name,
            model=name,
            error=str(exc),
        )
        return parent_llm
