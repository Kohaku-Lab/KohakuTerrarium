"""Unit tests for :mod:`kohakuterrarium.session.reader` (E9).

Drives the reader against a REAL session produced by a real engine +
ScriptedLLM run — the reader's whole point is "no spelunking", so the
test reads back exactly what a programmatic run wrote.
"""

import pytest

from kohakuterrarium.session.reader import SessionReader
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.terrarium.engine import Terrarium
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
        from kohakuterrarium.session.memory import SessionMemory

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
