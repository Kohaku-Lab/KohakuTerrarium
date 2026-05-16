"""Extra coverage tests for studio.editors.codegen_subagent."""

import libcst as cst
import pytest

from kohakuterrarium.studio.editors import codegen_subagent as cg

# ── _eval_simple corner cases ───────────────────────────────


class TestEvalSimpleCorners:
    def test_tuple_form(self):
        node = cst.Tuple(
            elements=[
                cst.Element(value=cst.SimpleString('"a"')),
                cst.Element(value=cst.SimpleString('"b"')),
            ]
        )
        out = cg._eval_simple(node, {})
        assert out == ["a", "b"]

    def test_concatenated_string(self):
        node = cst.parse_expression('"a" "b"')
        out = cg._eval_simple(node, {})
        # Adjacent string literals concatenate to "ab".
        assert out == "ab"


# ── _collect_string_assignments concatenated ────────────────


class TestCollectStringAssignmentsConcat:
    def test_concatenated_string_collected(self):
        tree = cst.parse_module('FOO = "a" "b"\n')
        out = cg._collect_string_assignments(tree)
        # ConcatenatedString assignments are captured via evaluated_value.
        assert out == {"FOO": "ab"}


# ── _literal_to_cst unsupported types ───────────────────────


class TestLiteralUnsupported:
    def test_set_raises(self):
        with pytest.raises(ValueError):
            cg._literal_to_cst({1, 2, 3})

    def test_dict_raises(self):
        with pytest.raises(ValueError):
            cg._literal_to_cst({"a": 1})


# ── parse_back positional args branch ───────────────────────


class TestParseBackPositional:
    def test_positional_then_kwargs(self):
        src = 'CFG = SubAgentConfig("name_pos", description="d")\n'
        out = cg.parse_back(src)
        # Positional ignored; keyword captured.
        assert out["form"]["description"] == "d"


# ── update_existing: preserves non-form kwargs ──────────────


class TestUpdateExistingPreserves:
    def test_unmanaged_kwargs_kept(self):
        src = (
            "CFG = SubAgentConfig(\n"
            '    name="x",\n'
            '    custom_unmanaged_key="should-stay",\n'
            ")\n"
        )
        out = cg.update_existing(src, {"name": "y"}, "")
        assert "custom_unmanaged_key" in out
        assert "should-stay" in out

    def test_positional_args_preserved(self):
        src = 'CFG = SubAgentConfig("alice", description="d")\n'
        out = cg.update_existing(src, {"description": "new"}, "")
        # Positional arg untouched, keyword rewritten.
        call = cg._find_subagent_config_call(cst.parse_module(out))
        positional = [a for a in call.args if a.keyword is None]
        assert positional[0].value.evaluated_value == "alice"
        back = cg.parse_back(out)
        assert back["form"]["description"] == "new"

    def test_other_calls_in_module_left_untouched(self):
        # The _Replacer transformer must only rewrite the matched
        # SubAgentConfig call — sibling calls (here a plain function
        # call) pass through unchanged.
        src = "helper(1, 2)\n" 'CFG = SubAgentConfig(name="x")\n'
        out = cg.update_existing(src, {"name": "y"}, "")
        assert "helper(1, 2)" in out
        back = cg.parse_back(out)
        assert back["form"]["name"] == "y"


# ── _find_subagent_config_call: non-simple statements skipped ──


class TestFindCallSkipsNonSimpleStatements:
    def test_function_def_before_config_is_skipped(self):
        # A module-level `def` is a compound statement, not a
        # SimpleStatementLine — the finder must step over it and still
        # locate the config assignment below.
        src = "def setup():\n" "    return 1\n" 'CFG = SubAgentConfig(name="x")\n'
        call = cg._find_subagent_config_call(cst.parse_module(src))
        assert call is not None

    def test_no_config_anywhere_returns_none(self):
        src = "def setup():\n    return 1\nX = 5\n"
        assert cg._find_subagent_config_call(cst.parse_module(src)) is None


# ── _eval_simple: malformed-literal fallback arms ──────────────


class TestEvalSimpleFallbacks:
    def test_simple_string_eval_failure_falls_back_to_raw(self, monkeypatch):
        node = cst.SimpleString('"hello"')

        # Force evaluated_value to raise so the except arm runs.
        class _Boom:
            def __get__(self, obj, objtype=None):
                raise ValueError("bad string")

        monkeypatch.setattr(cst.SimpleString, "evaluated_value", _Boom())
        out = cg._eval_simple(node, {})
        # Falls back to the raw .value token.
        assert out == '"hello"'

    def test_concatenated_string_eval_failure_falls_back_to_empty(self, monkeypatch):
        node = cst.parse_expression('"a" "b"')

        class _Boom:
            def __get__(self, obj, objtype=None):
                raise ValueError("bad concat")

        monkeypatch.setattr(cst.ConcatenatedString, "evaluated_value", _Boom())
        out = cg._eval_simple(node, {})
        assert out == ""

    def test_hex_integer_literal_falls_back_to_zero(self):
        # A hex literal (`0x1F`) is a valid cst.Integer, but the helper
        # parses with `int(value)` (base 10), which raises ValueError on
        # the "0x" prefix → documented 0 fallback rather than a crash.
        node = cst.parse_expression("0x1F")
        assert isinstance(node, cst.Integer)
        assert cg._eval_simple(node, {}) == 0


# ── _collect_string_assignments: skip + failure arms ───────────


class TestCollectStringAssignmentsCorners:
    def test_compound_statement_skipped(self):
        # `if` block is not a SimpleStatementLine — skipped; the real
        # string assignment after it is still collected.
        src = 'if True:\n    pass\nFOO = "bar"\n'
        out = cg._collect_string_assignments(cst.parse_module(src))
        assert out == {"FOO": "bar"}

    def test_non_name_target_skipped(self):
        # Subscript-target assignment (`d["k"] = "v"`) has no plain Name
        # target — must be skipped, not crash.
        src = 'd["k"] = "v"\nFOO = "bar"\n'
        out = cg._collect_string_assignments(cst.parse_module(src))
        assert out == {"FOO": "bar"}

    def test_simple_string_eval_failure_is_swallowed(self, monkeypatch):
        src = 'FOO = "bar"\n'
        tree = cst.parse_module(src)

        class _Boom:
            def __get__(self, obj, objtype=None):
                raise ValueError("bad")

        monkeypatch.setattr(cst.SimpleString, "evaluated_value", _Boom())
        # The failing assignment is silently dropped — no entry, no raise.
        out = cg._collect_string_assignments(tree)
        assert out == {}

    def test_concatenated_string_eval_failure_is_swallowed(self, monkeypatch):
        src = 'FOO = "a" "b"\n'
        tree = cst.parse_module(src)

        class _Boom:
            def __get__(self, obj, objtype=None):
                raise ValueError("bad")

        monkeypatch.setattr(cst.ConcatenatedString, "evaluated_value", _Boom())
        out = cg._collect_string_assignments(tree)
        assert out == {}
