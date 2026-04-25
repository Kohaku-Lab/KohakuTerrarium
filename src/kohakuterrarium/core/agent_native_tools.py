"""Per-agent runtime overrides for provider-native tool options.

Composition helper attached to :class:`~kohakuterrarium.core.agent.Agent`
as ``agent.native_tool_options``. Kept as a standalone class (not a
mixin) so the Agent file size guard stays clean.

Policy:

* The override map ``{tool_name: {key: value}}`` lives on this helper.
* :meth:`set` updates the matching tool in ``agent.registry`` in place
  (via ``BaseTool.refresh_native_options``) so the next provider
  request picks up the change without rebuilding the agent.
* The map is persisted to the session scratchpad under the reserved
  key ``__native_tool_options__`` (JSON-encoded). Resume rebuilds the
  agent then calls :meth:`apply` after the scratchpad rehydrates.
"""

import json
from typing import TYPE_CHECKING, Any

from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.core.agent import Agent

logger = get_logger(__name__)

NATIVE_TOOL_OPTIONS_KEY = "__native_tool_options__"


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
        cleaned: dict[str, Any] = {
            k: v for k, v in (values or {}).items() if v not in (None, "")
        }
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
        scratchpad = self._scratchpad()
        if scratchpad is None:
            return
        raw = scratchpad.get(NATIVE_TOOL_OPTIONS_KEY)
        if not raw:
            return
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            logger.warning(
                "native_tool_options_parse_failed",
                agent_name=getattr(self._agent.config, "name", ""),
                raw=str(raw)[:120],
            )
            return
        if not isinstance(data, dict):
            return
        for tool_name, values in data.items():
            if not isinstance(values, dict):
                continue
            self._values[str(tool_name)] = dict(values)
            self._refresh_in_registry(str(tool_name), values)

    # ── Internals ───────────────────────────────────────────────

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

    def _persist(self) -> None:
        """Write the override map to scratchpad as JSON (or clear)."""
        scratchpad = self._scratchpad()
        if scratchpad is None:
            return
        if not self._values:
            scratchpad.delete(NATIVE_TOOL_OPTIONS_KEY)
            return
        scratchpad.set(
            NATIVE_TOOL_OPTIONS_KEY,
            json.dumps(self._values, ensure_ascii=False),
        )
