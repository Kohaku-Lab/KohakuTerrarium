"""Unit tests for compact-rule compatibility and coverage selection.

These lock the narrow selection contract for :func:`compact_replacement_rules`:

* compatibility (proven ancestry) is decided before coverage;
* an unknown (null) recorded source never falls back to the replacement path;
* ranking uses actual replacement coverage, not source-pair count or round;
* non-dominated/disjoint legacy segments are retained in order;
* ambiguous data is never resolved by silently erasing represented history.
"""

from kohakuterrarium.core.compact_branch import (
    CompactRule,
    compact_rule_from_event,
    summary_result_event_ids,
)
from kohakuterrarium.llm.message import create_message
from kohakuterrarium.session.history import (
    compact_replacement_rules,
    replay_conversation,
)


def _linear_events(turns: int, branch: int = 1) -> list[dict]:
    """A single-branch history where turn ``t`` owns events ``2t-1``/``2t``."""
    events: list[dict] = []
    eid = 0
    for turn in range(1, turns + 1):
        path = [[t, branch] for t in range(1, turn)]
        eid += 1
        events.append(
            {
                "type": "user_message",
                "content": f"U{turn}",
                "event_id": eid,
                "turn_index": turn,
                "branch_id": branch,
                "parent_branch_path": path,
            }
        )
        eid += 1
        events.append(
            {
                "type": "text_chunk",
                "content": f"R{turn}",
                "event_id": eid,
                "turn_index": turn,
                "branch_id": branch,
                "parent_branch_path": path,
            }
        )
    return events


def _full_path(turns: int, branch: int = 1) -> list[list[int]]:
    return [[t, branch] for t in range(1, turns + 1)]


def _selected_pairs(turns: int, branch: int = 1) -> set[tuple[int, int]]:
    return {(t, branch) for t in range(1, turns + 1)}


def _summaries(rules: list[CompactRule]) -> list[str]:
    return [rule.summary for rule in rules]


def test_summary_remains_compatible_when_only_the_uncaptured_tail_forks():
    events = _linear_events(2)
    events.extend(
        [
            {
                "event_id": 5,
                "type": "compact_complete",
                "summary": "PREFIX",
                "replaced_from_event_id": 1,
                "replaced_to_event_id": 2,
                "compact_path": [[1, 1], [2, 1]],
                "compact_source_path": [[1, 1]],
            },
            {
                "event_id": 6,
                "type": "user_message",
                "content": "sibling",
                "turn_index": 2,
                "branch_id": 2,
                "parent_branch_path": [[1, 1]],
            },
        ]
    )
    assert _summaries(compact_replacement_rules(events, {1: 1, 2: 2})) == ["PREFIX"]
    assert replay_conversation(events, branch_view={1: 1, 2: 2}) == [
        {"role": "assistant", "content": "PREFIX"},
        {"role": "user", "content": "sibling"},
    ]


def test_wider_rule_without_inherited_sources_keeps_older_summary():
    events = _linear_events(4)
    events.extend(
        [
            {
                "event_id": 9,
                "type": "compact_complete",
                "summary": "EARLY_FACT",
                "replaced_from_event_id": 1,
                "replaced_to_event_id": 4,
                "compact_path": _full_path(4),
                "compact_source_path": [[1, 1], [2, 1]],
            },
            {
                "event_id": 10,
                "type": "compact_complete",
                "summary": "LATER_FACT",
                "replaced_from_event_id": 1,
                "replaced_to_event_id": 6,
                "compact_path": _full_path(4),
                "compact_source_path": [[3, 1]],
            },
        ]
    )
    assert _summaries(compact_replacement_rules(events)) == ["EARLY_FACT", "LATER_FACT"]


class TestUnknownSourceCompatibility:
    """A recorded-but-null source must never be validated via its path."""

    def test_null_source_is_not_proven_compatible(self):
        evt = {
            "type": "compact_complete",
            "summary": "[UNKNOWN]",
            "replaced_from_event_id": 1,
            "replaced_to_event_id": 20,
            "compact_path": _full_path(10),
            "compact_source_path": None,
        }
        rule = compact_rule_from_event(evt)
        assert rule is not None
        assert rule.source_recorded is True
        assert rule.source_path is None
        # Unknown provenance cannot be treated as proven compatibility.
        assert rule.priority(_selected_pairs(10))[0] == 0

    def test_null_source_does_not_beat_proven_smaller_coverage(self):
        # The unknown-source rule claims the whole history; the proven rule
        # only a small prefix. Compatibility must win: the proven summary is
        # authoritative and the unknown claim cannot silently replace it.
        events = _linear_events(20)
        events.append(
            {
                "type": "compact_complete",
                "summary": "[UNKNOWN big]",
                "event_id": 41,
                "replaced_from_event_id": 1,
                "replaced_to_event_id": 40,
                "compact_path": _full_path(20),
                "compact_source_path": None,
            }
        )
        events.append(
            {
                "type": "compact_complete",
                "summary": "[PROVEN small]",
                "event_id": 42,
                "replaced_from_event_id": 1,
                "replaced_to_event_id": 10,
                "compact_path": _full_path(20),
                "compact_source_path": [[1, 1]],
            }
        )
        rules = compact_replacement_rules(events)
        assert [r.summary for r in rules] == ["[PROVEN small]"]


class TestCoverageRanking:
    """Select by actual replacement coverage, never source-pair count."""

    def test_more_coverage_wins_despite_fewer_source_pairs(self):
        # Both segments remain when wider coverage lacks inherited dependencies.
        events = _linear_events(30)
        events.append(
            {
                "type": "compact_complete",
                "summary": "[OLD narrow]",
                "event_id": 61,
                "replaced_from_event_id": 1,
                "replaced_to_event_id": 10,
                "compact_path": _full_path(30),
                "compact_source_path": [[t, 1] for t in range(1, 24)],
            }
        )
        events.append(
            {
                "type": "compact_complete",
                "summary": "[NEW wide]",
                "event_id": 62,
                "replaced_from_event_id": 1,
                "replaced_to_event_id": 60,
                "compact_path": _full_path(30),
                "compact_source_path": [[t, 1] for t in range(1, 16)],
            }
        )
        rules = compact_replacement_rules(events)
        assert [r.summary for r in rules] == ["[OLD narrow]", "[NEW wide]"]
        assert [message["content"] for message in replay_conversation(events)] == [
            "[OLD narrow]",
            "[NEW wide]",
        ]

    def test_lower_round_with_more_coverage_wins(self):
        # ``round`` is not an ordering: branches can lower it. Coverage is.
        # The low-round big summary is recorded EARLIER, so neither round nor
        # event order can pick it - only its larger coverage can.
        events = _linear_events(20)
        events.append(
            {
                "type": "compact_complete",
                "summary": "[LOW round big]",
                "round": 4,
                "event_id": 41,
                "replaced_from_event_id": 1,
                "replaced_to_event_id": 40,
                "compact_path": _full_path(20),
                "compact_source_path": [[1, 1]],
            }
        )
        events.append(
            {
                "type": "compact_complete",
                "summary": "[HIGH round small]",
                "round": 9,
                "event_id": 42,
                "replaced_from_event_id": 1,
                "replaced_to_event_id": 10,
                "compact_path": _full_path(20),
                "compact_source_path": [[1, 1]],
            }
        )
        rules = compact_replacement_rules(events)
        assert len(rules) == 1
        assert "[LOW round big]" in rules[0].summary


class TestDisjointCoverage:
    """Do not erase represented history when segments are disjoint."""

    def test_disjoint_legacy_segments_are_both_kept_in_order(self):
        events = _linear_events(20)
        events.append(
            {
                "type": "compact_replace",
                "summary_text": "[SEG A]",
                "event_id": 41,
                "replaced_from_event_id": 1,
                "replaced_to_event_id": 10,
            }
        )
        events.append(
            {
                "type": "compact_replace",
                "summary_text": "[SEG B]",
                "event_id": 42,
                "replaced_from_event_id": 21,
                "replaced_to_event_id": 30,
            }
        )
        rules = compact_replacement_rules(events)
        assert _summaries(rules) == ["[SEG A]", "[SEG B]"]

    def test_partial_overlap_keeps_both(self):
        events = _linear_events(20)
        events.append(
            {
                "type": "compact_complete",
                "summary": "[PART A]",
                "event_id": 41,
                "replaced_from_event_id": 1,
                "replaced_to_event_id": 25,
                "compact_path": [[t, 1] for t in range(1, 11)],
                "compact_source_path": [[1, 1]],
            }
        )
        events.append(
            {
                "type": "compact_complete",
                "summary": "[PART B]",
                "event_id": 42,
                "replaced_from_event_id": 15,
                "replaced_to_event_id": 40,
                "compact_path": [[t, 1] for t in range(8, 21)],
                "compact_source_path": [[1, 1]],
            }
        )
        rules = compact_replacement_rules(events)
        assert _summaries(rules) == ["[PART A]", "[PART B]"]


class TestCumulativeSubsumption:
    """A proving cumulative summary replaces an older segment; unknown does not."""

    def test_proven_superset_replaces_older(self):
        events = _linear_events(20)
        events.append(
            {
                "type": "compact_complete",
                "summary": "[OLD segment]",
                "event_id": 41,
                "replaced_from_event_id": 1,
                "replaced_to_event_id": 10,
                "compact_path": _full_path(20),
                "compact_source_path": [[1, 1], [2, 1]],
            }
        )
        events.append(
            {
                "type": "compact_complete",
                "summary": "[NEW cumulative]",
                "event_id": 42,
                "replaced_from_event_id": 1,
                "replaced_to_event_id": 40,
                "compact_path": _full_path(20),
                "compact_source_path": [[1, 1], [2, 1], [3, 1]],
            }
        )
        rules = compact_replacement_rules(events)
        assert [r.summary for r in rules] == ["[NEW cumulative]"]


class TestIncompatibleAndAmbiguity:
    def test_incompatible_source_is_excluded(self):
        events = _linear_events(20)
        events.append(
            {
                "type": "compact_complete",
                "summary": "[FOREIGN]",
                "event_id": 41,
                "replaced_from_event_id": 1,
                "replaced_to_event_id": 40,
                "compact_path": [[99, 1]],
                "compact_source_path": [[99, 1]],
            }
        )
        events.append(
            {
                "type": "compact_complete",
                "summary": "[HERE]",
                "event_id": 42,
                "replaced_from_event_id": 1,
                "replaced_to_event_id": 10,
                "compact_path": _full_path(20),
                "compact_source_path": [[1, 1]],
            }
        )
        rules = compact_replacement_rules(events)
        assert [r.summary for r in rules] == ["[HERE]"]

    def test_equal_coverage_is_deterministic_single_later(self):
        events = _linear_events(20)
        events.append(
            {
                "type": "compact_complete",
                "summary": "[EARLIER]",
                "event_id": 41,
                "replaced_from_event_id": 1,
                "replaced_to_event_id": 20,
                "compact_path": _full_path(20),
                "compact_source_path": [[1, 1]],
            }
        )
        events.append(
            {
                "type": "compact_complete",
                "summary": "[LATER]",
                "event_id": 42,
                "replaced_from_event_id": 1,
                "replaced_to_event_id": 20,
                "compact_path": _full_path(20),
                "compact_source_path": [[1, 1]],
            }
        )
        rules = compact_replacement_rules(events)
        assert [r.summary for r in rules] == ["[LATER]"]

    def test_single_legacy_pathless_rule_still_applies(self):
        # Guard against over-restricting: a lone legacy global range must stay.
        events = _linear_events(20)
        events.append(
            {
                "type": "compact_replace",
                "summary_text": "[LEGACY]",
                "event_id": 41,
                "replaced_from_event_id": 1,
                "replaced_to_event_id": 10,
            }
        )
        rules = compact_replacement_rules(events)
        assert [r.summary for r in rules] == ["[LEGACY]"]


def test_folded_result_keeps_its_consumed_identity():
    events = [
        {
            "event_id": 3,
            "type": "tool_call",
            "call_id": "job-1",
            "turn_index": 1,
            "branch_id": 1,
        },
        {
            "event_id": 4,
            "type": "tool_result",
            "call_id": "job-1",
            "turn_index": 1,
            "branch_id": 1,
        },
    ]
    folded = create_message(
        "user",
        "folded result",
        metadata={
            "kind": "injected",
            "origin_known": True,
            "job_ids": ["job-1"],
            "turn_index": 1,
            "branch_id": 1,
        },
    )
    assert summary_result_event_ids([folded], events) == {4}
