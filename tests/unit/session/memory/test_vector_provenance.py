"""Vectors must match the selected corpus generation and embedding model."""

import numpy as np
import pytest

from kohakuterrarium.session.memory.embedding import BaseEmbedder
from kohakuterrarium.session.memory.index import SessionMemory
from kohakuterrarium.session.store import SessionStore


class AxisEmbedder(BaseEmbedder):
    dimensions = 4

    def __init__(self, axis: int):
        self.axis = axis
        self.encoded = []

    def encode(self, texts):
        self.encoded.extend(texts)
        values = np.zeros((len(texts), 4), dtype=np.float32)
        values[:, self.axis] = 1
        return values


def _events():
    return [
        {
            "type": "user_input",
            "content": f"needle branch {branch}",
            "event_id": branch,
            "turn_index": 1,
            "branch_id": branch,
            "parent_branch_path": [],
        }
        for branch in (1, 2)
    ]


def test_keyword_only_v5_migration_rejects_old_vector_reads(tmp_path):
    path = str(tmp_path / "stale-vectors.kohakutr")
    rows = _events()
    original = SessionMemory(path, AxisEmbedder(0))
    try:
        original.index_events("alice", rows)
        assert original.get_stats()["vec_blocks"] > 0
    finally:
        original.close()

    keyword = SessionMemory(path, index_all_branches=True)
    try:
        keyword.index_events("alice", rows)
        assert keyword._state["memory:index_manifest"]["format"] == 5
        assert keyword.get_stats()["vec_blocks"] > 0
    finally:
        keyword.close()

    reopened = SessionMemory(path, AxisEmbedder(0), index_all_branches=True)
    try:
        with pytest.raises(ValueError, match="vector.*(stale|reindex|refresh)"):
            reopened.search("needle", mode="semantic", branch_view={1: 1})
        with pytest.raises(ValueError, match="vector.*(stale|reindex|refresh)"):
            reopened.search("needle", mode="hybrid", branch_view={1: 1})
        reopened.index_events("alice", rows)
        assert {
            hit.content
            for hit in reopened.search("needle", mode="semantic", branch_view={1: 1})
        } == {"needle branch 1"}
    finally:
        reopened.close()


def test_same_dimensions_different_model_cannot_mix_vectors(tmp_path):
    path = str(tmp_path / "mixed-models.kohakutr")
    rows = _events()
    a = SessionMemory(path, AxisEmbedder(0), index_all_branches=True)
    try:
        a.index_events("alice", rows[:1])
    finally:
        a.close()

    b = SessionMemory(path, AxisEmbedder(1), index_all_branches=True)
    try:
        with pytest.raises(ValueError, match="vector.*(stale|reindex|refresh)"):
            b.search("needle", mode="semantic", branch_view={1: 1})
        b.index_events("alice", rows)
        assert b.get_stats()["vec_blocks"] == 2
        distances = b._vec.search(np.array([0, 1, 0, 0], dtype=np.float32), k=2)
        assert all(float(distance) == 0 for _, distance, _ in distances)
        # One query embedding preceded the full two-event rebuild.
        assert b._embedder.encoded[-2:] == ["needle branch 1", "needle branch 2"]
    finally:
        b.close()


def test_vector_cursor_behind_keywords_cannot_return_partial_semantic_results(tmp_path):
    path = str(tmp_path / "partial-vector-cursor.kohakutr")
    rows = _events()
    memory = SessionMemory(path, AxisEmbedder(0), index_all_branches=True)
    try:
        memory.index_events("alice", rows[:1])
        memory._index_keywords("alice", rows)
        with pytest.raises(ValueError, match="vector.*(stale|reindex|refresh)"):
            memory.search("needle", mode="semantic", branch_view={1: 1})
        memory.index_events("alice", rows)
        assert memory.get_stats()["vec_blocks"] == 2
    finally:
        memory.close()


def test_cli_search_refreshes_keywords_without_writing_wrong_model_vectors(
    tmp_path, monkeypatch, capsys
):
    from kohakuterrarium.cli import memory as cli_memory

    path = tmp_path / "cli.kohakutr"
    store = SessionStore(path)
    store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
    store.append_event(
        "alice",
        "user_input",
        {"content": "needle first"},
        turn_index=1,
        branch_id=1,
        parent_branch_path=[],
    )
    store.state["embedding_config"] = {"provider": "api", "model": "saved-A"}
    store.close(update_status=False)
    rows = _events()[:1]
    stored = SessionMemory(str(path), AxisEmbedder(0), index_all_branches=True)
    try:
        stored.index_events("alice", rows)
        count = stored.get_stats()["vec_blocks"]
    finally:
        stored.close()

    store = SessionStore(path)
    store.append_event(
        "alice",
        "user_input",
        {"content": "needle later"},
        turn_index=2,
        branch_id=1,
        parent_branch_path=[[1, 1]],
    )
    store.close(update_status=False)

    called = []

    def factory(config):
        called.append(config)
        return AxisEmbedder(1)  # Different model, equal dimension.

    monkeypatch.setattr(cli_memory, "_resolve_session", lambda _: path)
    monkeypatch.setattr(cli_memory, "create_embedder", factory)
    cli_memory.search_cli("s", "needle", mode="hybrid")
    assert called == [{"provider": "api", "model": "saved-A"}]

    check = SessionMemory(str(path), index_all_branches=True)
    try:
        assert check.get_stats()["vec_blocks"] == count
        assert {
            r.content
            for r in check.search("needle", mode="fts", branch_view={1: 1, 2: 1})
        } == {"needle first", "needle later"}
    finally:
        check.close()
