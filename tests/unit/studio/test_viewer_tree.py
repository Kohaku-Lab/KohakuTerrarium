"""Unit tests for :mod:`kohakuterrarium.studio.persistence.viewer.tree`."""

from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence.viewer.tree import build_tree_payload


def _store(tmp_path, name="s.kohakutr") -> SessionStore:
    return SessionStore(str(tmp_path / name))


class TestBuildTreePayload:
    def test_minimal(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.init_meta("sess", "agent", "/p", "/w", ["alice"])
            payload = build_tree_payload(s, "sess")
            assert payload["session_name"] == "sess"
            assert payload["session_id"] == "sess"
            assert len(payload["nodes"]) == 1
            assert payload["nodes"][0]["is_focus"] is True
            assert payload["edges"] == []
        finally:
            s.close()

    def test_with_parent_lineage(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.init_meta("sess", "agent", "/p", "/w", ["alice"])
            s.meta["lineage"] = {
                "fork": {"parent_session_id": "parent", "fork_point": 5}
            }
            payload = build_tree_payload(s, "sess")
            node_ids = [n["id"] for n in payload["nodes"]]
            assert "parent" in node_ids
            # Parent → child edge.
            edges = [e for e in payload["edges"] if e["type"] == "fork"]
            assert any(e["from"] == "parent" and e["to"] == "sess" for e in edges)
        finally:
            s.close()

    def test_with_forked_children(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.init_meta("sess", "agent", "/p", "/w", ["alice"])
            s.meta["forked_children"] = [
                {"session_id": "child-1", "fork_point": 3, "fork_created_at": "t"},
                {"session_id": "child-2", "fork_point": 4, "fork_created_at": "t"},
            ]
            payload = build_tree_payload(s, "sess")
            ids = {n["id"] for n in payload["nodes"]}
            assert {"child-1", "child-2"} <= ids
            child_edges = [e for e in payload["edges"] if e["type"] == "fork"]
            assert len(child_edges) == 2
        finally:
            s.close()

    def test_skips_malformed_children(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.init_meta("sess", "agent", "/p", "/w", ["alice"])
            s.meta["forked_children"] = [
                "not-a-dict",
                {},  # missing session_id
                {"session_id": "real-child"},
            ]
            payload = build_tree_payload(s, "sess")
            ids = [n["id"] for n in payload["nodes"]]
            assert "real-child" in ids

        finally:
            s.close()

    def test_with_attached_agents(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.init_meta("sess", "agent", "/p", "/w", ["alice"])
            s.append_event("alice:attached:reviewer:3", "x", {})
            s.flush()
            payload = build_tree_payload(s, "sess")
            attached_nodes = [n for n in payload["nodes"] if n["type"] == "attached"]
            assert len(attached_nodes) == 1
            assert attached_nodes[0]["role"] == "reviewer"
            attach_edges = [e for e in payload["edges"] if e["type"] == "attach"]
            assert len(attach_edges) == 1
        finally:
            s.close()
