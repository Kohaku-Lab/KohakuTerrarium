"""Choose the model passed to a spawned child creature."""

from collections.abc import Callable
from dataclasses import dataclass

from kohakuterrarium.core.config import AgentConfig
from kohakuterrarium.llm.preset_store import get_child_models
from kohakuterrarium.llm.profiles import resolve_controller_llm
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

_INHERIT_CALLER = frozenset({"default", "inherit", "parent"})
_USE_CHILD_TABLE = frozenset({"", "child-default", "child_default"})
_GLOBAL_CHILD_KEY = "default"


@dataclass(frozen=True)
class ChildModelChoice:
    """The selector and provenance for one spawned child."""

    selector: str
    source: str
    fallback_from: str = ""


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

    table_selector = _matching_selector(config, child_models)
    if table_selector:
        source = (
            "child_default"
            if table_selector[0] == _GLOBAL_CHILD_KEY
            else "child_models"
        )
        if resolves(table_selector[1]):
            return ChildModelChoice(table_selector[1], source)
        logger.warning(
            "Child model selector is unavailable; falling back",
            selector=table_selector[1],
            caller=caller or "",
        )
        choice = _caller_or_global(caller)
        return ChildModelChoice(choice.selector, choice.source, table_selector[1])

    configured = (config.llm_profile or config.model or "").strip()
    lowered = configured.lower()
    if lowered in _INHERIT_CALLER:
        return _caller_or_global(caller)
    if configured and lowered not in _USE_CHILD_TABLE:
        return ChildModelChoice("", "config")
    return _caller_or_global(caller)


def _matching_selector(
    config: AgentConfig, child_models: dict[str, str]
) -> tuple[str, str] | None:
    for ref in config.package_config_refs:
        selector = child_models.get(ref, "").strip()
        if selector:
            return ref, selector
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
    return resolve_controller_llm({}, llm=selector) is not None


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
        "llm": choice.selector,
        "llm_source": choice.source,
        "llm_fallback_from": choice.fallback_from,
    }
