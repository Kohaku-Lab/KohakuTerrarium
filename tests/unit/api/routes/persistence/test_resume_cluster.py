"""Unit tests for persisted cluster-membership validation."""

import pytest
from fastapi import HTTPException

from kohakuterrarium.api.routes.persistence import resume_cluster


class _ExistingPath:
    def exists(self) -> bool:
        return True


class TestReadSavedClusterMembers:
    def test_absent_membership_is_single_session(self, monkeypatch):
        monkeypatch.setattr(resume_cluster, "read_session_meta", lambda _path: {})

        assert resume_cluster.read_saved_cluster_members(_ExistingPath()) is None

    @pytest.mark.parametrize(
        "raw",
        [
            [
                {"sid": "A", "on_node": "worker-a"},
                {"sid": "B"},
                {"sid": "C", "on_node": "worker-c"},
            ],
            [
                {"sid": "A", "on_node": "worker-a"},
                {"sid": "B"},
            ],
            [{"sid": "A", "on_node": "worker-a"}],
            "not-a-list",
        ],
    )
    def test_malformed_membership_fails_closed(self, monkeypatch, raw):
        monkeypatch.setattr(
            resume_cluster,
            "read_session_meta",
            lambda _path: {"cluster_members": raw},
        )

        with pytest.raises(HTTPException) as exc_info:
            resume_cluster.read_saved_cluster_members(_ExistingPath())

        assert exc_info.value.status_code == 422
        assert exc_info.value.detail["code"] == "corrupt_cluster_members"

    def test_unreadable_membership_fails_closed(self, monkeypatch):
        def unreadable(_path):
            raise RuntimeError("corrupt metadata")

        monkeypatch.setattr(resume_cluster, "read_session_meta", unreadable)

        with pytest.raises(HTTPException) as exc_info:
            resume_cluster.read_saved_cluster_members(_ExistingPath())

        assert exc_info.value.status_code == 422
        assert exc_info.value.detail["code"] == "corrupt_cluster_members"

    def test_non_object_metadata_fails_closed(self, monkeypatch):
        monkeypatch.setattr(
            resume_cluster,
            "read_session_meta",
            lambda _path: None,
        )

        with pytest.raises(HTTPException) as exc_info:
            resume_cluster.read_saved_cluster_members(_ExistingPath())

        assert exc_info.value.status_code == 422
        assert exc_info.value.detail["code"] == "corrupt_cluster_members"

    def test_complete_membership_is_preserved(self, monkeypatch):
        raw = [
            {"sid": "A", "on_node": "worker-a"},
            {"sid": "B", "on_node": "worker-b"},
            {"sid": "C", "on_node": "worker-c"},
        ]
        monkeypatch.setattr(
            resume_cluster,
            "read_session_meta",
            lambda _path: {"cluster_members": raw},
        )

        members = resume_cluster.read_saved_cluster_members(_ExistingPath())

        assert members == [
            resume_cluster.ClusterMember(sid="A", on_node="worker-a"),
            resume_cluster.ClusterMember(sid="B", on_node="worker-b"),
            resume_cluster.ClusterMember(sid="C", on_node="worker-c"),
        ]
