"""fix_anthropic_tool_block_pairing: every tool_use ends up with exactly one
tool_result in the immediately following user message."""

import collections

from kohakuterrarium.llm.anthropic_pairing import (
    SYNTHETIC_TOOL_RESULT_TEXT,
    fix_anthropic_tool_block_pairing,
)


def _use(tid, name="bash"):
    return {
        "role": "assistant",
        "content": [{"type": "tool_use", "id": tid, "name": name, "input": {}}],
    }


def _result(tid, text):
    return {"type": "tool_result", "tool_use_id": tid, "content": text}


def _result_ids(messages):
    return [
        block["tool_use_id"]
        for msg in messages
        if msg.get("role") == "user" and isinstance(msg.get("content"), list)
        for block in msg["content"]
        if isinstance(block, dict) and block.get("type") == "tool_result"
    ]


class TestDuplicateResults:
    def test_repeated_results_collapse_to_one_beside_the_tool_use(self):
        messages = [
            {"role": "user", "content": "start"},
            _use("call_x"),
            {"role": "user", "content": [_result("call_x", "out")]},
            {"role": "assistant", "content": "later turn"},
            {"role": "user", "content": [_result("call_x", "out")]},
            {"role": "assistant", "content": "another turn"},
            {
                "role": "user",
                "content": [_result("call_x", "out"), {"type": "text", "text": "hi"}],
            },
        ]

        fixed = fix_anthropic_tool_block_pairing(messages)

        assert collections.Counter(_result_ids(fixed)) == {"call_x": 1}
        use_at = next(
            i
            for i, m in enumerate(fixed)
            if m["role"] == "assistant" and isinstance(m["content"], list)
        )
        assert _result_ids([fixed[use_at + 1]]) == ["call_x"]
        assert {"type": "text", "text": "hi"} in fixed[-1]["content"]

    def test_user_message_left_empty_by_duplicate_removal_is_dropped(self):
        messages = [
            _use("call_y"),
            {"role": "user", "content": [_result("call_y", "first")]},
            {"role": "assistant", "content": "turn"},
            {"role": "user", "content": [_result("call_y", "again")]},
        ]

        fixed = fix_anthropic_tool_block_pairing(messages)

        assert collections.Counter(_result_ids(fixed)) == {"call_y": 1}
        assert all(m.get("content") != [] for m in fixed)

    def test_each_of_several_uses_keeps_exactly_one_result(self):
        messages = [
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "a", "name": "read", "input": {}},
                    {"type": "tool_use", "id": "b", "name": "grep", "input": {}},
                ],
            },
            {"role": "user", "content": [_result("a", "A"), _result("b", "B")]},
            {"role": "assistant", "content": "turn"},
            {"role": "user", "content": [_result("b", "B"), _result("a", "A")]},
        ]

        fixed = fix_anthropic_tool_block_pairing(messages)

        assert collections.Counter(_result_ids(fixed)) == {"a": 1, "b": 1}


class TestDuplicateToolUses:
    def test_repeated_tool_use_is_dropped_and_not_paired_twice(self):
        messages = [
            _use("dup"),
            {"role": "user", "content": [_result("dup", "out")]},
            {"role": "assistant", "content": "middle"},
            _use("dup"),
            {"role": "user", "content": [_result("dup", "out")]},
            {"role": "user", "content": "next question"},
        ]

        fixed = fix_anthropic_tool_block_pairing(messages)

        use_ids = [
            b["id"]
            for m in fixed
            if m["role"] == "assistant" and isinstance(m["content"], list)
            for b in m["content"]
            if b.get("type") == "tool_use"
        ]
        assert use_ids == ["dup"]
        assert collections.Counter(_result_ids(fixed)) == {"dup": 1}
        assert fixed[-1] == {"role": "user", "content": "next question"}

    def test_repeat_keeps_other_blocks_of_the_assistant_message(self):
        messages = [
            _use("dup"),
            {"role": "user", "content": [_result("dup", "out")]},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "thinking aloud"},
                    {"type": "tool_use", "id": "dup", "name": "bash", "input": {}},
                    {"type": "tool_use", "id": "fresh", "name": "read", "input": {}},
                ],
            },
            {
                "role": "user",
                "content": [_result("dup", "out"), _result("fresh", "new")],
            },
        ]

        fixed = fix_anthropic_tool_block_pairing(messages)

        repeat = fixed[2]
        assert [b.get("id") or b.get("text") for b in repeat["content"]] == [
            "thinking aloud",
            "fresh",
        ]
        assert _result_ids([fixed[3]]) == ["fresh"]
        assert collections.Counter(_result_ids(fixed)) == {"dup": 1, "fresh": 1}

    def test_same_tool_use_twice_in_one_message_is_paired_once(self):
        messages = [
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "twin", "name": "bash", "input": {}},
                    {"type": "tool_use", "id": "twin", "name": "bash", "input": {}},
                ],
            },
            {"role": "user", "content": [_result("twin", "out")]},
        ]

        fixed = fix_anthropic_tool_block_pairing(messages)

        assert [b["id"] for b in fixed[0]["content"]] == ["twin"]
        assert _result_ids(fixed) == ["twin"]


class TestExistingPairingBehaviour:
    def test_matching_result_kept_once(self):
        messages = [_use("c1"), {"role": "user", "content": [_result("c1", "ok")]}]

        fixed = fix_anthropic_tool_block_pairing(messages)

        assert _result_ids(fixed) == ["c1"]
        assert fixed[1]["content"][0]["content"] == "ok"

    def test_missing_result_is_synthesised(self):
        fixed = fix_anthropic_tool_block_pairing([_use("c2")])

        assert _result_ids(fixed) == ["c2"]
        assert SYNTHETIC_TOOL_RESULT_TEXT in fixed[1]["content"][0]["content"]

    def test_orphan_result_before_any_use_is_dropped(self):
        messages = [
            {"role": "user", "content": [_result("ghost", "x")]},
            {"role": "user", "content": "hello"},
        ]

        fixed = fix_anthropic_tool_block_pairing(messages)

        assert _result_ids(fixed) == []
        assert fixed == [{"role": "user", "content": "hello"}]
