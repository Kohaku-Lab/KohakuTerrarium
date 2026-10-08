"""A tool's argument schema and flags survive the form: read, edited, written back."""

from kohakuterrarium.core.loader import ModuleLoader
from kohakuterrarium.studio.editors import codegen_tool
from kohakuterrarium.studio.editors.modules_crud import render_module
from kohakuterrarium.studio.editors.tool_params import rows_to_schema, schema_to_rows
from kohakuterrarium.studio.editors.wiring import first_class_name


def _live(tmp_path, source, name="t"):
    """The tool the source defines, loaded as a creature would load it."""
    path = tmp_path / f"{name}.py"
    path.write_text(source, encoding="utf-8")
    return ModuleLoader().load_instance(str(path), first_class_name(source))


ROWS = [
    {
        "name": "query",
        "type_hint": "string",
        "description": "What to find",
        "required": True,
    },
    {
        "name": "limit",
        "type_hint": "integer",
        "description": "",
        "required": False,
        "default": 5,
    },
    {"name": "", "type_hint": "string"},
]
SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "What to find"},
        "limit": {"type": "integer", "default": 5},
    },
    "required": ["query"],
}


def test_rows_and_schema_convert_both_ways():
    assert rows_to_schema(ROWS) == SCHEMA
    assert rows_to_schema([]) is None
    assert rows_to_schema([{"name": "x", "type_hint": "weird"}])["properties"]["x"] == {
        "type": "string"
    }
    assert schema_to_rows(SCHEMA) == [
        {
            "name": "query",
            "type_hint": "string",
            "description": "What to find",
            "required": True,
            "default": None,
        },
        {
            "name": "limit",
            "type_hint": "integer",
            "description": "",
            "required": False,
            "default": 5,
        },
    ]


def test_a_starter_schema_reads_into_the_form(tmp_path):
    form = codegen_tool.parse_back(render_module("tools", "echo", "blank"))["form"]
    assert form["params_editable"] is True
    assert form["params"] == [
        {
            "name": "text",
            "type_hint": "string",
            "description": "Text to echo back.",
            "required": True,
            "default": None,
        }
    ]


def test_edited_rows_are_what_the_loaded_tool_returns(tmp_path):
    source = render_module("tools", "echo", "blank")
    form = codegen_tool.parse_back(source)["form"]
    updated = codegen_tool.update_existing(source, {**form, "params": ROWS}, None)
    assert _live(tmp_path, updated).get_parameters_schema() == SCHEMA
    assert codegen_tool.parse_back(updated)["form"]["params"] == schema_to_rows(SCHEMA)
    assert all(len(line) <= 88 for line in updated.splitlines())


def test_rows_on_a_tool_without_a_schema_add_the_method_before_execute(tmp_path):
    source = codegen_tool.update_existing(
        render_module("tools", "plain", None), {"params": []}, None
    )
    assert "get_parameters_schema" not in source
    assert codegen_tool.parse_back(source)["form"]["params"] == []
    added = codegen_tool.update_existing(source, {"params": ROWS}, None)
    assert added.index("def get_parameters_schema") < added.index("async def _execute")
    assert _live(tmp_path, added).get_parameters_schema() == SCHEMA


def test_a_computed_schema_is_left_alone(tmp_path):
    source = render_module("tools", "calc", None).replace(
        'return {\n            "type": "object",',
        'base = {"type": "object"}\n        return {\n            **base,',
    )
    assert "**base" in source
    back = codegen_tool.parse_back(source)
    assert back["form"]["params_editable"] is False
    assert back["warnings"][0]["code"] == "params_computed"
    updated = codegen_tool.update_existing(source, {"params": []}, None)
    assert "**base" in updated


def test_mode_and_flags_are_written_only_when_they_change(tmp_path):
    source = render_module("tools", "echo", "blank")
    same = codegen_tool.update_existing(
        source,
        {
            "execution_mode": "direct",
            "needs_context": False,
            "require_manual_read": False,
        },
        None,
    )
    assert same == source
    changed = codegen_tool.update_existing(
        source, {"execution_mode": "background", "needs_context": True}, None
    )
    tool = _live(tmp_path, changed)
    assert tool.execution_mode.value == "background"
    assert tool.needs_context is True
    back = codegen_tool.parse_back(changed)["form"]
    assert (back["execution_mode"], back["needs_context"]) == ("background", True)
    assert (
        codegen_tool.update_existing(changed, {"execution_mode": "bogus"}, None)
        == changed
    )
