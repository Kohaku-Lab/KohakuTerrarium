"""Unit tests for the runtime module command's ordinary-tool path."""

from types import SimpleNamespace

from kohakuterrarium.builtins.user_commands import module as module_command


class _Tool:
    is_provider_native = False
    description = "search"

    def runtime_option_schema(self):
        return {
            "backend": {
                "type": "enum",
                "values": ["duckduckgo", "deepseek"],
                "default": "duckduckgo",
                "disabled_values": {"deepseek": "configure key"},
            }
        }


class _Registry:
    def list_tools(self):
        return ["web_search"]

    def get_tool(self, name):
        return _Tool() if name == "web_search" else None


class _Options:
    def __init__(self):
        self.values = {"backend": "duckduckgo"}

    def get(self, name):
        return dict(self.values)

    def set(self, name, values):
        if values:
            self.values.update(values)
        else:
            self.values = {"backend": "duckduckgo"}
        return dict(self.values)


def _agent():
    return SimpleNamespace(
        plugins=None,
        registry=_Registry(),
        native_tool_options=None,
        tool_options=_Options(),
    )


class TestOrdinaryToolModuleCommand:
    def test_inventory_and_show_include_tool_and_unavailable_reason(self):
        agent = _agent()
        module = module_command._inventory(agent)[0]

        assert module["type"] == "tool"
        rendered = module_command._render_show_module(module)
        assert "tool/web_search" in rendered
        assert "deepseek — configure key" in rendered

    def test_set_and_reset_route_through_tool_options(self):
        agent = _agent()

        changed = module_command._do_set(agent, ["web_search", "backend", "deepseek"])
        reset = module_command._do_reset(agent, ["web_search"])

        assert "deepseek" in changed.output
        assert "tool/web_search" in reset.output
        assert agent.tool_options.values == {"backend": "duckduckgo"}
