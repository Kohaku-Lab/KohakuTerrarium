"""Framework tool metadata is not part of provider requests."""

from copy import deepcopy

from kohakuterrarium.llm.responses_tools import prepare_request_tools


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
