"""Framework tool metadata is not part of provider requests."""

from copy import deepcopy

from kohakuterrarium.llm.base import ToolSchema
from kohakuterrarium.llm.responses.tools import function_tool, prepare_request_tools


def test_function_tool_is_explicitly_non_strict_and_keeps_schema():
    schema = {
        "type": "object",
        "properties": {"city": {"type": "string"}, "note": {"type": "string"}},
        "required": ["city"],
    }
    tool = ToolSchema(name="book_room", description="Book a room.", parameters=schema)
    wire = function_tool(tool)
    assert wire == {
        "type": "function",
        "name": "book_room",
        "description": "Book a room.",
        "parameters": schema,
        "strict": False,
    }
    assert wire["parameters"] is schema
    assert prepare_request_tools({"tools": [wire]})[0]["tools"] == [wire]


def test_only_top_level_declaration_is_removed_without_mutating_input():
    schema = {"type": "object", "properties": {"request_replay": {"type": "string"}}}
    body = {
        "tools": [
            {
                "type": "function",
                "name": "client",
                "parameters": schema,
                "request_replay": "forbid",
            },
            {"type": "mcp", "request_replay": "forbid"},
            {"type": "future_tool"},
        ]
    }
    original = deepcopy(body)
    wire, blocked = prepare_request_tools(body)
    assert body == original
    assert blocked == ["mcp"]
    assert wire["tools"][0]["parameters"] == schema
    assert all("request_replay" not in tool for tool in wire["tools"])


def test_missing_and_null_tools_preserve_wire_shape():
    assert prepare_request_tools({"model": "m"}) == ({"model": "m"}, [])
    assert prepare_request_tools({"tools": None}) == ({"tools": None}, [])
