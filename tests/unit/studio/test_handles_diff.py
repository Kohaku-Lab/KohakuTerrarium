"""Unit tests for :mod:`kohakuterrarium.studio.sessions.handles` and
:mod:`kohakuterrarium.studio.persistence.viewer.diff` helpers."""

import pytest
from fastapi import HTTPException

from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence.viewer.diff import (
    _agents_for,
    _flatten,
    _load_messages,
    _msg_signature,
    _summarize_msg,
    build_diff_payload,
)
from kohakuterrarium.studio.sessions.handles import Session, SessionListing

# ── handles dataclasses ─────────────────────────────────────────


class TestSession:
    def test_defaults(self):
        s = Session(session_id="s", name="n")
        assert s.creatures == []
        assert s.channels == []
        assert s.created_at == ""
        assert s.config_path == ""
        assert s.pwd == ""
        assert s.has_root is False
        assert s.home_node == "_host"

    def test_to_dict(self):
        s = Session(
            session_id="s",
            name="n",
            creatures=[{"id": "c1"}],
            channels=[{"name": "chat"}],
            created_at="2026-01-01",
            config_path="/cfg",
            pwd="/work",
            has_root=True,
            home_node="worker-1",
        )
        # to_dict serializes every field, no omissions.
        assert s.to_dict() == {
            "session_id": "s",
            "name": "n",
            "creatures": [{"id": "c1"}],
            "channels": [{"name": "chat"}],
            "created_at": "2026-01-01",
            "config_path": "/cfg",
            "pwd": "/work",
            "has_root": True,
            "home_node": "worker-1",
        }


class TestSessionListing:
    def test_defaults(self):
        s = SessionListing(session_id="s", name="n")
        assert s.running is True
        assert s.creatures == 0
        assert s.node_id == "_host"

    def test_to_dict(self):
        s = SessionListing(
            session_id="s", name="n", running=False, creatures=3, node_id="w1"
        )
        assert s.to_dict() == {
            "session_id": "s",
            "name": "n",
            "running": False,
            "creatures": 3,
            "node_id": "w1",
        }


# ── _flatten ────────────────────────────────────────────────────


class TestFlatten:
    def test_str(self):
        assert _flatten("hi") == "hi"

    def test_list_of_text_dicts(self):
        # Text parts are space-joined in order.
        assert _flatten([{"text": "a"}, {"text": "b"}]) == "a b"

    def test_list_with_content_key(self):
        # ``content`` key is used when ``text`` is absent.
        assert _flatten([{"content": "x"}]) == "x"

    def test_object_with_text_attribute(self):
        class _P:
            text = "from-attr"

        assert _flatten([_P()]) == "from-attr"

    def test_other_types_return_empty(self):
        assert _flatten(None) == ""
        assert _flatten(42) == ""


# ── _msg_signature ──────────────────────────────────────────────


class TestMsgSignature:
    def test_role_content_match(self):
        a = {"role": "user", "content": "hi"}
        b = {"role": "user", "content": "hi"}
        # Signature is (role, flattened_content, tool_call_sig).
        assert _msg_signature(a) == ("user", "hi", ())
        assert _msg_signature(a) == _msg_signature(b)

    def test_differing_content_differs(self):
        a = {"role": "user", "content": "hi"}
        b = {"role": "user", "content": "bye"}
        assert _msg_signature(a) != _msg_signature(b)

    def test_differing_role_differs(self):
        a = {"role": "user", "content": "hi"}
        b = {"role": "assistant", "content": "hi"}
        assert _msg_signature(a) != _msg_signature(b)

    def test_tool_calls_in_signature(self):
        a = {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": "bash", "arguments": "{}"}}],
        }
        b = {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": "echo", "arguments": "{}"}}],
        }
        # Tool-call name is part of the signature.
        assert _msg_signature(a)[2] == (("bash", "{}"),)
        assert _msg_signature(b)[2] == (("echo", "{}"),)
        assert _msg_signature(a) != _msg_signature(b)


# ── _summarize_msg ──────────────────────────────────────────────


class TestSummarizeMsg:
    def test_includes_role_and_preview(self):
        out = _summarize_msg({"role": "user", "content": "hi", "name": "alice"})
        assert out == {
            "role": "user",
            "content_preview": "hi",
            "has_tool_calls": False,
            "name": "alice",
        }

    def test_preview_truncated_to_200(self):
        out = _summarize_msg({"role": "user", "content": "x" * 500})
        assert out["content_preview"] == "x" * 200

    def test_has_tool_calls(self):
        out = _summarize_msg({"role": "assistant", "tool_calls": [{"x": 1}]})
        assert out["has_tool_calls"] is True
        assert out["content_preview"] == ""


# ── _agents_for ─────────────────────────────────────────────────


def _store(tmp_path, name="s.kohakutr") -> SessionStore:
    return SessionStore(str(tmp_path / name))


class TestAgentsFor:
    def test_explicit_known_agent(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.init_meta("sess", "agent", "/p", "/w", ["alice"])
            meta = s.load_meta()
            assert _agents_for(meta, s, "alice") == "alice"
        finally:
            s.close()

    def test_explicit_unknown_raises(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.init_meta("sess", "agent", "/p", "/w", ["alice"])
            meta = s.load_meta()
            with pytest.raises(HTTPException) as exc:
                _agents_for(meta, s, "ghost")
            assert exc.value.status_code == 404
        finally:
            s.close()

    def test_default_first_main_agent(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.init_meta("sess", "agent", "/p", "/w", ["alice", "bob"])
            meta = s.load_meta()
            assert _agents_for(meta, s, None) == "alice"
        finally:
            s.close()

    def test_viewer_default_wins(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.init_meta("sess", "agent", "/p", "/w", ["alice"])
            s.set_viewer_default_agent("alice:attached:rev:0")
            s.append_event("alice:attached:rev:0", "x", {})
            s.flush()
            meta = s.load_meta()
            out = _agents_for(meta, s, None)
            assert out == "alice:attached:rev:0"
        finally:
            s.close()

    def test_no_agents_raises(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.init_meta("sess", "agent", "/p", "/w", [])
            meta = s.load_meta()
            with pytest.raises(HTTPException):
                _agents_for(meta, s, None)
        finally:
            s.close()


# ── _load_messages / build_diff_payload ─────────────────────────


class TestLoadMessages:
    def test_round_trip(self, tmp_path):
        path = tmp_path / "s.kohakutr"
        s = SessionStore(str(path))
        try:
            s.init_meta("sess", "agent", "/p", "/w", ["alice"])
            s.append_event("alice", "user_message", {"content": "hi"})
            s.flush()
        finally:
            s.close()
        msgs, name, agent = _load_messages(path, None)
        assert agent == "alice"
        assert any(m.get("content") == "hi" for m in msgs)


class TestBuildDiffPayload:
    def _seed(self, path, messages):
        s = SessionStore(str(path))
        try:
            s.init_meta("sess", "agent", "/p", "/w", ["alice"])
            for content in messages:
                s.append_event("alice", "user_message", {"content": content})
            s.flush()
        finally:
            s.close()

    def test_identical_sessions(self, tmp_path):
        a = tmp_path / "a.kohakutr"
        b = tmp_path / "b.kohakutr"
        self._seed(a, ["hi"])
        self._seed(b, ["hi"])
        out = build_diff_payload(a, b, agent=None)
        assert out["identical"] is True
        assert out["shared_prefix_length"] == 1

    def test_diverging_sessions(self, tmp_path):
        a = tmp_path / "a.kohakutr"
        b = tmp_path / "b.kohakutr"
        self._seed(a, ["hi", "then-a"])
        self._seed(b, ["hi", "then-b"])
        out = build_diff_payload(a, b, agent=None)
        assert out["identical"] is False
        assert out["shared_prefix_length"] == 1
        assert len(out["a_only"]) == 1
        assert len(out["b_only"]) == 1
