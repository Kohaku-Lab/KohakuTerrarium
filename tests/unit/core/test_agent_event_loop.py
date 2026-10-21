"""Unit tests for ``AgentEventLoopMixin._message_origin`` provenance.

The origin dict the event loop declares for a batch is what the controller
writes onto the produced user message, so it must carry the branch position
*and* the issuing job ids of any folded result. Compaction resolves those ids
back to issuing turns through :func:`job_origin_pairs`; an id that names no
persisted tool call must still yield "unknown" rather than a partial path.
"""

from kohakuterrarium.core.agent_event_loop import AgentEventLoopMixin
from kohakuterrarium.core.compact_branch import job_origin_pairs, summary_source_path
from kohakuterrarium.core.events import (
    TriggerEvent,
    create_tool_complete_event,
    create_user_input_event,
)
from kohakuterrarium.llm.message import create_message


class _Loop(AgentEventLoopMixin):
    """Minimal host exposing only the coordinates ``_message_origin`` reads."""

    def __init__(self, turn_index: int | None = 3, branch_id: int | None = 1) -> None:
        self._turn_index = turn_index
        self._branch_id = branch_id


def _message(origin: dict, *, kind: str | None = None):
    msg = create_message("user", "feedback")
    if kind:
        msg.metadata["kind"] = kind
    msg.metadata.update(origin)
    return msg


def _events_for(*job_ids: str) -> list[dict]:
    """Persisted log echoing the tool calls that issued ``job_ids``."""
    return [
        {
            "type": "tool_call",
            "call_id": job_id,
            "job_id": job_id,
            "turn_index": 2,
            "branch_id": 1,
        }
        for job_id in job_ids
    ]


def test_pure_tool_feedback_names_issuing_job():
    origin = _Loop()._message_origin([create_tool_complete_event("bg", "done")])

    assert origin == {
        "turn_index": 3,
        "branch_id": 1,
        "origin_known": True,
        "job_ids": ["bg"],
    }
    message = _message(origin, kind="tool_results")
    assert summary_source_path(
        [message], origins=job_origin_pairs(_events_for("bg"))
    ) == {(3, 1), (2, 1)}


def test_mixed_batch_keeps_position_and_job_ids():
    origin = _Loop()._message_origin(
        [create_user_input_event("hi"), create_tool_complete_event("bg", "done")]
    )

    assert origin == {
        "turn_index": 3,
        "branch_id": 1,
        "origin_known": True,
        "job_ids": ["bg"],
    }
    # A batch carrying real user input is never tagged as tool feedback.
    message = _message(origin)
    assert summary_source_path(
        [message], origins=job_origin_pairs(_events_for("bg"))
    ) == {(3, 1), (2, 1)}


def test_unresolvable_job_id_yields_no_provenance():
    origin = _Loop()._message_origin([create_tool_complete_event("ghost", "done")])

    # The id is named so the resolver may check it; nothing resolves it here.
    assert origin["origin_known"] is True
    assert origin["job_ids"] == ["ghost"]
    assert summary_source_path([_message(origin)], origins=job_origin_pairs([])) is None


def test_result_without_job_id_is_unknown():
    origin = _Loop()._message_origin(
        [
            create_tool_complete_event("bg", "done"),
            TriggerEvent(type="tool_complete", content="wake"),
        ]
    )

    assert origin == {"turn_index": 3, "branch_id": 1, "origin_known": False}
    assert (
        summary_source_path(
            [_message(origin)], origins=job_origin_pairs(_events_for("bg"))
        )
        is None
    )


def test_empty_internal_wake_does_not_erase_known_provenance():
    origin = _Loop()._message_origin(
        [
            create_tool_complete_event("bg", "done"),
            TriggerEvent(type="tool_complete", content=""),
        ]
    )

    # A wake that contributes no text names no issuer and proves nothing,
    # yet it must not downgrade the result that does name its job.
    assert origin["origin_known"] is True
    assert origin["job_ids"] == ["bg"]


def test_peer_channel_content_claims_only_local_position():
    origin = _Loop()._message_origin(
        [TriggerEvent(type="channel_message", content="peer says hi")]
    )

    # Known current-turn position, no invented cross-creature ancestry.
    assert origin == {"turn_index": 3, "branch_id": 1, "origin_known": True}
    assert summary_source_path([_message(origin)]) == {(3, 1)}


def test_missing_branch_coordinates_declare_nothing():
    for turn_index, branch_id in ((None, 1), (3, None)):
        origin = _Loop(turn_index, branch_id)._message_origin(
            [create_tool_complete_event("bg", "done")]
        )
        assert origin == {}
        assert summary_source_path([_message(origin)]) is None
