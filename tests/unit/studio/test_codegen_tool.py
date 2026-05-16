"""Unit tests for :mod:`kohakuterrarium.studio.editors.codegen_tool`."""

import pytest

from kohakuterrarium.studio.editors import codegen_tool as cg
from kohakuterrarium.studio.editors.codegen_common import RoundTripError

# ── render_new ───────────────────────────────────────────────


class TestRenderNew:
    def test_basic_form(self):
        out = cg.render_new(
            {
                "name": "my_tool",
                "class_name": "MyTool",
                "description": "test",
                "execution_mode": "background",
                "needs_context": True,
                "execute_body": "return ToolResult(output='hi')",
            }
        )
        # Round-trip: the rendered source must parse back to the form.
        back = cg.parse_back(out)
        assert back["mode"] == "simple"
        assert back["form"]["class_name"] == "MyTool"
        assert back["form"]["tool_name"] == "my_tool"
        assert back["form"]["description"] == "test"
        assert back["form"]["execution_mode"] == "background"
        assert back["form"]["needs_context"] is True
        assert "return ToolResult(output='hi')" in back["execute_body"]

    def test_default_class_name_from_name(self):
        out = cg.render_new({"name": "search"})
        back = cg.parse_back(out)
        # _to_class_name always appends the "Tool" suffix.
        assert back["form"]["class_name"] == "SearchTool"
        assert back["form"]["tool_name"] == "search"

    def test_falls_back_to_my_tool_default(self):
        out = cg.render_new({})
        back = cg.parse_back(out)
        # Default name "my_tool" -> class "MyToolTool" (suffix always added).
        assert back["form"]["class_name"] == "MyToolTool"
        assert back["form"]["tool_name"] == "my_tool"

    def test_dashed_name_converted(self):
        out = cg.render_new({"name": "fancy-thing"})
        back = cg.parse_back(out)
        # Dashes become word boundaries; "Tool" suffix appended.
        assert back["form"]["class_name"] == "FancyThingTool"
        assert back["form"]["tool_name"] == "fancy-thing"


# ── update_existing ─────────────────────────────────────────


class TestUpdateExisting:
    def test_replaces_tool_name(self):
        src = (
            "from kohakuterrarium.modules.tool.base import BaseTool\n"
            "class FooTool(BaseTool):\n"
            "    @property\n"
            "    def tool_name(self) -> str:\n"
            "        return 'old'\n"
        )
        out = cg.update_existing(src, {"tool_name": "new"}, None)
        # Old value is gone, new value reads back through the property.
        assert "old" not in out
        from kohakuterrarium.studio.editors.codegen_common import (
            find_class,
            parse,
            read_property_string,
        )

        klass = find_class(parse(out), "FooTool")
        assert read_property_string(klass, "tool_name") == "new"

    def test_replaces_description(self):
        src = (
            "from kohakuterrarium.modules.tool.base import BaseTool\n"
            "class FooTool(BaseTool):\n"
            "    @property\n"
            "    def description(self) -> str:\n"
            "        return 'old'\n"
        )
        out = cg.update_existing(src, {"description": "new desc"}, None)
        assert "old" not in out
        from kohakuterrarium.studio.editors.codegen_common import (
            find_class,
            parse,
            read_property_string,
        )

        klass = find_class(parse(out), "FooTool")
        assert read_property_string(klass, "description") == "new desc"

    def test_replaces_execute_body(self):
        src = (
            "from kohakuterrarium.modules.tool.base import BaseTool\n"
            "class FooTool(BaseTool):\n"
            "    async def _execute(self, args, context=None):\n"
            "        return 1\n"
        )
        out = cg.update_existing(src, {}, "return 42")
        # Body fully replaced — old statement gone, new one reads back.
        from kohakuterrarium.studio.editors.codegen_common import (
            find_class,
            parse,
            read_method_body,
        )

        klass = find_class(parse(out), "FooTool")
        body = read_method_body(klass, "_execute")
        assert "return 42" in body
        assert "return 1" not in body

    def test_unknown_class_raises(self):
        src = "x = 1\n"
        with pytest.raises(RoundTripError, match="no class found"):
            cg.update_existing(src, {"class_name": "MyTool"}, None)

    def test_first_class_fallback(self):
        # No class_name in form → use first class.
        src = (
            "from kohakuterrarium.modules.tool.base import BaseTool\n"
            "class FooTool(BaseTool):\n"
            "    @property\n"
            "    def tool_name(self) -> str:\n"
            "        return 'foo'\n"
        )
        out = cg.update_existing(src, {"tool_name": "bar"}, None)
        from kohakuterrarium.studio.editors.codegen_common import (
            find_class,
            parse,
            read_property_string,
        )

        klass = find_class(parse(out), "FooTool")
        assert read_property_string(klass, "tool_name") == "bar"


# ── parse_back ──────────────────────────────────────────────


class TestParseBack:
    def test_full_extraction(self):
        src = (
            "from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode\n"
            "class MyTool(BaseTool):\n"
            "    needs_context = True\n"
            "\n"
            "    @property\n"
            "    def tool_name(self) -> str:\n"
            "        return 'my_tool'\n"
            "\n"
            "    @property\n"
            "    def description(self) -> str:\n"
            "        return 'does stuff'\n"
            "\n"
            "    @property\n"
            "    def execution_mode(self) -> ExecutionMode:\n"
            "        return ExecutionMode.BACKGROUND\n"
            "\n"
            "    async def _execute(self, args, context=None):\n"
            "        return 1\n"
        )
        out = cg.parse_back(src)
        assert out["mode"] == "simple"
        assert out["form"]["tool_name"] == "my_tool"
        assert out["form"]["description"] == "does stuff"
        assert out["form"]["execution_mode"] == "background"
        assert out["form"]["needs_context"] is True

    def test_parse_failure_returns_raw(self):
        out = cg.parse_back("def broken(:\n")
        assert out["mode"] == "raw"

    def test_no_class_returns_raw(self):
        out = cg.parse_back("x = 1\n")
        assert out["mode"] == "raw"

    def test_missing_execute_returns_raw(self):
        src = (
            "from kohakuterrarium.modules.tool.base import BaseTool\n"
            "class FooTool(BaseTool):\n"
            "    pass\n"
        )
        out = cg.parse_back(src)
        assert out["mode"] == "raw"
        assert any(w["code"] == "execute_not_found" for w in out["warnings"])

    def test_decorators_on_execute_warning(self):
        src = (
            "from kohakuterrarium.modules.tool.base import BaseTool\n"
            "class FooTool(BaseTool):\n"
            "    @staticmethod\n"
            "    async def _execute(args, context=None):\n"
            "        return 1\n"
        )
        out = cg.parse_back(src)
        assert any(w["code"] == "ast_roundtrip_unsafe" for w in out["warnings"])


# ── _pick_tool_class / _has_base ─────────────────────────────


class TestPickToolClass:
    def test_prefers_basetool_subclass(self):
        import libcst as cst

        src = "class Other:\n    pass\n" "class T(BaseTool):\n    pass\n"
        tree = cst.parse_module(src)
        cls = cg._pick_tool_class(tree)
        assert cls.name.value == "T"

    def test_falls_back_to_first_class(self):
        import libcst as cst

        src = "class A:\n    pass\n"
        tree = cst.parse_module(src)
        cls = cg._pick_tool_class(tree)
        assert cls.name.value == "A"

    def test_no_classes(self):
        import libcst as cst

        tree = cst.parse_module("x = 1\n")
        assert cg._pick_tool_class(tree) is None


class TestHasBase:
    def test_simple_name_base(self):
        import libcst as cst

        cls = cst.parse_module("class A(BaseTool): pass\n").body[0]
        assert cg._has_base(cls, "BaseTool")

    def test_attribute_base(self):
        import libcst as cst

        cls = cst.parse_module("class A(mod.BaseTool): pass\n").body[0]
        assert cg._has_base(cls, "BaseTool")

    def test_no_base(self):
        import libcst as cst

        cls = cst.parse_module("class A: pass\n").body[0]
        assert not cg._has_base(cls, "BaseTool")


# ── _read_execution_mode ─────────────────────────────────────


class TestReadExecutionMode:
    def test_extracts_mode(self):
        import libcst as cst

        src = (
            "class T:\n"
            "    @property\n"
            "    def execution_mode(self):\n"
            "        return ExecutionMode.STATEFUL\n"
        )
        cls = cst.parse_module(src).body[0]
        assert cg._read_execution_mode(cls) == "stateful"

    def test_no_method_returns_none(self):
        import libcst as cst

        cls = cst.parse_module("class T:\n    pass\n").body[0]
        assert cg._read_execution_mode(cls) is None


# ── _to_class_name ─────────────────────────────────────────


class TestToClassName:
    def test_snake_to_pascal(self):
        # ``_to_class_name`` always appends ``Tool``.
        assert cg._to_class_name("my") == "MyTool"

    def test_dashes_converted(self):
        assert cg._to_class_name("fancy-thing") == "FancyThingTool"

    def test_empty(self):
        assert cg._to_class_name("") == "Tool"

    def test_single_word(self):
        assert cg._to_class_name("zap") == "ZapTool"
