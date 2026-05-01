"""Per-agent runtime overrides for plugin options.

Composition helper attached to :class:`~kohakuterrarium.core.agent.Agent`
as ``agent.plugin_options``. Mirrors :class:`NativeToolOptions` in shape:

* The override map ``{plugin_name: {option_key: value}}`` lives on this
  helper.
* :meth:`set` validates against the plugin's :meth:`option_schema`,
  applies via the plugin's :meth:`set_options` (which calls
  :meth:`refresh_options`), and persists.
* The map is persisted to private session state when a SessionStore is
  attached, and to ``session.extra`` for ephemeral runs.
* :meth:`apply` is called from session resume to rehydrate stored
  values into the live plugin instances.
"""

from typing import TYPE_CHECKING, Any

from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.core.agent import Agent

logger = get_logger(__name__)

PLUGIN_OPTIONS_STATE_SUFFIX = "plugin_options"


class PluginOptions:
    """Session-wise option-override controller for plugins."""

    def __init__(self, agent: "Agent") -> None:
        self._agent = agent
        self._values: dict[str, dict[str, Any]] = {}

    # ── Read ────────────────────────────────────────────────────

    def get(self, plugin_name: str) -> dict[str, Any]:
        """Return the current overrides for ``plugin_name`` (copy)."""
        return dict(self._values.get(plugin_name, {}))

    def list(self) -> dict[str, dict[str, Any]]:
        """Return a deep copy of every overridden plugin's options."""
        return {name: dict(opts) for name, opts in self._values.items()}

    # ── Mutate ──────────────────────────────────────────────────

    def set(self, plugin_name: str, values: dict[str, Any]) -> dict[str, Any]:
        """Apply option overrides to a plugin instance + persist.

        Returns the plugin's full post-merge options dict.
        Raises ``KeyError`` if the plugin is not registered.
        Raises ``ValueError`` (subclass ``PluginOptionError``) on
        invalid input — unknown keys, wrong types, out-of-range values.
        """
        manager = getattr(self._agent, "plugins", None)
        if manager is None:
            raise KeyError(plugin_name)
        applied = manager.set_plugin_options(plugin_name, values or {})
        if applied:
            self._values[plugin_name] = dict(applied)
        else:
            self._values.pop(plugin_name, None)
        self._persist()
        return applied

    def apply(self) -> None:
        """Pull stored overrides → live plugin instances.

        Called from ``session/resume.py`` after the plugin manager has
        loaded all plugins. Fresh agents with no stored state are a
        no-op. Invalid stored entries are logged and skipped — they
        don't block other plugins from rehydrating.
        """
        data = self._load_private_state()
        if not isinstance(data, dict):
            return
        manager = getattr(self._agent, "plugins", None)
        if manager is None:
            return
        for plugin_name, values in data.items():
            if not isinstance(values, dict) or not values:
                continue
            try:
                applied = manager.set_plugin_options(str(plugin_name), values)
            except KeyError:
                logger.debug(
                    "plugin_options apply skipped — plugin not registered",
                    agent=getattr(self._agent.config, "name", ""),
                    plugin_name=str(plugin_name),
                )
                continue
            except ValueError as exc:
                logger.warning(
                    "plugin_options apply invalid",
                    agent=getattr(self._agent.config, "name", ""),
                    plugin_name=str(plugin_name),
                    error=str(exc),
                )
                continue
            if applied:
                self._values[str(plugin_name)] = dict(applied)
        self._persist()

    # ── Internals ───────────────────────────────────────────────

    def _state_key(self) -> str:
        return f"{self._agent.config.name}:{PLUGIN_OPTIONS_STATE_SUFFIX}"

    def _load_private_state(self) -> dict[str, Any]:
        store = getattr(self._agent, "session_store", None)
        key = self._state_key()
        if store is not None:
            try:
                raw = store.state.get(key)
            except (KeyError, TypeError):
                raw = None
            if isinstance(raw, dict):
                return raw
        session = getattr(self._agent, "session", None)
        extra = getattr(session, "extra", None) if session is not None else None
        raw = (
            extra.get(PLUGIN_OPTIONS_STATE_SUFFIX) if isinstance(extra, dict) else None
        )
        return raw if isinstance(raw, dict) else {}

    def _persist(self) -> None:
        """Write the override map to private session state."""
        store = getattr(self._agent, "session_store", None)
        key = self._state_key()
        if store is not None:
            store.state[key] = dict(self._values)
        session = getattr(self._agent, "session", None)
        extra = getattr(session, "extra", None) if session is not None else None
        if isinstance(extra, dict):
            if self._values:
                extra[PLUGIN_OPTIONS_STATE_SUFFIX] = dict(self._values)
            else:
                extra.pop(PLUGIN_OPTIONS_STATE_SUFFIX, None)
