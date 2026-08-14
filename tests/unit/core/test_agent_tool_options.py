"""Unit tests for :mod:`kohakuterrarium.core.agent_tool_options`."""

from types import SimpleNamespace

import pytest

from kohakuterrarium.core.agent_tool_options import (
    TOOL_OPTIONS_STATE_SUFFIX,
    ToolOptions,
)


class _Tool:
    is_provider_native = False
    description = "search"

    def __init__(self, *, disabled=False):
        self.config = SimpleNamespace(extra={"backend": "duckduckgo", "opaque": "keep"})
        self.disabled = disabled
        self.applied = None

    def runtime_option_schema(self):
        disabled = {"deepseek": "missing key"} if self.disabled else {}
        return {
            "backend": {
                "type": "enum",
                "values": ["duckduckgo", "deepseek"],
                "default": "duckduckgo",
                "disabled_values": disabled,
            },
            "fallback": {
                "type": "enum",
                "values": ["none", "duckduckgo"],
                "default": "none",
            },
        }

    def refresh_runtime_options(self, options):
        self.applied = dict(options)


class _Registry:
    def __init__(self, tool):
        self.tool = tool

    def get_tool(self, name):
        return self.tool if name == "web_search" else None


class _Store:
    def __init__(self):
        self.state = {}


def _agent(tool, store=None):
    return SimpleNamespace(
        config=SimpleNamespace(name="a"),
        registry=_Registry(tool),
        session=None,
        session_store=store,
    )


class TestToolOptions:
    def test_patch_persists_only_override_and_refreshes_effective_values(self):
        tool = _Tool()
        store = _Store()
        options = ToolOptions(_agent(tool, store))

        applied = options.set("web_search", {"backend": "deepseek"})

        assert applied == {"backend": "deepseek", "fallback": "none"}
        assert tool.applied == applied
        assert tool.config.extra == {
            "backend": "deepseek",
            "opaque": "keep",
        }
        saved = store.state[f"a:{TOOL_OPTIONS_STATE_SUFFIX}"]
        assert saved == {"web_search": {"backend": "deepseek"}}

    def test_reset_restores_yaml_baseline_and_preserves_non_schema_config(self):
        tool = _Tool()
        options = ToolOptions(_agent(tool))
        options.set("web_search", {"backend": "deepseek", "fallback": "duckduckgo"})

        applied = options.set("web_search", {})

        assert applied == {"backend": "duckduckgo", "fallback": "none"}
        assert tool.config.extra == {"backend": "duckduckgo", "opaque": "keep"}
        assert options.list() == {}

    def test_unavailable_value_is_rejected_without_changing_current_backend(self):
        tool = _Tool(disabled=True)
        options = ToolOptions(_agent(tool))

        with pytest.raises(ValueError, match="missing key"):
            options.set("web_search", {"backend": "deepseek"})

        assert options.get("web_search")["backend"] == "duckduckgo"
        assert tool.config.extra["backend"] == "duckduckgo"

    def test_apply_restores_session_override_on_new_tool_instance(self):
        store = _Store()
        first = ToolOptions(_agent(_Tool(), store))
        first.set("web_search", {"backend": "deepseek"})
        resumed_tool = _Tool()
        resumed = ToolOptions(_agent(resumed_tool, store))

        resumed.apply()

        assert resumed.get("web_search")["backend"] == "deepseek"
        assert resumed_tool.applied["backend"] == "deepseek"

    def test_apply_empty_session_clears_previous_session_override(self):
        tool = _Tool()
        store = _Store()
        options = ToolOptions(_agent(tool, store))
        options.set("web_search", {"backend": "deepseek"})
        store.state[f"a:{TOOL_OPTIONS_STATE_SUFFIX}"] = {}

        options.apply()

        assert options.get("web_search")["backend"] == "duckduckgo"
        assert tool.applied["backend"] == "duckduckgo"
        assert options.list() == {}

    def test_apply_preserves_unavailable_desired_override_without_activating_it(self):
        store = _Store()
        first = ToolOptions(_agent(_Tool(), store))
        first.set("web_search", {"backend": "deepseek"})
        resumed_tool = _Tool(disabled=True)
        resumed = ToolOptions(_agent(resumed_tool, store))

        resumed.apply()

        assert resumed.get("web_search")["backend"] == "duckduckgo"
        assert resumed_tool.applied["backend"] == "duckduckgo"
        saved = store.state[f"a:{TOOL_OPTIONS_STATE_SUFFIX}"]
        assert saved == {"web_search": {"backend": "deepseek"}}

        resumed_tool.disabled = False
        resumed.apply()

        assert resumed.get("web_search")["backend"] == "deepseek"
        assert resumed_tool.applied["backend"] == "deepseek"
