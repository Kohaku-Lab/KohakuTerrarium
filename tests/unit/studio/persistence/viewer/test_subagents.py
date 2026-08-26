"""Unit tests for persisted sub-agent viewer payloads."""

import pytest

from kohakuterrarium.errors import ConflictError, NotFoundError
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence.viewer.subagents import (
    build_subagent_conversation_payload,
    build_subagent_runs_payload,
)


class TestSubagentViewerPayloads:
    def test_lists_and_reads_exact_job(self, tmp_path):
        store = SessionStore(str(tmp_path / "session.kohakutr"))
        try:
            store.init_meta("session", "agent", "/p", "/w", ["parent"])
            store.save_subagent(
                "parent",
                "explore",
                0,
                {
                    "job_id": "agent_explore_11111111",
                    "task": "first task",
                    "success": True,
                },
                conv_json='{"messages":[{"role":"assistant","content":"first"}]}',
            )
            store.save_subagent(
                "parent",
                "explore",
                1,
                {
                    "job_id": "agent_explore_22222222",
                    "task": "second task",
                    "success": True,
                },
                conv_json='{"messages":[{"role":"assistant","content":"second"}]}',
            )

            listed = build_subagent_runs_payload(
                store, "session", parent="parent", name="explore"
            )
            assert [row["job_id"] for row in listed["runs"]] == [
                "agent_explore_11111111",
                "agent_explore_22222222",
            ]

            payload = build_subagent_conversation_payload(
                store,
                "session",
                parent="parent",
                job_id="agent_explore_11111111",
                name="explore",
                run=None,
            )
            assert payload["run"] == 0
            assert payload["job_id"] == "agent_explore_11111111"
            assert payload["live"] is False
            assert payload["can_receive"] is False
            assert payload["messages"][-1]["content"] == "first"
            assert payload["resolution"] == "exact_job_id"
        finally:
            store.close()

    def test_job_id_takes_precedence_and_rejects_conflicting_coordinates(
        self, tmp_path
    ):
        store = SessionStore(str(tmp_path / "precedence.kohakutr"))
        try:
            store.init_meta("precedence", "agent", "/p", "/w", ["parent"])
            store.save_subagent(
                "parent",
                "explore",
                0,
                {"job_id": "agent_explore_11111111", "task": "first"},
                conv_json='{"messages":[{"role":"assistant","content":"first"}]}',
            )
            store.save_subagent(
                "parent",
                "explore",
                1,
                {"job_id": "agent_explore_22222222", "task": "second"},
                conv_json='{"messages":[{"role":"assistant","content":"second"}]}',
            )

            with pytest.raises(ConflictError, match="requested sub-agent run"):
                build_subagent_conversation_payload(
                    store,
                    "precedence",
                    parent="parent",
                    job_id="agent_explore_11111111",
                    name="explore",
                    run=1,
                )
        finally:
            store.close()

    def test_legacy_fallback_requires_one_unambiguous_candidate(self, tmp_path):
        store = SessionStore(str(tmp_path / "legacy.kohakutr"))
        try:
            store.init_meta("legacy", "agent", "/p", "/w", ["parent"])
            store.save_subagent(
                "parent",
                "explore",
                0,
                {"task": "legacy first"},
                conv_json='{"messages":[{"role":"assistant","content":"legacy one"}]}',
            )

            unique = build_subagent_conversation_payload(
                store,
                "legacy",
                parent="parent",
                job_id="agent_explore_11111111",
                name="explore",
                run=None,
            )
            assert unique["run"] == 0
            assert unique["resolution"] == "unique_legacy_candidate"

            store.save_subagent(
                "parent",
                "explore",
                1,
                {"task": "legacy second"},
                conv_json='{"messages":[{"role":"assistant","content":"legacy two"}]}',
            )
            with pytest.raises(ConflictError, match="multiple legacy runs"):
                build_subagent_conversation_payload(
                    store,
                    "legacy",
                    parent="parent",
                    job_id="agent_explore_11111111",
                    name="explore",
                    run=None,
                )
        finally:
            store.close()

    def test_missing_identifiers_or_runs_are_not_found(self, tmp_path):
        store = SessionStore(str(tmp_path / "missing.kohakutr"))
        try:
            store.init_meta("missing", "agent", "/p", "/w", ["parent"])
            with pytest.raises(NotFoundError):
                build_subagent_conversation_payload(
                    store,
                    "missing",
                    parent="parent",
                    job_id="agent_explore_11111111",
                    name="explore",
                    run=None,
                )
        finally:
            store.close()
