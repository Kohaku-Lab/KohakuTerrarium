"""Choose the model passed to a spawned child creature."""

from collections.abc import Callable
from dataclasses import dataclass

from kohakuterrarium.core.config.types import AgentConfig
from kohakuterrarium.llm.presets.store import get_child_models
from kohakuterrarium.llm.profiles.resolve import resolve_controller_llm
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

_INHERIT_CALLER = frozenset({"default", "inherit", "parent"})
_USE_CHILD_TABLE = frozenset({"", "child-default", "child_default"})
_GLOBAL_CHILD_KEY = "default"


@dataclass(frozen=True)
class ChildModelChoice:
    """The requested selector, actual selector, and why it was chosen."""

    selector: str
    source: str
    requested: str = ""
    matched_key: str = ""
    fallback_from: str = ""
    fallback_reason: str = ""


def choose_child_model(
    config: AgentConfig,
    *,
    explicit: str | None,
    caller: str | None,
    child_models: dict[str, str],
    resolves: Callable[[str], bool],
) -> ChildModelChoice:
    """Apply child model precedence.

    The user table overrides a model written in the creature config. An
    explicit spawn argument still wins. An unresolvable table entry falls
    back to the caller, then to the global default, without failing the spawn.
    """
    requested = (explicit or "").strip()
    if requested:
        return ChildModelChoice(requested, "explicit")

    configured = (config.llm_profile or config.model or "").strip()
    concrete = _is_concrete(configured)
    table_selector = _matching_selector(config, child_models, allow_global=not concrete)
    if table_selector:
        key, selector = table_selector
        source = "child_default" if key == _GLOBAL_CHILD_KEY else "child_models"
        if resolves(selector):
            return ChildModelChoice(selector, source, selector, key)
        logger.warning(
            "Child model selector is unavailable; falling back",
            selector=selector,
            matched_key=key,
            caller=caller or "",
        )
        choice = _caller_or_global(caller)
        return ChildModelChoice(
            choice.selector,
            choice.source,
            selector,
            key,
            selector,
            "selector_unavailable",
        )

    lowered = configured.lower()
    if lowered in _INHERIT_CALLER:
        return _caller_or_global(caller)
    if configured and lowered not in _USE_CHILD_TABLE:
        return ChildModelChoice("", "config")
    return _caller_or_global(caller)


def _is_concrete(configured: str) -> bool:
    return (
        bool(configured)
        and configured.lower() not in _INHERIT_CALLER | _USE_CHILD_TABLE
    )


def _matching_selector(
    config: AgentConfig, child_models: dict[str, str], *, allow_global: bool
) -> tuple[str, str] | None:
    for ref in config.package_config_refs:
        selector = child_models.get(ref, "").strip()
        if selector:
            return ref, selector
    if not allow_global:
        return None
    selector = child_models.get(_GLOBAL_CHILD_KEY, "").strip()
    if selector:
        return _GLOBAL_CHILD_KEY, selector
    return None


def _caller_or_global(caller: str | None) -> ChildModelChoice:
    if caller:
        return ChildModelChoice(caller, "caller")
    return ChildModelChoice("", "default_model")


def selector_resolves(selector: str) -> bool:
    """Return whether a selector names a known profile."""
    try:
        return resolve_controller_llm({}, llm=selector) is not None
    except ValueError:
        return False


def resolve_spawn_model(
    config, explicit: str | None, caller: str | None
) -> ChildModelChoice:
    """Resolve a child model from the user table and an already loaded config."""
    return choose_child_model(
        config,
        explicit=explicit,
        caller=caller,
        child_models=get_child_models(),
        resolves=selector_resolves,
    )


def spawn_llm_args(
    choice: ChildModelChoice, caller: str | None
) -> dict[str, str | None]:
    """Translate a choice into the existing add_creature model arguments."""
    if choice.source == "config":
        return {"llm": None, "fallback_llm": caller}
    if choice.source == "default_model":
        return {"llm": None, "fallback_llm": None}
    return {"llm": choice.selector or None, "fallback_llm": caller}


def spawn_config(config_ref: str, loaded, choice: ChildModelChoice):
    """Pass a path unless a sentinel would be reloaded as a profile name."""
    if choice.source != "default_model":
        return config_ref
    loaded.llm_profile = ""
    loaded.model = ""
    return loaded


def model_payload(choice: ChildModelChoice) -> dict[str, str]:
    """Return the model provenance fields for a successful spawn."""
    return {
        "requested_llm": choice.requested,
        "llm": choice.selector,
        "llm_source": choice.source,
        "matched_key": choice.matched_key,
        "llm_fallback_from": choice.fallback_from,
        "fallback_reason": choice.fallback_reason,
    }


def preview_child_model(
    config: AgentConfig, *, explicit: str | None = None, caller: str | None = None
) -> dict[str, str]:
    """Return the model decision without spawning a creature."""
    return model_payload(resolve_spawn_model(config, explicit, caller))
