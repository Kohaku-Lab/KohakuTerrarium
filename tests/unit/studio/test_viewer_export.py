"""Unit tests for :mod:`kohakuterrarium.studio.persistence.viewer.export`."""

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence.viewer import export as ex_mod

# ── _flatten_content ────────────────────────────────────────


class TestFlattenContent:
    def test_string(self):
        assert ex_mod._flatten_content("hi") == "hi"

    def test_list_of_dicts_with_text(self):
        out = ex_mod._flatten_content([{"text": "a"}, {"text": "b"}])
        assert out == "a b"

    def test_list_of_dicts_with_content_key(self):
        out = ex_mod._flatten_content([{"content": "x"}, {"content": "y"}])
        assert out == "x y"

    def test_list_with_text_attribute(self):
        part = SimpleNamespace(text="z")
        assert ex_mod._flatten_content([part]) == "z"

    def test_empty_list(self):
        assert ex_mod._flatten_content([]) == ""

    def test_other_returns_empty(self):
        assert ex_mod._flatten_content(123) == ""


# ── _agents_for ─────────────────────────────────────────────


class TestAgentsFor:
    def test_no_filter_returns_all(self):
        out = ex_mod._agents_for({"agents": ["alice", "bob"]}, None)
        assert out == ["alice", "bob"]

    def test_no_agents_returns_empty(self):
        out = ex_mod._agents_for({}, None)
        assert out == []

    def test_filter_to_existing(self):
        out = ex_mod._agents_for({"agents": ["alice", "bob"]}, "bob")
        assert out == ["bob"]

    def test_filter_to_missing_raises(self):
        with pytest.raises(HTTPException) as exc:
            ex_mod._agents_for({"agents": ["alice"]}, "ghost")
        assert exc.value.status_code == 404


# ── render_* functions via build_export ─────────────────────


def _make_store(tmp_path):
    """Build a session store with a small conversation."""
    path = tmp_path / "s.kohakutr"
    store = SessionStore(str(path))
    store.init_meta("s1", "agent", "/p", "/w", ["alice"])
    # Append a few events to drive replay_conversation.
    store.append_event(
        "alice",
        "user_message",
        {"role": "user", "content": "hi"},
    )
    store.append_event(
        "alice",
        "assistant_text",
        {"role": "assistant", "content": "hello"},
    )
    store.flush()
    return store, path


class TestRenderMarkdown:
    def test_basic(self, tmp_path):
        store, _ = _make_store(tmp_path)
        try:
            out = ex_mod._render_markdown(store, "s", agent=None)
            assert "Session: s" in out
            assert "Agent" in out
        finally:
            store.close()

    def test_no_messages(self, tmp_path):
        path = tmp_path / "empty.kohakutr"
        store = SessionStore(str(path))
        store.init_meta("s1", "agent", "/p", "/w", ["alice"])
        try:
            out = ex_mod._render_markdown(store, "empty", agent=None)
            assert "no recorded conversation" in out
        finally:
            store.close()


class TestRenderHtml:
    def test_basic(self, tmp_path):
        store, _ = _make_store(tmp_path)
        try:
            out = ex_mod._render_html(store, "s", agent=None)
            assert "<html>" in out
            assert "alice" in out
        finally:
            store.close()

    def test_no_messages(self, tmp_path):
        path = tmp_path / "empty.kohakutr"
        store = SessionStore(str(path))
        store.init_meta("s1", "agent", "/p", "/w", ["alice"])
        try:
            out = ex_mod._render_html(store, "empty", agent=None)
            assert "no recorded conversation" in out
        finally:
            store.close()


class TestRenderJsonl:
    def test_basic(self, tmp_path):
        store, _ = _make_store(tmp_path)
        try:
            out = ex_mod._render_jsonl(store, "s", agent=None)
            lines = [json.loads(line) for line in out.strip().split("\n")]
            assert all("agent" in line for line in lines)
        finally:
            store.close()

    def test_empty_session(self, tmp_path):
        path = tmp_path / "empty.kohakutr"
        store = SessionStore(str(path))
        store.init_meta("s1", "agent", "/p", "/w", ["alice"])
        try:
            out = ex_mod._render_jsonl(store, "empty", agent=None)
            assert out == ""
        finally:
            store.close()


# ── build_export ────────────────────────────────────────────


class TestBuildExport:
    def test_unknown_format(self, tmp_path):
        store, _ = _make_store(tmp_path)
        try:
            with pytest.raises(HTTPException) as exc:
                ex_mod.build_export(store, "s", "pdf", None)
            assert exc.value.status_code == 400
        finally:
            store.close()

    def test_md_format(self, tmp_path):
        store, _ = _make_store(tmp_path)
        try:
            ct, body = ex_mod.build_export(store, "s", "md", None)
            assert "markdown" in ct
            assert body
        finally:
            store.close()

    def test_html_format(self, tmp_path):
        store, _ = _make_store(tmp_path)
        try:
            ct, body = ex_mod.build_export(store, "s", "html", None)
            assert "html" in ct
        finally:
            store.close()

    def test_jsonl_format(self, tmp_path):
        store, _ = _make_store(tmp_path)
        try:
            ct, body = ex_mod.build_export(store, "s", "jsonl", None)
            assert "jsonl" in ct
        finally:
            store.close()

    def test_with_agent_filter(self, tmp_path):
        store, _ = _make_store(tmp_path)
        try:
            ct, body = ex_mod.build_export(store, "s", "md", "alice")
            assert "alice" in body
        finally:
            store.close()


# ── helpers ─────────────────────────────────────────────────


class TestHtmlHelpers:
    def test_esc_escapes_html(self):
        assert ex_mod._esc("<script>") == "&lt;script&gt;"

    def test_esc_none(self):
        assert ex_mod._esc(None) == ""

    def test_html_head_includes_title(self):
        out = ex_mod._html_head("MySession")
        assert "MySession" in out
        assert "<html>" in out
