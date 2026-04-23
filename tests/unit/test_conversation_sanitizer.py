"""Tests for :meth:`Conversation.sanitize_orphan_tool_pairs`.

The sanitiser is the pre-LLM guard that strips orphan tool_call /
tool-result pairs produced by compaction before they reach the
provider. Most OpenAI-compatible providers reject a ``role=tool``
message that has no matching ``assistant.tool_calls`` entry, and
vice-versa — see ``plans/harness/proposal.md §5.16``.

These tests cover:

* Pass-through on a clean conversation.
* Partial orphan (some tool_call ids missing) → the matched ones stay,
  the unmatched ones are dropped from ``tool_calls``.
* Full orphan with no content → assistant message dropped wholesale.
* Full orphan with content → assistant kept (no empty ``tool_calls``).
* Orphan tool-result (no matching preceding assistant) dropped.
* Interleaved assistants — each pair survives or gets dropped
  independently.
* Idempotence: running the sanitiser twice is a no-op on the output.
* Opt-out via ``ConversationConfig.sanitize_orphan_tool_calls = False``.
"""

from kohakuterrarium.core.conversation import Conversation, ConversationConfig


def _assistant_with_calls(call_ids: list[str], content: str = "") -> dict:
    return {
        "role": "assistant",
        "content": content,
        "tool_calls": [
            {
                "id": cid,
                "type": "function",
                "function": {"name": "demo", "arguments": "{}"},
            }
            for cid in call_ids
        ],
    }


def _tool_result(call_id: str, content: str = "ok") -> dict:
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "content": content,
    }


def test_clean_conversation_passes_through_unchanged():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        _assistant_with_calls(["A"], content="thinking"),
        _tool_result("A", "result-a"),
        {"role": "assistant", "content": "done"},
    ]
    out = Conversation.sanitize_orphan_tool_pairs(messages)
    assert out == messages


def test_partial_orphan_assistant_tool_calls_drops_unmatched():
    # Assistant announces A and B, but only A has a result.
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        _assistant_with_calls(["A", "B"], content=""),
        _tool_result("A"),
        {"role": "user", "content": "thanks"},
    ]
    out = Conversation.sanitize_orphan_tool_pairs(messages)

    # Assistant kept, but tool_calls trimmed to just A.
    assistants = [m for m in out if m.get("role") == "assistant"]
    assert len(assistants) == 1
    assert [tc["id"] for tc in assistants[0]["tool_calls"]] == ["A"]
    # Tool result for A is still present; none for B remains.
    tool_ids = [m.get("tool_call_id") for m in out if m.get("role") == "tool"]
    assert tool_ids == ["A"]


def test_fully_orphan_assistant_without_content_is_dropped():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        _assistant_with_calls(["A"], content=""),
        # no tool result
        {"role": "user", "content": "next"},
    ]
    out = Conversation.sanitize_orphan_tool_pairs(messages)
    assert all(m.get("role") != "assistant" for m in out)
    assert [m["role"] for m in out] == ["system", "user", "user"]


def test_fully_orphan_assistant_with_content_is_kept_without_tool_calls():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        _assistant_with_calls(["A"], content="here is a long reply"),
        {"role": "user", "content": "next"},
    ]
    out = Conversation.sanitize_orphan_tool_pairs(messages)
    assistants = [m for m in out if m.get("role") == "assistant"]
    assert len(assistants) == 1
    # tool_calls key removed since all entries orphaned
    assert "tool_calls" not in assistants[0]
    assert assistants[0]["content"] == "here is a long reply"


def test_orphan_tool_result_is_dropped():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        # no assistant with tool_calls preceding this tool result
        _tool_result("ghost-id", "stale"),
        {"role": "assistant", "content": "done"},
    ]
    out = Conversation.sanitize_orphan_tool_pairs(messages)
    assert all(m.get("role") != "tool" for m in out)
    assert [m["role"] for m in out] == ["system", "user", "assistant"]


def test_interleaved_assistants_partial_survival():
    # Two assistants each with two tool_calls. First pair: A matched,
    # B orphan. Second pair: C matched, D matched.
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        _assistant_with_calls(["A", "B"], content=""),
        _tool_result("A"),
        # B never gets a result; next assistant begins a new turn.
        _assistant_with_calls(["C", "D"], content="middle"),
        _tool_result("C"),
        _tool_result("D"),
        {"role": "user", "content": "more"},
    ]
    out = Conversation.sanitize_orphan_tool_pairs(messages)
    assistants = [m for m in out if m.get("role") == "assistant"]
    assert len(assistants) == 2
    assert [tc["id"] for tc in assistants[0]["tool_calls"]] == ["A"]
    assert [tc["id"] for tc in assistants[1]["tool_calls"]] == ["C", "D"]
    tool_ids = [m["tool_call_id"] for m in out if m.get("role") == "tool"]
    assert tool_ids == ["A", "C", "D"]


def test_sanitizer_is_idempotent():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        _assistant_with_calls(["A", "B"], content=""),
        _tool_result("A"),
        _tool_result("stray-id"),
        _assistant_with_calls(["C"], content="ok"),
        {"role": "user", "content": "again"},
    ]
    once = Conversation.sanitize_orphan_tool_pairs(messages)
    twice = Conversation.sanitize_orphan_tool_pairs(once)
    assert once == twice


def test_sanitizer_disabled_passes_messages_through():
    config = ConversationConfig(sanitize_orphan_tool_calls=False)
    conv = Conversation(config)
    # Populate via append_message so the Conversation owns the state.
    conv.append("system", "sys")
    conv.append("user", "hi")
    conv.append(
        "assistant",
        "",
        tool_calls=[
            {
                "id": "A",
                "type": "function",
                "function": {"name": "demo", "arguments": "{}"},
            },
            {
                "id": "B",
                "type": "function",
                "function": {"name": "demo", "arguments": "{}"},
            },
        ],
    )
    conv.append("tool", "result-a", tool_call_id="A")
    # No result for B. With sanitizer disabled the orphan pair survives.
    out = conv.to_messages()
    assistant = next(m for m in out if m["role"] == "assistant")
    assert [tc["id"] for tc in assistant["tool_calls"]] == ["A", "B"]


def test_to_messages_default_applies_sanitizer():
    conv = Conversation()
    conv.append("system", "sys")
    conv.append("user", "hi")
    conv.append(
        "assistant",
        "",
        tool_calls=[
            {
                "id": "A",
                "type": "function",
                "function": {"name": "demo", "arguments": "{}"},
            },
            {
                "id": "B",
                "type": "function",
                "function": {"name": "demo", "arguments": "{}"},
            },
        ],
    )
    conv.append("tool", "result-a", tool_call_id="A")
    out = conv.to_messages()
    assistant = next(m for m in out if m["role"] == "assistant")
    # Only A survived since B had no matching tool result.
    assert [tc["id"] for tc in assistant["tool_calls"]] == ["A"]


def test_sanitizer_handles_assistant_with_list_content():
    # Multimodal content list — sanitizer should treat non-empty list
    # as meaningful content and keep the assistant message even when
    # tool_calls are all orphaned.
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "analysed"}],
            "tool_calls": [
                {
                    "id": "A",
                    "type": "function",
                    "function": {"name": "demo", "arguments": "{}"},
                }
            ],
        },
        {"role": "user", "content": "next"},
    ]
    out = Conversation.sanitize_orphan_tool_pairs(messages)
    assistants = [m for m in out if m.get("role") == "assistant"]
    assert len(assistants) == 1
    assert "tool_calls" not in assistants[0]
    assert assistants[0]["content"] == [{"type": "text", "text": "analysed"}]
