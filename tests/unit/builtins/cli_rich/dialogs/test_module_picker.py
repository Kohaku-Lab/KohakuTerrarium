"""Unit tests for ordinary tools in the Rich module picker."""

from types import SimpleNamespace

from kohakuterrarium.builtins.cli_rich.dialogs.module_picker import ModulePicker


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
        return _Tool()


class _Options:
    def get(self, name):
        return {"backend": "duckduckgo"}

    def set(self, name, values):
        return values


class TestModulePickerToolTab:
    def test_opens_tool_form_and_excludes_unavailable_enum_value(self):
        agent = SimpleNamespace(
            plugins=None,
            registry=_Registry(),
            native_tool_options=None,
            tool_options=_Options(),
        )
        picker = ModulePicker(lambda: agent)

        picker.open(edit_target="tool/web_search")

        assert picker.active_type == "tool"
        assert picker._form is not None
        field = picker._form.fields[0]
        assert field.options == ["duckduckgo"]
        assert field.unavailable == {"deepseek": "configure key"}
