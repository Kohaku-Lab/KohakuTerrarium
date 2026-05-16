"""Unit tests for ``llm/codex_format.py`` — Responses API message shapes.

Behavior-first: assert the exact Responses-API ``input`` items produced
from Chat Completions messages, the function_call / function_call_output
pairing repair, multimodal tool-output array form, and artifact-URL
resolution to data URLs (with on-disk fixtures).
"""

import base64

from kohakuterrarium.llm import codex_format
from kohakuterrarium.llm.codex_format import (
    _resolve_artifact_url,
    fix_tool_call_pairing,
    maybe_capture_stream_rate_limit,
    to_responses_input,
)
from kohakuterrarium.llm.codex_rate_limits import (
    UsageSnapshot,
    parse_rate_limit_event,
)


class TestToResponsesInput:
    def test_string_user_message_becomes_input_text(self):
        out = to_responses_input([{"role": "user", "content": "hello"}])
        assert out == [
            {"role": "user", "content": [{"type": "input_text", "text": "hello"}]}
        ]

    def test_multimodal_user_message_keeps_text_and_image(self):
        out = to_responses_input(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "look"},
                        {"type": "image_url", "image_url": {"url": "https://x/y.png"}},
                    ],
                }
            ]
        )
        assert out[0]["content"] == [
            {"type": "input_text", "text": "look"},
            {"type": "input_image", "image_url": "https://x/y.png"},
        ]

    def test_empty_user_content_dropped(self):
        out = to_responses_input([{"role": "user", "content": []}])
        assert out == []

    def test_assistant_text_becomes_output_text_item(self):
        out = to_responses_input([{"role": "assistant", "content": "answer"}])
        assert out == [
            {
                "role": "assistant",
                "content": [{"type": "output_text", "text": "answer"}],
            }
        ]

    def test_assistant_tool_calls_become_function_call_items(self):
        out = to_responses_input(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "c1",
                            "function": {"name": "bash", "arguments": '{"cmd": "ls"}'},
                        }
                    ],
                }
            ]
        )
        assert out == [
            {
                "type": "function_call",
                "call_id": "c1",
                "name": "bash",
                "arguments": '{"cmd": "ls"}',
            }
        ]

    def test_tool_message_becomes_function_call_output(self):
        out = to_responses_input(
            [{"role": "tool", "tool_call_id": "c1", "content": "result text"}]
        )
        assert out == [
            {
                "type": "function_call_output",
                "call_id": "c1",
                "output": "result text",
            }
        ]

    def test_multimodal_tool_result_uses_array_output_form(self):
        out = to_responses_input(
            [
                {
                    "role": "tool",
                    "tool_call_id": "c1",
                    "content": [
                        {"type": "text", "text": "see image"},
                        {"type": "image_url", "image_url": {"url": "https://x/i.png"}},
                    ],
                }
            ]
        )
        assert out[0]["output"] == [
            {"type": "input_text", "text": "see image"},
            {"type": "input_image", "image_url": "https://x/i.png"},
        ]

    def test_text_only_list_tool_result_uses_string_output_form(self):
        out = to_responses_input(
            [
                {
                    "role": "tool",
                    "tool_call_id": "c1",
                    "content": [{"type": "text", "text": "just text"}],
                }
            ]
        )
        # no image part → falls back to the simple string form
        assert out[0]["output"] == "just text"


class TestFixToolCallPairing:
    def test_function_call_followed_by_its_output(self):
        api_input = [
            {"type": "function_call", "call_id": "c1", "name": "bash"},
            {"type": "function_call_output", "call_id": "c1", "output": "ok"},
        ]
        out = fix_tool_call_pairing(api_input)
        assert out == api_input

    def test_missing_output_synthesised_after_call(self):
        api_input = [{"type": "function_call", "call_id": "c1", "name": "bash"}]
        out = fix_tool_call_pairing(api_input)
        assert len(out) == 2
        assert out[1]["type"] == "function_call_output"
        assert out[1]["call_id"] == "c1"
        assert "removed by context compaction" in out[1]["output"]

    def test_orphan_output_without_call_is_dropped(self):
        api_input = [
            {"type": "function_call_output", "call_id": "ghost", "output": "x"},
            {"role": "user", "content": [{"type": "input_text", "text": "hi"}]},
        ]
        out = fix_tool_call_pairing(api_input)
        # orphan output removed, the user message survives
        assert out == [
            {"role": "user", "content": [{"type": "input_text", "text": "hi"}]}
        ]

    def test_output_moved_to_immediately_follow_its_call(self):
        api_input = [
            {"type": "function_call", "call_id": "c1", "name": "bash"},
            {"role": "user", "content": [{"type": "input_text", "text": "noise"}]},
            {"type": "function_call_output", "call_id": "c1", "output": "ok"},
        ]
        out = fix_tool_call_pairing(api_input)
        # the output is repositioned right after its function_call
        assert out[0]["type"] == "function_call"
        assert out[1] == {
            "type": "function_call_output",
            "call_id": "c1",
            "output": "ok",
        }
        assert out[2]["role"] == "user"


class TestResolveArtifactUrl:
    def test_non_artifact_url_passed_through(self):
        assert _resolve_artifact_url("https://example.com/x.png") == (
            "https://example.com/x.png"
        )
        assert _resolve_artifact_url("data:image/png;base64,QUJD") == (
            "data:image/png;base64,QUJD"
        )

    def test_non_string_input_passed_through(self):
        assert _resolve_artifact_url(None) is None

    def test_malformed_artifact_path_passed_through(self):
        # starts with /api/sessions/ but doesn't match the full pattern
        assert _resolve_artifact_url("/api/sessions/onlysid") == "/api/sessions/onlysid"

    def test_artifact_resolved_to_data_url(self, tmp_path, monkeypatch):
        # lay down a real artifact file the resolver can read.
        # layout: <session_dir>/<session_name>.artifacts/<rel>
        session_dir = tmp_path / "sessions"
        artifacts = session_dir / "sid123.artifacts"
        artifacts.mkdir(parents=True)
        (artifacts / "pic.png").write_bytes(b"PNGDATA")
        monkeypatch.setattr(codex_format, "_session_dir", lambda: session_dir)

        out = _resolve_artifact_url("/api/sessions/sid123/artifacts/pic.png")
        assert out.startswith("data:image/png;base64,")
        # base64 of b"PNGDATA"
        assert out == "data:image/png;base64," + base64.b64encode(b"PNGDATA").decode()

    def test_missing_artifact_file_falls_back_to_original_url(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(codex_format, "_session_dir", lambda: tmp_path)
        url = "/api/sessions/sid/artifacts/nope.png"
        # file does not exist → resolver swallows the error and returns input
        assert _resolve_artifact_url(url) == url


class _Event:
    """Minimal stand-in for a Codex SDK stream event."""

    def __init__(self, data=None):
        self.data = data


class TestMaybeCaptureStreamRateLimit:
    def test_rate_limit_event_captured_into_cache(self):
        captured = []
        payload = {
            "type": "codex.rate_limits",
            "rate_limits": {"primary": {"used_percent": 33.0, "window_minutes": 300}},
        }
        maybe_capture_stream_rate_limit(
            _Event(data=payload),
            parse_rate_limit_event,
            UsageSnapshot,
            captured.append,
        )
        # a real rate-limit event flows through to set_cached
        assert len(captured) == 1
        assert captured[0].snapshots[0].primary.used_percent == 33.0

    def test_non_rate_limit_event_ignored(self):
        captured = []
        maybe_capture_stream_rate_limit(
            _Event(data={"type": "response.delta"}),
            parse_rate_limit_event,
            UsageSnapshot,
            captured.append,
        )
        assert captured == []

    def test_event_with_no_payload_ignored(self):
        captured = []
        maybe_capture_stream_rate_limit(
            _Event(data=None),
            parse_rate_limit_event,
            UsageSnapshot,
            captured.append,
        )
        assert captured == []

    def test_payload_with_model_dump_used(self):
        captured = []

        class _Payload:
            def model_dump(self):
                return {
                    "type": "codex.rate_limits",
                    "rate_limits": {"primary": {"used_percent": 10.0}},
                }

        maybe_capture_stream_rate_limit(
            _Event(data=_Payload()),
            parse_rate_limit_event,
            UsageSnapshot,
            captured.append,
        )
        assert len(captured) == 1


class TestUserItemEdgeCases:
    def test_non_list_non_string_user_content_returns_none(self):
        assert to_responses_input([{"role": "user", "content": 42}]) == []

    def test_non_dict_parts_skipped_in_user_content(self):
        out = to_responses_input(
            [{"role": "user", "content": ["raw", {"type": "text", "text": "kept"}]}]
        )
        assert out[0]["content"] == [{"type": "input_text", "text": "kept"}]

    def test_image_url_with_empty_url_skipped(self):
        out = to_responses_input(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "t"},
                        {"type": "image_url", "image_url": {}},
                    ],
                }
            ]
        )
        # the empty image is dropped, the text survives
        assert out[0]["content"] == [{"type": "input_text", "text": "t"}]

    def test_image_url_as_plain_string_resolved(self):
        out = to_responses_input(
            [
                {
                    "role": "user",
                    "content": [{"type": "image_url", "image_url": "https://x/i.png"}],
                }
            ]
        )
        assert out[0]["content"][0] == {
            "type": "input_image",
            "image_url": "https://x/i.png",
        }

    def test_assistant_with_text_and_tool_calls_emits_both_items(self):
        out = to_responses_input(
            [
                {
                    "role": "assistant",
                    "content": "let me run that",
                    "tool_calls": [
                        {"id": "c1", "function": {"name": "bash", "arguments": "{}"}}
                    ],
                }
            ]
        )
        assert out[0]["content"][0]["type"] == "output_text"
        assert out[1]["type"] == "function_call"

    def test_image_only_tool_result_summarised_when_no_text(self):
        # an image-only assistant turn falls back to the multimodal summary
        out = to_responses_input(
            [
                {
                    "role": "assistant",
                    "content": [{"type": "image_url", "image_url": {"url": "u"}}],
                }
            ]
        )
        assert "[assistant multimodal content: 1 image(s)]" in (
            out[0]["content"][0]["text"]
        )
