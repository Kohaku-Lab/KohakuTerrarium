"""Unit tests for :mod:`kohakuterrarium.session.readonly.reader` (E9).

Drives the reader against a REAL session produced by a real engine +
ScriptedLLM run — the reader's whole point is "no spelunking", so the
test reads back exactly what a programmatic run wrote.
"""

import pytest

from kohakuterrarium.session.readonly.reader import SessionReader
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.terrarium.engine.engine import Terrarium
from kohakuterrarium.testing.llm import ScriptedLLM


def _write_cfg(tmp_path, name="reader-agent"):
    d = tmp_path / "creature"
    d.mkdir(exist_ok=True)
    (d / "config.yaml").write_text(
        f"name: {name}\ninput:\n  type: none\noutput:\n  type: none\n",
        encoding="utf-8",
    )
    return d


@pytest.fixture
async def recorded_session(tmp_path):
    """Run two real turns through the engine, return the session path."""
    cfg_dir = _write_cfg(tmp_path)
    target = tmp_path / "run.kohakutr"
    t = Terrarium(pwd=str(tmp_path))
    try:
        c = await t.add_creature(
            str(cfg_dir),
            llm=ScriptedLLM(["First reply about score.json.", "Second reply."]),
            io="headless",
            session=str(target),
        )
        await c.run("grade the notebook")
        await c.run("now summarize")
    finally:
        await t.shutdown()
    return target


class TestSessionReader:
    async def test_meta_and_agents(self, recorded_session):
        with SessionReader(recorded_session) as r:
            assert r.meta["config_type"] == "agent"
            assert r.meta["status"] == "paused"
            assert r.agents == ["reader-agent"]

    async def test_turns_reassembled(self, recorded_session):
        with SessionReader(recorded_session) as r:
            turns = r.turns()
            assert len(turns) == 2
            assert turns[0].user_text == "grade the notebook"
            assert "First reply" in turns[0].assistant_text
            assert turns[1].user_text == "now summarize"
            assert "Second reply" in turns[1].assistant_text

    async def test_events_and_conversation(self, recorded_session):
        with SessionReader(recorded_session) as r:
            events = r.events()
            assert any(e.get("type") == "user_input" for e in events)
            convo = r.conversation()
            assert any(m.get("role") == "assistant" for m in convo)

    async def test_reading_never_mutates_meta(self, recorded_session):
        with SessionReader(recorded_session) as r:
            before = dict(r.meta)
            r.turns()
            r.events()
        check = SessionStore.open_readonly(recorded_session)
        try:
            after = check.load_meta()
            assert after["status"] == before["status"] == "paused"
            assert after["last_active"] == before["last_active"]
        finally:
            check.close()

    async def test_index_then_search(self, recorded_session):
        with SessionReader(recorded_session) as r:
            indexed = r.index()
            assert indexed > 0
            hits = r.search("score.json")
            assert hits
            assert any("score.json" in h.content for h in hits)

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            SessionReader(tmp_path / "ghost.kohakutr")


class TestSemanticModeStrict:
    def test_explicit_semantic_without_embedder_raises(self, tmp_path):
        from kohakuterrarium.session.memory.index import SessionMemory

        store = SessionStore(tmp_path / "m.kohakutr")
        store.init_meta("x", "agent", "", ".", ["a"])
        store.close()
        memory = SessionMemory(str(tmp_path / "m.kohakutr"))
        try:
            # E4: explicit semantic used to silently degrade to FTS.
            with pytest.raises(ValueError, match="embedding model"):
                memory.search("q", mode="semantic")
            with pytest.raises(ValueError, match="Unknown search mode"):
                memory.search("q", mode="telepathy")
        finally:
            memory.close()


def test_legacy_index_migrates_on_search_but_never_indexes_an_empty_reader(tmp_path):
    from kohakuterrarium.session.memory.index import SessionMemory

    path = tmp_path / "old-index.kohakutr"
    store = SessionStore(path)
    store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
    for branch in (1, 2):
        store.append_event(
            "alice",
            "user_input",
            {"content": f"needle branch {branch}"},
            turn_index=1,
            branch_id=branch,
            parent_branch_path=[],
        )
    store.state["alice:branch_view"] = {"1": 1}
    rows = store.get_events("alice")
    store.close(update_status=False)

    with SessionReader(path) as reader:
        assert reader.search("needle") == []
        original_meta = dict(reader.meta)

    original = SessionMemory(str(path))
    try:
        assert original.index_events("alice", rows) == 1
        assert original._state["memory:index_manifest"]["format"] == 1
    finally:
        original.close()

    with SessionReader(path) as reader:
        results = reader.search("needle")
        assert {result.content for result in results} == {"needle branch 1"}
        assert reader.meta == original_meta

    upgraded = SessionMemory(str(path), index_all_branches=True)
    try:
        assert upgraded._state["memory:index_manifest"]["format"] == 3
        assert upgraded.index_events("alice", rows) == 0
        assert upgraded.get_stats()["fts_blocks"] == 2
    finally:
        upgraded.close()


def test_indexed_reader_search_reuses_source_proof_without_discovery(
    tmp_path, monkeypatch
):
    """Reader.search() must not bypass Source Prefix Closure through .agents."""
    from contextlib import closing

    from kohakuterrarium.session.memory.index import SessionMemory
    from kohakuterrarium.session.memory.reconcile import SessionIndexReconciler

    path = tmp_path / "reader-steady.kohakutr"
    with closing(SessionStore(path)) as writer:
        writer.init_meta("s", "terrarium", "", str(tmp_path), ["alice"])
        writer.append_event(
            "alice",
            "user_input",
            {"content": "alice first-needle"},
            turn_index=1,
            branch_id=1,
            parent_branch_path=[],
        )
        with closing(SessionMemory(str(path), index_all_branches=True)) as memory:
            reconciler = SessionIndexReconciler(writer, memory)
            reconciler.build(reconciler.capture([], all_agents=True))

    with SessionReader(path) as reader:

        def cannot_discover():
            raise AssertionError("Reader stable query scanned all source keys")

        with monkeypatch.context() as patch:
            patch.setattr(reader._store, "_event_key_names", cannot_discover)
            for _ in range(3):
                assert [hit.content for hit in reader.search("first-needle")] == [
                    "alice first-needle"
                ]
        # An undeclared Agent added through the supported writer API must be
        # discovered after physical-growth conservation fails, not hidden.
        with closing(SessionStore(path)) as writer:
            writer.append_event(
                "charlie",
                "user_input",
                {"content": "charlie new-needle"},
                turn_index=1,
                branch_id=1,
                parent_branch_path=[],
            )
        hits = reader.search("new-needle")
        assert {hit.content for hit in hits} == {"charlie new-needle"}


def test_explicit_reader_index_discovers_dynamic_roster_once(tmp_path, monkeypatch):
    """Full index admits undeclared KT Agents, without duplicate pre-discovery."""
    from contextlib import closing

    path = tmp_path / "reader-explicit-index.kohakutr"
    with closing(SessionStore(path)) as writer:
        writer.init_meta("s", "terrarium", "", str(tmp_path), ["alice"])
        for name in ("alice", "charlie"):
            writer.append_event(
                name,
                "user_input",
                {"content": f"{name} source-marker"},
                turn_index=1,
                branch_id=1,
                parent_branch_path=[],
            )

    with SessionReader(path) as reader:
        original = reader._store._event_key_names
        scans = []

        def tracked():
            scans.append(True)
            return original()

        monkeypatch.setattr(reader._store, "_event_key_names", tracked)
        assert reader.index() == 2
        assert len(scans) == 1
        assert {hit.content for hit in reader.search("source-marker")} == {
            "alice source-marker",
            "charlie source-marker",
        }
        assert len(scans) == 1
