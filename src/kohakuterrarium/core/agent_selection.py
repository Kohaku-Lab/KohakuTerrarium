"""Persist runtime model / plugin selections so resume keeps them.

``switch_model`` and plugin toggles are pure in-memory operations today;
a resumed agent rebuilds its LLM and plugin manager from config and falls
back to the defaults. These helpers snapshot the selections into private
session state (the same ``store.state["{agent}:*"]`` surface used by
``NativeToolOptions``) and re-apply them during resume. Restores are
best-effort: an invalid or vanished profile/plugin name silently keeps
the default rather than failing the resume.
"""

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

MODEL_SELECTION_STATE_KEY = "model_selection"
PLUGIN_SELECTION_STATE_KEY = "plugin_selection"


def _store_of(agent: Any) -> Any | None:
    """Resolve the agent's session store, or ``None`` when detached."""
    return getattr(agent, "session_store", None)


def _state_key(agent: Any, suffix: str) -> str | None:
    config = getattr(agent, "config", None)
    name = getattr(config, "name", None)
    return f"{name}:{suffix}" if name else None


def persist_model_selection(agent: Any, selector: str) -> None:
    """Snapshot the model selector so resume can restore it."""
    store = _store_of(agent)
    key = _state_key(agent, MODEL_SELECTION_STATE_KEY)
    if store is None or key is None:
        return
    try:
        store.state[key] = str(selector)
    except Exception:  # pragma: no cover - persistence must never break a switch
        logger.warning("model selection persist skipped", exc_info=True)


def persist_plugin_selection(agent: Any, enabled_names: list[str]) -> None:
    """Snapshot the currently enabled plugin names."""
    store = _store_of(agent)
    key = _state_key(agent, PLUGIN_SELECTION_STATE_KEY)
    if store is None or key is None:
        return
    try:
        store.state[key] = json.dumps(sorted(enabled_names))
    except Exception:  # pragma: no cover - persistence must never break a toggle
        logger.warning("plugin selection persist skipped", exc_info=True)


def load_model_selection(agent: Any) -> str | None:
    store = _store_of(agent)
    key = _state_key(agent, MODEL_SELECTION_STATE_KEY)
    if store is None or key is None:
        return None
    raw = store.state.get(key)
    return str(raw) if raw else None


def load_plugin_selection(agent: Any) -> list[str]:
    store = _store_of(agent)
    key = _state_key(agent, PLUGIN_SELECTION_STATE_KEY)
    if store is None or key is None:
        return []
    raw = store.state.get(key)
    if not raw:
        return []
    try:
        parsed = json.loads(str(raw))
    except (TypeError, ValueError):
        return []
    return [p for p in parsed if isinstance(p, str)] if isinstance(parsed, list) else []


def restore_selections(agent: Any) -> None:
    """Re-apply persisted model / plugin selections on resume.

    Called after the conversation, scratchpad, and native-tool-option
    state have been restored. Both restores are best-effort:
    ``switch_model`` validates the profile and plugin toggles tolerate
    names that no longer exist, so a stale selection degrades to the
    default instead of failing the resume.
    """
    selector = load_model_selection(agent)
    if selector:
        switch = getattr(agent, "switch_model", None)
        if callable(switch):
            try:
                switch(selector)
            except Exception:
                logger.warning(
                    "stale model selection ignored",
                    selector=selector,
                    exc_info=True,
                )

    enabled = load_plugin_selection(agent)
    pm = getattr(agent, "plugins", None)
    if pm is None or not hasattr(pm, "enable") or not hasattr(pm, "disable"):
        return
    # Only align when a snapshot exists: an empty set is meaningful (the
    # user disabled everything) but an absent key means "never saved" and
    # must leave the config defaults untouched.
    store = _store_of(agent)
    key = _state_key(agent, PLUGIN_SELECTION_STATE_KEY)
    if store is None or key is None or key not in store.state:
        return
    # Align the manager to the persisted set: enable plugins that were on
    # and disable plugins the user had turned off (config defaults would
    # otherwise re-enable them on a fresh build).
    persisted = set(enabled)
    current = {p.get("name") for p in pm.list_plugins() if p.get("enabled")}
    for name in sorted(persisted - current):
        if pm.get_plugin(name) is None:
            continue
        try:
            pm.enable(name)
        except Exception:
            logger.warning(
                "stale plugin selection ignored",
                plugin_name=name,
                exc_info=True,
            )
    for name in sorted(current - persisted):
        if pm.get_plugin(name) is None:
            continue
        try:
            pm.disable(name)
        except Exception:
            logger.warning(
                "stale plugin selection ignored",
                plugin_name=name,
                exc_info=True,
            )
