"""Unit tests for ordinary-tool runtime option validation."""

import pytest

from kohakuterrarium.modules.tool.runtime_options import (
    ToolOptionError,
    validate_tool_options,
)


class TestValidateToolOptions:
    schema = {
        "backend": {
            "type": "enum",
            "values": ["duckduckgo", "deepseek"],
            "disabled_values": {"deepseek": "configure the DeepSeek key"},
        },
        "limit": {"type": "int", "min": 1, "max": 10},
        "enabled": {"type": "bool"},
    }

    def test_coerces_supported_values(self):
        assert validate_tool_options(
            "search",
            {"backend": "duckduckgo", "limit": "3", "enabled": "yes"},
            self.schema,
        ) == {"backend": "duckduckgo", "limit": 3, "enabled": True}

    def test_rejects_disabled_enum_with_actionable_reason(self):
        with pytest.raises(ToolOptionError, match="configure the DeepSeek key"):
            validate_tool_options("search", {"backend": "deepseek"}, self.schema)

    def test_rejects_unknown_and_out_of_bounds_values(self):
        with pytest.raises(ToolOptionError, match="Unknown option"):
            validate_tool_options("search", {"bogus": 1}, self.schema)
        with pytest.raises(ToolOptionError, match=">= 1"):
            validate_tool_options("search", {"limit": 0}, self.schema)
