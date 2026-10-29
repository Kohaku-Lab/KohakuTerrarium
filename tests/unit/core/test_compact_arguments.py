"""Unit tests for :mod:`kohakuterrarium.core.compact_arguments`."""

import json

from kohakuterrarium.core.compact_arguments import after_compact, compact_tool_arguments
from kohakuterrarium.core.conversation import Conversation
from kohakuterrarium.core.registry import Registry
from kohakuterrarium.modules.tool.base import BaseTool, ToolResult


class _Shrinking(BaseTool):
    @property
    def tool_name(self):
        return "shrink"

    @property
    def description(self):
        return "shrinks"

    async def _execute(self, args, **kwargs):
        return ToolResult(output="ok")

    def compact_arguments(self, args):
        if args.get("body") == "short":
            return None
        return {**args, "body": "short"}


class _Plain(_Shrinking):
    @property
    def tool_name(self):
        return "plain"

    def compact_arguments(self, args):
        return BaseTool.compact_arguments(self, args)


def _call(call_id, name, args):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


def _conversation(tail_tool: bool):
    conv = Conversation()
    conv.append("system", "sys")
    conv.append("user", "go")
    conv.append(
        "assistant",
        "",
        tool_calls=[
            _call("c1", "shrink", {"body": "x" * 500}),
            _call("c2", "plain", {"body": "y" * 500}),
            _call("c3", "missing", {"body": "z"}),
        ],
    )
    conv.append("tool", "done", tool_call_id="c1")
    conv.append("tool", "done", tool_call_id="c2")
    conv.append("tool", "done", tool_call_id="c3")
    conv.append("assistant", "", tool_calls=[_call("c4", "shrink", {"body": "new"})])
    if tail_tool:
        conv.append("tool", "done", tool_call_id="c4")
    else:
        conv.append("assistant", "answer")
    return conv


def _args(conv, call_id):
    for msg in conv.get_messages():
        for call in getattr(msg, "tool_calls", None) or []:
            if call["id"] == call_id:
                return json.loads(call["function"]["arguments"])
    raise KeyError(call_id)


def _registry():
    reg = Registry()
    reg.register_tool(_Shrinking())
    reg.register_tool(_Plain())
    return reg


def test_old_calls_shrink_and_the_latest_round_is_kept():
    conv = _conversation(tail_tool=True)
    assert compact_tool_arguments(conv, _registry()) == 1
    assert _args(conv, "c1") == {"body": "short"}
    assert _args(conv, "c2") == {"body": "y" * 500}, "default hook keeps arguments"
    assert _args(conv, "c4") == {"body": "new"}, "latest feedback round untouched"
    assert compact_tool_arguments(conv, _registry()) == 0, "idempotent"


def test_without_a_trailing_tool_round_every_call_is_old():
    conv = _conversation(tail_tool=False)
    assert compact_tool_arguments(conv, _registry()) == 2
    assert _args(conv, "c4") == {"body": "short"}


def test_no_conversation_or_registry_is_a_no_op():
    assert compact_tool_arguments(None, _registry()) == 0
    assert compact_tool_arguments(_conversation(True), None) == 0


def test_after_compact_elides_results_then_shrinks_arguments():
    class _Controller:
        config = None

        def __init__(self, conv):
            self.conversation = conv

    class _Agent:
        registry = _registry()

    conv = _conversation(tail_tool=False)
    after_compact(_Controller(conv), _Agent())
    assert _args(conv, "c1") == {"body": "short"}
