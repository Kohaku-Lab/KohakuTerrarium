"""Unit tests for ``scripts/inspect_session.py`` reasoning helpers."""

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "scripts" / "inspect_session.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("inspect_session", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_inspect_session = _load_script()


class TestBoundedText:
    def test_short_text_unchanged(self):
        assert _inspect_session._bounded_text("short", 10) == "short"

    def test_long_text_gets_marker(self):
        out = _inspect_session._bounded_text("abcdef", 3)
        assert out.startswith("abc")
        assert "3 more chars" in out


class TestReasoningEntries:
    def test_flat_fields(self):
        out = _inspect_session._reasoning_entries(
            {
                "role": "assistant",
                "content": "answer",
                "reasoning_content": "private",
                "reasoning": "plain",
            }
        )
        assert out == [
            ("reasoning_content", "private"),
            ("reasoning", "plain"),
        ]

    def test_reasoning_details_with_signature(self):
        out = _inspect_session._reasoning_entries(
            {
                "role": "assistant",
                "reasoning_details": [
                    {"type": "reasoning.text", "text": "think", "signature": "sig"}
                ],
            }
        )
        assert out == [
            ("reasoning_details[0]:reasoning.text", "think\n[signature: sig]")
        ]

    def test_anthropic_thinking_blocks(self):
        out = _inspect_session._reasoning_entries(
            {
                "role": "assistant",
                "_kt_anthropic_content": [
                    {"type": "text", "text": "answer"},
                    {"type": "thinking", "thinking": "hmm", "signature": "sig2"},
                ],
            }
        )
        assert out == [("anthropic:thinking[1]", "hmm\n[signature: sig2]")]

    def test_ordered_segments_preferred_over_flat_fields(self):
        out = _inspect_session._reasoning_entries(
            {
                "role": "assistant",
                "reasoning_content": "flat",
                "_kt_assistant_segments": [
                    {
                        "type": "reasoning",
                        "source": "reasoning_content",
                        "text": "think 1",
                    },
                    {"type": "text", "text": "answer 1"},
                    {"type": "tool_call_ref", "call_id": "call_1"},
                ],
            }
        )
        assert out == [
            ("segments[0] reasoning:reasoning_content", "think 1"),
            ("segments[1] text", "answer 1"),
            ("segments[2] tool_call_ref", "call_1"),
        ]

    def test_non_assistant_ignored_by_print_path(self):
        assert _inspect_session._reasoning_entries({"role": "user"}) == []
