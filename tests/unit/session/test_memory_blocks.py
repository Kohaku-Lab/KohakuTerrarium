"""Incremental extraction matches full replay without rescanning old events."""

import pytest

from kohakuterrarium.session.memory_blocks import (
    TOOL_RESULT_INDEX_CHARS,
    _content_to_text,
    _extract_blocks,
)

from kohakuterrarium.session.memory_blocks import RebuildRequired, extract_batch


def test_append_uses_only_the_tail_and_retains_paragraph_identity():
    rows = [{"type": "user_input", "content": f"prompt {i}"} for i in range(1000)]
    _, cursor = extract_batch("alice", rows)
    paragraph = "long paragraph " * 25
    rows.append({"type": "text", "content": paragraph + "\n\nsecond"})

    class TailOnly(list):
        def __getitem__(self, key):
            assert key.start >= 1000 if isinstance(key, slice) else key in (999, -1)
            return super().__getitem__(key)

    blocks, cursor = extract_batch("alice", TailOnly(rows), cursor)
    assert [b.round_num for b in blocks] == [1000, 1000]
    assert [b.block_num for b in blocks] == [1, 2]
    assert [b.block_id for b in blocks] == ["alice:e1000:p0", "alice:e1000:p1"]
    assert extract_batch("alice", rows, cursor)[0] == []


def test_trigger_end_duplicate_and_noncontiguous_ids_match_single_pass():
    rows = [
        {
            "type": "trigger_fired",
            "channel": "tasks",
            "content": "needle",
            "event_id": 4,
        },
        {"type": "text", "content": "reply", "event_id": 8},
        {"type": "text", "content": "reply", "event_id": 9},
        {"type": "processing_end", "event_id": 20},
        {"type": "text", "content": "outside", "event_id": 21},
        {"type": "user_input", "content": "next", "event_id": 30},
    ]
    cursor, incremental = None, []
    for end in range(1, len(rows) + 1):
        blocks, cursor = extract_batch("alice", rows[:end], cursor)
        incremental.extend(blocks)
    assert incremental == extract_batch("alice", rows)[0]
    assert [b.content for b in incremental] == [
        "[trigger:tasks] needle",
        "reply",
        "next",
    ]


def test_nested_branch_switch_requests_rebuild_and_selects_compatible_history():
    rows = [
        {
            "type": "user_input",
            "content": "old",
            "event_id": 1,
            "turn_index": 1,
            "branch_id": 1,
        },
        {
            "type": "user_input",
            "content": "child",
            "event_id": 2,
            "turn_index": 2,
            "branch_id": 1,
            "parent_branch_path": [[1, 1]],
        },
    ]
    _, cursor = extract_batch("alice", rows)
    rows.append(
        {
            "type": "user_input",
            "content": "new",
            "event_id": 4,
            "turn_index": 1,
            "branch_id": 2,
        }
    )
    with pytest.raises(RebuildRequired):
        extract_batch("alice", rows, cursor)
    assert [b.content for b in extract_batch("alice", rows)[0]] == ["new"]


def test_legacy_child_does_not_reactivate_incompatible_ancestry():
    rows = [
        {
            "type": "user_input",
            "content": "root",
            "event_id": 1,
            "turn_index": 1,
            "branch_id": 2,
        },
        {
            "type": "user_input",
            "content": "selected",
            "event_id": 2,
            "turn_index": 2,
            "branch_id": 1,
            "parent_branch_path": [[1, 2]],
        },
        {
            "type": "user_input",
            "content": "incompatible",
            "event_id": 3,
            "turn_index": 2,
            "branch_id": 3,
            "parent_branch_path": [[1, 1]],
        },
    ]
    _, cursor = extract_batch("alice", rows)
    rows.append(
        {
            "type": "user_input",
            "content": "legacy child",
            "event_id": 4,
            "turn_index": 3,
            "branch_id": 1,
        }
    )
    with pytest.raises(RebuildRequired):
        extract_batch("alice", rows, cursor)
    assert [b.content for b in extract_batch("alice", rows)[0]] == ["root", "selected"]


def test_late_earlier_turn_can_invalidate_a_previously_unconstrained_child():
    rows = [
        {
            "type": "user_input",
            "content": "child",
            "event_id": 9,
            "turn_index": 2,
            "branch_id": 1,
            "parent_branch_path": [[1, 1]],
        }
    ]
    _, cursor = extract_batch("alice", rows)
    rows.append(
        {
            "type": "user_input",
            "content": "parent",
            "event_id": 12,
            "turn_index": 1,
            "branch_id": 2,
        }
    )
    with pytest.raises(RebuildRequired):
        extract_batch("alice", rows, cursor)
    assert [b.content for b in extract_batch("alice", rows)[0]] == ["parent"]


class TestContentToText:
    """Regression coverage for the 500 on memory/search when an event's
    ``content`` is a multimodal list and a caller naively calls
    ``.strip()`` / ``.split()`` on it."""

    def test_string_passthrough(self):
        assert _content_to_text("hello") == "hello"

    def test_empty_string(self):
        assert _content_to_text("") == ""

    def test_none_returns_empty(self):
        assert _content_to_text(None) == ""

    def test_list_of_text_parts(self):
        parts = [
            {"type": "text", "text": "first"},
            {"type": "text", "text": "second"},
        ]
        assert _content_to_text(parts) == "first second"

    def test_list_with_image_part(self):
        parts = [
            {"type": "text", "text": "look at this"},
            {"type": "image_url", "image_url": {"url": "https://x"}},
        ]
        # Image is preserved as a placeholder, not dropped silently.
        assert _content_to_text(parts) == "look at this [image]"

    def test_list_with_file_part(self):
        parts = [{"type": "file", "name": "x.pdf"}]
        assert _content_to_text(parts) == "[file]"

    def test_bare_string_inside_list(self):
        # Some pre-multimodal sessions stored content as ["text"]
        assert _content_to_text(["raw"]) == "raw"

    def test_dict_input_wrapped(self):
        assert _content_to_text({"type": "text", "text": "x"}) == "x"

    def test_other_type_str_fallback(self):
        # Numbers shouldn't crash — get coerced to str.
        assert _content_to_text(42) == "42"


class TestExtractBlocksMultimodal:
    """Regression: ``user_input`` events with multimodal content
    (image attached to a message) used to take down ``index_events`` →
    memory.search with ``'list' object has no attribute 'strip'``.
    """

    def test_multimodal_user_input_is_indexed(self):
        events = [
            {
                "type": "user_input",
                "content": [
                    {"type": "text", "text": "what's in this picture"},
                    {"type": "image_url", "image_url": {"url": "data:..."}},
                ],
                "event_id": 1,
            }
        ]
        # Must not raise.
        blocks = _extract_blocks("alice", events)
        assert len(blocks) == 1
        assert blocks[0].block_type == "user"
        assert "what's in this picture" in blocks[0].content
        assert "[image]" in blocks[0].content

    def test_empty_multimodal_input_skipped(self):
        events = [
            {
                "type": "user_input",
                "content": [],
                "event_id": 1,
            }
        ]
        blocks = _extract_blocks("alice", events)
        # Empty list flattens to "" which strips to "" → skipped.
        assert blocks == []


class TestExtractBlocks:
    def test_user_input_starts_round(self):
        events = [
            {
                "type": "user_input",
                "content": "find the bug",
                "event_id": 1,
            }
        ]
        blocks = _extract_blocks("alice", events)
        assert len(blocks) == 1
        assert blocks[0].block_type == "user"
        assert blocks[0].round_num == 1

    def test_empty_user_input_skipped(self):
        events = [
            {"type": "user_input", "content": "   ", "event_id": 1},
        ]
        blocks = _extract_blocks("alice", events)
        assert blocks == []

    def test_trigger_fired_creates_round(self):
        events = [
            {
                "type": "trigger_fired",
                "channel": "ch1",
                "content": "ping",
                "event_id": 1,
            }
        ]
        blocks = _extract_blocks("alice", events)
        assert len(blocks) == 1
        assert blocks[0].block_type == "trigger"
        assert blocks[0].channel == "ch1"

    def test_text_only_indexed_inside_round(self):
        events = [
            # No user_input → in_round=False, text dropped.
            {"type": "text", "content": "orphan text", "event_id": 1},
        ]
        blocks = _extract_blocks("alice", events)
        assert blocks == []

    def test_text_chunk_indexed_inside_round(self):
        events = [
            {"type": "user_input", "content": "q", "event_id": 1},
            {"type": "text_chunk", "content": "first reply", "event_id": 2},
        ]
        blocks = _extract_blocks("alice", events)
        # 1 user + 1 text block.
        types = [b.block_type for b in blocks]
        assert "user" in types
        assert "text" in types

    def test_coalesced_segment_indexes_as_one_whole_block(self):
        # UXI-02: streamed text is now stored as ONE text_chunk per
        # segment, so a whole assistant reply embeds as a single block
        # instead of one fragment per streamed chunk (which fractured
        # embeddings across former chunk boundaries).
        events = [
            {"type": "user_input", "content": "q", "event_id": 1},
            {
                "type": "text_chunk",
                "content": "Hello, world! Here is the complete answer.",
                "event_id": 2,
            },
        ]
        blocks = _extract_blocks("alice", events)
        text_blocks = [b for b in blocks if b.block_type == "text"]
        assert len(text_blocks) == 1
        assert text_blocks[0].content == "Hello, world! Here is the complete answer."

    def test_long_text_splits_on_double_newline(self):
        long_text = "para1\n\n" + ("y" * 350) + "\n\n" + "para3"
        events = [
            {"type": "user_input", "content": "q", "event_id": 1},
            {"type": "text", "content": long_text, "event_id": 2},
        ]
        blocks = _extract_blocks("alice", events)
        text_blocks = [b for b in blocks if b.block_type == "text"]
        assert len(text_blocks) == 3

    def test_tool_call_indexed(self):
        events = [
            {"type": "user_input", "content": "q", "event_id": 1},
            {
                "type": "tool_call",
                "name": "bash",
                "args": {"cmd": "ls"},
                "event_id": 2,
            },
        ]
        blocks = _extract_blocks("alice", events)
        tool_blocks = [b for b in blocks if b.block_type == "tool"]
        assert len(tool_blocks) == 1
        assert tool_blocks[0].tool_name == "bash"
        assert "cmd=ls" in tool_blocks[0].content

    def test_tool_result_indexed(self):
        events = [
            {"type": "user_input", "content": "q", "event_id": 1},
            {
                "type": "tool_result",
                "name": "bash",
                "output": "this is the output of the tool call",
                "event_id": 2,
            },
        ]
        blocks = _extract_blocks("alice", events)
        tool_blocks = [b for b in blocks if b.block_type == "tool"]
        assert len(tool_blocks) == 1
        assert "output" in tool_blocks[0].content

    def test_short_tool_result_skipped(self):
        events = [
            {"type": "user_input", "content": "q", "event_id": 1},
            {
                "type": "tool_result",
                "name": "x",
                "output": "ok",
                "event_id": 2,
            },
        ]
        blocks = _extract_blocks("alice", events)
        # Tool result content "[result:x] ok" is <=20 chars → skipped.
        tool_blocks = [b for b in blocks if b.block_type == "tool"]
        assert tool_blocks == []

    def test_large_tool_result_indexed_beyond_old_2000_cap(self):
        # Elided tool results (≤256KB) must stay recoverable: the index cap
        # covers far more than the historical 2000-char truncation.

        big = (
            "needle-at-tail "
            + "z" * (TOOL_RESULT_INDEX_CHARS - 50)
            + " unique-tail-marker"
        )
        events = [
            {"type": "user_input", "content": "q", "event_id": 1},
            {
                "type": "tool_result",
                "name": "bash",
                "output": big,
                "event_id": 2,
            },
        ]
        blocks = _extract_blocks("alice", events)
        tool_blocks = [b for b in blocks if b.block_type == "tool"]
        assert len(tool_blocks) == 1
        assert "unique-tail-marker" in tool_blocks[0].content

    def test_processing_end_ends_round(self):
        events = [
            {"type": "user_input", "content": "q", "event_id": 1},
            {"type": "processing_end", "event_id": 2},
            # After processing_end, text events are no longer indexed.
            {"type": "text", "content": "post round", "event_id": 3},
        ]
        blocks = _extract_blocks("alice", events)
        types = [b.block_type for b in blocks]
        assert "text" not in types


# ── SearchResult ─────────────────────────────────────────────────
