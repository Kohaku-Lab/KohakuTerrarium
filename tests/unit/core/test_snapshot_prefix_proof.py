"""Unit coverage for the checkpoint summary proof and its consumed-identity rules."""

import pytest

from kohakuterrarium.core.snapshot_prefix_proof import (
    INVALID,
    SummaryProof,
    ancestor_path,
    prove_unknown_summary,
    selected_target_event_id,
    summary_identity,
)

_BODY = "proven summary body"
_PATH = {(1, 1), (2, 1)}
_SELECTED = {1: 1, 2: 1, 3: 1}
_TARGET = 100
_WATERMARK = 7


def _summary(body: str = _BODY, round_number: int = 1) -> dict:
    return {
        "role": "assistant",
        "content": f"[Previous context summary (compact round {round_number})]\n\n{body}",
        "metadata": {"compact_summary": True, "compact_source_path": None},
    }


def _completion(
    event_id: int,
    *,
    path=((1, 1), (2, 1)),
    round_number: int = 1,
    body: str = _BODY,
    replaced=(1, 2),
    result_ids=(),
    turn: int = 2,
    branch: int = 1,
    parents=None,
) -> dict:
    event = {
        "event_id": event_id,
        "type": "compact_complete",
        "round": round_number,
        "summary": body,
        "compact_path": [[t, b] for t, b in path],
        "compact_source_path": None,
        "compact_result_event_ids": list(result_ids),
        "replaced_from_event_id": replaced[0],
        "replaced_to_event_id": replaced[1],
        "turn_index": turn,
        "branch_id": branch,
    }
    if parents is not None:
        event["parent_branch_path"] = parents
    return event


def _base_events() -> list[dict]:
    """A compacted turns-1-2 lineage plus an original and an edited turn 3.

    The target and edit events carry high ids so crafted completions can sit
    between the compacted turns and the pre-target user event.
    """
    return [
        {
            "event_id": 1,
            "type": "user_message",
            "content": "raw_u1",
            "turn_index": 1,
            "branch_id": 1,
        },
        {
            "event_id": 2,
            "type": "user_message",
            "content": "raw_u2",
            "turn_index": 2,
            "branch_id": 1,
            "parent_branch_path": [[1, 1]],
        },
        {
            "event_id": _TARGET,
            "type": "user_message",
            "content": "orig_u3",
            "turn_index": 3,
            "branch_id": 1,
            "parent_branch_path": [[1, 1], [2, 1]],
        },
        {
            "event_id": _TARGET + 1,
            "type": "user_message",
            "content": "edited_u3",
            "turn_index": 3,
            "branch_id": 2,
            "parent_branch_path": [[1, 1], [2, 1]],
        },
    ]


def _job_events(call_ids: tuple[int, ...], result_id: int) -> list[dict]:
    events = _base_events()
    for event_id in call_ids:
        events.append(
            {
                "event_id": event_id,
                "type": "tool_call",
                "call_id": "job",
                "name": "echo",
                "args": {},
                "turn_index": 1,
                "branch_id": 1,
            }
        )
    events.append(
        {
            "event_id": result_id,
            "type": "tool_result",
            "call_id": "job",
            "name": "echo",
            "output": "consumed result",
            "turn_index": 2,
            "branch_id": 1,
            "parent_branch_path": [[1, 1]],
        }
    )
    return events


def _prove(message: dict, events: list[dict], **overrides):
    kwargs = {
        "events": events,
        "path": _PATH,
        "watermark": _WATERMARK,
        "target": _TARGET,
        "selected": _SELECTED,
    }
    kwargs.update(overrides)
    return prove_unknown_summary(message, **kwargs)


def test_summary_identity_requires_marker_and_nonempty_body() -> None:
    assert summary_identity(_summary()) == (1, _BODY)
    assert summary_identity({"content": "no marker"}) is None
    body = "[Previous context summary (compact round 1)]\n"
    assert summary_identity({"content": body}) is None
    assert summary_identity({"content": None}) is None


def test_ancestor_path_filters_by_target_and_rejects_malformed() -> None:
    assert ancestor_path([[1, 1], [2, 1], [3, 1]], 3) == {(1, 1), (2, 1)}
    assert ancestor_path([[1, 1], [3]], 3) is None
    assert ancestor_path([[1, "1"]], 3) is None


def test_selected_target_event_id_requires_one_live_original() -> None:
    events = _base_events()
    live = {event["event_id"] for event in events}
    assert selected_target_event_id(events, _SELECTED, live, 3, _PATH) == _TARGET
    # A second live user event on the selected turn makes the target ambiguous.
    events.append(
        {
            "event_id": _TARGET + 2,
            "type": "user_message",
            "content": "dup_u3",
            "turn_index": 3,
            "branch_id": 1,
            "parent_branch_path": [[1, 1], [2, 1]],
        }
    )
    assert (
        selected_target_event_id(events, _SELECTED, live | {_TARGET + 2}, 3, _PATH)
        is None
    )


def test_prove_unknown_summary_requires_target_and_identity() -> None:
    events = _base_events() + [_completion(5)]
    assert _prove(_summary(), events, target=None) is None
    assert _prove({"content": "plain"}, events) is None


@pytest.mark.parametrize("end", [5, 6])
def test_summary_cannot_cover_events_at_or_after_its_completion(end):
    events = _base_events() + [_completion(5, replaced=(1, end))]
    assert _prove(_summary(), events) is INVALID


@pytest.mark.parametrize("kind", ["tool_call", "tool_result"])
def test_consumed_result_and_owner_require_consistent_parent_ancestry(kind):
    events = _job_events((3,), result_id=4) + [_completion(5, result_ids=[4])]
    event = next(event for event in events if event["type"] == kind)
    event["parent_branch_path"] = [[1, 2]]
    assert _prove(_summary(), events) is INVALID


def test_prove_unknown_summary_positive() -> None:
    events = _base_events() + [_completion(5)]
    proof = _prove(_summary(), events)
    assert isinstance(proof, SummaryProof)
    assert proof.covered_event_ids == frozenset({1, 2})
    assert proof.last_turn == 2
    assert proof.consumed_result_ids == frozenset()


def test_prove_unknown_summary_ignores_completion_after_watermark_or_target() -> None:
    events = _base_events() + [_completion(5)]
    assert _prove(_summary(), events, watermark=4) is None
    # The completion must strictly precede the target user event.
    assert _prove(_summary(), events, target=5) is None
    assert isinstance(_prove(_summary(), events, watermark=5), SummaryProof)


def test_prove_unknown_summary_ambiguous_completions_are_not_proven() -> None:
    events = _base_events() + [_completion(5), _completion(6)]
    assert _prove(_summary(), events) is None


def test_prove_unknown_summary_rejects_foreign_completion_coordinates() -> None:
    events = _base_events() + [_completion(5, turn=1, branch=2)]
    assert _prove(_summary(), events) is INVALID


def test_prove_unknown_summary_rejects_sibling_parent() -> None:
    events = _base_events() + [_completion(5, parents=[[1, 2]])]
    assert _prove(_summary(), events) is INVALID


def test_prove_unknown_summary_requires_completion_inside_record_capture() -> None:
    """A short record cannot borrow a completion from a turn it never captured.

    The completion's ``compact_path`` is the only changed fact: it must lie
    inside the record's own pretarget ancestry, not merely inside the larger
    selected prefix the caller resolves for the edit target.
    """
    extra = _base_events() + [_completion(5, path=((1, 1), (2, 1), (3, 1)))]
    assert _prove(_summary(), extra) is None
    wider = _prove(_summary(), extra, path={(1, 1), (2, 1), (3, 1)})
    assert isinstance(wider, SummaryProof)


def test_prove_unknown_summary_positive_consumed_result() -> None:
    events = _job_events((5,), result_id=6) + [_completion(7, result_ids=[6])]
    proof = _prove(_summary(), events)
    assert isinstance(proof, SummaryProof)
    assert proof.consumed_result_ids == frozenset({6})
    assert proof.consumed_call_ids == frozenset({"job"})
    assert proof.consumed_call_event_ids == frozenset({5})


def test_prove_unknown_summary_rejects_result_that_does_not_precede_completion() -> (
    None
):
    events = _job_events((5,), result_id=7) + [_completion(6, result_ids=[7])]
    assert _prove(_summary(), events) is INVALID


def test_prove_unknown_summary_rejects_duplicate_owner_call() -> None:
    events = _job_events((5, 6), result_id=7) + [_completion(8, result_ids=[7])]
    assert _prove(_summary(), events, watermark=8) is INVALID


def test_prove_unknown_summary_rejects_owner_after_result() -> None:
    events = _job_events((6,), result_id=5) + [_completion(7, result_ids=[5])]
    assert _prove(_summary(), events) is INVALID


def test_prove_unknown_summary_rejects_consumed_result_without_owner() -> None:
    events = _base_events() + [
        {
            "event_id": 5,
            "type": "tool_result",
            "call_id": "ghost",
            "name": "echo",
            "output": "no producer",
            "turn_index": 2,
            "branch_id": 1,
            "parent_branch_path": [[1, 1]],
        },
        _completion(6, result_ids=[5]),
    ]
    assert _prove(_summary(), events) is INVALID


def test_prove_unknown_summary_does_not_trust_a_copied_summary() -> None:
    """A changed body or round is not the completion's identity."""
    events = _base_events() + [_completion(5)]
    assert _prove(_summary(body="different body"), events) is None
    assert _prove(_summary(round_number=2), events) is None
