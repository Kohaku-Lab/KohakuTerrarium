"""Tool codegen — scaffold, round-trip, parse_back."""

import pytest

from kohakuterrarium.studio.editors import codegen_tool as tool_cg
from kohakuterrarium.studio.editors.codegen_init import RoundTripError

MINIMAL_SOURCE = '''\
"""Sample."""
from typing import Any

from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ExecutionMode,
    ToolResult,
)


class SampleTool(BaseTool):
    needs_context = False

    @property
    def tool_name(self) -> str:
        return "sample"

    @property
    def description(self) -> str:
        return "original desc"

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        return ToolResult(output="hi")
'''


# ---------- render_new -----------------------------------------------


def test_render_new_compiles():
    source = tool_cg.render_new(
        {
            "name": "my_tool",
            "class_name": "MyTool",
            "tool_name": "my_tool",
            "description": "TODO",
            "execution_mode": "direct",
            "needs_context": False,
            "execute_body": 'return ToolResult(output="hi")',
        }
    )
    compile(source, "<rendered>", "exec")
    assert "class MyTool(BaseTool)" in source
    assert "ExecutionMode.DIRECT" in source


def test_render_new_with_needs_context():
    source = tool_cg.render_new(
        {
            "name": "my_tool",
            "tool_name": "my_tool",
            "description": "x",
            "execution_mode": "direct",
            "needs_context": True,
            "execute_body": "return ToolResult()",
        }
    )
    compile(source, "<rendered>", "exec")
    assert "needs_context = True" in source


# ---------- parse_back -----------------------------------------------


def test_parse_back_extracts_form():
    env = tool_cg.parse_back(MINIMAL_SOURCE)
    assert env["mode"] == "simple"
    form = env["form"]
    assert form["class_name"] == "SampleTool"
    assert form["tool_name"] == "sample"
    assert form["description"] == "original desc"
    assert form["execution_mode"] == "direct"
    assert form["needs_context"] is False
    assert "ToolResult" in env["execute_body"]
    assert env["warnings"] == []


def test_parse_back_handles_non_class():
    env = tool_cg.parse_back("x = 1\n")
    assert env["mode"] == "raw"
    assert env["warnings"]


def test_parse_back_handles_decorated_execute():
    src = MINIMAL_SOURCE.replace(
        "    async def _execute",
        "    @staticmethod\n    async def _execute",
    )
    env = tool_cg.parse_back(src)
    codes = [w["code"] for w in env["warnings"]]
    assert "ast_roundtrip_unsafe" in codes


# ---------- update_existing ------------------------------------------


def test_update_existing_rewrites_description():
    new_src = tool_cg.update_existing(
        MINIMAL_SOURCE,
        {"class_name": "SampleTool", "description": "new description"},
        'return ToolResult(output="hi")',
    )
    compile(new_src, "<updated>", "exec")
    assert "new description" in new_src
    assert "original desc" not in new_src


def test_update_existing_rewrites_tool_name():
    new_src = tool_cg.update_existing(
        MINIMAL_SOURCE,
        {"class_name": "SampleTool", "tool_name": "renamed"},
        'return ToolResult(output="hi")',
    )
    assert "'renamed'" in new_src or '"renamed"' in new_src
    assert "'sample'" not in new_src and '"sample"' not in new_src


def test_update_existing_rewrites_body():
    new_src = tool_cg.update_existing(
        MINIMAL_SOURCE,
        {"class_name": "SampleTool"},
        "return ToolResult(output='totally new')",
    )
    compile(new_src, "<updated>", "exec")
    assert "totally new" in new_src


def test_update_existing_preserves_imports_and_docstring():
    new_src = tool_cg.update_existing(
        MINIMAL_SOURCE,
        {"class_name": "SampleTool", "description": "x"},
        'return ToolResult(output="y")',
    )
    assert '"""Sample."""' in new_src
    assert "from kohakuterrarium.modules.tool.base" in new_src


def test_update_existing_missing_class_raises():
    with pytest.raises(RoundTripError):
        tool_cg.update_existing(
            MINIMAL_SOURCE,
            {"class_name": "Nonexistent"},
            "return None",
        )


# ---------- round-trip identity --------------------------------------


def test_roundtrip_form_source_form():
    """Form → source via render_new → parse_back → same form."""
    form_in = {
        "name": "my_tool",
        "class_name": "MyTool",
        "tool_name": "my_tool",
        "description": "round trip",
        "execution_mode": "direct",
        "needs_context": True,
        "execute_body": 'return ToolResult(output="yo")',
    }
    source = tool_cg.render_new(form_in)
    env = tool_cg.parse_back(source)
    out = env["form"]
    assert out["class_name"] == "MyTool"
    assert out["tool_name"] == "my_tool"
    assert out["description"] == "round trip"
    assert out["execution_mode"] == "direct"
    assert out["needs_context"] is True
    assert 'ToolResult(output="yo")' in env["execute_body"]
