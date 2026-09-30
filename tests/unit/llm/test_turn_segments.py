"""Unit tests for ``llm/turn_segments.py``."""

from kohakuterrarium.llm.turn_segments import (
    KT_ASSISTANT_SEGMENTS,
    TurnSegmentsBuilder,
    inject_anthropic_segments,
    segments_from_anthropic_blocks,
)


class TestTurnSegmentsBuilder:
    def test_records_reasoning_text_tool_order(self):
        builder = TurnSegmentsBuilder()
        builder.append_reasoning("think 1", source="reasoning_content")
        builder.append_text("answer 1")
        builder.append_tool_call_ref("call_1")
        builder.append_reasoning("think 2", source="responses_text")
        builder.append_tool_call_ref("call_2")
        builder.append_text("answer 2")

        fields = builder.inject_into({"reasoning_content": "think 1"})
        assert fields[KT_ASSISTANT_SEGMENTS] == [
            {"type": "reasoning", "source": "reasoning_content", "text": "think 1"},
            {"type": "text", "text": "answer 1"},
            {"type": "tool_call_ref", "call_id": "call_1"},
            {"type": "reasoning", "source": "responses_text", "text": "think 2"},
            {"type": "tool_call_ref", "call_id": "call_2"},
            {"type": "text", "text": "answer 2"},
        ]

    def test_adjacent_same_source_reasoning_is_merged(self):
        builder = TurnSegmentsBuilder()
        builder.append_reasoning("think ", source="reasoning_content")
        builder.append_reasoning("hard", source="reasoning_content")

        assert builder.as_list() == [
            {"type": "reasoning", "source": "reasoning_content", "text": "think hard"}
        ]

    def test_alternating_parallel_forms_merge_within_the_run(self):
        builder = TurnSegmentsBuilder()
        for piece in ("He", "llo", " wor", "ld"):
            builder.append_reasoning(piece, source="reasoning_content")
            builder.append_reasoning(
                piece, source="reasoning_details", key="0:reasoning.text"
            )
        builder.append_text("hi")

        assert builder.as_list() == [
            {"type": "reasoning", "source": "reasoning_content", "text": "Hello world"},
            {
                "type": "reasoning",
                "source": "reasoning_details",
                "key": "0:reasoning.text",
                "text": "Hello world",
            },
            {"type": "text", "text": "hi"},
        ]

    def test_reasoning_does_not_merge_across_text_or_tool_refs(self):
        builder = TurnSegmentsBuilder()
        builder.append_reasoning("a", source="reasoning_content")
        builder.append_reasoning("x", source="reasoning_details", key="0:t")
        builder.append_text("mid")
        builder.append_reasoning("b", source="reasoning_content")
        builder.append_tool_call_ref("call_1")
        builder.append_reasoning("c", source="reasoning_content")

        texts = [
            (s.get("source"), s.get("text"))
            for s in builder.as_list()
            if s["type"] != "tool_call_ref"
        ]
        assert texts == [
            ("reasoning_content", "a"),
            ("reasoning_details", "x"),
            (None, "mid"),
            ("reasoning_content", "b"),
            ("reasoning_content", "c"),
        ]

    def test_same_source_different_key_stays_separate_in_a_run(self):
        builder = TurnSegmentsBuilder()
        builder.append_reasoning("one", source="reasoning_details", key="0:t")
        builder.append_reasoning("two", source="reasoning_details", key="1:t")
        builder.append_reasoning("!", source="reasoning_details", key="0:t")

        assert [s["text"] for s in builder.as_list()] == ["one!", "two"]

    def test_signature_updates_the_matching_segment_in_the_run(self):
        builder = TurnSegmentsBuilder()
        builder.append_reasoning("a", source="reasoning_details", key="0:t")
        builder.append_reasoning("b", source="reasoning_content")
        builder.append_reasoning(
            "", source="reasoning_details", key="0:t", signature="sig"
        )

        first = builder.as_list()[0]
        assert first["signature"] == "sig"
        assert len(builder.as_list()) == 2

    def test_text_only_turn_does_not_emit_segments(self):
        builder = TurnSegmentsBuilder()
        builder.append_text("answer")
        assert builder.inject_into({}) == {}

    def test_tool_refs_are_finalized_by_index(self):
        builder = TurnSegmentsBuilder()
        builder.append_tool_call_ref("", index=0)
        builder.finalize_tool_call_refs(
            {0: {"id": "call_1", "name": "", "arguments": ""}}
        )
        builder.append_reasoning("think", source="reasoning_content")
        assert builder.as_list()[0] == {"type": "tool_call_ref", "call_id": "call_1"}

    def test_replace_reasoning_drops_partial_segments(self):
        builder = TurnSegmentsBuilder()
        builder.append_reasoning("partial", source="responses_text", key="item_1")
        builder.append_text("answer")
        builder.replace_reasoning("complete", source="responses_text", key="item_1")

        assert builder.as_list() == [
            {"type": "text", "text": "answer"},
            {
                "type": "reasoning",
                "source": "responses_text",
                "key": "item_1",
                "text": "complete",
            },
        ]


class TestAnthropicSegments:
    def test_blocks_preserve_thinking_text_tool_order(self):
        blocks = [
            {"type": "thinking", "thinking": "think 1", "signature": "sig1"},
            {"type": "text", "text": "answer 1"},
            {"type": "tool_use", "id": "call_1"},
            {"type": "thinking", "thinking": "think 2"},
        ]

        assert segments_from_anthropic_blocks(blocks) == [
            {
                "type": "reasoning",
                "source": "anthropic_thinking",
                "text": "think 1",
                "signature": "sig1",
            },
            {"type": "text", "text": "answer 1"},
            {"type": "tool_call_ref", "call_id": "call_1"},
            {
                "type": "reasoning",
                "source": "anthropic_thinking",
                "text": "think 2",
            },
        ]

    def test_inject_only_when_thinking_exists(self):
        assert inject_anthropic_segments({}, [{"type": "text", "text": "hi"}]) == {}
        fields = inject_anthropic_segments(
            {"_kt_anthropic_content": []},
            [{"type": "thinking", "thinking": "hmm"}],
        )
        assert fields[KT_ASSISTANT_SEGMENTS] == [
            {
                "type": "reasoning",
                "source": "anthropic_thinking",
                "text": "hmm",
            }
        ]
