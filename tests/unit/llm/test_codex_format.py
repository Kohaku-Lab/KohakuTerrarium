"""Unit tests for ``llm/codex_format.py`` — Responses API message shapes.

Behavior-first: assert the exact Responses-API ``input`` items produced
from Chat Completions messages, the function_call / function_call_output
pairing repair, multimodal tool-output array form, and artifact-URL
resolution to data URLs (with on-disk fixtures).
"""

import base64
from copy import deepcopy

import pytest

from kohakuterrarium.llm import artifact_resolve
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
    @pytest.mark.parametrize("model", ["slurm/ds", "kimi-k2", "glm-5", "alias"])
    def test_explicit_replay_preserves_reasoning_for_aliases(self, model):
        messages = [
            {"role": "assistant", "content": "Done", "reasoning_content": "Think"}
        ]
        items = to_responses_input(messages, model=model, replay_reasoning=True)
        assert items[0] == {
            "type": "reasoning",
            "summary": [],
            "content": [{"type": "reasoning_text", "text": "Think"}],
        }
        messages[0]["reasoning_content"] = "Edited"
        assert (
            to_responses_input(messages, model=model, replay_reasoning=True)[0][
                "content"
            ][0]["text"]
            == "Edited"
        )
        assert items[0]["content"][0]["text"] == "Think"

    def test_explicit_false_disables_legacy_replay(self):
        assert (
            to_responses_input(
                [{"role": "assistant", "content": "", "reasoning_content": "Think"}],
                model="deepseek-flash",
                replay_reasoning=False,
            )
            == []
        )

    @pytest.mark.parametrize("flag", ["false", "true", 0, 1, [], {}])
    def test_invalid_replay_capability_is_rejected(self, flag):
        with pytest.raises(ValueError, match="responses_reasoning_replay"):
            to_responses_input([], replay_reasoning=flag)

    def test_opt_in_does_not_turn_summary_or_encrypted_state_into_plaintext(self):
        assert (
            to_responses_input(
                [
                    {
                        "role": "assistant",
                        "content": "",
                        "reasoning_summary": "Summary",
                        "encrypted_content": "opaque",
                    }
                ],
                model="alias",
                replay_reasoning=True,
            )
            == []
        )

    @pytest.mark.parametrize(
        "model", ["", "gpt-6-astra", "other-model", "not-deepseek-flash"]
    )
    def test_other_models_omit_foreign_reasoning_without_mutating_history(self, model):
        messages = [
            {
                "role": "assistant",
                "content": "Done",
                "reasoning_content": "Inspect files",
            }
        ]
        original = deepcopy(messages)
        assert to_responses_input(messages, model=model) == [
            {"role": "assistant", "content": [{"type": "output_text", "text": "Done"}]}
        ]
        assert messages == original

    @pytest.mark.parametrize("content", ["", "Checking files."])
    def test_reasoning_precedes_assistant_text_and_tool_calls(self, content):
        messages = [
            {
                "role": "assistant",
                "content": content,
                "reasoning_content": "Inspect the workspace.\nThen check the files.",
                "reasoning_summary": "A display summary",
                "tool_calls": [
                    {"id": "c1", "function": {"name": "tree", "arguments": "{}"}}
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "README.md"},
        ]
        original = deepcopy(messages)
        items = fix_tool_call_pairing(
            to_responses_input(messages, model="deepseek-flash")
        )
        expected = [
            {
                "type": "reasoning",
                "summary": [],
                "content": [
                    {
                        "type": "reasoning_text",
                        "text": messages[0]["reasoning_content"],
                    }
                ],
            }
        ]
        if content:
            expected.append(
                {
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": content}],
                }
            )
        expected.extend(
            [
                {
                    "type": "function_call",
                    "call_id": "c1",
                    "name": "tree",
                    "arguments": "{}",
                },
                {
                    "type": "function_call_output",
                    "call_id": "c1",
                    "output": "README.md",
                },
            ]
        )
        assert items == expected
        assert messages == original

    @pytest.mark.parametrize("reasoning", [None, "", [], {}])
    def test_summary_does_not_become_reasoning_content(self, reasoning):
        assert to_responses_input(
            [
                {
                    "role": "assistant",
                    "content": "answer",
                    "reasoning_content": reasoning,
                    "reasoning_summary": "summary only",
                }
            ],
            model="deepseek-flash",
        ) == [
            {
                "role": "assistant",
                "content": [{"type": "output_text", "text": "answer"}],
            }
        ]

    def test_reasoning_only_assistant_turn_is_preserved(self):
        items = to_responses_input(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "reasoning_content": "Still thinking",
                }
            ],
            model="deepseek-flash",
        )
        assert items == [
            {
                "type": "reasoning",
                "summary": [],
                "content": [{"type": "reasoning_text", "text": "Still thinking"}],
            }
        ]

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
    def test_parallel_calls_stay_together_before_outputs(self):
        reasoning = {
            "type": "reasoning",
            "summary": [],
            "content": [{"type": "reasoning_text", "text": "Check both files."}],
        }
        calls = [
            {"type": "function_call", "call_id": call_id, "name": "read"}
            for call_id in ("c1", "c2")
        ]
        outputs = [
            {"type": "function_call_output", "call_id": call_id, "output": "ok"}
            for call_id in ("c1", "c2")
        ]
        assert fix_tool_call_pairing([reasoning, *calls, *reversed(outputs)]) == [
            reasoning,
            *calls,
            *outputs,
        ]

    def test_separate_tool_rounds_are_not_merged(self):
        items = []
        for call_id in ("c1", "c2"):
            items.extend(
                [
                    {"type": "function_call", "call_id": call_id, "name": "read"},
                    {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": "ok",
                    },
                ]
            )
        assert fix_tool_call_pairing(items) == items

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
        monkeypatch.setattr(artifact_resolve, "_session_dir", lambda: session_dir)

        out = _resolve_artifact_url("/api/sessions/sid123/artifacts/pic.png")
        assert out.startswith("data:image/png;base64,")
        # base64 of b"PNGDATA"
        assert out == "data:image/png;base64," + base64.b64encode(b"PNGDATA").decode()

    def test_missing_artifact_file_falls_back_to_original_url(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(artifact_resolve, "_session_dir", lambda: tmp_path)
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
