"""Per-agent runtime overrides for provider-native tool options.

Composition helper attached to :class:`~kohakuterrarium.core.agent.Agent`
as ``agent.native_tool_options``. Kept as a standalone class (not a
mixin) so the Agent file size guard stays clean.

Policy:

* The override map ``{tool_name: {key: value}}`` lives on this helper.
* :meth:`set` updates the matching tool in ``agent.registry`` in place
  (via ``BaseTool.refresh_native_options``) so the next provider
  request picks up the change without rebuilding the agent.
* The map is persisted to private session state when a SessionStore is
  attached, and to ``session.extra`` for ephemeral runs. Legacy
  scratchpad-backed values are migrated on apply.
"""

import json
from typing import TYPE_CHECKING, Any

from kohakuterrarium.core.native_tool_validation import validate_native_tool_options
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.core.agent import Agent

logger = get_logger(__name__)

NATIVE_TOOL_OPTIONS_KEY = "__native_tool_options__"
NATIVE_TOOL_OPTIONS_STATE_SUFFIX = "native_tool_options"


class NativeToolOptions:
    """Session-wise option-override controller for provider-native tools."""

    def __init__(self, agent: "Agent") -> None:
        self._agent = agent
        self._values: dict[str, dict[str, Any]] = {}

    # ── Read ────────────────────────────────────────────────────

    def get(self, tool_name: str) -> dict[str, Any]:
        """Return the current overrides for ``tool_name`` (copy)."""
        return dict(self._values.get(tool_name, {}))

    def list(self) -> dict[str, dict[str, Any]]:
        """Return a deep copy of every overridden tool's options."""
        return {tool: dict(opts) for tool, opts in self._values.items()}

    # ── Mutate ──────────────────────────────────────────────────

    def set(self, tool_name: str, values: dict[str, Any]) -> dict[str, Any]:
        """Replace the override dict for one provider-native tool.

        Empty / falsy values clear the override (tool reverts to its
        constructor defaults). Returns the cleaned dict that was
        applied (after dropping empty entries).
        """
        cleaned = self._validate(tool_name, values or {})
        if cleaned:
            self._values[tool_name] = cleaned
        else:
            self._values.pop(tool_name, None)
        self._refresh_in_registry(tool_name, cleaned)
        self._persist()
        return cleaned

    def apply(self) -> None:
        """Pull options from scratchpad → in-memory map + tool registry.

        Called from ``session/resume.py`` after scratchpad rehydrate.
        Fresh agents with no scratchpad are a no-op.
        """
        data = self._load_private_state()
        legacy = self._load_legacy_scratchpad()
        if legacy:
            data.update(legacy)
            scratchpad = self._scratchpad()
            if scratchpad is not None:
                scratchpad.delete(NATIVE_TOOL_OPTIONS_KEY)
        if not isinstance(data, dict):
            return
        for tool_name, values in data.items():
            if not isinstance(values, dict):
                continue
            try:
                cleaned = self._validate(str(tool_name), values)
            except ValueError as exc:
                logger.warning(
                    "native_tool_options_invalid_on_apply",
                    agent_name=getattr(self._agent.config, "name", ""),
                    tool_name=str(tool_name),
                    error=str(exc),
                )
                continue
            if not cleaned:
                continue
            self._values[str(tool_name)] = cleaned
            self._refresh_in_registry(str(tool_name), cleaned)
        self._persist()

    # ── Internals ───────────────────────────────────────────────

    def _validate(self, tool_name: str, values: dict[str, Any]) -> dict[str, Any]:
        registry = getattr(self._agent, "registry", None)
        tool = registry.get_tool(tool_name) if registry is not None else None
        if tool is None or not getattr(tool, "is_provider_native", False):
            raise ValueError(f"Unknown provider-native tool: {tool_name}")
        schema_fn = getattr(type(tool), "provider_native_option_schema", None)
        schema = schema_fn() if callable(schema_fn) else {}
        return validate_native_tool_options(tool_name, values or {}, schema or {})

    def _scratchpad(self) -> Any:
        """Resolve the session scratchpad, or ``None`` when not attached."""
        agent = self._agent
        session = getattr(agent, "_explicit_session", None) or getattr(
            agent, "session", None
        )
        return getattr(session, "scratchpad", None) if session else None

    def _refresh_in_registry(self, tool_name: str, values: dict[str, Any]) -> None:
        """Update the tool's ``ToolConfig.extra`` and re-resolve fields."""
        registry = getattr(self._agent, "registry", None)
        if registry is None:
            return
        tool = registry.get_tool(tool_name)
        if tool is None or not getattr(tool, "is_provider_native", False):
            return
        cfg = getattr(tool, "config", None)
        if cfg is None:
            return
        cfg.extra = dict(values or {})
        refresh = getattr(tool, "refresh_native_options", None)
        if callable(refresh):
            refresh()

    def _state_key(self) -> str:
        return f"{self._agent.config.name}:{NATIVE_TOOL_OPTIONS_STATE_SUFFIX}"

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
            if isinstance(raw, str):
                try:
                    parsed = json.loads(raw)
                except ValueError:
                    return {}
                return parsed if isinstance(parsed, dict) else {}
        session = getattr(self._agent, "session", None)
        extra = getattr(session, "extra", None) if session is not None else None
        raw = (
            extra.get(NATIVE_TOOL_OPTIONS_STATE_SUFFIX)
            if isinstance(extra, dict)
            else None
        )
        return raw if isinstance(raw, dict) else {}

    def _load_legacy_scratchpad(self) -> dict[str, Any]:
        scratchpad = self._scratchpad()
        if scratchpad is None:
            return {}
        raw = scratchpad.get(NATIVE_TOOL_OPTIONS_KEY)
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            logger.warning(
                "native_tool_options_parse_failed",
                agent_name=getattr(self._agent.config, "name", ""),
                raw=str(raw)[:120],
            )
            return {}
        return data if isinstance(data, dict) else {}

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
                extra[NATIVE_TOOL_OPTIONS_STATE_SUFFIX] = dict(self._values)
            else:
                extra.pop(NATIVE_TOOL_OPTIONS_STATE_SUFFIX, None)
        scratchpad = self._scratchpad()
        if scratchpad is not None:
            scratchpad.delete(NATIVE_TOOL_OPTIONS_KEY)
