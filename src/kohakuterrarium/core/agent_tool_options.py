"""Session-scoped runtime option overrides for ordinary local tools."""

import json
from typing import TYPE_CHECKING, Any

from kohakuterrarium.modules.tool.runtime_options import validate_tool_options
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.core.agent import Agent

logger = get_logger(__name__)

TOOL_OPTIONS_STATE_SUFFIX = "tool_options"


class ToolOptions:
    """Manage validated overrides while preserving each tool's config baseline."""

    def __init__(self, agent: "Agent") -> None:
        self._agent = agent
        self._values: dict[str, dict[str, Any]] = {}
        self._pending_values: dict[str, dict[str, Any]] = {}
        self._baselines: dict[str, dict[str, Any]] = {}

    def get(self, tool_name: str) -> dict[str, Any]:
        """Return effective schema values, including defaults and config baseline."""
        tool = self._tool(tool_name)
        schema = self._schema(tool)
        effective = {
            key: spec.get("default")
            for key, spec in schema.items()
            if isinstance(spec, dict) and "default" in spec
        }
        effective.update(self._baseline_values(tool_name, tool, schema))
        effective.update(self._values.get(tool_name, {}))
        return effective

    def list(self) -> dict[str, dict[str, Any]]:
        """Return effective values for tools that currently have overrides."""
        return {name: self.get(name) for name in self._values}

    def set(self, tool_name: str, values: dict[str, Any]) -> dict[str, Any]:
        """Patch runtime overrides, or reset them when ``values`` is empty."""
        tool = self._tool(tool_name)
        schema = self._schema(tool)
        baseline = self._baseline_values(tool_name, tool, schema)
        defaults = {
            key: spec.get("default")
            for key, spec in schema.items()
            if isinstance(spec, dict) and "default" in spec
        }
        base_effective = {**defaults, **baseline}
        if not values:
            self._values.pop(tool_name, None)
            self._pending_values.pop(tool_name, None)
            self._refresh(tool_name, tool, {})
            self._persist()
            return self.get(tool_name)

        candidate_overrides = dict(self._values.get(tool_name, {}))
        for key, value in values.items():
            if value is None:
                candidate_overrides.pop(key, None)
            else:
                candidate_overrides[key] = value
        candidate = {**base_effective, **candidate_overrides}
        cleaned = validate_tool_options(tool_name, candidate, schema)
        overrides = {
            key: value
            for key, value in cleaned.items()
            if key not in base_effective or value != base_effective[key]
        }
        if overrides:
            self._values[tool_name] = overrides
        else:
            self._values.pop(tool_name, None)
        self._pending_values.pop(tool_name, None)
        self._refresh(tool_name, tool, overrides)
        self._persist()
        return self.get(tool_name)

    def apply(self) -> None:
        """Load persisted overrides and refresh every still-registered tool."""
        data = self._load_private_state()
        previous_tools = set(self._values) | set(self._pending_values)
        self._values.clear()
        self._pending_values.clear()
        for tool_name in previous_tools:
            try:
                tool = self._tool(tool_name)
            except ValueError:
                continue
            self._refresh(tool_name, tool, {})
        if not isinstance(data, dict):
            self._persist()
            return
        for tool_name, values in dict(data).items():
            if not isinstance(values, dict):
                continue
            try:
                self.set(str(tool_name), values)
            except (KeyError, ValueError) as exc:
                try:
                    pending = self._validate_without_availability(
                        str(tool_name), values
                    )
                except (KeyError, ValueError):
                    pending = None
                if pending:
                    tool = self._tool(str(tool_name))
                    self._pending_values[str(tool_name)] = pending
                    self._refresh(str(tool_name), tool, {})
                    logger.warning(
                        "tool_options_unavailable_on_apply",
                        agent_name=getattr(self._agent.config, "name", ""),
                        tool_name=str(tool_name),
                        error=str(exc),
                    )
                    continue
                logger.warning(
                    "tool_options_invalid_on_apply",
                    agent_name=getattr(self._agent.config, "name", ""),
                    tool_name=str(tool_name),
                    error=str(exc),
                )
        self._persist()

    def _validate_without_availability(
        self, tool_name: str, values: dict[str, Any]
    ) -> dict[str, Any]:
        """Validate persisted intent while ignoring transient availability gates."""
        tool = self._tool(tool_name)
        schema = self._schema(tool)
        available_schema = {
            key: {
                option_key: option_value
                for option_key, option_value in (
                    spec.items() if isinstance(spec, dict) else []
                )
                if option_key != "disabled_values"
            }
            for key, spec in schema.items()
        }
        baseline = self._baseline_values(tool_name, tool, schema)
        defaults = {
            key: spec.get("default")
            for key, spec in schema.items()
            if isinstance(spec, dict) and "default" in spec
        }
        base_effective = {**defaults, **baseline}
        cleaned = validate_tool_options(
            tool_name, {**base_effective, **values}, available_schema
        )
        return {
            key: value
            for key, value in cleaned.items()
            if key not in base_effective or value != base_effective[key]
        }

    def _tool(self, tool_name: str) -> Any:
        registry = getattr(self._agent, "registry", None)
        tool = registry.get_tool(tool_name) if registry is not None else None
        if tool is None or getattr(tool, "is_provider_native", False):
            raise ValueError(f"Unknown configurable tool: {tool_name}")
        if not self._schema(tool):
            raise ValueError(f"Tool has no runtime options: {tool_name}")
        return tool

    @staticmethod
    def _schema(tool: Any) -> dict[str, dict[str, Any]]:
        schema_fn = getattr(tool, "runtime_option_schema", None)
        schema = schema_fn() if callable(schema_fn) else {}
        return schema if isinstance(schema, dict) else {}

    def _baseline_values(
        self,
        tool_name: str,
        tool: Any,
        schema: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        if tool_name not in self._baselines:
            config = getattr(tool, "config", None)
            self._baselines[tool_name] = dict(getattr(config, "extra", {}) or {})
        baseline = self._baselines[tool_name]
        return {key: baseline[key] for key in schema if key in baseline}

    def _refresh(self, tool_name: str, tool: Any, overrides: dict[str, Any]) -> None:
        config = getattr(tool, "config", None)
        if config is None:
            return
        baseline = dict(self._baselines.get(tool_name, {}))
        config.extra = {**baseline, **overrides}
        refresh = getattr(tool, "refresh_runtime_options", None)
        if callable(refresh):
            refresh(self.get(tool_name))

    def _state_key(self) -> str:
        return f"{self._agent.config.name}:{TOOL_OPTIONS_STATE_SUFFIX}"

    def _load_private_state(self) -> dict[str, Any]:
        store = getattr(self._agent, "session_store", None)
        if store is not None:
            try:
                raw = store.state.get(self._state_key())
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
        raw = extra.get(TOOL_OPTIONS_STATE_SUFFIX) if isinstance(extra, dict) else None
        return raw if isinstance(raw, dict) else {}

    def _persist(self) -> None:
        persisted = {**self._pending_values, **self._values}
        store = getattr(self._agent, "session_store", None)
        if store is not None:
            store.state[self._state_key()] = dict(persisted)
        session = getattr(self._agent, "session", None)
        extra = getattr(session, "extra", None) if session is not None else None
        if isinstance(extra, dict):
            if persisted:
                extra[TOOL_OPTIONS_STATE_SUFFIX] = dict(persisted)
            else:
                extra.pop(TOOL_OPTIONS_STATE_SUFFIX, None)


def agent_tool_inventory(agent: Any) -> list[dict[str, Any]]:
    """Return configurable ordinary tools in the shared module shape."""
    registry = getattr(agent, "registry", None)
    if registry is None:
        return []
    helper = getattr(agent, "tool_options", None)
    out: list[dict[str, Any]] = []
    for name in registry.list_tools():
        tool = registry.get_tool(name)
        if tool is None or getattr(tool, "is_provider_native", False):
            continue
        schema_fn = getattr(tool, "runtime_option_schema", None)
        try:
            schema = schema_fn() if callable(schema_fn) else {}
        except Exception:
            schema = {}
        if not schema:
            continue
        out.append(
            {
                "name": name,
                "description": getattr(tool, "description", "") or "",
                "option_schema": schema,
                "values": helper.get(name) if helper else {},
            }
        )
    out.sort(key=lambda entry: entry["name"])
    return out


def agent_get_tool_options(agent: Any) -> dict[str, dict[str, Any]]:
    """Return current effective values for overridden ordinary tools."""
    helper = getattr(agent, "tool_options", None)
    return helper.list() if helper is not None else {}


def agent_set_tool_options(
    agent: Any, tool: str, values: dict[str, Any]
) -> dict[str, Any]:
    """Apply ordinary-tool option values through the agent helper."""
    helper = getattr(agent, "tool_options", None)
    if helper is None:
        raise ValueError("agent has no tool_options helper")
    return helper.set(tool, values or {})
