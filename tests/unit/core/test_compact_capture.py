"""Actual captured-message coverage for compact metadata.

The persisted replacement span must describe the messages a compaction
actually summarized, not a live-turn count heuristic. These tests pin:

* ``live_turn_window`` counts the leading system prefix, not a hard-coded 1;
* job origins include sub-agent dispatches and reject reused/ambiguous ids;
* a long single-turn tool round with a mid-turn boundary still persists a
  usable, bounded span;
* a partial/reattached user turn never swallows its own event;
* consumed summaries inherit coverage only from their exact, compatible
  ``compact_complete`` completion;
* an unprovable event inside a span aborts the span instead of dropping raw
  history from replay.
Residual uncovered ``compact_binding`` guards are defensive branches (non-``Message`` payloads, non-dict tool calls, duplicated round starts) that real producers cannot emit; they are left uncovered rather than padded with synthetic objects.
"""

import asyncio
from types import SimpleNamespace

import pytest

from kohakuterrarium.core.compact_branch import (
    capture_compact_metadata,
    compact_rule_from_event,
    job_origin_pairs,
    live_turn_window,
    summary_result_event_ids,
    summary_source_path,
)
from kohakuterrarium.core.compact_capture import (
    compute_captured_range,
    compute_replaced_range,
    matching_compact_complete,
    message_pairs,
    uses_canonical_rounds,
)
from kohakuterrarium.core.compact_binding import resolve_capture_ids
from kohakuterrarium.llm.message import create_message


class _Store:
    def __init__(self, events, on_read=None):
        self.events = events
        self.on_read = on_read

    def get_events(self, name):
        return list(self.events)

    async def run(self, fn, store, name):
        if self.on_read is not None:
            self.on_read()
        return fn(store, name)


def _agent(turn=2, branch=1, path=((1, 1),)):
    return SimpleNamespace(
        _turn_index=turn,
        _branch_id=branch,
        _parent_branch_path=list(path),
    )


def _event(eid, etype, turn=1, branch=1, **extra):
    return {
        "event_id": eid,
        "type": etype,
        "turn_index": turn,
        "branch_id": branch,
        **extra,
    }


def _user(content, turn, branch=1, **metadata):
    return create_message(
        "user", content, metadata={"turn_index": turn, "branch_id": branch, **metadata}
    )


def _tool(content, job_id, event_id=None):
    metadata = {"job_id": job_id, "tool_result_phase": "final"}
    if event_id is not None:
        metadata["result_event_id"] = event_id
    return create_message("tool", content, tool_call_id=job_id, metadata=metadata)


def _summary(round_no, body, **metadata):
    return create_message(
        "assistant",
        f"[Previous context summary (compact round {round_no})]\n\n{body}",
        metadata={"compact_summary": True, **metadata},
    )


def _capture(
    events,
    captured,
    retained=(),
    keep_count=0,
    retain_latest_user=False,
    agent=None,
    on_read=None,
):
    return asyncio.run(
        capture_compact_metadata(
            _Store(events, on_read),
            "alice",
            agent or _agent(),
            captured,
            keep_count,
            2,
            retained_messages=retained,
            retain_latest_user=retain_latest_user,
        )
    )


def _rule(metadata):
    return compact_rule_from_event({"type": "compact_complete", **metadata})


class TestLiveTurnWindowLeading:
    def test_systemless_compact_zone_counts_first_user(self):
        messages = [
            _user("goal", 1),
            create_message("assistant", "plan"),
            create_message("assistant", "work"),
            _user("next", 2),
        ]
        # boundary > preferred: the first user turn is reattached from the
        # compact zone. ``messages[1:]`` missed it when index 0 was the goal.
        assert live_turn_window(messages, 3, 2, 0) == 2

    def test_one_leading_system_is_skipped(self):
        messages = [
            create_message("system", "SYS"),
            _user("goal", 1),
            create_message("assistant", "work"),
            _user("next", 2),
        ]
        assert live_turn_window(messages, 3, 2, 0) == 2


class TestJobOriginPairs:
    def test_subagent_dispatch_resolves(self):
        events = [_event(1, "subagent_call", turn=2, job_id="agent_x")]
        assert job_origin_pairs(events) == {"agent_x": (2, 1)}

    def test_reused_id_across_branches_is_ambiguous(self):
        events = [
            _event(1, "tool_call", job_id="c1", call_id="c1"),
            _event(2, "tool_call", branch=2, job_id="c1", call_id="c1"),
            _event(3, "subagent_call", turn=3, job_id="agent_ok"),
        ]
        assert job_origin_pairs(events) == {"agent_ok": (3, 1)}

    def test_identity_on_both_fields_resolves_once(self):
        # Internal ``job_id`` and provider ``call_id`` may differ; both name
        # the same issuing position.
        events = [_event(1, "tool_call", job_id="job_1", call_id="call_1")]
        assert job_origin_pairs(events) == {
            "job_1": (1, 1),
            "call_1": (1, 1),
        }


class TestLongToolRoundCoverage:
    def test_single_turn_mid_turn_boundary_still_has_current_coverage(self):
        events = [
            _event(1, "user_message"),
            _event(2, "tool_call", call_id="c1", job_id="c1"),
            _event(3, "tool_result", call_id="c1", job_id="c1"),
            _event(4, "text_chunk"),
            _event(5, "tool_call", call_id="c2", job_id="c2"),
            _event(6, "tool_result", call_id="c2", job_id="c2"),
            _event(7, "text_chunk"),
        ]
        captured = [
            _user("goal", 1),
            create_message("assistant", "round 1"),
            _tool("r1", "c1", event_id=3),
            create_message("assistant", "round 2"),
            _tool("r2", "c2", event_id=6),
        ]
        retained = [create_message("assistant", "live tail")]

        metadata = _capture(events, captured, retained)

        assert metadata["replaced_from_event_id"] == 3
        assert metadata["replaced_to_event_id"] == 6
        rule = _rule(metadata)
        assert rule is not None
        assert rule.covers(3, 1, 1) is True
        assert rule.covers(6, 1, 1) is True
        # The retained tail stays live: no false coverage beyond the proven
        # captured prefix.
        assert rule.covers(7, 1, 1) is False
        assert rule.priority({(1, 1), (2, 1)})[0] == 2

    def test_complete_prefix_stops_before_retained_user(self):
        events = [
            _event(1, "user_message", turn=1),
            _event(2, "text_chunk", turn=1),
            _event(3, "user_message", turn=2),
            _event(4, "text_chunk", turn=2),
            _event(5, "user_message", turn=3),
            _event(6, "text_chunk", turn=3),
        ]
        captured = [_user("u1", 1), _user("u2", 2)]
        retained = [_user("u3", 3)]
        assert compute_captured_range(
            events, {(1, 1), (2, 1), (3, 1)}, captured, retained
        ) == (1, 4)

    def test_reattached_user_event_stays_live(self):
        events = [
            _event(4, "user_message", turn=3),
            _event(5, "tool_result", turn=3, call_id="c1", job_id="c1"),
            _event(6, "text_chunk", turn=3),
        ]
        captured = [_user("u3", 3), _tool("r1", "c1", event_id=5)]
        retained = [create_message("assistant", "live")]
        metadata = _capture(
            events,
            captured,
            retained,
            keep_count=1,
            retain_latest_user=True,
            agent=_agent(turn=3, path=[(1, 1), (2, 1)]),
        )
        rule = _rule(metadata)
        assert rule is not None
        assert metadata["replaced_from_event_id"] == 5
        assert rule.covers(4, 3, 1) is False
        assert rule.covers(5, 3, 1) is True

    def test_unprovable_event_inside_span_refuses_coverage(self):
        # A merged/migrated event from the previous turn sits between two
        # captured events. A contiguous range would swallow it.
        events = [
            _event(3, "user_message", turn=2),
            _event(4, "text_chunk", turn=2),
            _event(5, "text_chunk", turn=1),
            _event(6, "tool_result", turn=2, call_id="c", job_id="c"),
        ]
        captured = [_user("u2", 2), _tool("r", "c", event_id=6)]
        assert (
            compute_captured_range(events, {(1, 1), (2, 1)}, captured, [], keep_count=0)
            is None
        )


class TestConsumedSummaryInheritance:
    def _events(self):
        return [
            _event(1, "user_message", turn=1),
            _event(2, "text_chunk", turn=1),
            _event(3, "user_message", turn=2),
            _event(4, "text_chunk", turn=2),
            {
                "event_id": 5,
                "type": "compact_complete",
                "round": 1,
                "summary": "OLD",
                "replaced_from_event_id": 1,
                "replaced_to_event_id": 2,
                "compact_path": [[1, 1]],
                "compact_source_path": [[1, 1]],
                "compact_result_event_ids": [2],
            },
        ]

    def test_consumed_summary_inherits_actual_completion(self):
        captured = [
            _summary(1, "OLD", compact_source_path=[[1, 1]]),
            _user("u2", 2),
        ]
        path = {(1, 1), (2, 1)}
        assert compute_captured_range(self._events(), path, captured, []) == (1, 4)
        # Result ids also come from the matching completion, not from a raw
        # scan of every captured turn.
        assert summary_result_event_ids(captured, self._events()) == {2}

    def test_pathless_legacy_summary_is_not_inherited(self):
        events = self._events()
        del events[-1]["compact_path"]
        captured = [_summary(1, "OLD"), _user("u2", 2)]
        assert compute_captured_range(events, {(1, 1), (2, 1)}, captured, []) == (
            3,
            4,
        )

    def test_null_source_completion_is_not_inherited(self):
        events = self._events()
        events[-1]["compact_source_path"] = None
        captured = [
            _summary(1, "OLD", compact_source_path=[[1, 1]]),
            _user("u2", 2),
        ]
        assert compute_captured_range(events, {(1, 1), (2, 1)}, captured, []) == (
            3,
            4,
        )

    def test_ambiguous_completion_is_not_inherited(self):
        events = self._events()
        events.append(
            {
                **events[-1],
                "event_id": 6,
                "replaced_to_event_id": 4,
            }
        )
        summary = _summary(1, "OLD")
        assert matching_compact_complete(summary, events) is None

    def test_non_summary_and_malformed_messages_are_ignored(self):
        assert message_pairs([create_message("assistant", "x")]) == set()
        assert matching_compact_complete(create_message("assistant", "x"), []) is None
        assert (
            matching_compact_complete(
                create_message("assistant", "[Previous context summary"), []
            )
            is None
        )


class TestMalformedInputRefusal:
    def test_nonpositive_or_missing_coordinates_are_ignored(self):
        messages = [
            create_message("user", "x", metadata={"turn_index": 0, "branch_id": 1}),
            create_message("user", "y", metadata={"branch_id": 1}),
            _user("ok", 2),
        ]
        assert message_pairs(messages) == {(2, 1)}

    def test_malformed_summary_markers_are_ignored(self):
        assert (
            matching_compact_complete(
                create_message(
                    "assistant",
                    "[Previous context summary (compact round nope)]\n\nX",
                ),
                [],
            )
            is None
        )
        assert (
            matching_compact_complete(
                create_message(
                    "assistant",
                    "[Previous context summary (compact round 1\n\nX",
                ),
                [],
            )
            is None
        )
        # A single newline still yields the exact body.
        summary = create_message(
            "assistant", "[Previous context summary (compact round 1)]\nBODY"
        )
        event = {
            "event_id": 1,
            "type": "compact_complete",
            "round": 1,
            "summary": "BODY",
            "replaced_from_event_id": 1,
            "replaced_to_event_id": 2,
        }
        assert matching_compact_complete(summary, [event]) == event

    def test_noncompletion_and_incomplete_completions_do_not_match(self):
        summary = _summary(1, "BODY")
        base = {
            "event_id": 1,
            "type": "compact_complete",
            "round": 1,
            "summary": "BODY",
            "replaced_from_event_id": 1,
            "replaced_to_event_id": 2,
        }
        assert (
            matching_compact_complete(summary, [{**base, "summary": "OTHER"}]) is None
        )
        assert matching_compact_complete(summary, [{**base, "round": 2}]) is None
        no_range = {
            key: value
            for key, value in base.items()
            if key not in {"replaced_from_event_id", "replaced_to_event_id"}
        }
        assert matching_compact_complete(summary, [no_range]) is None
        assert matching_compact_complete(summary, [{"type": "compact_replace"}]) is None

    def test_incompatible_or_empty_inherited_completion_is_skipped(self):
        summary = _summary(1, "OLD")
        user = _user("u2", 2)
        events = [
            _event(3, "user_message", turn=2),
            _event(4, "text_chunk", turn=2),
            {
                "event_id": 5,
                "type": "compact_complete",
                "round": 1,
                "summary": "OLD",
                "replaced_from_event_id": 1,
                "replaced_to_event_id": 2,
                "compact_path": [[1]],
                "compact_source_path": [[1, 1]],
            },
        ]
        assert compute_captured_range(events, {(2, 1)}, [summary, user], []) == (3, 4)
        events[-1]["compact_path"] = [[9, 9]]
        events[-1]["compact_source_path"] = [[9, 9]]
        assert compute_captured_range(events, {(2, 1)}, [summary, user], []) == (3, 4)
        events[-1]["compact_path"] = [[1, 1]]
        events[-1]["compact_source_path"] = None
        assert compute_captured_range(events, {(2, 1)}, [summary, user], []) == (3, 4)
        # The range names no event in the log and no result id -> no inherit.
        events[-1].pop("compact_source_path")
        events[-1]["replaced_from_event_id"] = 1
        events[-1]["replaced_to_event_id"] = 1
        assert compute_captured_range(events, {(2, 1)}, [summary, user], []) == (3, 4)

    def test_no_path_or_no_proven_base_is_none(self):
        events = [_event(1, "user_message")]
        assert compute_captured_range(events, set(), [_user("u", 1)], []) is None
        assert compute_captured_range(events, {(1, 1)}, [], [], keep_count=None) is None
        assert (
            compute_captured_range(events, {(1, 1)}, [_user("u", 9)], [], keep_count=0)
            is None
        )

    def test_reattached_user_inside_inherited_span_is_refused(self):
        summary = _summary(1, "OLD")
        user = _user("u3", 3)
        events = [
            _event(4, "user_message", turn=3),
            {
                "event_id": 5,
                "type": "compact_complete",
                "round": 1,
                "summary": "OLD",
                "replaced_from_event_id": 1,
                "replaced_to_event_id": 4,
                "compact_path": [[3, 1]],
                "compact_source_path": [[3, 1]],
            },
        ]
        assert (
            compute_captured_range(
                events,
                {(3, 1)},
                [summary, user],
                [],
                retain_latest_user=True,
            )
            is None
        )


class TestLegacyFallback:
    def test_metadata_free_capture_uses_turn_window(self):
        events = [
            _event(1, "user_message", turn=1),
            _event(2, "text_chunk", turn=1),
            _event(3, "user_message", turn=2),
            _event(4, "text_chunk", turn=2),
        ]
        assert compute_captured_range(
            events, {(1, 1), (2, 1)}, [], [], keep_count=1
        ) == (
            1,
            2,
        )


class TestLegacyRangeEdges:
    def test_legacy_range_ignores_malformed_and_foreign_events(self):
        events = [
            _event(1, "user_message", turn=1),
            _event(2, "text_chunk", turn=1),
            _event(3, "user_message", turn=2),
            _event(4, "text_chunk", turn=2),
            {"type": "text_chunk"},
            _event(9, "text_chunk", turn=9),
        ]
        path = {(1, 1), (2, 1)}
        assert compute_replaced_range(events, 1, path) == (1, 2)
        assert compute_replaced_range(events, 2, path) is None
        assert compute_replaced_range(events, 1, set()) is None

    def test_invalid_pair_lists_are_not_inherited(self):
        summary = _summary(1, "OLD")
        user = _user("u2", 2)
        events = [
            _event(3, "user_message", turn=2),
            _event(4, "text_chunk", turn=2),
            {
                "event_id": 5,
                "type": "compact_complete",
                "round": 1,
                "summary": "OLD",
                "replaced_from_event_id": 1,
                "replaced_to_event_id": 2,
                "compact_path": [[0, 1]],
                "compact_source_path": [[1, 1]],
            },
        ]
        assert compute_captured_range(
            events, {(1, 1), (2, 1)}, [summary, user], []
        ) == (
            3,
            4,
        )


def _canonical_round(base, i, turn=1, branch=1, content=None, call=True):
    """One persisted assistant round: start, display, [call, result], end."""
    content = content if content is not None else f"SEG_{i}"
    events = [
        _event(base, "assistant_round_start", turn=turn, branch=branch),
        _event(
            base + 1,
            "text_chunk",
            turn=turn,
            branch=branch,
            content=content,
            chunk_seq=i,
        ),
    ]
    if not call:
        events.append(
            _event(
                base + 2,
                "assistant_message",
                turn=turn,
                branch=branch,
                round_start_event_id=base,
                message={"role": "assistant", "content": content},
            )
        )
        return events
    events.append(
        _event(
            base + 2,
            "tool_call",
            turn=turn,
            branch=branch,
            call_id=f"job_{i}",
            tool_call_id=f"call{i}",
            name="echo",
            tool_call_arguments=f'{{"i": {i}}}',
        )
    )
    events.append(
        _event(
            base + 3,
            "tool_result",
            turn=turn,
            branch=branch,
            call_id=f"job_{i}",
            output=f"RESULT_{i}",
        )
    )
    events.append(
        _event(
            base + 4,
            "assistant_message",
            turn=turn,
            branch=branch,
            round_start_event_id=base,
            message={
                "role": "assistant",
                "content": content,
                "tool_calls": [
                    {
                        "id": f"call{i}",
                        "type": "function",
                        "function": {"name": "echo", "arguments": f'{{"i": {i}}}'},
                    }
                ],
            },
        )
    )
    return events


def _canonical_session(rounds=4, turn=1):
    events = [
        _event(1, "user_input", turn=turn, content="GOAL"),
        _event(2, "user_message", turn=turn, content="GOAL"),
        _event(3, "processing_start", turn=turn),
    ]
    for i in range(rounds):
        events.extend(_canonical_round(4 + i * 5, i, turn=turn))
    return events


def _echo_assistant(i, metadata=None):
    return create_message(
        "assistant",
        f"SEG_{i}",
        tool_calls=[
            {
                "id": f"call{i}",
                "type": "function",
                "function": {"name": "echo", "arguments": f'{{"i": {i}}}'},
            }
        ],
        metadata=metadata or {},
    )


def _echo_result(i):
    return create_message(
        "tool",
        f"RESULT_{i}",
        name="echo",
        tool_call_id=f"call{i}",
        metadata={"tool_result_phase": "final", "job_id": f"job_{i}"},
    )


def _round_messages(index):
    return [_echo_assistant(index), _echo_result(index)]


class TestCanonicalExactBinding:
    """Canonical sessions bind each captured message to one recorded identity."""

    def test_single_turn_rounds_bind_message_by_message(self):
        events = _canonical_session()
        captured = [_user("GOAL", 1)]
        for i in range(3):
            captured.extend(_round_messages(i))
        retained = _round_messages(3)

        metadata = _capture(events, captured, retained)

        assert metadata["replaced_from_event_id"] == 2
        assert metadata["replaced_to_event_id"] == 18
        rule = _rule(metadata)
        assert rule is not None
        assert rule.covers(7, 1, 1) is True
        assert rule.covers(18, 1, 1) is True
        # The retained round keeps its own events.
        assert rule.covers(19, 1, 1) is False
        assert rule.covers(23, 1, 1) is False
        # The user_input envelope restates the proven user message event.
        assert metadata["compact_result_event_ids"] == [7, 12, 17]

    def test_identical_payloads_are_ambiguous(self):
        events = [
            _event(1, "user_message", content="GOAL"),
            _event(2, "user_input", content="GOAL"),
            *_canonical_round(3, 0, content="SAME", call=False),
            *_canonical_round(6, 1, content="SAME", call=False),
        ]
        captured = [_user("GOAL", 1), create_message("assistant", "SAME")]

        assert (
            compute_captured_range(events, {(1, 1)}, captured, [], keep_count=0) is None
        )

    def test_missing_round_members_refuse_the_span(self):
        events = _canonical_session()
        captured = [_user("GOAL", 1), *_round_messages(0), *_round_messages(2)]

        # Round 1 sits inside the proposed span but nothing captured it.
        assert (
            compute_captured_range(events, {(1, 1)}, captured, [], keep_count=0) is None
        )

    def test_retained_identity_inside_span_refuses(self):
        events = _canonical_session()
        captured = [_user("GOAL", 1)]
        for i in range(3):
            captured.extend(_round_messages(i))

        # A retained copy of an already captured round would stay live.
        assert (
            compute_captured_range(
                events, {(1, 1)}, captured, [_echo_assistant(0)], keep_count=0
            )
            is None
        )

    def test_sibling_branch_payload_does_not_bind(self):
        events = [
            _event(1, "user_message", content="GOAL"),
            _event(2, "user_input", content="GOAL"),
            *_canonical_round(3, 0, branch=2),
        ]
        captured = [_user("GOAL", 1), _echo_assistant(0, metadata={"branch_id": 1})]

        # No heuristic fallback: the only canonical round is on branch 2.
        assert (
            compute_captured_range(events, {(1, 1)}, captured, [], keep_count=0) is None
        )

    def test_stale_explicit_result_id_is_refused(self):
        events = _canonical_session(rounds=2)
        captured = [
            _user("GOAL", 1),
            _echo_assistant(0),
            _tool("RESULT_0", "call0", event_id=12),
        ]

        # Event 12 is round 1's result, not the result of ``call0``.
        assert (
            compute_captured_range(events, {(1, 1)}, captured, [], keep_count=0) is None
        )

    def test_pending_tool_result_is_refused(self):
        events = _canonical_session(rounds=1)
        pending = create_message(
            "tool",
            "RESULT_0",
            name="echo",
            tool_call_id="call0",
            metadata={"tool_result_phase": "pending"},
        )
        captured = [_user("GOAL", 1), _echo_assistant(0), pending]

        assert (
            compute_captured_range(events, {(1, 1)}, captured, [], keep_count=0) is None
        )

    def test_settled_result_off_span_binds_its_dispatch(self):
        # A settled background result is recorded by the turn that observed it,
        # on a branch the user later left; the live conversation still shows it
        # at its dispatch. The dispatch binds the message, the rescheduled
        # result does not widen the span, and the durable id still names it.
        events = [
            _event(1, "user_input", content="GOAL"),
            _event(2, "user_message", content="GOAL"),
            _event(3, "assistant_round_start"),
            _event(4, "text_chunk", content="SEG_0", chunk_seq=0),
            _event(
                5,
                "tool_call",
                call_id="job_0",
                tool_call_id="call0",
                name="echo",
                tool_call_arguments='{"i": 0}',
            ),
            _event(
                6,
                "assistant_message",
                round_start_event_id=3,
                message={
                    "role": "assistant",
                    "content": "SEG_0",
                    "tool_calls": [
                        {
                            "id": "call0",
                            "type": "function",
                            "function": {"name": "echo", "arguments": '{"i": 0}'},
                        }
                    ],
                },
            ),
            _event(7, "tool_result", turn=2, output="RESULT_0", call_id="job_0"),
            _event(8, "user_input", turn=2, branch=2, content="NEXT"),
            _event(9, "user_message", turn=2, branch=2, content="NEXT"),
            *_canonical_round(10, 1, turn=2, branch=2, content="SEG_1", call=False),
        ]
        captured = [
            _user("GOAL", 1),
            _echo_assistant(0),
            _tool("RESULT_0", "call0", event_id=7),
            _user("NEXT", 2, branch=2),
        ]
        retained = [create_message("assistant", "SEG_1")]
        agent = _agent(turn=2, branch=2, path=((1, 1),))

        metadata = _capture(events, captured, retained, agent=agent)

        assert metadata["replaced_from_event_id"] == 2
        assert metadata["replaced_to_event_id"] == 9
        assert metadata["_capture_unproven"] is False
        assert metadata["compact_result_event_ids"] == [7]

        stale = dict(events[6], call_id="job_9")
        assert (
            compute_captured_range(
                [*events[:6], stale, *events[7:]], {(1, 1), (2, 2)}, captured, retained
            )
            is None
        )

    def test_recorded_end_ids_resolve_duplicated_payloads(self):
        events = [
            _event(1, "user_message", content="GOAL"),
            _event(2, "user_input", content="GOAL"),
            *_canonical_round(3, 0, content="SAME", call=False),
            *_canonical_round(6, 1, content="SAME", call=False),
        ]
        # Replayed messages carry the event id of their canonical end, so an
        # otherwise ambiguous payload still resolves to its own round.
        captured = [
            _user("GOAL", 1),
            create_message("assistant", "SAME", metadata={"event_id": 5}),
            create_message("assistant", "SAME", metadata={"event_id": 8}),
        ]

        assert compute_captured_range(events, {(1, 1)}, captured, [], keep_count=0) == (
            1,
            8,
        )

    def test_reattached_user_stays_live(self):
        events = _canonical_session()
        captured = [_user("GOAL", 1), *_round_messages(0)]
        retained = _round_messages(1)

        metadata = _capture(events, captured, retained, retain_latest_user=True)

        assert metadata["replaced_from_event_id"] == 4
        assert metadata["replaced_to_event_id"] == 8
        rule = _rule(metadata)
        assert rule is not None
        assert rule.covers(2, 1, 1) is False

    def test_retained_user_inside_span_refuses(self):
        events = [
            _event(1, "user_message", turn=1, content="GOAL"),
            *_canonical_round(2, 0, turn=1),
            _event(7, "user_message", turn=2, content="NEXT"),
            *_canonical_round(8, 1, turn=2),
        ]
        captured = [
            _user("GOAL", 1),
            *_round_messages(0),
            _user("NEXT", 2),
            *_round_messages(1),
        ]

        # The reattached turn-2 user message would be swallowed mid-span.
        assert (
            compute_captured_range(
                events,
                {(1, 1), (2, 1)},
                captured,
                [],
                retain_latest_user=True,
            )
            is None
        )


def _round_end(eid, start, content, i, turn=1, branch=1):
    """A canonical round end carrying the same payload ``_echo_assistant`` has."""
    message = {
        "role": "assistant",
        "content": content,
        "tool_calls": [
            {
                "id": f"call{i}",
                "type": "function",
                "function": {"name": "echo", "arguments": f'{{"i": {i}}}'},
            }
        ],
    }
    return _event(
        eid,
        "assistant_message",
        turn=turn,
        branch=branch,
        round_start_event_id=start,
        message=message,
    )


class TestCanonicalFormatDetection:
    """Canonical format presence alone forbids the live-turn heuristic."""

    def test_unpaired_round_start_is_still_canonical(self):
        events = [
            _event(1, "user_message", turn=1, content="GOAL"),
            _event(2, "assistant_round_start", turn=1),
            _event(3, "user_message", turn=2, content="NEXT"),
            _event(4, "text_chunk", turn=2),
        ]
        assert uses_canonical_rounds(events) is True
        # The live-turn heuristic would have claimed (1, 2).
        assert (
            compute_captured_range(events, {(1, 1), (2, 1)}, [], [], keep_count=1)
            is None
        )

    def test_orphan_assistant_message_is_still_canonical(self):
        events = [
            _event(1, "user_message", turn=1, content="GOAL"),
            _event(
                2,
                "assistant_message",
                turn=1,
                round_start_event_id=99,
                message={"role": "assistant", "content": "X"},
            ),
            _event(3, "user_message", turn=2, content="NEXT"),
        ]
        assert uses_canonical_rounds(events) is True
        assert (
            compute_captured_range(events, {(1, 1), (2, 1)}, [], [], keep_count=1)
            is None
        )

    def test_legacy_log_without_rounds_stays_heuristic(self):
        events = [_event(1, "user_message"), _event(2, "text_chunk")]
        assert uses_canonical_rounds(events) is False


class TestDuplicateEventIds:
    """A duplicated event id makes every identity lookup ambiguous."""

    def test_conflicting_duplicate_id_refuses(self):
        events = _canonical_session(rounds=1)
        events.append(_event(7, "tool_result", call_id="job_9", output="OTHER"))
        captured = [_user("GOAL", 1), *_round_messages(0)]
        assert (
            compute_captured_range(events, {(1, 1)}, captured, [], keep_count=0) is None
        )

    def test_identical_duplicate_id_refuses(self):
        events = _canonical_session(rounds=1)
        events.append(dict(events[6]))
        captured = [_user("GOAL", 1), *_round_messages(0)]
        assert (
            compute_captured_range(events, {(1, 1)}, captured, [], keep_count=0) is None
        )


class TestCapturedOrder:
    """Captured identities must appear in recorded conversation order."""

    def test_reordered_assistant_rounds_refuse(self):
        events = [
            _event(1, "user_message", content="GOAL"),
            _event(2, "user_input", content="GOAL"),
            *_canonical_round(3, 0, content="FIRST", call=False),
            *_canonical_round(6, 1, content="SECOND", call=False),
        ]
        captured = [
            _user("GOAL", 1),
            create_message("assistant", "SECOND"),
            create_message("assistant", "FIRST"),
        ]
        assert (
            compute_captured_range(events, {(1, 1)}, captured, [], keep_count=0) is None
        )

    def test_reordered_user_anchors_refuse(self):
        events = [
            _event(1, "user_message", turn=1, content="A"),
            _event(2, "user_message", turn=1, content="B"),
            *_canonical_round(3, 0, turn=1),
        ]
        captured = [
            _user("B", 1),
            _user("A", 1),
            _echo_assistant(0),
            _echo_result(0),
        ]
        assert (
            compute_captured_range(events, {(1, 1)}, captured, [], keep_count=0) is None
        )


class TestSummaryCoverageRequired:
    """Every captured summary must prove its own inherited coverage."""

    def _events(self):
        return [
            _event(1, "user_message", turn=1, content="GOAL"),
            _event(2, "user_input", turn=1, content="GOAL"),
            *_canonical_round(3, 0, turn=1),
            *_canonical_round(8, 1, turn=1),
            {
                "event_id": 13,
                "type": "compact_complete",
                "round": 1,
                "summary": "OLD",
                "replaced_from_event_id": 1,
                "replaced_to_event_id": 2,
                "compact_path": [[1, 1]],
                "compact_source_path": [[1, 1]],
            },
        ]

    def _captured(self, body):
        return [_summary(1, body), _user("GOAL", 1), *_round_messages(0)]

    def test_known_summary_inherits_exact_coverage(self):
        assert compute_captured_range(
            self._events(), {(1, 1)}, self._captured("OLD"), _round_messages(1)
        ) == (1, 7)

    def test_unknown_summary_refuses_exact_coverage(self):
        assert (
            compute_captured_range(
                self._events(), {(1, 1)}, self._captured("ORPHAN"), _round_messages(1)
            )
            is None
        )


class TestResultDuplicateNormalization:
    """Only an immediately repeated identical result collapses to one record."""

    def _events(self, gap):
        events = [
            _event(1, "user_message", turn=1, content="GOAL"),
            _event(2, "assistant_round_start", turn=1),
            _event(3, "text_chunk", turn=1, content="SEG_0", chunk_seq=0),
            _event(
                4,
                "tool_call",
                turn=1,
                call_id="job_0",
                tool_call_id="call0",
                name="echo",
                tool_call_arguments='{"i": 0}',
            ),
            _event(5, "tool_result", turn=1, call_id="job_0", output="RESULT_0"),
        ]
        if gap:
            events.append(_event(6, "processing_start", turn=1))
        twin, end = (7, 8) if gap else (6, 7)
        events.append(
            _event(twin, "tool_result", turn=1, call_id="job_0", output="RESULT_0")
        )
        events.append(_round_end(end, 2, "SEG_0", 0))
        return events

    def _captured(self):
        return [_user("GOAL", 1), _echo_assistant(0), _echo_result(0)]

    def test_adjacent_identical_result_is_one_record(self):
        assert compute_captured_range(
            self._events(gap=False), {(1, 1)}, self._captured(), [], keep_count=0
        ) == (1, 7)

    def test_separated_duplicate_result_refuses(self):
        assert (
            compute_captured_range(
                self._events(gap=True), {(1, 1)}, self._captured(), [], keep_count=0
            )
            is None
        )


class TestUserBindingRefusals:
    """Only a recorded real user event may back a captured user message."""

    def test_injected_user_message_is_not_a_real_user(self):
        injected = create_message(
            "user",
            "GOAL",
            metadata={"turn_index": 1, "branch_id": 1, "kind": "injected"},
        )
        assert (
            compute_captured_range(
                _canonical_session(rounds=1),
                {(1, 1)},
                [injected, *_round_messages(0)],
                [],
                keep_count=0,
            )
            is None
        )

    def test_tool_results_user_message_is_not_a_real_user(self):
        folded = create_message(
            "user",
            "GOAL",
            metadata={"turn_index": 1, "branch_id": 1, "kind": "tool_results"},
        )
        assert (
            compute_captured_range(
                _canonical_session(rounds=1),
                {(1, 1)},
                [folded, *_round_messages(0)],
                [],
                keep_count=0,
            )
            is None
        )

    def test_stale_explicit_user_event_id_refuses(self):
        captured = [_user("GOAL", 1, event_id=99), *_round_messages(0)]
        assert (
            compute_captured_range(
                _canonical_session(rounds=1), {(1, 1)}, captured, [], keep_count=0
            )
            is None
        )

    def test_explicit_user_event_id_binds_its_own_event(self):
        captured = [_user("GOAL", 1, event_id=2), *_round_messages(0)]
        assert compute_captured_range(
            _canonical_session(rounds=1), {(1, 1)}, captured, [], keep_count=0
        ) == (2, 8)


class TestRestatedInputPair:
    """A user_input envelope is excused only for its own turn and branch."""

    def test_user_input_from_another_turn_is_not_excused(self):
        events = [
            _event(1, "user_message", turn=1, content="GOAL"),
            _event(2, "user_input", turn=1, content="GOAL"),
            _event(3, "assistant_round_start", turn=1),
            _event(4, "text_chunk", turn=1, content="SEG_0", chunk_seq=0),
            _event(5, "user_input", turn=2, content="GOAL"),
            _event(
                6,
                "tool_call",
                turn=1,
                call_id="job_0",
                tool_call_id="call0",
                name="echo",
                tool_call_arguments='{"i": 0}',
            ),
            _event(7, "tool_result", turn=1, call_id="job_0", output="RESULT_0"),
            _round_end(8, 3, "SEG_0", 0),
        ]
        captured = [_user("GOAL", 1), *_round_messages(0)]
        assert (
            compute_captured_range(events, {(1, 1), (2, 1)}, captured, [], keep_count=0)
            is None
        )


def _pending(call_id, job_id):
    meta = {"job_id": job_id, "tool_result_phase": "pending"}
    return create_message("tool", "ACK", tool_call_id=call_id, metadata=meta)


def _folded(job_ids, turn=2):
    meta = {
        "kind": "tool_results",
        "job_ids": list(job_ids),
        "origin_known": True,
        "tool_result_phase": "final",
        "turn_index": turn,
        "branch_id": 1,
    }
    return create_message("user", "FOLDED", metadata=meta)


def _injection(**extra):
    meta = {"kind": "injected", "turn_index": 1, "branch_id": 1, **extra}
    return create_message("user", "INJECTED", metadata=meta)


def _bg_events(result_ids=(9,), turn=2):
    """Canonical turn-1 dispatch; final results settle on a later turn."""
    events = [e for e in _canonical_session(rounds=1) if e["event_id"] != 7]
    for index, rid in enumerate(result_ids):
        if index:
            events.append(_event(rid - 1, "processing_start", turn=turn))
        events.append(
            _event(
                rid,
                "tool_result",
                turn=turn,
                branch=1,
                output="RESULT_0",
                call_id="job_0",
            )
        )
    return events


def _injection_session(orphan=False):
    events = _canonical_session(rounds=1)
    if orphan:
        events[2] = _event(3, "text_chunk", content="ORPHAN")
    return events


class TestPendingAckAndFolded:
    """A pending ack proves its dispatch; a folded final proves both sides."""

    def _folded_captured(self):
        return [
            _user("GOAL", 1),
            _echo_assistant(0),
            _pending("call0", "job_0"),
            _folded(["job_0"]),
        ]

    def test_pending_ack_with_folded_final_captured_covers(self):
        assert compute_captured_range(
            _bg_events(), {(1, 1), (2, 1)}, self._folded_captured(), []
        ) == (2, 9)

    def test_pending_ack_captured_final_retained_excludes_result(self):
        captured = [_user("GOAL", 1), _echo_assistant(0), _pending("call0", "job_0")]
        metadata = _capture(
            _bg_events(),
            captured,
            [_folded(["job_0"])],
            agent=_agent(turn=2, path=((1, 1),)),
        )
        # The retained folded envelope keeps result 9 live; the pending ack
        # consumes no result identity, so the span stops at the round end (8).
        assert metadata["replaced_from_event_id"] == 2
        assert metadata["replaced_to_event_id"] == 8
        assert metadata["compact_result_event_ids"] == []

    def test_ambiguous_folded_result_refuses(self):
        assert (
            compute_captured_range(
                _bg_events(result_ids=(9, 11)),
                {(1, 1), (2, 1)},
                self._folded_captured(),
                [],
            )
            is None
        )

    @pytest.mark.parametrize(
        ("provider", "job"), [("call0", "foreign"), ("foreign", "job_0")]
    )
    def test_conflicting_pending_identities_refuse(self, provider, job):
        captured = [_user("GOAL", 1), _echo_assistant(0), _pending(provider, job)]
        assert compute_captured_range(_bg_events(), {(1, 1), (2, 1)}, captured) is None

    def test_mismatched_pending_dispatch_refuses(self):
        events = _bg_events()
        events[5]["tool_call_arguments"] = '{"i": 9}'
        captured = [_user("GOAL", 1), _echo_assistant(0), _pending("call0", "job_0")]
        assert compute_captured_range(events, {(1, 1), (2, 1)}, captured, []) is None


class TestUnknownInjection:
    """A no-job injected input proves nothing and blocks nothing."""

    def _captured(self):
        return [
            _user("GOAL", 1),
            _injection(origin_known=False),
            _echo_assistant(0),
            _echo_result(0),
        ]

    def test_unknown_injection_keeps_known_range(self):
        captured = self._captured()
        assert summary_source_path(captured) is None
        assert compute_captured_range(_injection_session(), {(1, 1)}, captured, []) == (
            2,
            8,
        )

    def test_unknown_injection_still_refuses_unrepresented_text(self):
        assert (
            compute_captured_range(
                _injection_session(orphan=True), {(1, 1)}, self._captured(), []
            )
            is None
        )


@pytest.mark.parametrize(
    "event_type",
    [
        "scratchpad_write",
        "token_usage",
        "turn_token_usage",
        "cache_stats",
        "compact_skipped",
        "processing_complete",
    ],
)
def test_capture_can_cross_nonconversational_records(event_type):
    events = _canonical_session(rounds=1)
    events[2] = _event(3, event_type)
    assert compute_captured_range(
        events, {(1, 1)}, [_user("GOAL", 1), *_round_messages(0)]
    ) == (2, 8)


class TestOrphanReasoning:
    def test_captured_boundary_includes_trailing_reasoning(self):
        events = [
            _event(1, "user_message", content="GOAL"),
            *_canonical_round(2, 0, call=False),
        ]
        events[-1]["message"]["reasoning_content"] = "THOUGHT"
        events += [
            _event(5, "assistant_reasoning", reasoning_content="THOUGHT"),
            *_canonical_round(6, 1, call=False),
        ]
        first = create_message("assistant", "SEG_0")
        first.extra_fields["reasoning_content"] = "THOUGHT"
        assert compute_captured_range(
            events,
            {(1, 1)},
            [_user("GOAL", 1), first],
            [create_message("assistant", "SEG_1")],
        ) == (1, 5)

    @pytest.mark.parametrize("matching", [True, False])
    def test_trailing_reasoning_must_restate_captured_canonical_state(self, matching):
        events = [
            _event(1, "user_message", content="GOAL"),
            *_canonical_round(2, 0, call=False),
        ]
        events[-1]["message"]["reasoning_content"] = "THOUGHT"
        events += [
            _event(
                5,
                "assistant_reasoning",
                reasoning_content="THOUGHT" if matching else "FOREIGN",
            ),
            *_canonical_round(6, 1, call=False),
        ]
        first = create_message("assistant", "SEG_0")
        first.extra_fields["reasoning_content"] = "THOUGHT"
        captured = [_user("GOAL", 1), first, create_message("assistant", "SEG_1")]
        assert compute_captured_range(events, {(1, 1)}, captured) == (
            (1, 8) if matching else None
        )

    def test_orphan_reasoning_between_known_events_refuses(self):
        events = [
            _event(1, "user_message", content="GOAL"),
            _event(2, "user_input", content="GOAL"),
            *_canonical_round(3, 0, call=False),
            _event(6, "assistant_reasoning", content="ORPHAN"),
            *_canonical_round(7, 1, call=False),
        ]
        captured = [
            _user("GOAL", 1),
            create_message("assistant", "SEG_0"),
            create_message("assistant", "SEG_1"),
        ]
        assert compute_captured_range(events, {(1, 1)}, captured, []) is None


class TestRepeatedSummaryInheritance:
    def test_inherited_late_result_does_not_expand_across_retained_tail(self):
        events = [
            _event(1, "user_message", content="GOAL"),
            *_canonical_round(2, 0, call=False),
            *_canonical_round(5, 1, call=False),
            *_canonical_round(8, 2, call=False),
            _event(20, "tool_result", call_id="old_job", output="old result"),
            _event(
                21,
                "compact_complete",
                round=1,
                summary="OLD",
                replaced_from_event_id=1,
                replaced_to_event_id=4,
                compact_path=[[1, 1]],
                compact_source_path=[[1, 1]],
                compact_result_event_ids=[20],
            ),
        ]
        captured = [
            _summary(1, "OLD", compact_result_event_ids=[20]),
            create_message("assistant", "SEG_1"),
        ]
        assert compute_captured_range(
            events, {(1, 1)}, captured, [create_message("assistant", "SEG_2")]
        ) == (1, 7)

    """A consumed summary keeps its lineage across a fresh compaction."""

    @pytest.mark.parametrize("completion_inside", [False, True])
    def test_repeated_inheritance_adds_new_round(self, completion_inside):
        events = [
            _event(1, "user_message", content="GOAL"),
            _event(2, "user_input", content="GOAL"),
            *_canonical_round(3, 0),
            *_canonical_round(8, 1),
            {
                "event_id": 13,
                "type": "compact_complete",
                "round": 1,
                "summary": "OLD",
                "replaced_from_event_id": 1,
                "replaced_to_event_id": 7,
                "compact_path": [[1, 1]],
                "compact_source_path": [[1, 1]],
            },
        ]
        if completion_inside:
            complete = events[-1]
            complete.update(event_id=8, turn_index=1, branch_id=1)
            events = events[:7] + [complete] + _canonical_round(9, 1)
        captured = [_summary(1, "OLD"), *_round_messages(1)]
        assert compute_captured_range(events, {(1, 1)}, captured, []) == (
            1,
            13 if completion_inside else 12,
        )


class TestCaptureFreeze:
    """Capture snapshots both sides of the split before awaiting the store."""

    def test_mutation_during_store_read_is_ignored(self):
        captured = [_user("GOAL", 1), *_round_messages(0)]
        retained = _round_messages(1)

        def mutate():
            captured[0].content = "MUTATED"
            captured[0].metadata["turn_index"] = 9
            captured[1].content = "MUTATED"
            retained[0].content = "MUTATED"
            retained.append(create_message("assistant", "EXTRA"))

        metadata = _capture(
            _canonical_session(rounds=2), captured, retained, on_read=mutate
        )
        assert metadata["replaced_from_event_id"] == 2
        assert metadata["replaced_to_event_id"] == 8


class TestExactBindingRefusals:
    """Residual identity safety: ambiguous dispatches and bad result ids."""

    def test_pending_job_only_binds_its_dispatch(self):
        # A pending ack with no provider call id still proves its job's dispatch.
        captured = [
            _user("GOAL", 1),
            _echo_assistant(0),
            _pending(None, "job_0"),
            _folded(["job_0"]),
        ]
        assert compute_captured_range(_bg_events(), {(1, 1), (2, 1)}, captured, []) == (
            2,
            9,
        )

    def test_pending_dispatch_name_mismatch_refuses(self):
        events = _bg_events()
        events[5]["name"] = "other"
        captured = [_user("GOAL", 1), _echo_assistant(0), _pending("call0", "job_0")]
        assert compute_captured_range(events, {(1, 1), (2, 1)}, captured, []) is None

    def test_duplicate_provider_dispatch_refuses(self):
        events = _bg_events()
        events.append(
            _event(10, "tool_call", call_id="job_x", tool_call_id="call0", name="echo")
        )
        captured = [_user("GOAL", 1), _echo_assistant(0), _echo_result(0)]
        assert compute_captured_range(events, {(1, 1), (2, 1)}, captured, []) is None

    @pytest.mark.parametrize(
        ("field", "value"), [("name", "other"), ("tool_call_arguments", '{"i": 9}')]
    )
    def test_dispatch_payload_mismatch_refuses(self, field, value):
        events = _bg_events()
        events[5][field] = value
        captured = [_user("GOAL", 1), _echo_assistant(0), _echo_result(0)]
        assert compute_captured_range(events, {(1, 1), (2, 1)}, captured, []) is None

    def test_explicit_result_id_to_nonresult_refuses(self):
        captured = [_user("GOAL", 1), _echo_assistant(0), _tool("RESULT_0", "call0", 8)]
        assert (
            compute_captured_range(_bg_events(), {(1, 1), (2, 1)}, captured, []) is None
        )

    def test_result_without_dispatch_binds_explicit_result(self):
        # A settled result may be recorded with no surviving dispatch record;
        # the explicit result id still anchors it.
        events = [
            _event(1, "user_message", content="GOAL"),
            *_canonical_round(2, 0, call=False),
            _event(5, "tool_result", call_id="job_x", output="R"),
        ]
        captured = [
            _user("GOAL", 1),
            create_message("assistant", "SEG_0"),
            _tool("R", "job_x", event_id=5),
        ]
        assert compute_captured_range(events, {(1, 1)}, captured, []) == (1, 5)

    def test_dict_dispatch_arguments_match_declared_call(self):
        events = _bg_events()
        events[5]["tool_call_arguments"] = {"i": 0}
        captured = [_user("GOAL", 1), _echo_assistant(0), _echo_result(0)]
        assert (
            compute_captured_range(events, {(1, 1), (2, 1)}, captured, []) is not None
        )


class TestResolveCaptureIdsEdges:
    def test_empty_path_refuses(self):
        captured = [_user("GOAL", 1), *_round_messages(0)]
        assert (
            resolve_capture_ids(_canonical_session(rounds=1), set(), captured) is None
        )

    def test_folded_missing_job_refuses(self):
        captured = [_user("GOAL", 1), _echo_assistant(0), _folded(["job_9"])]
        assert (
            compute_captured_range(_canonical_session(rounds=1), {(1, 1)}, captured)
            is None
        )

    def test_malformed_missing_event_id_is_ignored(self):
        events = _canonical_session(rounds=1)
        events.append(
            {"type": "tool_result", "call_id": "job_9", "turn_index": 1, "branch_id": 1}
        )
        captured = [_user("GOAL", 1), *_round_messages(0)]
        assert compute_captured_range(events, {(1, 1)}, captured) == (2, 8)
