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
"""

import asyncio
from types import SimpleNamespace

from kohakuterrarium.core.compact_branch import (
    capture_compact_metadata,
    compact_rule_from_event,
    job_origin_pairs,
    live_turn_window,
    summary_result_event_ids,
)
from kohakuterrarium.core.compact_capture import (
    compute_captured_range,
    compute_replaced_range,
    matching_compact_complete,
    message_pairs,
)
from kohakuterrarium.llm.message import create_message


class _Store:
    def __init__(self, events):
        self.events = events

    def get_events(self, name):
        return list(self.events)


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
):
    return asyncio.run(
        capture_compact_metadata(
            _Store(events),
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
