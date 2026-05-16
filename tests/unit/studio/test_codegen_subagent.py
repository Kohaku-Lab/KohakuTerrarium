"""Unit tests for :mod:`kohakuterrarium.studio.editors.codegen_subagent`."""

import libcst as cst
import pytest

from kohakuterrarium.studio.editors import codegen_subagent as cg
from kohakuterrarium.studio.editors.codegen_common import RoundTripError

# ── render_new ───────────────────────────────────────────────


class TestRenderNew:
    def test_basic(self):
        out = cg.render_new(
            {
                "name": "explore",
                "description": "exploration",
                "tools": ["read", "grep"],
                "system_prompt": "explorer prompt",
                "can_modify": True,
                "stateless": False,
            }
        )
        # Round-trip: rendered module parses back to the same form.
        back = cg.parse_back(out)
        assert back["mode"] == "simple"
        assert back["form"]["name"] == "explore"
        assert back["form"]["description"] == "exploration"
        assert back["form"]["tools"] == ["read", "grep"]
        assert back["form"]["system_prompt"] == "explorer prompt"
        assert back["form"]["can_modify"] is True
        assert back["form"]["stateless"] is False

    def test_defaults_applied(self):
        out = cg.render_new({})
        back = cg.parse_back(out)
        assert back["form"]["name"] == "my_subagent"
        # Defaults from render_new: stateless True, can_modify False.
        assert back["form"]["stateless"] is True
        assert back["form"]["can_modify"] is False


# ── update_existing ─────────────────────────────────────────


class TestUpdateExisting:
    def test_unknown_subagent_raises(self):
        with pytest.raises(RoundTripError):
            cg.update_existing("x = 1\n", {}, "")

    def test_rewrites_existing_call(self):
        src = (
            "CFG = SubAgentConfig(\n"
            '    name="old",\n'
            '    description="old",\n'
            ")\n"
        )
        out = cg.update_existing(src, {"name": "new", "description": "new"}, "")
        back = cg.parse_back(out)
        assert back["form"]["name"] == "new"
        assert back["form"]["description"] == "new"

    def test_attribute_call_form(self):
        src = "CFG = mod.SubAgentConfig(\n" '    name="x",\n' ")\n"
        out = cg.update_existing(src, {"name": "y"}, "")
        back = cg.parse_back(out)
        assert back["form"]["name"] == "y"

    def test_adds_missing_kwargs(self):
        src = 'CFG = SubAgentConfig(\n    name="x",\n)\n'
        out = cg.update_existing(src, {"name": "x", "description": "added"}, "")
        back = cg.parse_back(out)
        # description was absent in source; update_existing must add it.
        assert back["form"]["name"] == "x"
        assert back["form"]["description"] == "added"


# ── parse_back ──────────────────────────────────────────────


class TestParseBack:
    def test_parse_failure_returns_raw(self):
        out = cg.parse_back("def broken(:\n")
        assert out["mode"] == "raw"

    def test_no_call_returns_raw(self):
        out = cg.parse_back("x = 1\n")
        assert out["mode"] == "raw"

    def test_full_extraction(self):
        src = (
            'SYSTEM_PROMPT = "you are an explorer"\n'
            "CFG = SubAgentConfig(\n"
            '    name="explore",\n'
            '    description="exploration agent",\n'
            '    tools=["read", "grep"],\n'
            "    system_prompt=SYSTEM_PROMPT,\n"
            "    can_modify=False,\n"
            "    stateless=True,\n"
            ")\n"
        )
        out = cg.parse_back(src)
        assert out["mode"] == "simple"
        assert out["form"]["name"] == "explore"
        assert out["form"]["description"] == "exploration agent"
        assert out["form"]["tools"] == ["read", "grep"]
        assert out["form"]["system_prompt"] == "you are an explorer"

    def test_skips_unknown_kwargs(self):
        src = 'CFG = SubAgentConfig(name="x", unknown_key="x")\n'
        out = cg.parse_back(src)
        assert out["form"]["name"] == "x"

    def test_positional_args_ignored(self):
        src = 'CFG = SubAgentConfig("positional")\n'
        out = cg.parse_back(src)
        # Positional arg carries no keyword → form stays at defaults.
        assert out["mode"] == "simple"
        assert out["form"]["name"] == ""
        assert out["form"]["tools"] == []


# ── _find_subagent_config_call ──────────────────────────────


class TestFindCall:
    def test_attribute_call(self):
        tree = cst.parse_module("CFG = mod.SubAgentConfig(name='x')\n")
        call = cg._find_subagent_config_call(tree)
        assert call is not None

    def test_no_call(self):
        tree = cst.parse_module("x = 1\n")
        assert cg._find_subagent_config_call(tree) is None

    def test_other_call(self):
        tree = cst.parse_module("y = OtherCall()\n")
        assert cg._find_subagent_config_call(tree) is None


# ── _literal_to_cst ─────────────────────────────────────────


class TestLiteralToCst:
    def test_bool(self):
        assert cg._literal_to_cst(True).value == "True"
        assert cg._literal_to_cst(False).value == "False"

    def test_int(self):
        assert cg._literal_to_cst(42).value == "42"

    def test_float(self):
        out = cg._literal_to_cst(1.5)
        assert out.value == "1.5"

    def test_string_simple(self):
        out = cg._literal_to_cst("hello")
        # Re-parse the literal back to the original string.
        assert cst.parse_expression(out.value).evaluated_value == "hello"

    def test_string_multi_line(self):
        out = cg._literal_to_cst("line1\nline2")
        assert out.value.startswith('"""')
        assert cst.parse_expression(out.value).evaluated_value == "line1\nline2"

    def test_list(self):
        out = cg._literal_to_cst(["a", "b"])
        assert isinstance(out, cst.List)
        assert [e.value.evaluated_value for e in out.elements] == ["a", "b"]

    def test_none(self):
        out = cg._literal_to_cst(None)
        assert out.value == "None"

    def test_unserializable_raises(self):
        with pytest.raises(ValueError):
            cg._literal_to_cst({"unsupported"})  # set


# ── _eval_simple ─────────────────────────────────────────────


class TestEvalSimple:
    def test_simple_string(self):
        node = cst.SimpleString(value='"hello"')
        assert cg._eval_simple(node, {}) == "hello"

    def test_name_true_false_none(self):
        assert cg._eval_simple(cst.Name(value="True"), {}) is True
        assert cg._eval_simple(cst.Name(value="False"), {}) is False
        assert cg._eval_simple(cst.Name(value="None"), {}) is None

    def test_name_lookup_in_bindings(self):
        out = cg._eval_simple(cst.Name(value="PROMPT"), {"PROMPT": "prompt body"})
        assert out == "prompt body"

    def test_unknown_name_empty(self):
        assert cg._eval_simple(cst.Name(value="GHOST"), {}) == ""

    def test_integer(self):
        assert cg._eval_simple(cst.Integer(value="42"), {}) == 42

    def test_list(self):
        node = cst.List(
            elements=[
                cst.Element(value=cst.SimpleString(value='"a"')),
                cst.Element(value=cst.SimpleString(value='"b"')),
            ]
        )
        assert cg._eval_simple(node, {}) == ["a", "b"]

    def test_unknown_returns_none(self):
        # Use a complex expression we don't handle.
        node = cst.parse_expression("1 + 2")
        assert cg._eval_simple(node, {}) is None


# ── _collect_string_assignments ─────────────────────────────


class TestCollectStringAssignments:
    def test_simple_string(self):
        tree = cst.parse_module('FOO = "hello"\nBAR = 42\n')
        out = cg._collect_string_assignments(tree)
        assert out == {"FOO": "hello"}

    def test_multiple_assignments(self):
        tree = cst.parse_module('A = "x"\nB = "y"\n')
        out = cg._collect_string_assignments(tree)
        assert out == {"A": "x", "B": "y"}

    def test_skip_non_simple_targets(self):
        tree = cst.parse_module('x = y = "x"\n')
        out = cg._collect_string_assignments(tree)
        # Multi-target assignment not captured.
        assert "x" not in out
