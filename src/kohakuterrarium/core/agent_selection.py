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
from typing import Any

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

MODEL_SELECTION_STATE_KEY = "model_selection"
PLUGIN_SELECTION_STATE_KEY = "plugin_selection"


def _store_of(agent: Any, store: Any | None = None) -> Any | None:
    """Resolve the session store: an explicit store wins, else the agent's.

    Resume calls restore before ``attach_session_store``, so callers must
    be able to pass the store being restored from explicitly.
    """
    if store is not None:
        return store
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


def load_model_selection(agent: Any, store: Any | None = None) -> str | None:
    store = _store_of(agent, store)
    key = _state_key(agent, MODEL_SELECTION_STATE_KEY)
    if store is None or key is None:
        return None
    raw = store.state.get(key)
    return str(raw) if raw else None


def load_plugin_selection(
    agent: Any, store: Any | None = None
) -> tuple[list[str], bool]:
    """Return ``(enabled_names, snapshot_ok)``.

    ``snapshot_ok`` is False when the snapshot is absent or malformed, so
    callers can keep config defaults instead of treating a corrupted key
    as an explicit empty set.
    """
    store = _store_of(agent, store)
    key = _state_key(agent, PLUGIN_SELECTION_STATE_KEY)
    if store is None or key is None or key not in store.state:
        return [], False
    raw = store.state.get(key)
    if raw is None or raw == "":
        # Corrupted/migrated blank values are treated like an absent
        # snapshot, not an explicit empty set — otherwise resume would
        # disable every plugin.
        return [], False
    try:
        parsed = json.loads(str(raw))
    except (TypeError, ValueError):
        return [], False
    if not isinstance(parsed, list):
        return [], False
    return [p for p in parsed if isinstance(p, str)], True


def restore_selections(agent: Any, store: Any | None = None) -> None:
    """Re-apply persisted model / plugin selections on resume.

    Called after the conversation, scratchpad, and native-tool-option
    state have been restored. ``store`` may be passed explicitly because
    resume calls this before ``attach_session_store``. Both restores are
    best-effort: ``switch_model`` validates the profile and plugin toggles
    tolerate names that no longer exist, so a stale selection degrades to
    the default instead of failing the resume.
    """
    selector = load_model_selection(agent, store)
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

    enabled, snapshot_ok = load_plugin_selection(agent, store)
    pm = getattr(agent, "plugins", None)
    if pm is None or not hasattr(pm, "enable") or not hasattr(pm, "disable"):
        return
    # Only align when a snapshot parses successfully: an empty set is
    # meaningful (the user disabled everything) but an absent or malformed
    # key means "never saved" and must leave the config defaults untouched.
    if not snapshot_ok:
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
