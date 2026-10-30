"""Tests for pending snapshot slot ownership resolution."""

from kohakuterrarium.core.conversation.settled_identity import pending_snapshot_jobs


def test_explicit_job_overrides_colliding_provider_alias():
    aliases = {"jobA": {"jobA"}, "jobB": {"jobA", "jobB"}, "providerB": {"jobB"}}
    assert pending_snapshot_jobs(
        [
            {
                "metadata": {"job_id": "jobB", "tool_result_phase": "pending"},
                "tool_call_id": "jobB",
            }
        ],
        aliases,
    ) == {"jobB"}
    assert (
        pending_snapshot_jobs(
            [{"metadata": {"tool_result_phase": "pending"}, "tool_call_id": "jobB"}],
            aliases,
        )
        == set()
    )
    assert pending_snapshot_jobs(
        [{"metadata": {"tool_result_phase": "pending", "tool_call_id": "providerB"}}],
        aliases,
    ) == {"jobB"}


def test_absent_unknown_and_final_slots_do_not_claim_pending_jobs():
    aliases = {"jobA": {"jobA"}, "providerA": {"jobA"}}
    assert (
        pending_snapshot_jobs(
            [
                {},
                {"metadata": None},
                {"metadata": {"tool_result_phase": "final", "job_id": "jobA"}},
                {
                    "metadata": {"tool_result_phase": "pending", "job_id": "unknown"},
                    "tool_call_id": "providerA",
                },
                {
                    "metadata": {"tool_result_phase": "pending"},
                    "tool_call_id": "unknown",
                },
            ],
            aliases,
        )
        == set()
    )
    assert pending_snapshot_jobs(
        [{"metadata": {"call_id": "jobA", "tool_result_phase": "pending"}}], aliases
    ) == {"jobA"}
