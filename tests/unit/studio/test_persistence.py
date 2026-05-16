"""Unit tests for studio.persistence.{artifacts, history, resume, fork}."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence import (
    artifacts as artifacts_mod,
    fork as fork_mod,
    history as history_mod,
    resume as resume_mod,
)

# ── artifacts ───────────────────────────────────────────────


class TestResolveArtifactsDir:
    def test_direct_match(self, tmp_path):
        (tmp_path / "sess.artifacts").mkdir()
        out = artifacts_mod.resolve_artifacts_dir("sess", tmp_path)
        assert out == tmp_path / "sess.artifacts"

    def test_sibling_via_session_path(self, monkeypatch, tmp_path):
        sess = tmp_path / "sess.kohakutr"
        sess.touch()
        sib = tmp_path / "sess.artifacts"
        sib.mkdir()
        monkeypatch.setattr(
            artifacts_mod, "resolve_session_path_default", lambda n: sess
        )
        out = artifacts_mod.resolve_artifacts_dir("sess", tmp_path / "other")
        assert out == sib

    def test_not_found_raises(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            artifacts_mod, "resolve_session_path_default", lambda n: None
        )
        with pytest.raises(HTTPException) as exc:
            artifacts_mod.resolve_artifacts_dir("ghost", tmp_path)
        assert exc.value.status_code == 404


class TestResolveArtifactFile:
    def test_empty_filepath_raises(self, tmp_path):
        with pytest.raises(HTTPException) as exc:
            artifacts_mod.resolve_artifact_file(tmp_path, "")
        assert exc.value.status_code == 400

    def test_absolute_path_rejected(self, tmp_path):
        with pytest.raises(HTTPException):
            artifacts_mod.resolve_artifact_file(tmp_path, "/etc/passwd")

    def test_parent_traversal_rejected(self, tmp_path):
        with pytest.raises(HTTPException):
            artifacts_mod.resolve_artifact_file(tmp_path, "../escape")

    def test_outside_artifacts_rejected(self, tmp_path):
        artifacts = tmp_path / "art"
        artifacts.mkdir()
        # Symlinks could allow escape, but here we just trust the resolve.
        # Pass a name that resolves outside.
        with pytest.raises(HTTPException):
            artifacts_mod.resolve_artifact_file(artifacts, "../outside.png")

    def test_not_a_file_raises(self, tmp_path):
        (tmp_path / "subdir").mkdir()
        with pytest.raises(HTTPException) as exc:
            artifacts_mod.resolve_artifact_file(tmp_path, "subdir")
        assert exc.value.status_code == 404

    def test_valid_file(self, tmp_path):
        (tmp_path / "img.png").write_bytes(b"x")
        out = artifacts_mod.resolve_artifact_file(tmp_path, "img.png")
        assert out.name == "img.png"


# ── history ─────────────────────────────────────────────────


class TestHistoryIndexPayload:
    def test_basic(self, tmp_path):
        path = tmp_path / "s.kohakutr"
        store = SessionStore(str(path))
        store.init_meta("s1", "agent", "/p", "/w", ["alice"])
        store.append_event("alice", "user_input", {"content": "hi"})
        store.flush()
        store.close()
        out = history_mod.history_index_payload(path)
        assert out["session_name"] == "s"
        assert "meta" in out
        assert "targets" in out

    def test_failure_raises_500(self, tmp_path, monkeypatch):
        # Force SessionStore() to raise so the except branch fires.
        def _boom(p):
            raise RuntimeError("boom")

        monkeypatch.setattr(history_mod, "SessionStore", _boom)
        with pytest.raises(HTTPException) as exc:
            history_mod.history_index_payload(tmp_path / "any.kohakutr")
        assert exc.value.status_code == 500


class TestHistoryPayload:
    def test_unknown_target_404(self, tmp_path):
        path = tmp_path / "s.kohakutr"
        store = SessionStore(str(path))
        store.init_meta("s1", "agent", "/p", "/w", ["alice"])
        store.close()
        with pytest.raises(HTTPException) as exc:
            history_mod.history_payload(path, "ghost")
        assert exc.value.status_code == 404

    def test_valid_target(self, tmp_path):
        path = tmp_path / "s.kohakutr"
        store = SessionStore(str(path))
        store.init_meta("s1", "agent", "/p", "/w", ["alice"])
        store.append_event("alice", "user_input", {"content": "hi"})
        store.flush()
        store.close()
        out = history_mod.history_payload(path, "alice")
        assert out["session_name"] == "s"

    def test_failure_raises_500(self, tmp_path, monkeypatch):
        def _boom(p):
            raise RuntimeError("boom")

        monkeypatch.setattr(history_mod, "SessionStore", _boom)
        with pytest.raises(HTTPException) as exc:
            history_mod.history_payload(tmp_path / "any.kohakutr", "alice")
        assert exc.value.status_code == 500


# ── resume ──────────────────────────────────────────────────


class TestAnnounceMigration:
    def test_no_candidates_silent(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setattr(resume_mod, "discover_versions", lambda p: [])
        resume_mod.announce_migration_if_needed(tmp_path / "x.kohakutr")
        out = capsys.readouterr()
        assert "upgrading" not in out.out

    def test_already_latest_silent(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setattr(
            resume_mod,
            "discover_versions",
            lambda p: [(resume_mod.MAX_SUPPORTED_VERSION, p)],
        )
        resume_mod.announce_migration_if_needed(tmp_path / "x.kohakutr")
        out = capsys.readouterr()
        assert "upgrading" not in out.out

    def test_outdated_prints(self, monkeypatch, tmp_path, capsys):
        p_old = tmp_path / "x.v1.kohakutr"
        p_new = tmp_path / "x.v2.kohakutr"
        monkeypatch.setattr(resume_mod, "discover_versions", lambda p: [(1, p_old)])
        monkeypatch.setattr(resume_mod, "MAX_SUPPORTED_VERSION", 2)
        monkeypatch.setattr(resume_mod, "path_for_version", lambda p, v: p_new)
        resume_mod.announce_migration_if_needed(p_old)
        out = capsys.readouterr()
        assert "upgrading" in out.out


class TestResolveSessionKind:
    def test_agent_config_is_creature(self):
        assert resume_mod._resolve_session_kind({"config_type": "agent"}) == "creature"

    def test_terrarium_single_agent_is_creature(self):
        out = resume_mod._resolve_session_kind(
            {"config_type": "terrarium", "agents": ["alice"]}
        )
        assert out == "creature"

    def test_terrarium_multi_agent_stays_terrarium(self):
        out = resume_mod._resolve_session_kind(
            {"config_type": "terrarium", "agents": ["alice", "bob"]}
        )
        assert out == "terrarium"

    def test_terrarium_no_agents_is_creature(self):
        out = resume_mod._resolve_session_kind({"config_type": "terrarium"})
        assert out == "creature"


class TestFirstAgentName:
    def test_with_agents(self):
        assert resume_mod._first_agent_name({"agents": ["a", "b"]}) == "a"

    def test_no_agents(self):
        assert resume_mod._first_agent_name({}) is None

    def test_non_list(self):
        assert resume_mod._first_agent_name({"agents": "not-a-list"}) is None


class TestResumeSessionStudio:
    async def test_full_flow(self, monkeypatch, tmp_path):
        from kohakuterrarium.studio.sessions import lifecycle

        lifecycle._meta.clear()
        lifecycle._session_stores.clear()

        # Build a real saved store so resolve_session_kind sees real meta.
        path = tmp_path / "s.kohakutr"
        store = SessionStore(str(path))
        store.init_meta("s1", "agent", "/p", "/w", ["alice"])
        store.close()

        # Stub engine adopt_session.
        engine = MagicMock()
        engine.adopt_session = AsyncMock(return_value="g1")
        engine._session_stores = {"g1": store}
        engine.list_graphs = lambda: [
            SimpleNamespace(graph_id="g1", creature_ids=set())
        ]
        engine._environments = {}

        # Stub as_engine.
        monkeypatch.setattr(resume_mod, "as_engine", lambda s: engine)

        # Open the store again for the rest of the resume.
        store2 = SessionStore(str(path))
        engine._session_stores = {"g1": store2}

        session = await resume_mod.resume_session(MagicMock(), path)
        assert session.session_id == "g1"
        assert "g1" in lifecycle._meta
        store2.close()

        lifecycle._meta.clear()
        lifecycle._session_stores.clear()


class TestOpenStore:
    def test_delegates(self, monkeypatch):
        called = []
        monkeypatch.setattr(
            resume_mod,
            "_open_store_with_migration",
            lambda p: called.append(p) or "STORE",
        )
        assert resume_mod.open_store("path") == "STORE"
        assert called == [Path("path")] or called[0] == "path"


# ── fork ────────────────────────────────────────────────────


class TestForkTargetPath:
    def test_strips_kohakutr_suffix(self, tmp_path):
        parent = tmp_path / "sess.kohakutr"
        out = fork_mod.fork_target_path(parent, "fork1")
        assert "sess-fork1" in out.name

    def test_versioned_parent(self, tmp_path):
        parent = tmp_path / "sess.kohakutr.v2"
        out = fork_mod.fork_target_path(parent, "f1")
        assert "sess-f1" in out.name


class TestMutationFromPayload:
    def test_drop_trailing(self):
        fn = fork_mod.mutation_from_payload(
            "drop_trailing", None, {"type": "user_message"}
        )
        assert fn({"x": 1}) is None

    def test_edit_user_message_wrong_type(self):
        with pytest.raises(HTTPException):
            fork_mod.mutation_from_payload(
                "edit_user_message",
                {"content": "x"},
                {"type": "assistant_tool_calls"},
            )

    def test_edit_user_message_missing_content(self):
        with pytest.raises(HTTPException):
            fork_mod.mutation_from_payload(
                "edit_user_message", {}, {"type": "user_message"}
            )

    def test_edit_user_message_valid(self):
        fn = fork_mod.mutation_from_payload(
            "edit_user_message",
            {"content": "new"},
            {"type": "user_message"},
        )
        out = fn({"content": "old", "type": "user_message"})
        assert out["content"] == "new"

    def test_inject_user_message_valid(self):
        fn = fork_mod.mutation_from_payload(
            "inject_user_message",
            {"content": "x"},
            {"type": "user_message"},
        )
        out = fn({"x": 1})
        assert out["_appended_user_message"] == "x"

    def test_inject_user_message_missing_content(self):
        with pytest.raises(HTTPException):
            fork_mod.mutation_from_payload(
                "inject_user_message", {}, {"type": "user_message"}
            )

    def test_inject_tool_result_wrong_type(self):
        with pytest.raises(HTTPException):
            fork_mod.mutation_from_payload(
                "inject_tool_result",
                {"tool_call_id": "x", "output": "y"},
                {"type": "user_message"},
            )

    def test_inject_tool_result_missing_fields(self):
        with pytest.raises(HTTPException):
            fork_mod.mutation_from_payload(
                "inject_tool_result",
                {},
                {"type": "assistant_tool_calls"},
            )

    def test_inject_tool_result_valid(self):
        fn = fork_mod.mutation_from_payload(
            "inject_tool_result",
            {"tool_call_id": "tc", "output": "result"},
            {"type": "assistant_tool_calls"},
        )
        out = fn({"x": 1})
        assert out["_injected_tool_results"][0]["call_id"] == "tc"

    def test_unknown_kind(self):
        with pytest.raises(HTTPException):
            fork_mod.mutation_from_payload("ghost_op", None, {"type": "user_message"})


class TestFindForkPoint:
    def test_finds_event(self, tmp_path):
        path = tmp_path / "s.kohakutr"
        store = SessionStore(str(path))
        try:
            store.init_meta("s1", "agent", "/p", "/w", ["alice"])
            store.append_event("alice", "user_input", {"content": "hi"})
            store.flush()
            # Get all event ids.
            for _key, evt in store.get_all_events():
                eid = evt["event_id"]
                found = fork_mod.find_fork_point(store, eid)
                assert found is not None
                break
            assert fork_mod.find_fork_point(store, 999999) is None
        finally:
            store.close()


class TestForkSessionHandler:
    async def test_invalid_event_id(self, tmp_path):
        path = tmp_path / "s.kohakutr"
        store = SessionStore(str(path))
        store.init_meta("s1", "agent", "/p", "/w", ["alice"])
        store.close()
        with pytest.raises(HTTPException) as exc:
            await fork_mod.fork_session_handler(
                path,
                at_event_id=0,
                mutate_kind=None,
                mutate_args=None,
                name=None,
            )
        assert exc.value.status_code == 400

    async def test_event_not_found(self, tmp_path):
        path = tmp_path / "s.kohakutr"
        store = SessionStore(str(path))
        store.init_meta("s1", "agent", "/p", "/w", ["alice"])
        store.close()
        with pytest.raises(HTTPException) as exc:
            await fork_mod.fork_session_handler(
                path,
                at_event_id=999,
                mutate_kind=None,
                mutate_args=None,
                name=None,
            )
        assert exc.value.status_code == 400
