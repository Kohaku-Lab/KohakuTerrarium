"""Sub-agent LLM resolution.

Selectors may name a full profile or a raw model ID on the parent provider.
"""

from kohakuterrarium.errors import LLMNotConfiguredError
from kohakuterrarium.bootstrap.llm import _create_from_profile
from kohakuterrarium.llm.base import LLMProvider
from kohakuterrarium.llm.profiles import get_profile
from kohakuterrarium.utils.logging import get_logger
from kohakuterrarium.modules.subagent.config import SubAgentConfig

logger = get_logger(__name__)


# These aliases bypass provider validation because they denote inheritance.
_INHERIT_PARENT_SENTINELS: frozenset[str] = frozenset(
    {"subagent-default", "subagent_default", "default", "inherit", "parent"}
)


def resolve_subagent_llm(
    parent_llm: LLMProvider, config: SubAgentConfig
) -> LLMProvider:
    """Resolve a profile selector or apply a same-provider model override."""
    name = (config.model or "").strip()
    if not name or name.lower() in _INHERIT_PARENT_SENTINELS:
        return parent_llm

    # Direct lookup avoids warning for valid raw model IDs that are not profiles.
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
