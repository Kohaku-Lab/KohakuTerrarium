"""Explicit old-index anchor migration; never run as an implicit Search step."""

import numpy as np
import pytest

import kohakuterrarium.cli.memory as memory_cli
from kohakuterrarium.cli._main import COMMANDS, _build_parser
from kohakuterrarium.session.memory.embedding import BaseEmbedder
from kohakuterrarium.session.memory.index import SessionMemory
from kohakuterrarium.session.store import SessionStore


class FakeEmbedding(BaseEmbedder):
    dimensions = 4
    calls = 0

    def encode(self, texts):
        type(self).calls += 1
        return np.ones((len(texts), 4), dtype=np.float32)

    def encode_one(self, query):
        return np.ones(4, dtype=np.float32)


def _legacy_index(tmp_path, *, with_vectors=False):
    path = tmp_path / "old-session.kohakutr"
    store = SessionStore(path)
    store.init_meta("s", "agent", "", str(tmp_path), ["alice", "bob"])
    embedder = FakeEmbedding() if with_vectors else None
    memory = SessionMemory(str(path), embedder)
    try:
        for agent in ("alice", "bob"):
            store.append_event(
                agent, "user_input", {"content": f"{agent.upper()}_OLD_SOURCE"}
            )
            memory.index_events(agent, store.get_events(agent))
        assert "source_ref" not in memory._fts.get_by_id(1)[1]
    finally:
        memory.close()
        store.close(update_status=False)
    return path


def test_real_cli_routes_explicit_agent_without_encoding_again(tmp_path, capsys):
    FakeEmbedding.calls = 0
    path = _legacy_index(tmp_path, with_vectors=True)
    calls = FakeEmbedding.calls
    parser = _build_parser()
    args = parser.parse_args(["migrate-memory-anchors", str(path), "--agent", "alice"])
    assert COMMANDS[args.command](args) == 0
    assert "FTS patched=1" in capsys.readouterr().out
    assert FakeEmbedding.calls == calls
    with_index = SessionMemory(str(path))
    try:
        fts = [
            with_index._fts.get_by_id(row)[1]
            for row in with_index._fts.keys(limit=with_index._fts.count())
        ]
        assert {meta["agent"] for meta in fts} == {"alice", "bob"}
        for meta in fts:
            assert ("source_ref" in meta) == (meta["agent"] == "alice")
        assert (
            with_index.search("ALICE_OLD_SOURCE", mode="fts")[0].source_ref
            == "alice:e000000"
        )
        assert with_index._vec.get_by_id(1)[1]["source_ref"] == "alice:e000000"
        assert "source_ref" not in with_index._vec.get_by_id(2)[1]
    finally:
        with_index.close()
    # Running the command again is a no-op, and still does not embed.
    assert COMMANDS[args.command](args) == 0
    assert FakeEmbedding.calls == calls


def test_cli_rejects_live_session_and_does_not_write(tmp_path, capsys):
    path = _legacy_index(tmp_path)
    active = SessionStore(path)
    try:
        assert memory_cli.migrate_anchors_cli(str(path), "alice") == 1
        assert "already open" in capsys.readouterr().out
    finally:
        active.close(update_status=False)
    restored = SessionMemory(str(path))
    try:
        assert "source_ref" not in restored._fts.get_by_id(1)[1]
    finally:
        restored.close()


def test_cli_failure_does_not_claim_success(tmp_path, capsys):
    path = _legacy_index(tmp_path)
    store = SessionStore(path)
    try:
        store.append_event("alice", "user_input", {"content": "STALE_SOURCE"})
    finally:
        store.close(update_status=False)
    assert memory_cli.migrate_anchors_cli(str(path), "alice") == 1
    output = capsys.readouterr().out
    assert "refused or incomplete" in output
    assert "Done." not in output
    index = SessionMemory(str(path))
    try:
        assert "source_ref" not in index._fts.get_by_id(1)[1]
    finally:
        index.close()


def test_required_cli_flags_expose_explicit_nonimplicit_contract():
    parser = _build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["migrate-memory-anchors", "old.kohakutr"])
    assert exc.value.code == 2
    args = parser.parse_args(
        ["migrate-memory-anchors", "old.kohakutr", "--agent", "alice"]
    )
    assert args.agent == "alice" and args.command == "migrate-memory-anchors"
