"""Unit tests for :mod:`kohakuterrarium.core.conversation_elide`."""

from kohakuterrarium.core.conversation import Conversation
from kohakuterrarium.core.conversation_elide import (
    ELISION_MARKER,
    TOOL_FEEDBACK_KIND,
    elide_stale_tool_results,
    estimate_tokens,
    maybe_elide,
)

BIG = "R" * 2000


def _feedback(conv: Conversation, text: str):
    msg = conv.append("user", text)
    msg.metadata["kind"] = TOOL_FEEDBACK_KIND
    return msg


class TestElideStaleToolResults:
    def test_older_rounds_stub_latest_round_kept(self):
        conv = Conversation()
        conv.append("system", "sys")
        conv.append("user", "real question " + "q" * 400)
        conv.append("assistant", "calling tools")
        first = _feedback(conv, "[tool result] round1 " + BIG)
        conv.append("assistant", "more tools")
        second = _feedback(conv, "[tool result] round2 " + BIG)

        before = conv.get_context_length()
        assert elide_stale_tool_results(conv) == 1
        after = conv.get_context_length()

        assert ELISION_MARKER in first.content
        assert first.content.startswith("[tool result] round1")
        assert "search_memory" in first.content
        assert first.metadata["tool_results_elided"] is True
        assert second.content.endswith(BIG)
        # A genuine user message is never elided, whatever its size.
        assert conv.get_messages()[1].content.endswith("q" * 400)
        assert after < before
        assert conv._metadata.total_chars == after

    def test_all_results_stale_once_the_turn_ended(self):
        conv = Conversation()
        conv.append("system", "sys")
        last = _feedback(conv, "final round " + BIG)
        conv.append("assistant", "answer")
        assert elide_stale_tool_results(conv) == 1
        assert ELISION_MARKER in last.content

    def test_native_tool_messages_preserve_tool_call_id(self):
        conv = Conversation()
        conv.append("system", "sys")
        calls = [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "grep", "arguments": "{}"},
            }
        ]
        conv.append("assistant", "dispatch", tool_calls=calls)
        old = conv.append("tool", "old native " + BIG, tool_call_id="call_1")
        conv.append(
            "assistant",
            "again",
            tool_calls=[{**calls[0], "id": "call_2"}],
        )
        latest = conv.append("tool", "new native " + BIG, tool_call_id="call_2")

        assert elide_stale_tool_results(conv) == 1
        assert ELISION_MARKER in old.content
        assert old.tool_call_id == "call_1"
        assert old.role == "tool"
        assert latest.content.endswith(BIG)

    def test_small_results_and_repeat_calls_are_noops(self):
        conv = Conversation()
        conv.append("system", "sys")
        small = _feedback(conv, "tiny result")
        big = _feedback(conv, "big " + BIG)
        conv.append("assistant", "answer")

        assert elide_stale_tool_results(conv) == 1
        stubbed = big.content
        assert small.content == "tiny result"
        # Second pass finds nothing new and rewrites nothing.
        assert elide_stale_tool_results(conv) == 0
        assert big.content == stubbed

    def test_stub_names_the_originating_tool_call(self):
        conv = Conversation()
        conv.append("system", "sys")
        calls = [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "read", "arguments": '{"path": "big.py"}'},
            }
        ]
        conv.append("assistant", "dispatch", tool_calls=calls)
        old = conv.append("tool", "file content " + BIG, tool_call_id="call_1")
        conv.append("assistant", "again", tool_calls=[{**calls[0], "id": "call_2"}])
        latest = conv.append("tool", "new content " + BIG, tool_call_id="call_2")

        assert elide_stale_tool_results(conv) == 1
        assert 'read({"path": "big.py"})' in old.content
        assert "from read" in old.content
        assert latest.content.endswith(BIG)

    def test_stub_without_call_id_falls_back_to_generic(self):
        conv = Conversation()
        conv.append("system", "sys")
        first = _feedback(conv, "[tool result] round1 " + BIG)
        conv.append("assistant", "more tools")
        _feedback(conv, "[tool result] round2 " + BIG)

        assert elide_stale_tool_results(conv) == 1
        assert "from a tool" in first.content


class TestMaybeElide:
    def test_skips_when_below_threshold(self):
        conv = Conversation()
        conv.append("system", "sys")
        _feedback(conv, "[tool result] small " + "x" * 100)
        conv.append("assistant", "ok")

        assert (
            maybe_elide(conv, 10_000, threshold_ratio=0.6, elide_max_tokens=256_000)
            == 0
        )
        assert "x" * 100 in conv.get_messages()[1].content

    def test_elides_when_crowded(self):
        conv = Conversation()
        conv.append("system", "sys")
        first = _feedback(conv, "[tool result] round1 " + BIG)
        conv.append("assistant", "more tools")
        _feedback(conv, "[tool result] round2 " + BIG)

        assert (
            maybe_elide(conv, 200_000, threshold_ratio=0.6, elide_max_tokens=256_000)
            == 1
        )
        assert ELISION_MARKER in first.content

    def test_skips_when_no_usage_yet(self):
        conv = Conversation()
        conv.append("system", "sys")
        _feedback(conv, "[tool result] round1 " + BIG)

        # Mirrors the resumed controller before the first real LLM call:
        # an empty/zero usage must not elide anything by itself.
        assert maybe_elide(conv, 0, threshold_ratio=0.6, elide_max_tokens=256_000) == 0

    def test_idempotent_across_repeated_calls(self):
        conv = Conversation()
        conv.append("system", "sys")
        first = _feedback(conv, "[tool result] round1 " + BIG)
        conv.append("assistant", "more tools")
        _feedback(conv, "[tool result] round2 " + BIG)

        # Reload paths may run before the per-turn check; the second call
        # must not re-stub already-elided messages.
        assert (
            maybe_elide(conv, 200_000, threshold_ratio=0.6, elide_max_tokens=256_000)
            == 1
        )
        assert (
            maybe_elide(conv, 200_000, threshold_ratio=0.6, elide_max_tokens=256_000)
            == 0
        )
        assert ELISION_MARKER in first.content


class TestEstimateTokens:
    def test_scales_with_content_length(self):
        conv = Conversation()
        conv.append("system", "sys")
        conv.append("user", "x" * 1000)
        small = estimate_tokens(conv)
        conv.append("user", "x" * 100_000)
        large = estimate_tokens(conv)
        assert small > 0
        assert large > small
        # Conservative: ~3.5 chars/token → 100KB content is at least ~20K tokens.
        assert large >= 20_000

    def test_counts_tool_call_arguments(self):
        conv = Conversation()
        conv.append("system", "sys")
        conv.append(
            "assistant",
            "",
            tool_calls=[
                {
                    "id": "c1",
                    "function": {
                        "name": "read",
                        "arguments": '{"path": "' + "y" * 500 + '"}',
                    },
                }
            ],
        )
        assert estimate_tokens(conv) >= 100
