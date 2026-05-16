"""Unit tests for :mod:`kohakuterrarium.core.native_tool_validation`."""

import pytest

from kohakuterrarium.core.native_tool_validation import (
    NativeToolOptionError,
    validate_native_tool_options,
)

# ── input shape & unknown keys ────────────────────────────────────


def test_non_dict_input_rejected():
    with pytest.raises(NativeToolOptionError, match="must be an object"):
        validate_native_tool_options("t", "not a dict", {})  # type: ignore[arg-type]


def test_unknown_key_rejected():
    with pytest.raises(NativeToolOptionError, match="Unknown option"):
        validate_native_tool_options("t", {"foo": 1}, {})


def test_none_value_skipped():
    out = validate_native_tool_options(
        "t",
        {"key": None},
        {"key": {"type": "string"}},
    )
    assert out == {}


def test_empty_string_value_skipped():
    out = validate_native_tool_options(
        "t",
        {"key": ""},
        {"key": {"type": "string"}},
    )
    assert out == {}


def test_unsupported_type_rejected():
    with pytest.raises(NativeToolOptionError, match="Unsupported option type"):
        validate_native_tool_options(
            "t",
            {"key": "x"},
            {"key": {"type": "weird"}},
        )


# ── enum ──────────────────────────────────────────────────────────


class TestEnum:
    schema = {"mode": {"type": "enum", "values": ["a", "b"]}}

    def test_valid(self):
        out = validate_native_tool_options("t", {"mode": "a"}, self.schema)
        assert out == {"mode": "a"}

    def test_invalid_value(self):
        with pytest.raises(NativeToolOptionError, match="must be one of"):
            validate_native_tool_options("t", {"mode": "z"}, self.schema)

    def test_non_string_rejected(self):
        with pytest.raises(NativeToolOptionError, match="must be a string"):
            validate_native_tool_options("t", {"mode": 1}, self.schema)


# ── string ────────────────────────────────────────────────────────


class TestString:
    def test_valid(self):
        out = validate_native_tool_options(
            "t", {"k": "hello"}, {"k": {"type": "string"}}
        )
        assert out == {"k": "hello"}

    def test_non_string_rejected(self):
        with pytest.raises(NativeToolOptionError, match="must be a string"):
            validate_native_tool_options("t", {"k": 1}, {"k": {"type": "string"}})

    def test_max_length_default(self):
        long = "x" * 200
        with pytest.raises(NativeToolOptionError, match="too long"):
            validate_native_tool_options("t", {"k": long}, {"k": {"type": "string"}})

    def test_max_length_custom(self):
        with pytest.raises(NativeToolOptionError, match="too long"):
            validate_native_tool_options(
                "t",
                {"k": "abcdef"},
                {"k": {"type": "string", "max_length": 3}},
            )

    def test_image_gen_size_validation_invoked(self):
        with pytest.raises(NativeToolOptionError):
            validate_native_tool_options(
                "image_gen",
                {"size": "garbage"},
                {"size": {"type": "string"}},
            )


# ── int ───────────────────────────────────────────────────────────


class TestInt:
    schema = {"n": {"type": "int"}}

    def test_valid(self):
        assert validate_native_tool_options("t", {"n": 5}, self.schema) == {"n": 5}

    def test_string_coerced(self):
        assert validate_native_tool_options("t", {"n": "5"}, self.schema) == {"n": 5}

    def test_bool_rejected(self):
        with pytest.raises(NativeToolOptionError, match="must be an integer"):
            validate_native_tool_options("t", {"n": True}, self.schema)

    def test_garbage_rejected(self):
        with pytest.raises(NativeToolOptionError, match="must be an integer"):
            validate_native_tool_options("t", {"n": "not-a-num"}, self.schema)

    def test_min_max(self):
        schema = {"n": {"type": "int", "min": 1, "max": 10}}
        with pytest.raises(NativeToolOptionError, match=">="):
            validate_native_tool_options("t", {"n": 0}, schema)
        with pytest.raises(NativeToolOptionError, match="<="):
            validate_native_tool_options("t", {"n": 11}, schema)
        assert validate_native_tool_options("t", {"n": 5}, schema) == {"n": 5}


# ── float ─────────────────────────────────────────────────────────


class TestFloat:
    schema = {"x": {"type": "float"}}

    def test_valid(self):
        out = validate_native_tool_options("t", {"x": 1.5}, self.schema)
        assert out == {"x": 1.5}

    def test_int_coerced(self):
        out = validate_native_tool_options("t", {"x": 5}, self.schema)
        assert out["x"] == 5.0

    def test_bool_rejected(self):
        with pytest.raises(NativeToolOptionError, match="must be a number"):
            validate_native_tool_options("t", {"x": False}, self.schema)

    def test_garbage_rejected(self):
        with pytest.raises(NativeToolOptionError, match="must be a number"):
            validate_native_tool_options("t", {"x": "abc"}, self.schema)

    def test_min_max(self):
        schema = {"x": {"type": "float", "min": 0.5, "max": 1.0}}
        with pytest.raises(NativeToolOptionError):
            validate_native_tool_options("t", {"x": 0.1}, schema)
        with pytest.raises(NativeToolOptionError):
            validate_native_tool_options("t", {"x": 2.0}, schema)


# ── bool ──────────────────────────────────────────────────────────


class TestBool:
    schema = {"b": {"type": "bool"}}

    def test_true_passthrough(self):
        assert validate_native_tool_options("t", {"b": True}, self.schema) == {
            "b": True
        }

    def test_false_passthrough(self):
        assert validate_native_tool_options("t", {"b": False}, self.schema) == {
            "b": False
        }

    def test_string_truthy(self):
        for s in ("true", "1", "YES", "y", "on"):
            assert validate_native_tool_options("t", {"b": s}, self.schema) == {
                "b": True
            }

    def test_string_falsy(self):
        for s in ("false", "0", "no", "n", "off"):
            assert validate_native_tool_options("t", {"b": s}, self.schema) == {
                "b": False
            }

    def test_invalid_string(self):
        with pytest.raises(NativeToolOptionError, match="must be a boolean"):
            validate_native_tool_options("t", {"b": "maybe"}, self.schema)

    def test_invalid_type(self):
        with pytest.raises(NativeToolOptionError, match="must be a boolean"):
            validate_native_tool_options("t", {"b": 1.5}, self.schema)


# ── image_gen size ────────────────────────────────────────────────


class TestImageSize:
    schema = {"size": {"type": "string"}}

    def test_auto(self):
        out = validate_native_tool_options("image_gen", {"size": "auto"}, self.schema)
        assert out == {"size": "auto"}

    def test_valid_size(self):
        out = validate_native_tool_options(
            "image_gen", {"size": "1024x1024"}, self.schema
        )
        assert out == {"size": "1024x1024"}

    def test_malformed(self):
        with pytest.raises(NativeToolOptionError, match="WIDTHxHEIGHT"):
            validate_native_tool_options(
                "image_gen", {"size": "1024 by 1024"}, self.schema
            )

    def test_width_too_small(self):
        with pytest.raises(NativeToolOptionError, match="width"):
            validate_native_tool_options("image_gen", {"size": "32x256"}, self.schema)

    def test_width_too_large(self):
        with pytest.raises(NativeToolOptionError, match="width"):
            validate_native_tool_options(
                "image_gen", {"size": "9999x1024"}, self.schema
            )

    def test_height_too_small(self):
        with pytest.raises(NativeToolOptionError, match="height"):
            validate_native_tool_options("image_gen", {"size": "1024x32"}, self.schema)

    def test_height_too_large(self):
        with pytest.raises(NativeToolOptionError, match="height"):
            validate_native_tool_options(
                "image_gen", {"size": "1024x9999"}, self.schema
            )

    def test_non_image_gen_skips_size_validation(self):
        # ``size`` validation only happens when ``tool_name == "image_gen"``.
        out = validate_native_tool_options(
            "other_tool", {"size": "garbage"}, self.schema
        )
        assert out == {"size": "garbage"}


# ── _coerce_value direct invocation for None-passthrough (lines 46, 56, 66, 77, 88) ──


class TestCoerceValueNoneBranches:
    """``_coerce_value`` is called directly during partial-merge logic
    (via ``NativeToolOptions.set``) which can hand it ``None`` values
    that the public validator would have filtered out."""

    def test_enum_none(self):
        from kohakuterrarium.core.native_tool_validation import _coerce_value

        out = _coerce_value("t", "k", None, {"type": "enum", "values": ["a"]})
        assert out is None

    def test_string_none(self):
        from kohakuterrarium.core.native_tool_validation import _coerce_value

        assert _coerce_value("t", "k", None, {"type": "string"}) is None

    def test_int_none(self):
        from kohakuterrarium.core.native_tool_validation import _coerce_value

        assert _coerce_value("t", "k", None, {"type": "int"}) is None

    def test_float_none(self):
        from kohakuterrarium.core.native_tool_validation import _coerce_value

        assert _coerce_value("t", "k", None, {"type": "float"}) is None

    def test_bool_none(self):
        from kohakuterrarium.core.native_tool_validation import _coerce_value

        assert _coerce_value("t", "k", None, {"type": "bool"}) is None
