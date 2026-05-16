"""Unit tests for :mod:`kohakuterrarium.modules.plugin.option_validation`."""

import pytest

from kohakuterrarium.modules.plugin.option_validation import (
    PluginOptionError,
    _check_bounds,
    validate_plugin_options,
)

# ── validate_plugin_options entry-point ─────────────────────────


class TestValidateEntryPoint:
    def test_non_dict_raises(self):
        with pytest.raises(PluginOptionError, match="object"):
            validate_plugin_options("p", "not a dict", {})  # type: ignore[arg-type]

    def test_empty_values_ok(self):
        assert validate_plugin_options("p", {}, {}) == {}

    def test_non_empty_with_no_schema_raises(self):
        with pytest.raises(PluginOptionError, match="declares no options"):
            validate_plugin_options("p", {"k": 1}, {})

    def test_unknown_key_raises(self):
        with pytest.raises(PluginOptionError, match="Unknown option"):
            validate_plugin_options("p", {"unknown": 1}, {"known": {"type": "int"}})

    def test_known_key_passes(self):
        out = validate_plugin_options("p", {"x": 5}, {"x": {"type": "int"}})
        assert out == {"x": 5}


# ── enum ────────────────────────────────────────────────────────


class TestEnum:
    schema = {"mode": {"type": "enum", "values": ["a", "b"]}}

    def test_valid(self):
        assert validate_plugin_options("p", {"mode": "a"}, self.schema) == {"mode": "a"}

    def test_non_string_rejected(self):
        with pytest.raises(PluginOptionError, match="must be one of"):
            validate_plugin_options("p", {"mode": 1}, self.schema)

    def test_invalid_value(self):
        with pytest.raises(PluginOptionError, match="must be one of"):
            validate_plugin_options("p", {"mode": "z"}, self.schema)


# ── string ──────────────────────────────────────────────────────


class TestString:
    schema = {"k": {"type": "string"}}

    def test_valid(self):
        assert validate_plugin_options("p", {"k": "hello"}, self.schema) == {
            "k": "hello"
        }

    def test_non_string_rejected(self):
        with pytest.raises(PluginOptionError, match="must be a string"):
            validate_plugin_options("p", {"k": 1}, self.schema)

    def test_max_length_enforced(self):
        with pytest.raises(PluginOptionError, match="max length"):
            validate_plugin_options(
                "p",
                {"k": "x" * 1000},
                {"k": {"type": "string", "max_length": 10}},
            )


# ── int ─────────────────────────────────────────────────────────


class TestInt:
    schema = {"n": {"type": "int"}}

    def test_valid(self):
        assert validate_plugin_options("p", {"n": 5}, self.schema) == {"n": 5}

    def test_string_coerced(self):
        assert validate_plugin_options("p", {"n": "5"}, self.schema) == {"n": 5}

    def test_bool_rejected(self):
        with pytest.raises(PluginOptionError, match="must be an integer"):
            validate_plugin_options("p", {"n": True}, self.schema)

    def test_garbage_rejected(self):
        with pytest.raises(PluginOptionError, match="must be an integer"):
            validate_plugin_options("p", {"n": "not-num"}, self.schema)

    def test_bounds(self):
        bounded = {"n": {"type": "int", "min": 1, "max": 10}}
        with pytest.raises(PluginOptionError, match=">="):
            validate_plugin_options("p", {"n": 0}, bounded)
        with pytest.raises(PluginOptionError, match="<="):
            validate_plugin_options("p", {"n": 11}, bounded)


# ── float ───────────────────────────────────────────────────────


class TestFloat:
    schema = {"x": {"type": "float"}}

    def test_valid(self):
        assert validate_plugin_options("p", {"x": 1.5}, self.schema)["x"] == 1.5

    def test_int_coerced(self):
        assert validate_plugin_options("p", {"x": 5}, self.schema)["x"] == 5.0

    def test_bool_rejected(self):
        with pytest.raises(PluginOptionError, match="must be a number"):
            validate_plugin_options("p", {"x": True}, self.schema)

    def test_garbage_rejected(self):
        with pytest.raises(PluginOptionError, match="must be a number"):
            validate_plugin_options("p", {"x": "nope"}, self.schema)


# ── bool ────────────────────────────────────────────────────────


class TestBool:
    schema = {"b": {"type": "bool"}}

    def test_true_passthrough(self):
        assert validate_plugin_options("p", {"b": True}, self.schema) == {"b": True}

    def test_false_passthrough(self):
        assert validate_plugin_options("p", {"b": False}, self.schema) == {"b": False}

    def test_string_true(self):
        for s in ("true", "1", "yes", "y", "on"):
            assert validate_plugin_options("p", {"b": s}, self.schema) == {"b": True}

    def test_string_false(self):
        for s in ("false", "0", "no", "n", "off"):
            assert validate_plugin_options("p", {"b": s}, self.schema) == {"b": False}

    def test_invalid_string(self):
        with pytest.raises(PluginOptionError, match="must be a boolean"):
            validate_plugin_options("p", {"b": "maybe"}, self.schema)

    def test_invalid_type(self):
        with pytest.raises(PluginOptionError, match="must be a boolean"):
            validate_plugin_options("p", {"b": 1.5}, self.schema)


# ── list ────────────────────────────────────────────────────────


class TestList:
    def test_valid(self):
        assert validate_plugin_options("p", {"k": [1, 2]}, {"k": {"type": "list"}}) == {
            "k": [1, 2]
        }

    def test_non_list_rejected(self):
        with pytest.raises(PluginOptionError, match="must be a list"):
            validate_plugin_options("p", {"k": "no"}, {"k": {"type": "list"}})

    def test_max_items(self):
        with pytest.raises(PluginOptionError, match="exceeds max length"):
            validate_plugin_options(
                "p",
                {"k": [1, 2, 3]},
                {"k": {"type": "list", "max_items": 2}},
            )

    def test_item_type_coerces(self):
        out = validate_plugin_options(
            "p",
            {"k": ["1", "2"]},
            {"k": {"type": "list", "item_type": "int"}},
        )
        assert out == {"k": [1, 2]}

    def test_tuple_accepted(self):
        # Tuple input works (treated like list).
        out = validate_plugin_options("p", {"k": (1, 2)}, {"k": {"type": "list"}})
        assert out == {"k": [1, 2]}


# ── dict ────────────────────────────────────────────────────────


class TestDict:
    def test_valid(self):
        out = validate_plugin_options("p", {"k": {"a": 1}}, {"k": {"type": "dict"}})
        assert out == {"k": {"a": 1}}

    def test_non_dict_rejected(self):
        with pytest.raises(PluginOptionError, match="object"):
            validate_plugin_options("p", {"k": [1, 2]}, {"k": {"type": "dict"}})


# ── unsupported type ────────────────────────────────────────────


class TestUnsupported:
    def test_unsupported_type_rejected(self):
        with pytest.raises(PluginOptionError, match="Unsupported"):
            validate_plugin_options("p", {"k": "x"}, {"k": {"type": "weird"}})


# ── None value ──────────────────────────────────────────────────


class TestNoneValue:
    def test_none_passes_through(self):
        out = validate_plugin_options("p", {"k": None}, {"k": {"type": "string"}})
        assert out == {"k": None}


# ── _check_bounds ──────────────────────────────────────────────


class TestCheckBoundsHelper:
    def test_no_bounds(self):
        _check_bounds("k", 100, {})  # must not raise

    def test_within_bounds(self):
        _check_bounds("k", 5, {"min": 0, "max": 10})
