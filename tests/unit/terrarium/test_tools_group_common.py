"""Unit tests for :mod:`kohakuterrarium.terrarium.tools_group_common`."""

import json
from types import SimpleNamespace


from kohakuterrarium.modules.tool.base import ToolResult
from kohakuterrarium.terrarium.tools_group_common import (
    err,
    ok,
    resolve_or_error,
    serialize_channel_history,
)


class TestErr:
    def test_returns_error_result(self):
        r = err("boom")
        assert isinstance(r, ToolResult)
        assert r.error == "boom"


class TestOk:
    def test_returns_json_result(self):
        r = ok({"x": 1})
        assert r.exit_code == 0
        assert json.loads(r.output) == {"x": 1}

    def test_default_handles_non_serializable(self):
        class _Obj:
            def __repr__(self):
                return "_Obj()"

        r = ok({"x": _Obj()})
        # ``default=str`` falls back to repr/str.
        assert "_Obj" in r.output


class TestResolveOrError:
    def test_resolution_error(self, monkeypatch):
        from kohakuterrarium.terrarium import tools_group_common as mod
        from kohakuterrarium.terrarium.group_tool_context import GroupToolError

        def fake_resolve(ctx, *, require_privileged):
            raise GroupToolError("not privileged")

        monkeypatch.setattr(mod, "resolve_group_context", fake_resolve)
        gctx, result = resolve_or_error(None)
        assert gctx is None
        assert result.error == "not privileged"

    def test_success(self, monkeypatch):
        from kohakuterrarium.terrarium import tools_group_common as mod

        fake_group = object()
        monkeypatch.setattr(
            mod, "resolve_group_context", lambda ctx, *, require_privileged: fake_group
        )
        gctx, result = resolve_or_error(None)
        assert gctx is fake_group
        assert result is None


class TestSerializeChannelHistory:
    def test_empty(self):
        ch = SimpleNamespace(history=[])
        assert serialize_channel_history(ch, 5) == []

    def test_history(self):
        msg = SimpleNamespace(
            message_id="m1",
            sender="alice",
            content="hi",
            reply_to=None,
        )
        ch = SimpleNamespace(history=[msg])
        out = serialize_channel_history(ch, 5)
        assert out == [
            {"message_id": "m1", "sender": "alice", "content": "hi", "reply_to": None}
        ]

    def test_limit_applied(self):
        msgs = [
            SimpleNamespace(
                message_id=f"m{i}",
                sender="alice",
                content=str(i),
                reply_to=None,
            )
            for i in range(5)
        ]
        ch = SimpleNamespace(history=msgs)
        out = serialize_channel_history(ch, 2)
        # Last 2 only.
        assert [m["message_id"] for m in out] == ["m3", "m4"]

    def test_no_history_attribute(self):
        ch = SimpleNamespace()
        # Missing attribute → empty.
        assert serialize_channel_history(ch, 5) == []
