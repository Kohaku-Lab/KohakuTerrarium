"""Tests for :mod:`kohakuterrarium.core.plugin_option_validation`."""

import pytest

from kohakuterrarium.modules.plugin.option_validation import (
    PluginOptionError,
    validate_plugin_options,
)

PERMGATE_SCHEMA = {
    "gated_tools": {"type": "list", "item_type": "string", "default": []},
    "allowlist": {"type": "list", "item_type": "string", "default": []},
    "timeout_s": {"type": "float", "default": None, "min": 0},
    "surface": {
        "type": "enum",
        "values": ["modal", "chat"],
        "default": "modal",
    },
}


class TestEmptyValuesEmptySchema:
    def test_empty_values_returns_empty(self):
        assert validate_plugin_options("p", {}, {}) == {}

    def test_non_empty_values_against_empty_schema_raises(self):
        with pytest.raises(PluginOptionError, match="declares no options"):
            validate_plugin_options("p", {"x": 1}, {})

    def test_unknown_key_raises(self):
        with pytest.raises(PluginOptionError, match="Unknown option"):
            validate_plugin_options("permgate", {"nope": 1}, PERMGATE_SCHEMA)


class TestEnum:
    def test_valid_enum_passes(self):
        out = validate_plugin_options("permgate", {"surface": "chat"}, PERMGATE_SCHEMA)
        assert out == {"surface": "chat"}

    def test_invalid_enum_raises(self):
        with pytest.raises(PluginOptionError, match="must be one of"):
            validate_plugin_options("permgate", {"surface": "popup"}, PERMGATE_SCHEMA)

    def test_none_passes_through(self):
        out = validate_plugin_options("permgate", {"surface": None}, PERMGATE_SCHEMA)
        assert out == {"surface": None}


class TestList:
    def test_string_list_valid(self):
        out = validate_plugin_options(
            "permgate", {"gated_tools": ["bash", "write"]}, PERMGATE_SCHEMA
        )
        assert out == {"gated_tools": ["bash", "write"]}

    def test_non_list_raises(self):
        with pytest.raises(PluginOptionError, match="must be a list"):
            validate_plugin_options(
                "permgate", {"gated_tools": "bash"}, PERMGATE_SCHEMA
            )

    def test_max_items_enforced(self):
        schema = {"items": {"type": "list", "item_type": "string", "max_items": 2}}
        with pytest.raises(PluginOptionError, match="exceeds max length"):
            validate_plugin_options("p", {"items": ["a", "b", "c"]}, schema)


class TestNumeric:
    def test_int_within_bounds(self):
        schema = {"x": {"type": "int", "min": 0, "max": 100}}
        out = validate_plugin_options("p", {"x": 42}, schema)
        assert out == {"x": 42}

    def test_int_string_coerces(self):
        schema = {"x": {"type": "int"}}
        assert validate_plugin_options("p", {"x": "5"}, schema) == {"x": 5}

    def test_int_bool_rejected(self):
        schema = {"x": {"type": "int"}}
        with pytest.raises(PluginOptionError, match="must be an integer"):
            validate_plugin_options("p", {"x": True}, schema)

    def test_int_below_min_raises(self):
        schema = {"x": {"type": "int", "min": 10}}
        with pytest.raises(PluginOptionError, match=">="):
            validate_plugin_options("p", {"x": 5}, schema)

    def test_float_above_max_raises(self):
        schema = {"x": {"type": "float", "max": 1.0}}
        with pytest.raises(PluginOptionError, match="<="):
            validate_plugin_options("p", {"x": 1.5}, schema)

    def test_float_string_coerces(self):
        out = validate_plugin_options(
            "permgate", {"timeout_s": "30.5"}, PERMGATE_SCHEMA
        )
        assert out == {"timeout_s": 30.5}


class TestBool:
    def test_truthy_strings_coerce(self):
        schema = {"on": {"type": "bool"}}
        for v in ("true", "1", "yes", "on", "Y"):
            assert validate_plugin_options("p", {"on": v}, schema) == {"on": True}

    def test_falsy_strings_coerce(self):
        schema = {"on": {"type": "bool"}}
        for v in ("false", "0", "no", "off"):
            assert validate_plugin_options("p", {"on": v}, schema) == {"on": False}

    def test_invalid_string_raises(self):
        schema = {"on": {"type": "bool"}}
        with pytest.raises(PluginOptionError, match="must be a boolean"):
            validate_plugin_options("p", {"on": "maybe"}, schema)


class TestDict:
    def test_dict_passes(self):
        schema = {"axis": {"type": "dict"}}
        out = validate_plugin_options(
            "budget", {"axis": {"soft": 30, "hard": 50}}, schema
        )
        assert out == {"axis": {"soft": 30, "hard": 50}}

    def test_non_dict_raises(self):
        schema = {"axis": {"type": "dict"}}
        with pytest.raises(PluginOptionError, match="must be an object"):
            validate_plugin_options("budget", {"axis": [1, 2]}, schema)

    def test_none_passes_through(self):
        schema = {"axis": {"type": "dict"}}
        assert validate_plugin_options("budget", {"axis": None}, schema) == {
            "axis": None
        }


class TestUnsupportedType:
    def test_unsupported_type_raises(self):
        schema = {"x": {"type": "magic"}}
        with pytest.raises(PluginOptionError, match="Unsupported option type"):
            validate_plugin_options("p", {"x": 1}, schema)
