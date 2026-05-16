"""Unit tests for the trigger and io codegen modules + pending shim."""

import pytest

from kohakuterrarium.studio.editors import (
    codegen_io as io,
    codegen_pending as pending,
    codegen_trigger as trig,
)
from kohakuterrarium.studio.editors.codegen_common import RoundTripError

# ── codegen_trigger.render_new ──────────────────────────────


class TestTriggerRenderNew:
    def test_basic(self):
        out = trig.render_new(
            {
                "name": "tick",
                "universal": True,
                "setup_tool_name": "set_tick",
                "setup_description": "tick desc",
                "wait_for_trigger_body": "return 'fired'",
            }
        )
        back = trig.parse_back(out)
        assert back["mode"] == "simple"
        assert back["form"]["class_name"] == "TickTrigger"
        assert back["form"]["universal"] is True
        assert back["form"]["setup_tool_name"] == "set_tick"
        assert back["form"]["setup_description"] == "tick desc"
        assert "return 'fired'" in back["execute_body"]

    def test_default_class_name(self):
        out = trig.render_new({"name": "ping"})
        back = trig.parse_back(out)
        assert back["form"]["class_name"] == "PingTrigger"


# ── codegen_trigger.update_existing ─────────────────────────


class TestTriggerUpdateExisting:
    def test_unknown_raises(self):
        with pytest.raises(RoundTripError):
            trig.update_existing("x = 1\n", {}, "")

    def test_rewrites_wait_method(self):
        src = (
            "class TickTrigger(BaseTrigger):\n"
            "    async def wait_for_trigger(self):\n"
            "        return None\n"
        )
        out = trig.update_existing(src, {"wait_for_trigger_body": "return 42"}, "")
        back = trig.parse_back(out)
        assert "return 42" in back["execute_body"]
        assert "return None" not in back["execute_body"]

    def test_execute_body_takes_precedence(self):
        src = (
            "class T(BaseTrigger):\n"
            "    async def wait_for_trigger(self):\n"
            "        return None\n"
        )
        out = trig.update_existing(
            src,
            {"wait_for_trigger_body": "return 1"},
            "return 2",  # execute_body wins
        )
        back = trig.parse_back(out)
        assert "return 2" in back["execute_body"]
        assert "return 1" not in back["execute_body"]

    def test_no_body_no_change(self):
        src = (
            "class T(BaseTrigger):\n"
            "    async def wait_for_trigger(self):\n"
            "        return None\n"
        )
        out = trig.update_existing(src, {}, "")
        # No body supplied → source unchanged.
        back = trig.parse_back(out)
        assert back["execute_body"].strip() == "return None"


# ── codegen_trigger.parse_back ──────────────────────────────


class TestTriggerParseBack:
    def test_parse_failure(self):
        out = trig.parse_back("def broken(:\n")
        assert out["mode"] == "raw"

    def test_no_class(self):
        out = trig.parse_back("x = 1\n")
        assert out["mode"] == "raw"

    def test_missing_wait_method(self):
        src = "class T(BaseTrigger):\n    pass\n"
        out = trig.parse_back(src)
        assert out["mode"] == "raw"
        assert any(w["code"] == "wait_for_trigger_not_found" for w in out["warnings"])

    def test_full_extraction(self):
        src = (
            "class TickTrigger(BaseTrigger):\n"
            "    universal = True\n"
            "    setup_tool_name = 'set_tick'\n"
            "    setup_description = 'tick'\n"
            "    async def wait_for_trigger(self):\n"
            "        return 42\n"
        )
        out = trig.parse_back(src)
        assert out["mode"] == "simple"
        assert out["form"]["universal"] is True
        assert out["form"]["setup_tool_name"] == "set_tick"
        assert "return 42" in out["execute_body"]

    def test_attribute_base(self):
        src = (
            "class T(mod.BaseTrigger):\n"
            "    async def wait_for_trigger(self):\n"
            "        return None\n"
        )
        out = trig.parse_back(src)
        assert out["form"]["class_name"] == "T"


# ── trigger helpers ─────────────────────────────────────────


class TestTriggerHelpers:
    def test_pick_falls_back_to_first(self):
        import libcst as cst

        tree = cst.parse_module("class A:\n    pass\n")
        cls = trig._pick_trigger_class(tree)
        assert cls.name.value == "A"

    def test_to_class_name(self):
        assert trig._to_class_name("my") == "MyTrigger"
        assert trig._to_class_name("cool-thing") == "CoolThingTrigger"

    def test_read_str_classvar_missing(self):
        import libcst as cst

        cls = cst.parse_module("class A:\n    pass\n").body[0]
        assert trig._read_str_classvar(cls, "ghost") is None

    def test_read_str_classvar_annotated(self):
        import libcst as cst

        cls = cst.parse_module("class A:\n    x: str = 'v'\n").body[0]
        assert trig._read_str_classvar(cls, "x") == "v"

    def test_assign_target_tuple(self):
        import libcst as cst

        stmt = cst.parse_module("x = y = 1").body[0].body[0]
        assert trig._assign_target(stmt) is None


# ── codegen_io.render_new ───────────────────────────────────


class TestIoRenderNew:
    def test_input_kind(self):
        out = io.render_new({"kind": "input", "name": "stdin", "body": "return 'x'"})
        back = io.parse_back(out)
        assert back["mode"] == "simple"
        assert back["form"]["class_name"] == "StdinInput"
        assert back["form"]["method_name"] == "get_input"
        assert "return 'x'" in back["execute_body"]

    def test_output_kind(self):
        out = io.render_new({"kind": "output", "name": "stdout"})
        back = io.parse_back(out)
        assert back["mode"] == "simple"
        assert back["form"]["class_name"] == "StdoutOutput"
        # Output modules expose a write-family protocol method.
        assert back["form"]["method_name"] in ("write", "write_output")

    def test_defaults_applied(self):
        out = io.render_new({})
        back = io.parse_back(out)
        # Default kind is input, default name my_input; _to_class_name
        # appends the "Input" suffix → "MyInputInput".
        assert back["form"]["class_name"] == "MyInputInput"


# ── codegen_io.update_existing ──────────────────────────────


class TestIoUpdateExisting:
    def test_empty_body_passthrough(self):
        out = io.update_existing("x = 1\n", {}, "")
        assert out == "x = 1\n"

    def test_unknown_class_raises(self):
        with pytest.raises(RoundTripError):
            io.update_existing("x = 1\n", {}, "return 1")

    def test_no_method_raises(self):
        src = "class A:\n    pass\n"
        with pytest.raises(RoundTripError, match="no get_input"):
            io.update_existing(src, {}, "return 1")

    def test_replaces_get_input(self):
        src = (
            "class S(BaseInput):\n"
            "    async def get_input(self):\n"
            "        return 'old'\n"
        )
        out = io.update_existing(src, {}, "return 'new'")
        back = io.parse_back(out)
        assert back["form"]["method_name"] == "get_input"
        assert "return 'new'" in back["execute_body"]
        assert "old" not in back["execute_body"]

    def test_replaces_write(self):
        src = (
            "class O(OutputModule):\n"
            "    async def write(self, text):\n"
            "        return None\n"
        )
        out = io.update_existing(src, {}, "return 42")
        back = io.parse_back(out)
        assert back["form"]["method_name"] == "write"
        assert "return 42" in back["execute_body"]
        assert "return None" not in back["execute_body"]


# ── codegen_io.parse_back ───────────────────────────────────


class TestIoParseBack:
    def test_parse_failure(self):
        out = io.parse_back("def broken(:\n")
        assert out["mode"] == "raw"

    def test_no_class(self):
        out = io.parse_back("x = 1\n")
        assert out["mode"] == "raw"

    def test_no_protocol_method(self):
        out = io.parse_back("class A:\n    pass\n")
        assert out["mode"] == "raw"

    def test_with_get_input(self):
        src = (
            "class S(BaseInput):\n"
            "    async def get_input(self):\n"
            "        return 'x'\n"
        )
        out = io.parse_back(src)
        assert out["mode"] == "simple"
        assert out["form"]["method_name"] == "get_input"

    def test_with_write_output(self):
        src = (
            "class O(OutputModule):\n"
            "    async def write_output(self, text):\n"
            "        return None\n"
        )
        out = io.parse_back(src)
        assert out["form"]["method_name"] == "write_output"


# ── codegen_pending shim ─────────────────────────────────────


class TestPending:
    def test_parse_back_stub_always_raw_with_warning(self):
        out = pending.parse_back_stub("class A(BaseTool): pass\n")
        assert out["mode"] == "raw"
        assert out["form"] == {}
        assert out["warnings"] == [pending.PENDING_WARNING]

    def test_update_existing_stub_passes_source_through(self):
        src = "x = 1\n# comment\n"
        assert pending.update_existing_stub(src, {"name": "y"}, "body") == src

    def test_render_new_stub_scaffolds_placeholder(self):
        out = pending.render_new_stub({"name": "widget"})
        assert out.startswith('"""widget — TODO: implement"""')
        assert "Placeholder scaffolded by studio" in out

    def test_render_new_stub_uses_header_comment(self):
        out = pending.render_new_stub({"name": "x"}, header_comment="custom hdr")
        assert out.startswith('"""custom hdr"""')
