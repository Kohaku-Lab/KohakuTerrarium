"""Unit tests for :mod:`kohakuterrarium.session.history`."""

import pytest

from kohakuterrarium.session.history import (
    InvalidBranchViewError,
    _coerce_path,
    index_parent_paths,
    _path_matches,
    resolve_selected_branches,
    collect_branch_metadata,
    collect_user_groups,
    dedupe_adjacent_duplicate_events,
    normalize_resumable_events,
    project_branch_metadata,
    replay_conversation,
    resolve_branch_view_strict,
    select_live_event_ids,
)

# ── _coerce_path ──────────────────────────────────────────────────


class TestCoercePath:
    def test_empty(self):
        assert _coerce_path(None) == ()
        assert _coerce_path([]) == ()
        assert _coerce_path("") == ()

    def test_list_of_pairs(self):
        assert _coerce_path([[0, 1], [1, 2]]) == ((0, 1), (1, 2))

    def test_tuple_of_pairs(self):
        assert _coerce_path(((0, 1), (1, 2))) == ((0, 1), (1, 2))

    def test_invalid_pair_length_skipped(self):
        assert _coerce_path([[0, 1, 2]]) == ()

    def test_non_int_skipped(self):
        assert _coerce_path([["a", 1]]) == ()

    def test_non_iterable_returns_empty(self):
        assert _coerce_path(42) == ()


# ── _path_matches ─────────────────────────────────────────────────


class TestPathMatches:
    def test_empty_path_always_matches(self):
        assert _path_matches((), {0: 1, 1: 2}) is True

    def test_match_when_selected_agrees(self):
        assert _path_matches(((0, 1),), {0: 1}) is True

    def test_no_match_when_selected_differs(self):
        assert _path_matches(((0, 1),), {0: 2}) is False

    def test_missing_turn_treated_as_match(self):
        assert _path_matches(((0, 1),), {}) is True


# ── replay_conversation ───────────────────────────────────────────


class TestReplayConversation:
    def test_empty_events(self):
        assert replay_conversation([]) == []

    def test_assistant_reasoning_attaches_to_previous_assistant(self):
        events = [
            {"type": "user_message", "content": "q", "event_id": 1},
            {
                "type": "text_chunk",
                "content": "answer",
                "event_id": 2,
                "turn_index": 1,
                "branch_id": 1,
            },
            {
                "type": "assistant_reasoning",
                "event_id": 3,
                "turn_index": 1,
                "branch_id": 1,
                "reasoning_content": "think",
                "_kt_assistant_segments": [
                    {
                        "type": "reasoning",
                        "source": "reasoning_content",
                        "text": "think",
                    },
                    {"type": "text", "text": "answer"},
                ],
            },
        ]
        out = replay_conversation(events)
        assistant = next(m for m in out if m["role"] == "assistant")
        assert assistant["reasoning_content"] == "think"
        assert assistant["_kt_assistant_segments"][0]["text"] == "think"

    def test_user_message(self):
        events = [{"type": "user_message", "content": "hi", "event_id": 0}]
        out = replay_conversation(events)
        assert out == [{"role": "user", "content": "hi"}]

    def test_duplicate_user_message_same_turn_branch_deduped(self):
        # Graph merge / migration can copy the same turn's user_message
        # event twice; replay must emit it once so turn-targeted edit
        # resolution stays unambiguous.
        events = [
            {
                "type": "user_message",
                "content": "q",
                "event_id": 1,
                "turn_index": 1,
                "branch_id": 1,
            },
            {
                "type": "user_message",
                "content": "q",
                "event_id": 2,
                "turn_index": 1,
                "branch_id": 1,
            },
        ]
        out = replay_conversation(events, include_metadata=True)
        assert len(out) == 1
        assert out[0]["content"] == "q"
        assert out[0]["metadata"]["turn_index"] == 1

    def test_same_content_different_turns_kept(self):
        # Same wording in different turns is legitimately distinct.
        events = [
            {
                "type": "user_message",
                "content": "再试",
                "event_id": 1,
                "turn_index": 1,
                "branch_id": 1,
            },
            {
                "type": "user_message",
                "content": "再试",
                "event_id": 2,
                "turn_index": 2,
                "branch_id": 1,
            },
        ]
        out = replay_conversation(events, include_metadata=True)
        assert [m["metadata"]["turn_index"] for m in out] == [1, 2]

    def test_text_chunks_collapse(self):
        events = [
            {"type": "text_chunk", "content": "Hel", "event_id": 0},
            {"type": "text_chunk", "content": "lo", "event_id": 1},
        ]
        out = replay_conversation(events)
        assert out == [{"role": "assistant", "content": "Hello"}]

    def test_text_chunks_flush_on_user_message(self):
        events = [
            {"type": "text_chunk", "content": "first ", "event_id": 0},
            {"type": "text_chunk", "content": "reply", "event_id": 1},
            {"type": "user_message", "content": "next", "event_id": 2},
        ]
        out = replay_conversation(events)
        assert out == [
            {"role": "assistant", "content": "first reply"},
            {"role": "user", "content": "next"},
        ]

    def test_tool_calls_attached_to_assistant(self):
        events = [
            {"type": "text_chunk", "content": "ok", "event_id": 0},
            {
                "type": "assistant_tool_calls",
                "tool_calls": [{"id": "c1", "type": "function"}],
                "event_id": 1,
            },
        ]
        out = replay_conversation(events)
        assert out[-1]["role"] == "assistant"
        assert out[-1]["tool_calls"] == [{"id": "c1", "type": "function"}]

    def test_tool_result_role(self):
        events = [
            {
                "type": "tool_result",
                "output": "result",
                "call_id": "c1",
                "name": "bash",
                "event_id": 0,
            }
        ]
        out = replay_conversation(events)
        assert out == [
            {
                "role": "tool",
                "content": "result",
                "tool_call_id": "c1",
                "name": "bash",
            }
        ]

    def test_tool_result_strips_job_label_suffix(self):
        # Resume replay must not pass the UI job label ("bash[ff6427]")
        # through as the tool message name — providers reject names that
        # do not match ^[a-zA-Z0-9_-]+$. The clean name is derived from
        # the job id (shared job-label logic), not by parsing the label.
        events = [
            {
                "type": "tool_result",
                "output": "result",
                "call_id": "bash_ff642767",
                "name": "bash[ff6427]",
                "event_id": 0,
            }
        ]
        out = replay_conversation(events)
        assert out[0]["name"] == "bash"
        assert out[0]["tool_call_id"] == "bash_ff642767"

    def test_tool_result_clean_name_unchanged(self):
        events = [
            {
                "type": "tool_result",
                "output": "r",
                "call_id": "c1",
                "name": "group_status",
                "event_id": 0,
            }
        ]
        out = replay_conversation(events)
        assert out[0]["name"] == "group_status"

    def test_tool_result_derives_name_from_call_id(self):
        # When the stored name is missing but the job id carries the tool
        # name, the clean name still resolves.
        events = [
            {
                "type": "tool_result",
                "output": "r",
                "call_id": "grep_1234abcd",
                "name": "",
                "event_id": 0,
            }
        ]
        out = replay_conversation(events)
        assert out[0]["name"] == "grep"

    def test_tool_result_non_string_name_coerced(self):
        # Legacy/malformed events with a non-string name and no usable job
        # id must still yield a provider-safe string, not a None/int.
        for bad in (None, 123):
            events = [
                {
                    "type": "tool_result",
                    "output": "r",
                    "call_id": "",
                    "name": bad,
                    "event_id": 0,
                }
            ]
            out = replay_conversation(events)
            assert out[0]["name"] == ""

    def test_system_prompt_set(self):
        events = [{"type": "system_prompt_set", "content": "be nice", "event_id": 0}]
        out = replay_conversation(events)
        assert out == [{"role": "system", "content": "be nice"}]

    def test_unknown_event_type_ignored(self):
        events = [
            {"type": "mystery", "content": "junk", "event_id": 0},
            {"type": "user_message", "content": "hi", "event_id": 1},
        ]
        out = replay_conversation(events)
        assert out == [{"role": "user", "content": "hi"}]

    def test_token_usage_filtered(self):
        events = [
            {"type": "token_usage", "tokens": 100, "event_id": 0},
            {"type": "text_chunk", "content": "x", "event_id": 1},
        ]
        out = replay_conversation(events)
        assert out == [{"role": "assistant", "content": "x"}]

    def test_compact_replace_swaps_range(self):
        events = [
            {"type": "user_message", "content": "msg0", "event_id": 0},
            {"type": "text_chunk", "content": "reply", "event_id": 1},
            {
                "type": "compact_replace",
                "summary_text": "[compacted summary]",
                "replaced_from_event_id": 0,
                "replaced_to_event_id": 1,
                "event_id": 2,
            },
            {"type": "user_message", "content": "msg1", "event_id": 3},
        ]
        out = replay_conversation(events)
        assert out == [
            {"role": "assistant", "content": "[compacted summary]"},
            {"role": "user", "content": "msg1"},
        ]


class TestReplayBranching:
    def test_latest_branch_wins_by_default(self):
        events = [
            {
                "type": "user_message",
                "content": "old",
                "event_id": 0,
                "turn_index": 0,
                "branch_id": 1,
            },
            {
                "type": "user_message",
                "content": "new",
                "event_id": 1,
                "turn_index": 0,
                "branch_id": 2,
            },
        ]
        out = replay_conversation(events)
        # Branch 2 (latest) wins.
        assert out == [{"role": "user", "content": "new"}]

    def test_branch_view_selects_specific_branch(self):
        events = [
            {
                "type": "user_message",
                "content": "old",
                "event_id": 0,
                "turn_index": 0,
                "branch_id": 1,
            },
            {
                "type": "user_message",
                "content": "new",
                "event_id": 1,
                "turn_index": 0,
                "branch_id": 2,
            },
        ]
        out = replay_conversation(events, branch_view={0: 1})
        assert out == [{"role": "user", "content": "old"}]

    def test_compact_replace_global_id_range_mis_covers_sibling_branch(self):
        # Regression: a compact_replace recorded on ONE branch with a GLOBAL
        # id range (as v1_to_v2 migration produces) must not delete the other
        # branch's events. Only events on the compact path get replaced.
        #
        # Tree:
        #   turn0-b1 (common)
        #     |-- turn1-b1 -> turn2-b1   [branch 1]
        #     `-- turn1-b2 -> turn2-b2   [branch 2, fork at turn1]
        #
        # branch 2 compacts events [0..7] (turn0 + turn1 on its path); the
        # live tail turn2-b2 stays. branch 1 is untouched by branch 2's
        # compact and must keep its full history.
        events = [
            {
                "type": "user_message",
                "content": "U0",
                "event_id": 0,
                "turn_index": 0,
                "branch_id": 1,
                "parent_branch_path": [],
            },
            {
                "type": "text_chunk",
                "content": "R0",
                "event_id": 1,
                "turn_index": 0,
                "branch_id": 1,
                "parent_branch_path": [],
            },
            {
                "type": "user_message",
                "content": "U1a",
                "event_id": 2,
                "turn_index": 1,
                "branch_id": 1,
                "parent_branch_path": [(0, 1)],
            },
            {
                "type": "text_chunk",
                "content": "R1a",
                "event_id": 3,
                "turn_index": 1,
                "branch_id": 1,
                "parent_branch_path": [(0, 1)],
            },
            {
                "type": "user_message",
                "content": "U2a",
                "event_id": 4,
                "turn_index": 2,
                "branch_id": 1,
                "parent_branch_path": [(0, 1), (1, 1)],
            },
            {
                "type": "text_chunk",
                "content": "R2a",
                "event_id": 5,
                "turn_index": 2,
                "branch_id": 1,
                "parent_branch_path": [(0, 1), (1, 1)],
            },
            {
                "type": "user_message",
                "content": "U1b",
                "event_id": 6,
                "turn_index": 1,
                "branch_id": 2,
                "parent_branch_path": [(0, 1)],
            },
            {
                "type": "text_chunk",
                "content": "R1b",
                "event_id": 7,
                "turn_index": 1,
                "branch_id": 2,
                "parent_branch_path": [(0, 1)],
            },
            {
                "type": "user_message",
                "content": "U2b",
                "event_id": 8,
                "turn_index": 2,
                "branch_id": 2,
                "parent_branch_path": [(0, 1), (1, 2)],
            },
            {
                "type": "text_chunk",
                "content": "R2b",
                "event_id": 9,
                "turn_index": 2,
                "branch_id": 2,
                "parent_branch_path": [(0, 1), (1, 2)],
            },
            {
                "type": "compact_replace",
                "summary_text": "[S: branch2 compact]",
                "replaced_from_event_id": 0,
                "replaced_to_event_id": 7,
                "compact_path": [[0, 1], [1, 2]],
                "round": 1,
                "event_id": 10,
            },
        ]
        # branch 1: branch 2's summary carries branch-2-specific content, so
        # it must NOT leak into branch 1. Branch 1 keeps its full history
        # (the shared ancestor turn0-b1 included), untouched by branch 2's
        # compact.
        out1 = replay_conversation(events, branch_view={0: 1, 1: 1, 2: 1})
        assert out1 == [
            {"role": "user", "content": "U0"},
            {"role": "assistant", "content": "R0"},
            {"role": "user", "content": "U1a"},
            {"role": "assistant", "content": "R1a"},
            {"role": "user", "content": "U2a"},
            {"role": "assistant", "content": "R2a"},
        ]
        # branch 2: compacted zone [0..7] -> summary, live tail U2b/R2b kept.
        out2 = replay_conversation(events, branch_view={0: 1, 1: 2, 2: 2})
        assert out2 == [
            {"role": "assistant", "content": "[S: branch2 compact]"},
            {"role": "user", "content": "U2b"},
            {"role": "assistant", "content": "R2b"},
        ]

    def test_compact_complete_with_path_replayed_branch_aware(self):
        # P2: v2 runtime compaction emits compact_complete with a replaced
        # range + compact_path (unlike the legacy compact_replace). Replay
        # must consume it branch-aware exactly like compact_replace.
        events = [
            {
                "type": "user_message",
                "content": "U1",
                "event_id": 1,
                "turn_index": 1,
                "branch_id": 1,
                "parent_branch_path": [],
            },
            {
                "type": "text_chunk",
                "content": "R1",
                "event_id": 2,
                "turn_index": 1,
                "branch_id": 1,
                "parent_branch_path": [],
            },
            {
                "type": "user_message",
                "content": "U2a",
                "event_id": 3,
                "turn_index": 2,
                "branch_id": 1,
                "parent_branch_path": [(1, 1)],
            },
            {
                "type": "text_chunk",
                "content": "R2a",
                "event_id": 4,
                "turn_index": 2,
                "branch_id": 1,
                "parent_branch_path": [(1, 1)],
            },
            {
                "type": "user_message",
                "content": "U3a",
                "event_id": 5,
                "turn_index": 3,
                "branch_id": 1,
                "parent_branch_path": [(1, 1), (2, 1)],
            },
            {
                "type": "text_chunk",
                "content": "R3a",
                "event_id": 6,
                "turn_index": 3,
                "branch_id": 1,
                "parent_branch_path": [(1, 1), (2, 1)],
            },
            {
                "type": "user_message",
                "content": "U2b",
                "event_id": 7,
                "turn_index": 2,
                "branch_id": 2,
                "parent_branch_path": [(1, 1)],
            },
            {
                "type": "text_chunk",
                "content": "R2b",
                "event_id": 8,
                "turn_index": 2,
                "branch_id": 2,
                "parent_branch_path": [(1, 1)],
            },
            {
                "type": "user_message",
                "content": "U3b",
                "event_id": 9,
                "turn_index": 3,
                "branch_id": 2,
                "parent_branch_path": [(1, 1), (2, 2)],
            },
            {
                "type": "text_chunk",
                "content": "R3b",
                "event_id": 10,
                "turn_index": 3,
                "branch_id": 2,
                "parent_branch_path": [(1, 1), (2, 2)],
            },
            {
                "type": "compact_complete",
                "summary": "[S: branch2]",
                "event_id": 11,
                "replaced_from_event_id": 1,
                "replaced_to_event_id": 8,
                "compact_path": [[1, 1], [2, 2]],
                "turn_index": 3,
                "branch_id": 2,
                "parent_branch_path": [(1, 1), (2, 2)],
            },
        ]
        out1 = replay_conversation(events, branch_view={1: 1, 2: 1, 3: 1})
        # branch1 must NOT see branch2's summary (it carries branch-2-specific
        # turn2 content); branch1 keeps its full history.
        assert out1 == [
            {"role": "user", "content": "U1"},
            {"role": "assistant", "content": "R1"},
            {"role": "user", "content": "U2a"},
            {"role": "assistant", "content": "R2a"},
            {"role": "user", "content": "U3a"},
            {"role": "assistant", "content": "R3a"},
        ]
        out2 = replay_conversation(events, branch_view={1: 1, 2: 2, 3: 2})
        assert out2 == [
            {"role": "assistant", "content": "[S: branch2]"},
            {"role": "user", "content": "U3b"},
            {"role": "assistant", "content": "R3b"},
        ]

    def test_compact_path_not_intersecting_branch_does_not_inject_summary(self):
        # A compact_replace whose compact_path does not intersect the selected
        # branch replaces nothing on that branch — replay must NOT inject a
        # spurious summary there.
        events = [
            {
                "type": "user_message",
                "content": "U1",
                "event_id": 1,
                "turn_index": 1,
                "branch_id": 1,
                "parent_branch_path": [],
            },
            {
                "type": "text_chunk",
                "content": "R1",
                "event_id": 2,
                "turn_index": 1,
                "branch_id": 1,
                "parent_branch_path": [],
            },
            {
                "type": "user_message",
                "content": "U2a",
                "event_id": 3,
                "turn_index": 2,
                "branch_id": 1,
                "parent_branch_path": [(1, 1)],
            },
            {
                "type": "user_message",
                "content": "U2b",
                "event_id": 4,
                "turn_index": 2,
                "branch_id": 2,
                "parent_branch_path": [(1, 1)],
            },
            {
                "type": "compact_replace",
                "summary_text": "[S: b2]",
                "event_id": 5,
                "replaced_from_event_id": 4,
                "replaced_to_event_id": 4,
                "compact_path": [[2, 2]],
            },
        ]
        # branch1: nothing on its path is covered -> no summary, full history
        out1 = replay_conversation(events, branch_view={1: 1, 2: 1})
        assert out1 == [
            {"role": "user", "content": "U1"},
            {"role": "assistant", "content": "R1"},
            {"role": "user", "content": "U2a"},
        ]
        # branch2: U2b covered -> summary injected in place, R1 stays before it
        out2 = replay_conversation(events, branch_view={1: 1, 2: 2})
        assert out2 == [
            {"role": "user", "content": "U1"},
            {"role": "assistant", "content": "R1"},
            {"role": "assistant", "content": "[S: b2]"},
        ]


class TestCompactRuleSupersession:
    def _events(self):
        events = []
        eid = 0
        for turn in range(1, 7):
            eid += 1
            events.append(
                {
                    "type": "user_message",
                    "content": f"U{turn}",
                    "event_id": eid,
                    "turn_index": turn,
                    "branch_id": 1,
                    "parent_branch_path": [(t, 1) for t in range(1, turn)],
                }
            )
            eid += 1
            events.append(
                {
                    "type": "text_chunk",
                    "content": f"R{turn}",
                    "event_id": eid,
                    "turn_index": turn,
                    "branch_id": 1,
                    "parent_branch_path": [(t, 1) for t in range(1, turn)],
                }
            )
        return events

    def test_later_round_supersedes_earlier_on_same_lineage(self):
        # Two compaction rounds on the same branch: round2's range covers
        # round1's and its path grows, so only the newest summary is
        # injected — never stacked summaries.
        events = self._events()
        events.append(
            {
                "type": "compact_complete",
                "summary": "[S1 round1]",
                "event_id": 13,
                "replaced_from_event_id": 1,
                "replaced_to_event_id": 4,
                "compact_path": [[1, 1], [2, 1]],
                "turn_index": 2,
                "branch_id": 1,
            }
        )
        events.append(
            {
                "type": "compact_complete",
                "summary": "[S2 round2]",
                "event_id": 14,
                "replaced_from_event_id": 1,
                "replaced_to_event_id": 8,
                "compact_path": [[1, 1], [2, 1], [3, 1], [4, 1]],
                "turn_index": 4,
                "branch_id": 1,
            }
        )
        out = replay_conversation(events, branch_view={t: 1 for t in range(1, 7)})
        assert out == [
            {"role": "assistant", "content": "[S2 round2]"},
            {"role": "user", "content": "U5"},
            {"role": "assistant", "content": "R5"},
            {"role": "user", "content": "U6"},
            {"role": "assistant", "content": "R6"},
        ]

    def test_identical_duplicate_rules_collapse(self):
        # The same round recorded twice (non-adjacent, so adjacent-dedup
        # cannot help) must not inject its summary twice.
        events = self._events()
        events.append(
            {
                "type": "compact_complete",
                "summary": "[S]",
                "event_id": 13,
                "replaced_from_event_id": 1,
                "replaced_to_event_id": 4,
                "compact_path": [[1, 1], [2, 1]],
                "turn_index": 2,
                "branch_id": 1,
            }
        )
        events.append(
            {
                "type": "compact_start",
                "round": 1,
                "event_id": 14,
                "turn_index": 2,
                "branch_id": 1,
            }
        )
        events.append(
            {
                "type": "compact_complete",
                "summary": "[S]",
                "event_id": 15,
                "replaced_from_event_id": 1,
                "replaced_to_event_id": 4,
                "compact_path": [[1, 1], [2, 1]],
                "turn_index": 2,
                "branch_id": 1,
            }
        )
        out = replay_conversation(events, branch_view={t: 1 for t in range(1, 7)})
        assert out == [
            {"role": "assistant", "content": "[S]"},
            {"role": "user", "content": "U3"},
            {"role": "assistant", "content": "R3"},
            {"role": "user", "content": "U4"},
            {"role": "assistant", "content": "R4"},
            {"role": "user", "content": "U5"},
            {"role": "assistant", "content": "R5"},
            {"role": "user", "content": "U6"},
            {"role": "assistant", "content": "R6"},
        ]

    def test_later_path_rule_supersedes_legacy_pathless_rule(self):
        # Migration data carries a pathless compact_replace; a later live
        # compact_complete on the same lineage with an enclosing range is
        # authoritative.
        events = self._events()
        events.append(
            {
                "type": "compact_replace",
                "summary_text": "[S legacy]",
                "event_id": 13,
                "replaced_from_event_id": 1,
                "replaced_to_event_id": 4,
            }
        )
        events.append(
            {
                "type": "compact_complete",
                "summary": "[S live]",
                "event_id": 14,
                "replaced_from_event_id": 1,
                "replaced_to_event_id": 8,
                "compact_path": [[1, 1], [2, 1], [3, 1], [4, 1]],
                "turn_index": 4,
                "branch_id": 1,
            }
        )
        out = replay_conversation(events, branch_view={t: 1 for t in range(1, 7)})
        assert out == [
            {"role": "assistant", "content": "[S live]"},
            {"role": "user", "content": "U5"},
            {"role": "assistant", "content": "R5"},
            {"role": "user", "content": "U6"},
            {"role": "assistant", "content": "R6"},
        ]


class TestDivergentBranchCompaction:
    """Two compactions on branches that share a prefix must not stack.

    branch1 compacts its prefix, then a sibling branch2 forks from the shared
    prefix and compacts its own (longer) prefix. Each branch's replay must see
    exactly one summary — its own — never the sibling's, and never both.
    """

    def _events(self):
        return [
            {
                "event_id": 1,
                "type": "user_message",
                "content": "U1",
                "turn_index": 1,
                "branch_id": 1,
                "parent_branch_path": [],
            },
            {
                "event_id": 2,
                "type": "text_chunk",
                "content": "R1",
                "turn_index": 1,
                "branch_id": 1,
                "parent_branch_path": [],
            },
            {
                "event_id": 3,
                "type": "user_message",
                "content": "U2",
                "turn_index": 2,
                "branch_id": 1,
                "parent_branch_path": [(1, 1)],
            },
            {
                "event_id": 4,
                "type": "text_chunk",
                "content": "R2",
                "turn_index": 2,
                "branch_id": 1,
                "parent_branch_path": [(1, 1)],
            },
            {
                "event_id": 5,
                "type": "user_message",
                "content": "U3a",
                "turn_index": 3,
                "branch_id": 1,
                "parent_branch_path": [(1, 1), (2, 1)],
            },
            {
                "event_id": 6,
                "type": "text_chunk",
                "content": "R3a",
                "turn_index": 3,
                "branch_id": 1,
                "parent_branch_path": [(1, 1), (2, 1)],
            },
            {
                "event_id": 7,
                "type": "compact_complete",
                "summary": "[S1]",
                "replaced_from_event_id": 1,
                "replaced_to_event_id": 4,
                "compact_path": [[1, 1], [2, 1], [3, 1]],
                "turn_index": 3,
                "branch_id": 1,
                "parent_branch_path": [(1, 1), (2, 1)],
            },
            {
                "event_id": 8,
                "type": "user_message",
                "content": "U3b",
                "turn_index": 3,
                "branch_id": 2,
                "parent_branch_path": [(1, 1), (2, 1)],
            },
            {
                "event_id": 9,
                "type": "text_chunk",
                "content": "R3b",
                "turn_index": 3,
                "branch_id": 2,
                "parent_branch_path": [(1, 1), (2, 1)],
            },
            {
                "event_id": 10,
                "type": "user_message",
                "content": "U4b",
                "turn_index": 4,
                "branch_id": 2,
                "parent_branch_path": [(1, 1), (2, 1), (3, 2)],
            },
            {
                "event_id": 11,
                "type": "text_chunk",
                "content": "R4b",
                "turn_index": 4,
                "branch_id": 2,
                "parent_branch_path": [(1, 1), (2, 1), (3, 2)],
            },
            {
                "event_id": 12,
                "type": "compact_complete",
                "summary": "[S2]",
                "replaced_from_event_id": 1,
                "replaced_to_event_id": 9,
                "compact_path": [[1, 1], [2, 1], [3, 2], [4, 2]],
                "turn_index": 4,
                "branch_id": 2,
                "parent_branch_path": [(1, 1), (2, 1), (3, 2)],
            },
        ]

    def test_each_branch_sees_only_its_own_summary(self):
        events = self._events()
        out1 = replay_conversation(events, branch_view={1: 1, 2: 1, 3: 1})
        assert [m["content"] for m in out1] == ["[S1]", "U3a", "R3a"]
        out2 = replay_conversation(events, branch_view={1: 1, 2: 1, 3: 2, 4: 2})
        assert [m["content"] for m in out2] == ["[S2]", "U4b", "R4b"]


class TestPendingSummaryScan:
    def _events(self):
        events = []
        eid = 0
        for turn in range(1, 9):
            eid += 1
            events.append(
                {
                    "type": "user_message",
                    "content": f"U{turn}",
                    "event_id": eid,
                    "turn_index": turn,
                    "branch_id": 2,
                }
            )
            eid += 1
            events.append(
                {
                    "type": "text_chunk",
                    "content": f"R{turn}",
                    "event_id": eid,
                    "turn_index": turn,
                    "branch_id": 2,
                }
            )
        # branch 3 (live at turns 9..12) — interleaved, not covered by SEL
        for turn in range(9, 13):
            eid += 1
            events.append(
                {
                    "type": "user_message",
                    "content": f"U{turn}",
                    "event_id": eid,
                    "turn_index": turn,
                    "branch_id": 3,
                }
            )
            eid += 1
            events.append(
                {
                    "type": "text_chunk",
                    "content": f"R{turn}",
                    "event_id": eid,
                    "turn_index": turn,
                    "branch_id": 3,
                }
            )
        for turn in range(13, 17):
            eid += 1
            events.append(
                {
                    "type": "user_message",
                    "content": f"U{turn}",
                    "event_id": eid,
                    "turn_index": turn,
                    "branch_id": 2,
                }
            )
            eid += 1
            events.append(
                {
                    "type": "text_chunk",
                    "content": f"R{turn}",
                    "event_id": eid,
                    "turn_index": turn,
                    "branch_id": 2,
                }
            )
        return events

    def test_sibling_rule_does_not_block_later_summary(self):
        # A sibling-branch compact (range 10..20, path branch 1) sorts before
        # the selected branch's compact (15..30). The pending-summary flush
        # must not stop at the sibling rule (whose range has not passed and
        # which never intersects this branch) — otherwise [SEL] is injected
        # after the interleaved branch-3 turns instead of before them.
        events = self._events()
        events.append(
            {
                "type": "compact_complete",
                "summary": "[SIB]",
                "event_id": 33,
                "replaced_from_event_id": 10,
                "replaced_to_event_id": 20,
                "compact_path": [[5, 1], [6, 1], [7, 1], [8, 1], [9, 1], [10, 1]],
                "turn_index": 10,
                "branch_id": 1,
            }
        )
        events.append(
            {
                "type": "compact_complete",
                "summary": "[SEL]",
                "event_id": 34,
                "replaced_from_event_id": 15,
                "replaced_to_event_id": 30,
                # branch2's own turns only (1..8, 13..16); turns 9..12 belong to
                # branch3 in the selected view and must not be claimed.
                "compact_path": [
                    [t, 2] for t in list(range(1, 9)) + list(range(13, 17))
                ],
                "turn_index": 16,
                "branch_id": 2,
            }
        )
        branch_view = {t: 2 for t in list(range(1, 9)) + list(range(13, 17))}
        branch_view.update({t: 3 for t in range(9, 13)})
        out = replay_conversation(events, branch_view=branch_view)
        # [SEL] must sit before the branch-3 turns (u9..a12) that follow the
        # replaced branch-2 prefix, and turn 16 stays live.
        assert out == [
            {"role": "user", "content": "U1"},
            {"role": "assistant", "content": "R1"},
            {"role": "user", "content": "U2"},
            {"role": "assistant", "content": "R2"},
            {"role": "user", "content": "U3"},
            {"role": "assistant", "content": "R3"},
            {"role": "user", "content": "U4"},
            {"role": "assistant", "content": "R4"},
            {"role": "user", "content": "U5"},
            {"role": "assistant", "content": "R5"},
            {"role": "user", "content": "U6"},
            {"role": "assistant", "content": "R6"},
            {"role": "user", "content": "U7"},
            {"role": "assistant", "content": "R7"},
            {"role": "assistant", "content": "[SEL]"},
            {"role": "user", "content": "U9"},
            {"role": "assistant", "content": "R9"},
            {"role": "user", "content": "U10"},
            {"role": "assistant", "content": "R10"},
            {"role": "user", "content": "U11"},
            {"role": "assistant", "content": "R11"},
            {"role": "user", "content": "U12"},
            {"role": "assistant", "content": "R12"},
            {"role": "user", "content": "U16"},
            {"role": "assistant", "content": "R16"},
        ]


# ── dedupe_adjacent_duplicate_events ──────────────────────────────


class TestDedupe:
    def test_identical_adjacent_events_dropped(self):
        events = [
            {"type": "text_chunk", "content": "a", "event_id": 0, "ts": 1.0},
            {"type": "text_chunk", "content": "a", "event_id": 1, "ts": 2.0},
        ]
        out = dedupe_adjacent_duplicate_events(events)
        # event_id and ts excluded from signature → duplicates collapse.
        assert len(out) == 1

    def test_different_content_kept(self):
        events = [
            {"type": "text_chunk", "content": "a", "event_id": 0},
            {"type": "text_chunk", "content": "b", "event_id": 1},
        ]
        out = dedupe_adjacent_duplicate_events(events)
        assert len(out) == 2

    def test_non_adjacent_duplicates_kept(self):
        events = [
            {"type": "text_chunk", "content": "a", "event_id": 0},
            {"type": "text_chunk", "content": "b", "event_id": 1},
            {"type": "text_chunk", "content": "a", "event_id": 2},
        ]
        out = dedupe_adjacent_duplicate_events(events)
        # Only adjacent collapses.
        assert len(out) == 3

    def test_unhashable_values_handled(self):
        events = [
            {"type": "tool_call", "args": {"k": [1, 2]}, "event_id": 0},
            {"type": "tool_call", "args": {"k": [1, 2]}, "event_id": 1},
        ]
        out = dedupe_adjacent_duplicate_events(events)
        assert len(out) == 1


# ── normalize_resumable_events ────────────────────────────────────


class TestNormalizeResumable:
    def test_no_unfinished_does_not_inject_interrupt(self):
        """A paired tool_call/tool_result must not produce an
        interrupt synthetic event. (B8: the normaliser now also
        injects an ``assistant_tool_calls`` announcement for every
        orphan tool pair so :func:`replay_conversation` can produce
        a complete assistant→tool pairing — that's separate from
        the interrupt-on-resume path being tested here.)
        """
        events = [
            {"type": "tool_call", "call_id": "c1", "name": "bash", "ts": 1.0},
            {"type": "tool_result", "call_id": "c1", "output": "ok", "ts": 2.0},
        ]
        out = normalize_resumable_events(events)
        synth_interrupts = [e for e in out if e.get("_synthetic_resume")]
        assert synth_interrupts == []
        # Original two events still present, in order.
        original = [
            e
            for e in out
            if not e.get("_synthetic_resume") and not e.get("_synthetic_announce")
        ]
        assert original == events

    def test_unfinished_tool_synthesizes_interrupted(self):
        events = [
            {"type": "tool_call", "call_id": "c1", "name": "bash", "ts": 1.0},
        ]
        out = normalize_resumable_events(events)
        synth = [e for e in out if e.get("_synthetic_resume")]
        assert len(synth) == 1
        assert synth[0]["type"] == "tool_result"
        assert synth[0]["interrupted"] is True
        assert synth[0]["error"] == "Interrupted by session resume"
        assert synth[0]["call_id"] == "c1"

    def test_live_job_id_excludes_from_interrupted(self):
        events = [
            {"type": "tool_call", "call_id": "c1", "name": "bash", "ts": 1.0},
        ]
        out = normalize_resumable_events(events, live_job_ids={"c1"})
        synth = [e for e in out if e.get("_synthetic_resume")]
        assert synth == []

    def test_unfinished_subagent_synthesizes_interrupted(self):
        events = [
            {
                "type": "subagent_call",
                "job_id": "j1",
                "name": "explore",
                "task": "find x",
                "ts": 1.0,
            }
        ]
        out = normalize_resumable_events(events)
        synth = [e for e in out if e.get("_synthetic_resume")]
        assert len(synth) == 1
        assert synth[0]["type"] == "subagent_result"
        assert synth[0]["interrupted"] is True

    def test_does_not_mutate_input(self):
        events = [{"type": "tool_call", "call_id": "c1", "ts": 0}]
        snapshot = [dict(e) for e in events]
        normalize_resumable_events(events)
        assert events == snapshot

    def test_synthesizes_assistant_tool_calls_for_orphan_pair(self):
        """B8: paired tool_call + tool_result without an announcing
        ``assistant_tool_calls`` must be augmented with a synthetic
        announcement so :func:`replay_conversation` produces a complete
        assistant→tool pair and the orphan sanitiser does not drop the
        tool message.
        """
        events = [
            {"type": "user_message", "content": "go", "ts": 1.0},
            {
                "type": "tool_call",
                "call_id": "j1",
                "name": "bash",
                "args": {"cmd": "ls"},
                "ts": 2.0,
            },
            {
                "type": "tool_result",
                "call_id": "j1",
                "name": "bash",
                "output": "ok",
                "ts": 3.0,
            },
        ]
        out = normalize_resumable_events(events)
        synth_announces = [
            e
            for e in out
            if e.get("type") == "assistant_tool_calls" and e.get("_synthetic_announce")
        ]
        assert len(synth_announces) == 1, (
            "expected exactly one synthetic assistant_tool_calls "
            f"announcement, got {synth_announces!r}"
        )
        tcs = synth_announces[0].get("tool_calls") or []
        assert len(tcs) == 1 and tcs[0].get("id") == "j1"
        assert tcs[0]["function"]["name"] == "bash"
        # Synthetic announcement MUST appear before the tool_result so
        # ``replay_conversation`` sees the assistant before the role=tool
        # message — otherwise the orphan sanitiser drops the tool msg.
        idx_announce = next(
            i
            for i, e in enumerate(out)
            if e.get("_synthetic_announce") and e.get("type") == "assistant_tool_calls"
        )
        idx_result = next(
            i
            for i, e in enumerate(out)
            if e.get("type") == "tool_result" and not e.get("_synthetic_resume")
        )
        assert idx_announce < idx_result

    def test_does_not_double_announce_when_event_already_present(self):
        """If the worker (e.g. v1→v2 migrator) already wrote an
        ``assistant_tool_calls`` event, the normaliser must NOT add a
        duplicate synthetic one — otherwise the conversation gets a
        ghost assistant turn."""
        events = [
            {
                "type": "assistant_tool_calls",
                "tool_calls": [
                    {
                        "id": "j1",
                        "type": "function",
                        "function": {"name": "bash", "arguments": "{}"},
                    }
                ],
                "ts": 1.5,
            },
            {
                "type": "tool_call",
                "call_id": "j1",
                "name": "bash",
                "args": {"cmd": "ls"},
                "ts": 2.0,
            },
            {
                "type": "tool_result",
                "call_id": "j1",
                "name": "bash",
                "output": "ok",
                "ts": 3.0,
            },
        ]
        out = normalize_resumable_events(events)
        synth_announces = [e for e in out if e.get("_synthetic_announce")]
        assert synth_announces == []

    def test_replay_after_normalize_pairs_assistant_and_tool(self):
        """End-to-end: normalize → replay should produce a tool_calls
        announcement immediately before the role=tool message, with
        matching ids — the conversation the orphan sanitiser sees has
        no dangling tool messages.
        """
        events = [
            {"type": "user_message", "content": "go", "ts": 1.0},
            {
                "type": "tool_call",
                "call_id": "j1",
                "name": "bash",
                "args": {"cmd": "ls"},
                "ts": 2.0,
            },
            {
                "type": "tool_result",
                "call_id": "j1",
                "name": "bash",
                "output": "ok",
                "ts": 3.0,
            },
            {"type": "text_chunk", "content": "done", "chunk_seq": 0, "ts": 4.0},
        ]
        normalized = normalize_resumable_events(events)
        messages = replay_conversation(normalized)
        # There must be an assistant message with tool_calls preceding
        # the role=tool message, and ids must match.
        tool_msg_idx = next(
            i for i, m in enumerate(messages) if m.get("role") == "tool"
        )
        assert tool_msg_idx > 0
        prior = messages[tool_msg_idx - 1]
        assert prior.get("role") == "assistant"
        assert prior.get(
            "tool_calls"
        ), f"assistant message preceding tool result lacks tool_calls: {prior!r}"
        announced_ids = {tc.get("id") for tc in prior["tool_calls"]}
        assert messages[tool_msg_idx].get("tool_call_id") in announced_ids

    def test_subagent_call_does_not_split_announcement(self):
        """B8 corner case: a single LLM turn dispatching
        ``[tool_call T1, subagent_call S1, tool_call T2]`` must produce
        ONE synthetic ``assistant_tool_calls`` event listing both T1 and
        T2 — the interleaved ``subagent_call`` is a sibling-pending
        event (like another tool_call), not a structural flush
        trigger. Splitting on subagent_call yields two announcement
        events, each carrying only one tool_call, and the conversation
        rebuilder can no longer pair the second tool_result with an
        announcing assistant message.
        """
        events = [
            {"type": "user_message", "content": "go", "ts": 1.0},
            {
                "type": "tool_call",
                "call_id": "T1",
                "name": "bash",
                "args": {"cmd": "ls"},
                "ts": 2.0,
            },
            {
                "type": "subagent_call",
                "job_id": "S1",
                "name": "explore",
                "task": "look around",
                "ts": 2.5,
            },
            {
                "type": "tool_call",
                "call_id": "T2",
                "name": "read",
                "args": {"path": "f.txt"},
                "ts": 3.0,
            },
            {
                "type": "tool_result",
                "call_id": "T1",
                "name": "bash",
                "output": "ok",
                "ts": 4.0,
            },
            {
                "type": "tool_result",
                "call_id": "T2",
                "name": "read",
                "output": "content",
                "ts": 5.0,
            },
            {
                "type": "subagent_result",
                "job_id": "S1",
                "name": "explore",
                "output": "done",
                "success": True,
                "ts": 6.0,
            },
        ]
        out = normalize_resumable_events(events)
        synth_announces = [
            e
            for e in out
            if e.get("type") == "assistant_tool_calls" and e.get("_synthetic_announce")
        ]
        assert len(synth_announces) == 1, (
            "expected exactly one synthetic assistant_tool_calls "
            f"event spanning both tool_calls, got {len(synth_announces)}: "
            f"{synth_announces!r}"
        )
        announced_ids = {
            tc.get("id") for tc in (synth_announces[0].get("tool_calls") or [])
        }
        assert announced_ids == {
            "T1",
            "T2",
        }, f"announcement should list BOTH T1 and T2, got {announced_ids!r}"


# ── collect_branch_metadata ────────────────────────────────────────


class TestStrictBranchView:
    @staticmethod
    def _events():
        return [
            {
                "type": "user_message",
                "turn_index": 1,
                "branch_id": 1,
                "parent_branch_path": [],
            },
            {
                "type": "user_message",
                "turn_index": 1,
                "branch_id": 2,
                "parent_branch_path": [],
            },
            {
                "type": "user_message",
                "turn_index": 2,
                "branch_id": 1,
                "parent_branch_path": [[1, 1]],
            },
            {
                "type": "user_message",
                "turn_index": 2,
                "branch_id": 2,
                "parent_branch_path": [[1, 2]],
            },
        ]

    def test_rejects_noncanonical_or_nonpositive_view_values(self):
        events = self._events()

        for view in ({"01": 1}, {1: "01"}, {1: 0}, {1: -1}, {True: 1}):
            with pytest.raises(InvalidBranchViewError):
                resolve_branch_view_strict(events, view)

    def test_rejects_nonexistent_selection_but_legacy_read_remains_permissive(self):
        events = self._events()

        with pytest.raises(InvalidBranchViewError, match="does not exist"):
            resolve_branch_view_strict(events, {1: 99})

        live_ids = select_live_event_ids(events, branch_view={1: 99})
        assert live_ids == set()

    def test_rejects_incompatible_ancestor_and_descendant(self):
        with pytest.raises(InvalidBranchViewError, match="incompatible"):
            resolve_branch_view_strict(self._events(), {1: 1, 2: 2})

    def test_descendant_selection_projects_required_ancestor(self):
        assert resolve_branch_view_strict(self._events(), {2: 1}) == {1: 1, 2: 1}

    def test_projects_authoritative_metadata_for_branch_navigation(self):
        projected = project_branch_metadata(self._events(), {2: 1})

        assert projected == {
            1: {
                "branches": [
                    {
                        "branch_id": 1,
                        "parent_branch_paths": [[]],
                        "selected": True,
                    },
                    {
                        "branch_id": 2,
                        "parent_branch_paths": [[]],
                        "selected": False,
                    },
                ],
                "latest": 2,
                "selected": 1,
            },
            2: {
                "branches": [
                    {
                        "branch_id": 1,
                        "parent_branch_paths": [[[1, 1]]],
                        "selected": True,
                    },
                    {
                        "branch_id": 2,
                        "parent_branch_paths": [[[1, 2]]],
                        "selected": False,
                    },
                ],
                "latest": 2,
                "selected": 1,
            },
        }


class TestCollectBranchMetadata:
    def test_single_branch_per_turn(self):
        events = [
            {
                "type": "user_message",
                "event_id": 0,
                "turn_index": 0,
                "branch_id": 1,
            },
            {
                "type": "text_chunk",
                "event_id": 1,
                "turn_index": 0,
                "branch_id": 1,
            },
        ]
        meta = collect_branch_metadata(events)
        assert 0 in meta
        assert meta[0]["branches"] == [1]
        assert meta[0]["latest_branch"] == 1
        assert meta[0]["events_by_branch"][1] == [0, 1]

    def test_multiple_branches(self):
        events = [
            {
                "type": "user_message",
                "event_id": 0,
                "turn_index": 0,
                "branch_id": 1,
            },
            {
                "type": "user_message",
                "event_id": 1,
                "turn_index": 0,
                "branch_id": 2,
            },
        ]
        meta = collect_branch_metadata(events)
        assert meta[0]["branches"] == [1, 2]
        assert meta[0]["latest_branch"] == 2

    def test_events_without_turn_ignored(self):
        events = [
            {"type": "token_usage", "event_id": 0, "tokens": 5},
        ]
        meta = collect_branch_metadata(events)
        assert meta == {}


# ── collect_user_groups ────────────────────────────────────────────


class TestCollectUserGroups:
    def test_same_user_content_groups_branches(self):
        events = [
            {
                "type": "user_message",
                "content": "hello",
                "event_id": 0,
                "turn_index": 0,
                "branch_id": 1,
            },
            {
                "type": "user_message",
                "content": "hello",
                "event_id": 1,
                "turn_index": 0,
                "branch_id": 2,
            },
        ]
        groups = collect_user_groups(events)
        assert 0 in groups
        assert len(groups[0]["groups"]) == 1
        assert groups[0]["groups"][0]["branches"] == [1, 2]

    def test_different_user_content_separate_groups(self):
        events = [
            {
                "type": "user_message",
                "content": "v1",
                "event_id": 0,
                "turn_index": 0,
                "branch_id": 1,
            },
            {
                "type": "user_message",
                "content": "v2",
                "event_id": 1,
                "turn_index": 0,
                "branch_id": 2,
            },
        ]
        groups = collect_user_groups(events)
        assert len(groups[0]["groups"]) == 2


# ── select_live_event_ids ──────────────────────────────────────────


class TestSelectLiveEventIds:
    def test_no_branches_all_live(self):
        events = [
            {"type": "user_message", "event_id": 0},
            {"type": "text_chunk", "event_id": 1},
        ]
        live = select_live_event_ids(events)
        assert live == {0, 1}

    def test_latest_branch_is_live_default(self):
        events = [
            {
                "type": "user_message",
                "event_id": 0,
                "turn_index": 0,
                "branch_id": 1,
            },
            {
                "type": "user_message",
                "event_id": 1,
                "turn_index": 0,
                "branch_id": 2,
            },
        ]
        live = select_live_event_ids(events)
        assert live == {1}

    def test_branch_view_override(self):
        events = [
            {
                "type": "user_message",
                "event_id": 0,
                "turn_index": 0,
                "branch_id": 1,
            },
            {
                "type": "user_message",
                "event_id": 1,
                "turn_index": 0,
                "branch_id": 2,
            },
        ]
        live = select_live_event_ids(events, branch_view={0: 1})
        assert live == {0}


# ── index_parent_paths ───────────────────────────────────────────


class TestIndexParentPaths:
    def test_explicit_path_wins(self):
        events = [
            {
                "event_id": 0,
                "turn_index": 0,
                "branch_id": 1,
                "parent_branch_path": [[0, 99]],
            }
        ]
        paths = index_parent_paths(events)
        assert paths[0] == ((0, 99),)

    def test_implicit_path_from_prior_turns(self):
        events = [
            {"event_id": 0, "turn_index": 0, "branch_id": 1},
            {"event_id": 1, "turn_index": 1, "branch_id": 1},
        ]
        paths = index_parent_paths(events)
        # Event 0 (turn 0) has no priors.
        assert paths[0] == ()
        # Event 1 (turn 1) has turn 0 latest branch in its path.
        assert paths[1] == ((0, 1),)


# ── resolve_selected_branches ────────────────────────────────────


class TestResolveSelectedBranches:
    def test_default_picks_max_branch(self):
        events = [
            {"event_id": 0, "turn_index": 0, "branch_id": 1},
            {"event_id": 1, "turn_index": 0, "branch_id": 3},
            {"event_id": 2, "turn_index": 0, "branch_id": 2},
        ]
        sel = resolve_selected_branches(events, index_parent_paths(events), None)
        assert sel == {0: 3}

    def test_branch_view_respected(self):
        events = [
            {"event_id": 0, "turn_index": 0, "branch_id": 1},
            {"event_id": 1, "turn_index": 0, "branch_id": 2},
        ]
        sel = resolve_selected_branches(events, index_parent_paths(events), {0: 1})
        assert sel == {0: 1}

    def test_branch_view_unknown_branch_falls_back(self):
        events = [
            {"event_id": 0, "turn_index": 0, "branch_id": 1},
            {"event_id": 1, "turn_index": 0, "branch_id": 2},
        ]
        # Branch 99 doesn't exist — falls back to max.
        sel = resolve_selected_branches(events, index_parent_paths(events), {0: 99})
        assert sel == {0: 2}


class TestSelectLiveEventIdsPrefix:
    """select_live_event_ids builds the prior-selected prefix per event; a
    parent path constraining only turn 0 must not reject an event whose
    later-turn ancestor is live."""

    def test_path_constrained_by_early_turn_only(self):
        events = [
            {
                "type": "user_message",
                "content": "U0",
                "event_id": 0,
                "turn_index": 0,
                "branch_id": 1,
                "parent_branch_path": [],
            },
            {
                "type": "text_chunk",
                "content": "R0",
                "event_id": 1,
                "turn_index": 0,
                "branch_id": 1,
                "parent_branch_path": [],
            },
            # Turn 1 has two branches; the live one is 2.
            {
                "type": "user_message",
                "content": "U1",
                "event_id": 2,
                "turn_index": 1,
                "branch_id": 1,
                "parent_branch_path": [(0, 1)],
            },
            {
                "type": "user_message",
                "content": "U1b",
                "event_id": 3,
                "turn_index": 1,
                "branch_id": 2,
                "parent_branch_path": [(0, 1)],
            },
            # Turn 2 event descends from turn1-b2; its path only constrains
            # turns 0 and 1, and both are selected as expected.
            {
                "type": "user_message",
                "content": "U2",
                "event_id": 4,
                "turn_index": 2,
                "branch_id": 1,
                "parent_branch_path": [(0, 1), (1, 2)],
            },
        ]
        live = select_live_event_ids(events)
        # Turn1-b1 (event 2) is not the selected branch and drops out;
        # everything on the live chain stays.
        assert live == {0, 1, 3, 4}

    def test_event_with_dead_ancestor_dropped(self):
        # Same shape, but the turn-2 event descends from the DEAD turn1-b1:
        # its path (1, 1) mismatches the selected turn1-b2 and it must drop.
        events = [
            {
                "type": "user_message",
                "content": "U0",
                "event_id": 0,
                "turn_index": 0,
                "branch_id": 1,
                "parent_branch_path": [],
            },
            {
                "type": "user_message",
                "content": "U1a",
                "event_id": 2,
                "turn_index": 1,
                "branch_id": 1,
                "parent_branch_path": [(0, 1)],
            },
            {
                "type": "user_message",
                "content": "U1b",
                "event_id": 3,
                "turn_index": 1,
                "branch_id": 2,
                "parent_branch_path": [(0, 1)],
            },
            {
                "type": "user_message",
                "content": "U2-dead",
                "event_id": 4,
                "turn_index": 2,
                "branch_id": 1,
                "parent_branch_path": [(0, 1), (1, 1)],
            },
        ]
        live = select_live_event_ids(events)
        assert live == {0, 3}
        assert 4 not in live
