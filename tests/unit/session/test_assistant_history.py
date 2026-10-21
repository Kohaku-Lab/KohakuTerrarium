"""Unit tests for :mod:`kohakuterrarium.session.assistant_history`."""

from copy import deepcopy

from kohakuterrarium.session.assistant_history import (
    assistant_round_identities,
    project_assistant_rounds,
)


def _evt(event_id, etype, **kwargs):
    event = {
        "event_id": event_id,
        "type": etype,
        "turn_index": kwargs.pop("turn_index", 1),
        "branch_id": kwargs.pop("branch_id", 1),
        "parent_branch_path": kwargs.pop("parent_branch_path", []),
    }
    event.update(kwargs)
    return event


def _message(**kwargs):
    message = {"role": "assistant", "content": kwargs.pop("content", "ok")}
    message.update(kwargs)
    return message


def _ids(events):
    return [evt.get("event_id") for evt in events]


def _types(events):
    return [evt.get("type") for evt in events]


class _CountGet(dict):
    """Dict that records ``.get`` scans for deterministic complexity checks."""

    calls = 0

    def get(self, key, default=None):
        type(self).calls += 1
        return super().get(key, default)


class _CountEvents(list):
    sliced_items = 0

    def __getitem__(self, key):
        value = super().__getitem__(key)
        if isinstance(key, slice):
            type(self).sliced_items += len(value)
        return value


class _CountId(int):
    calls = 0

    def __hash__(self):
        type(self).calls += 1
        return int.__hash__(self)


class TestProjectAssistantRounds:
    def test_text_mode_synthetic_declaration_is_not_suppressed(self):
        events = [
            _evt(1, "assistant_round_start"),
            {
                "type": "assistant_tool_calls",
                "tool_calls": [
                    {"id": "textcall", "function": {"name": "echo", "arguments": "{}"}}
                ],
            },
            _evt(
                2,
                "assistant_message",
                round_start_event_id=1,
                message=_message(content="[/echo]hello[echo/]"),
            ),
        ]
        assert project_assistant_rounds(events)[1]["type"] == "assistant_tool_calls"

    def test_empty(self):
        assert project_assistant_rounds([]) == []

    def test_ordered_content_media_extras_toolcalls_detached(self):
        media = {"type": "image_url", "image_url": {"url": "https://img"}}
        extra = {"source": "model"}
        tool_calls = [
            {
                "id": "c1",
                "type": "function",
                "function": {"name": "bash", "arguments": "{}"},
            }
        ]
        end_message = _message(
            content=[
                {"type": "text", "text": "hello"},
                media,
            ],
            tool_calls=tool_calls,
            reasoning_content="think",
            extra_field=extra,
        )
        events = [
            _evt(1, "user_message", content="q"),
            _evt(2, "assistant_round_start"),
            _evt(3, "text_chunk", content="hel"),
            _evt(4, "assistant_image", url="https://img"),
            _evt(5, "assistant_reasoning", reasoning_content="think"),
            _evt(
                6,
                "assistant_tool_calls",
                tool_calls=tool_calls,
            ),
            _evt(
                7,
                "assistant_message",
                round_start_event_id=2,
                message=end_message,
            ),
        ]
        original = deepcopy(events)

        out = project_assistant_rounds(events)

        assert events == original
        assert _ids(out) == [1, 2, 3, 4, 5, 6, 7]
        assert _types(out) == [
            "user_message",
            "assistant_message",
            "assistant_round_display",
            "assistant_round_display",
            "assistant_round_display",
            "assistant_round_display",
            "assistant_round_end",
        ]
        projected = out[1]
        assert projected["_assistant_projected"] is True
        assert projected["canonical_source_event_id"] == 7
        assert projected["event_id"] == 2
        assert projected["message"] == end_message
        assert projected["message"] is not events[6]["message"]
        assert projected["message"]["content"] is not events[6]["message"]["content"]
        assert (
            projected["message"]["tool_calls"] is not events[6]["message"]["tool_calls"]
        )
        assert (
            projected["message"]["extra_field"]
            is not events[6]["message"]["extra_field"]
        )

        projected["message"]["content"][0]["text"] = "mutated"
        projected["message"]["extra_field"]["source"] = "changed"
        assert events[6]["message"]["content"][0]["text"] == "hello"
        assert events[6]["message"]["extra_field"]["source"] == "model"
        assert out[6]["message"]["content"][0]["text"] == "hello"

    def test_two_rounds_same_turn(self):
        events = [
            _evt(1, "user_message", content="q"),
            _evt(2, "assistant_round_start"),
            _evt(3, "text_chunk", content="first"),
            _evt(4, "assistant_tool_calls", tool_calls=[{"id": "c1"}]),
            _evt(5, "tool_call", call_id="c1", name="bash"),
            _evt(6, "tool_result", call_id="c1", output="r"),
            _evt(
                7,
                "assistant_message",
                round_start_event_id=2,
                message=_message(content="", tool_calls=[{"id": "c1"}]),
            ),
            _evt(8, "assistant_round_start"),
            _evt(9, "text_chunk", content="final"),
            _evt(
                10,
                "assistant_message",
                round_start_event_id=8,
                message=_message(content="final"),
            ),
        ]

        out = project_assistant_rounds(events)

        assert _types(out) == [
            "user_message",
            "assistant_message",
            "assistant_round_display",
            "assistant_round_display",
            "tool_call",
            "tool_result",
            "assistant_round_end",
            "assistant_message",
            "assistant_round_display",
            "assistant_round_end",
        ]
        assert out[1]["canonical_source_event_id"] == 7
        assert out[7]["canonical_source_event_id"] == 10
        assert out[1]["message"]["tool_calls"] == [{"id": "c1"}]
        assert out[7]["message"]["content"] == "final"
        assert _ids(out) == list(range(1, 11))

    def test_foreign_branch_pair_does_not_apply(self):
        events = [
            _evt(1, "assistant_round_start", branch_id=1),
            _evt(2, "text_chunk", content="keep", branch_id=1),
            _evt(
                3,
                "assistant_message",
                branch_id=2,
                round_start_event_id=1,
                message=_message(content="other-branch"),
            ),
        ]

        out = project_assistant_rounds(events)

        assert _types(out) == [
            "assistant_round_start",
            "text_chunk",
            "assistant_message",
        ]
        assert "_assistant_projected" not in out[0]
        assert out[1]["content"] == "keep"

    def test_foreign_parent_path_pair_does_not_apply(self):
        events = [
            _evt(1, "assistant_round_start", parent_branch_path=[[0, 1]]),
            _evt(2, "text_chunk", content="keep", parent_branch_path=[[0, 1]]),
            _evt(
                3,
                "assistant_message",
                parent_branch_path=[[0, 2]],
                round_start_event_id=1,
                message=_message(content="other-path"),
            ),
        ]

        out = project_assistant_rounds(events)

        assert _types(out) == [
            "assistant_round_start",
            "text_chunk",
            "assistant_message",
        ]

    def test_interrupted_start_fallback_keeps_display(self):
        events = [
            _evt(1, "assistant_round_start"),
            _evt(2, "text_chunk", content="partial"),
            _evt(3, "assistant_reasoning", reasoning_content="mid"),
            _evt(4, "user_message", content="next"),
        ]

        out = project_assistant_rounds(events)

        assert _types(out) == [
            "assistant_round_start",
            "text_chunk",
            "assistant_reasoning",
            "user_message",
        ]
        assert out[1]["content"] == "partial"

    def test_duplicate_ends_preserve_legacy_display(self):
        events = [
            _evt(1, "assistant_round_start"),
            _evt(2, "text_chunk", content="legacy"),
            _evt(
                3,
                "assistant_message",
                round_start_event_id=1,
                message=_message(content="first"),
            ),
            _evt(
                4,
                "assistant_message",
                round_start_event_id=1,
                message=_message(content="second"),
            ),
        ]

        out = project_assistant_rounds(events)

        assert _types(out) == [
            "assistant_round_start",
            "text_chunk",
            "assistant_message",
            "assistant_message",
        ]
        assert out[1]["content"] == "legacy"
        assert out[2]["message"]["content"] == "first"
        assert out[3]["message"]["content"] == "second"

    def test_overlapping_next_start_does_not_project(self):
        events = [
            _evt(1, "assistant_round_start"),
            _evt(2, "text_chunk", content="one"),
            _evt(3, "assistant_round_start"),
            _evt(4, "text_chunk", content="two"),
            _evt(
                5,
                "assistant_message",
                round_start_event_id=1,
                message=_message(content="late"),
            ),
            _evt(
                6,
                "assistant_message",
                round_start_event_id=3,
                message=_message(content="inner"),
            ),
        ]

        out = project_assistant_rounds(events)

        assert out[0]["type"] == "assistant_round_start"
        assert out[1]["type"] == "text_chunk"
        assert out[4]["type"] == "assistant_message"
        assert out[2]["type"] == "assistant_message"
        assert out[3]["type"] == "assistant_round_display"
        assert out[5]["type"] == "assistant_round_end"
        assert out[2]["canonical_source_event_id"] == 6

    def test_user_message_between_does_not_project(self):
        events = [
            _evt(1, "assistant_round_start"),
            _evt(2, "text_chunk", content="keep"),
            _evt(3, "user_message", content="interrupt"),
            _evt(
                4,
                "assistant_message",
                round_start_event_id=1,
                message=_message(content="stale"),
            ),
        ]

        out = project_assistant_rounds(events)

        assert _types(out) == [
            "assistant_round_start",
            "text_chunk",
            "user_message",
            "assistant_message",
        ]

    def test_bool_and_non_positive_ids_are_ignored(self):
        events = [
            _evt(True, "assistant_round_start"),
            _evt(2, "text_chunk", content="a"),
            _evt(
                3,
                "assistant_message",
                round_start_event_id=True,
                message=_message(content="bool-start"),
            ),
            _evt(0, "assistant_round_start"),
            _evt(5, "text_chunk", content="b"),
            _evt(
                6,
                "assistant_message",
                round_start_event_id=0,
                message=_message(content="zero-start"),
            ),
            _evt(7, "assistant_round_start"),
            _evt(8, "text_chunk", content="c"),
            _evt(
                False,
                "assistant_message",
                round_start_event_id=7,
                message=_message(content="bool-end"),
            ),
        ]

        out = project_assistant_rounds(events)

        assert "assistant_round_display" not in _types(out)
        assert out[0]["type"] == "assistant_round_start"
        assert out[3]["type"] == "assistant_round_start"
        assert out[6]["type"] == "assistant_round_start"
        assert out[2]["type"] == "assistant_message"
        assert out[5]["type"] == "assistant_message"
        assert out[8]["type"] == "assistant_message"

    def test_eligible_partial_coverage_fallback(self):
        events = [
            _evt(1, "assistant_round_start"),
            _evt(2, "text_chunk", content="keep"),
            _evt(
                3,
                "assistant_message",
                round_start_event_id=1,
                message=_message(content="full"),
            ),
        ]

        partial_start = project_assistant_rounds(events, eligible_ids={1})
        partial_end = project_assistant_rounds(events, eligible_ids={3})
        none_eligible = project_assistant_rounds(events, eligible_ids=set())
        full = project_assistant_rounds(events, eligible_ids={1, 2, 3})

        assert _types(partial_start) == [
            "assistant_round_start",
            "text_chunk",
            "assistant_message",
        ]
        assert _types(partial_end) == _types(partial_start)
        assert _types(none_eligible) == _types(partial_start)
        assert _types(full) == [
            "assistant_message",
            "assistant_round_display",
            "assistant_round_end",
        ]

    def test_idempotent_reapplication_keeps_projected_anchor(self):
        message = _message(
            content=[{"type": "text", "text": "hello"}],
            extra={"k": "v"},
            reasoning_content="r",
        )
        events = [
            _evt(1, "assistant_round_start"),
            _evt(2, "text_chunk", content="hello"),
            _evt(
                3,
                "assistant_message",
                round_start_event_id=1,
                message=message,
            ),
        ]

        first = project_assistant_rounds(events)
        second = project_assistant_rounds(first)

        assert _types(first) == _types(second)
        assert _ids(first) == _ids(second)
        assert second[0]["_assistant_projected"] is True
        assert second[0]["canonical_source_event_id"] == 3
        assert first[0]["message"] == message
        assert first[0]["message"] is not message
        assert first[0]["message"]["extra"] is not message["extra"]
        assert second[0]["message"] == first[0]["message"]
        assert second[0] is not first[0]
        assert second[0]["message"] is not first[0]["message"]
        assert second[0]["message"]["extra"] is not first[0]["message"]["extra"]
        assert second[0]["message"] is not second[2]["message"]
        assert second[2]["message"] is not first[2]["message"]
        second[0]["message"]["content"] = "mutated"
        second[0]["message"]["extra"]["k"] = "changed"
        assert first[0]["message"]["content"] == [{"type": "text", "text": "hello"}]
        assert first[0]["message"]["extra"]["k"] == "v"
        assert first[2]["message"]["extra"]["k"] == "v"
        assert events[0].get("message") is None
        assert events[2]["message"]["content"] == [{"type": "text", "text": "hello"}]
        assert events[2]["message"]["extra"]["k"] == "v"
        assert message["extra"]["k"] == "v"

    def test_parent_path_list_and_tuple_are_same_lineage(self):
        events = [
            _evt(1, "assistant_round_start", parent_branch_path=[[0, 1]]),
            _evt(2, "text_chunk", content="x", parent_branch_path=[[0, 1]]),
            _evt(
                3,
                "assistant_message",
                parent_branch_path=((0, 1),),
                round_start_event_id=1,
                message=_message(content="x"),
            ),
        ]

        out = project_assistant_rounds(events)

        assert out[0]["type"] == "assistant_message"
        assert out[0]["_assistant_projected"] is True
        assert out[2]["type"] == "assistant_round_end"

    def test_keeps_non_display_events_inside_projected_round(self):
        events = [
            _evt(1, "user_message", content="q"),
            _evt(2, "assistant_round_start"),
            _evt(3, "text", content="stream"),
            _evt(4, "assistant_text", content="alias"),
            _evt(5, "text_chunk", content="calling"),
            _evt(6, "assistant_tool_calls", tool_calls=[{"id": "c1"}]),
            _evt(7, "tool_call", call_id="c1", name="bash"),
            _evt(8, "tool_result", call_id="c1", output="ok"),
            _evt(9, "activity:thinking", name="think"),
            _evt(10, "activity:tool", name="bash"),
            _evt(11, "processing_start"),
            _evt(
                12,
                "assistant_message",
                round_start_event_id=2,
                message=_message(content="stream", tool_calls=[{"id": "c1"}]),
            ),
            _evt(13, "assistant_round_start"),
            _evt(14, "assistant_text", content="b"),
            _evt(
                15,
                "assistant_message",
                round_start_event_id=13,
                message=_message(content="b"),
            ),
        ]

        out = project_assistant_rounds(events)

        assert len(out) == len(events)
        assert _ids(out) == _ids(events) == list(range(1, 16))
        assert [evt["event_id"] for evt in out] == sorted(
            evt["event_id"] for evt in out
        )
        assert _types(out) == [
            "user_message",
            "assistant_message",
            "assistant_round_display",
            "assistant_round_display",
            "assistant_round_display",
            "assistant_round_display",
            "tool_call",
            "tool_result",
            "activity:thinking",
            "activity:tool",
            "processing_start",
            "assistant_round_end",
            "assistant_message",
            "assistant_round_display",
            "assistant_round_end",
        ]
        assert out[1]["_assistant_projected"] is True
        assert out[1]["event_id"] == 2
        assert out[1]["message"]["tool_calls"] == [{"id": "c1"}]
        assert _ids(out).index(12) > _ids(out).index(8)
        assert out[7]["type"] == "tool_result"
        assert out[8]["type"] == "activity:thinking"

    def test_malformed_call_identity_keeps_legacy_display(self):
        events = [
            _evt(1, "assistant_round_start"),
            _evt(2, "text_chunk", content="recover"),
            _evt(
                3,
                "assistant_message",
                round_start_event_id=1,
                message=_message(tool_calls=[{"id": []}]),
            ),
        ]
        assert _types(project_assistant_rounds(events)) == _types(events)

    def test_cross_type_duplicate_id_does_not_activate_another_interval(self):
        events = [
            _evt(1, "assistant_round_start"),
            _evt(2, "text_chunk", content="first"),
            _evt(
                3,
                "assistant_message",
                round_start_event_id=1,
                message=_message(content="first"),
            ),
            _evt(1, "activity:thinking"),
            _evt(4, "text_chunk", content="later"),
        ]
        out = project_assistant_rounds(events)
        assert out[3]["type"] == "activity:thinking"
        assert out[4]["type"] == "text_chunk"
        assert out[4]["content"] == "later"

    def test_valid_round_projection_scans_scale_linearly(self):
        def count(rounds):
            events = []
            for index in range(rounds):
                start = _CountId(index * 3 + 1)
                events.extend(
                    [
                        _evt(start, "assistant_round_start"),
                        _evt(_CountId(start + 1), "text_chunk", content="x"),
                        _evt(
                            _CountId(start + 2),
                            "assistant_message",
                            round_start_event_id=start,
                            message=_message(content="x"),
                        ),
                    ]
                )
            _CountId.calls = 0
            out = project_assistant_rounds(events)
            assert len(out) == rounds * 3
            assert (
                _types(out)
                == [
                    "assistant_message",
                    "assistant_round_display",
                    "assistant_round_end",
                ]
                * rounds
            )
            return _CountId.calls

        assert count(128) < count(64) * 3

    def test_event_get_scans_scale_linearly_with_nested_rounds(self):
        def _nested(rounds: int) -> list[_CountGet]:
            events = _CountEvents()
            starts: list[int] = []
            event_id = 1
            for _ in range(rounds):
                events.append(_CountGet(_evt(event_id, "assistant_round_start")))
                starts.append(event_id)
                event_id += 1
            for _ in range(rounds):
                events.append(_CountGet(_evt(event_id, "text_chunk", content="x")))
                event_id += 1
            for start_id in reversed(starts):
                events.append(
                    _CountGet(
                        _evt(
                            event_id,
                            "assistant_message",
                            round_start_event_id=start_id,
                            message=_message(content="x"),
                        )
                    )
                )
                event_id += 1
            return events

        def _scan_count(rounds: int) -> int:
            _CountGet.calls = 0
            _CountEvents.sliced_items = 0
            out = project_assistant_rounds(_nested(rounds))
            assert _types(out)[:2] == ["assistant_round_start", "assistant_round_start"]
            assert out[-1]["type"] == "assistant_message"
            innermost_start = rounds - 1
            innermost_end = 2 * rounds
            assert out[innermost_start]["type"] == "assistant_message"
            assert out[innermost_end]["type"] == "assistant_round_end"
            return _CountGet.calls + _CountEvents.sliced_items

        small = _scan_count(32)
        large = _scan_count(64)
        assert small > 0
        assert large < small * 3


class TestAssistantRoundIdentities:
    def test_pair_owns_start_display_pieces_and_end(self):
        events = [
            _evt(1, "assistant_round_start"),
            _evt(2, "text_chunk", content="a"),
            _evt(None, "text_chunk", content="no id"),
            _evt(0, "text_chunk", content="zero id"),
            _evt(3, "tool_call", call_id="job", tool_call_id="call"),
            _evt(4, "assistant_image", url="https://img"),
            _evt(
                5,
                "assistant_message",
                round_start_event_id=1,
                message=_message(content="full"),
            ),
        ]

        assert assistant_round_identities(events) == {1: (1, 2, 4, 5)}

    def test_round_identity_requires_both_endpoints_eligible(self):
        events = [
            _evt(1, "assistant_round_start"),
            _evt(2, "text_chunk", content="a"),
            _evt(3, "assistant_message", round_start_event_id=1, message=_message()),
        ]

        assert assistant_round_identities(events, eligible_ids={1, 3}) == {1: (1, 3)}
        assert assistant_round_identities(events, eligible_ids={1, 2}) == {}

    def test_foreign_display_piece_is_not_round_owned(self):
        """A display record on another branch is not this round's content."""
        events = [
            _evt(6, "assistant_round_start", branch_id=1),
            _evt(7, "text_chunk", content="other", branch_id=2),
            _evt(8, "text_chunk", content="other", turn_index=None, branch_id=3),
            _evt(
                9,
                "assistant_message",
                branch_id=1,
                round_start_event_id=6,
                message=_message(content="full"),
            ),
        ]

        assert assistant_round_identities(events) == {6: (6, 9)}
        assert assistant_round_identities(events, eligible_ids={6, 7, 8, 9}) == {
            6: (6, 9)
        }

    def test_same_lineage_display_pieces_stay_round_owned(self):
        events = [
            _evt(1, "assistant_round_start", turn_index=2, parent_branch_path=[[1, 1]]),
            _evt(
                2, "text_chunk", content="a", turn_index=2, parent_branch_path=[[1, 1]]
            ),
            _evt(
                3,
                "assistant_text",
                content="b",
                turn_index=2,
                parent_branch_path=((1, 1),),
            ),
            _evt(
                4, "assistant_image", url="https://img", turn_index=None, branch_id=None
            ),
            _evt(
                5,
                "assistant_message",
                turn_index=2,
                parent_branch_path=[[1, 1]],
                round_start_event_id=1,
                message=_message(content="full"),
            ),
        ]

        assert assistant_round_identities(events) == {1: (1, 2, 3, 4, 5)}

    def test_display_piece_without_ancestry_stays_round_owned(self):
        """A missing ``parent_branch_path`` is unknown, not another lineage."""
        events = [
            _evt(6, "assistant_round_start", turn_index=2, parent_branch_path=[[1, 1]]),
            _evt(7, "text_chunk", content="chunk", turn_index=2, branch_id=1),
            _evt(
                10,
                "assistant_message",
                turn_index=2,
                branch_id=1,
                parent_branch_path=[[1, 1]],
                round_start_event_id=6,
                message=_message(content="full"),
            ),
        ]

        assert assistant_round_identities(events) == {6: (6, 7, 10)}

    def test_partial_display_metadata_stays_round_owned(self):
        """An unknown turn or branch alone never excludes a display piece."""
        events = [
            _evt(6, "assistant_round_start", turn_index=2, parent_branch_path=[[1, 1]]),
            _evt(7, "text_chunk", content="no turn", turn_index=None),
            _evt(8, "text_chunk", content="no branch", turn_index=2, branch_id=None),
            _evt(
                10,
                "assistant_message",
                turn_index=2,
                branch_id=1,
                parent_branch_path=[[1, 1]],
                round_start_event_id=6,
                message=_message(content="full"),
            ),
        ]

        assert assistant_round_identities(events) == {6: (6, 7, 8, 10)}

    def test_display_piece_with_conflicting_ancestry_is_not_round_owned(self):
        """Same turn/branch but a contradicting known parent coordinate is foreign."""
        events = [
            _evt(
                6,
                "assistant_round_start",
                turn_index=2,
                branch_id=1,
                parent_branch_path=[[1, 1]],
            ),
            _evt(7, "text_chunk", content="other", turn_index=2, branch_id=1),
            _evt(
                8,
                "text_chunk",
                content="sibling",
                turn_index=2,
                branch_id=1,
                parent_branch_path=[[1, 2]],
            ),
            _evt(
                10,
                "assistant_message",
                turn_index=2,
                branch_id=1,
                parent_branch_path=[[1, 1]],
                round_start_event_id=6,
                message=_message(content="full"),
            ),
        ]

        assert assistant_round_identities(events) == {6: (6, 7, 10)}

    def test_display_piece_with_conflicting_deep_ancestry_is_not_round_owned(self):
        """A contradicting coordinate below the shared prefix is still conflict."""
        path = [[1, 1], [2, 1]]
        events = [
            _evt(
                6,
                "assistant_round_start",
                turn_index=3,
                branch_id=1,
                parent_branch_path=path,
            ),
            _evt(
                7,
                "text_chunk",
                content="other",
                turn_index=3,
                branch_id=1,
                parent_branch_path=[[1, 1], [2, 2]],
            ),
            _evt(
                10,
                "assistant_message",
                turn_index=3,
                branch_id=1,
                parent_branch_path=path,
                round_start_event_id=6,
                message=_message(content="full"),
            ),
        ]

        assert assistant_round_identities(events) == {6: (6, 10)}

    def test_sparse_ancestry_coordinates_are_not_positional_conflicts(self):
        path = [[1, 1], [2, 1]]
        events = [
            _evt(6, "assistant_round_start", turn_index=3, parent_branch_path=path),
            _evt(7, "text_chunk", turn_index=3, parent_branch_path=[[2, 1]]),
            _evt(8, "text_chunk", turn_index=3, parent_branch_path=[[2, 2]]),
            _evt(
                10,
                "assistant_message",
                turn_index=3,
                parent_branch_path=path,
                round_start_event_id=6,
                message=_message(),
            ),
        ]
        assert assistant_round_identities(events) == {6: (6, 7, 10)}

    def test_projected_round_owns_only_itself(self):
        events = [
            _evt(4, "assistant_message", round_start_event_id=9, message=_message()),
        ]
        events[0]["_assistant_projected"] = True

        assert assistant_round_identities(events) == {4: (4,)}
