"""Wave C — unit tests for ``session.history.replay_conversation``.

Every event-type branch is exercised plus the collapsing / compaction
rules described in the spec (§2.1).
"""

from kohakuterrarium.session.history import replay_conversation


class TestUserMessage:
    def test_plain_text_user_message(self):
        events = [
            {"type": "user_message", "content": "hello", "event_id": 1},
        ]
        msgs = replay_conversation(events)
        assert msgs == [{"role": "user", "content": "hello"}]

    def test_multimodal_content_is_preserved_as_list(self):
        parts = [
            {"type": "text", "text": "caption"},
            {"type": "image_url", "image_url": {"url": "data:foo"}},
        ]
        events = [{"type": "user_message", "content": parts, "event_id": 1}]
        msgs = replay_conversation(events)
        assert msgs[0]["role"] == "user"
        assert msgs[0]["content"] == parts


class TestTextChunkCollapse:
    def test_consecutive_chunks_merge_into_one_assistant_message(self):
        events = [
            {"type": "text_chunk", "content": "Hello ", "chunk_seq": 0, "event_id": 1},
            {"type": "text_chunk", "content": "world", "chunk_seq": 1, "event_id": 2},
            {"type": "text_chunk", "content": "!", "chunk_seq": 2, "event_id": 3},
        ]
        msgs = replay_conversation(events)
        assert msgs == [{"role": "assistant", "content": "Hello world!"}]

    def test_empty_chunks_produce_no_message(self):
        events = [
            {"type": "text_chunk", "content": "", "chunk_seq": 0, "event_id": 1},
            {"type": "text_chunk", "content": "", "chunk_seq": 1, "event_id": 2},
        ]
        msgs = replay_conversation(events)
        assert msgs == []

    def test_interleaved_user_message_breaks_run(self):
        events = [
            {"type": "text_chunk", "content": "first", "chunk_seq": 0, "event_id": 1},
            {"type": "user_message", "content": "hi again", "event_id": 2},
            {"type": "text_chunk", "content": "second", "chunk_seq": 0, "event_id": 3},
        ]
        msgs = replay_conversation(events)
        assert msgs == [
            {"role": "assistant", "content": "first"},
            {"role": "user", "content": "hi again"},
            {"role": "assistant", "content": "second"},
        ]


class TestAssistantToolCalls:
    def test_attaches_tool_calls_to_preceding_assistant(self):
        tool_calls = [
            {
                "id": "tc1",
                "type": "function",
                "function": {"name": "bash", "arguments": "{}"},
            }
        ]
        events = [
            {"type": "text_chunk", "content": "running", "chunk_seq": 0, "event_id": 1},
            {"type": "assistant_tool_calls", "tool_calls": tool_calls, "event_id": 2},
        ]
        msgs = replay_conversation(events)
        assert len(msgs) == 1
        assert msgs[0]["role"] == "assistant"
        assert msgs[0]["content"] == "running"
        assert msgs[0]["tool_calls"] == tool_calls

    def test_standalone_tool_calls_emit_new_assistant_message(self):
        tool_calls = [
            {
                "id": "tc1",
                "type": "function",
                "function": {"name": "bash", "arguments": "{}"},
            }
        ]
        events = [
            {"type": "assistant_tool_calls", "tool_calls": tool_calls, "event_id": 1},
        ]
        msgs = replay_conversation(events)
        assert len(msgs) == 1
        assert msgs[0]["role"] == "assistant"
        assert msgs[0]["tool_calls"] == tool_calls


class TestToolResult:
    def test_tool_result_becomes_tool_role_message(self):
        events = [
            {
                "type": "tool_result",
                "name": "bash",
                "call_id": "tc1",
                "output": "ok",
                "event_id": 1,
            },
        ]
        msgs = replay_conversation(events)
        assert msgs == [
            {"role": "tool", "content": "ok", "tool_call_id": "tc1", "name": "bash"}
        ]


class TestSystemPromptSet:
    def test_emits_system_role_message(self):
        events = [
            {
                "type": "system_prompt_set",
                "content": "You are friendly.",
                "event_id": 1,
            },
        ]
        msgs = replay_conversation(events)
        assert msgs == [{"role": "system", "content": "You are friendly."}]


class TestCompactReplace:
    def test_compact_replace_swaps_range(self):
        events = [
            {"type": "user_message", "content": "old 1", "event_id": 1},
            {"type": "text_chunk", "content": "old ", "chunk_seq": 0, "event_id": 2},
            {"type": "text_chunk", "content": "reply", "chunk_seq": 1, "event_id": 3},
            {
                "type": "compact_replace",
                "replaced_from_event_id": 1,
                "replaced_to_event_id": 3,
                "summary_text": "[summary]",
                "tokens_saved": 42,
                "event_id": 4,
            },
            {"type": "user_message", "content": "new turn", "event_id": 5},
        ]
        msgs = replay_conversation(events)
        assert msgs == [
            {"role": "assistant", "content": "[summary]"},
            {"role": "user", "content": "new turn"},
        ]

    def test_compact_replace_without_range_is_noop_on_others(self):
        events = [
            {"type": "user_message", "content": "hello", "event_id": 1},
            {
                "type": "compact_replace",
                "replaced_from_event_id": 99,
                "replaced_to_event_id": 100,
                "summary_text": "unused",
                "event_id": 2,
            },
        ]
        msgs = replay_conversation(events)
        # event_id 1 falls outside [99, 100] so it survives; the
        # summary still emits.
        assert msgs == [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "unused"},
        ]


class TestUnknownEventsIgnored:
    def test_unknown_event_types_dropped(self):
        events = [
            {"type": "turn_token_usage", "event_id": 1, "prompt_tokens": 10},
            {"type": "scratchpad_write", "event_id": 2, "key": "plan"},
            {"type": "user_message", "content": "hi", "event_id": 3},
        ]
        msgs = replay_conversation(events)
        assert msgs == [{"role": "user", "content": "hi"}]
