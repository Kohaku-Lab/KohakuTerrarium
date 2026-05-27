"""Unit tests for ``llm/anthropic_format.py`` — Messages API conversion.

Behavior-first: every assert verifies the exact transformed wire shape
produced by a request/response converter, not just that a dict came
back. Covers system/body splitting, multimodal user content, assistant
tool-call blocks, tool-result coalescing, streaming-delta accumulation,
usage accounting, and the cache-marker placement helpers.
"""

from kohakuterrarium.llm.anthropic_format import (
    KT_CONTENT_KEY,
    anthropic_tools,
    apply_delta,
    assistant_message,
    block_to_dict,
    content_text,
    finalize_block,
    is_anthropic_api_endpoint,
    looks_like_bearer_endpoint,
    mark_last_cacheable_block,
    mark_system_cache,
    mark_tail_cache,
    merge_usage,
    normalise_started_block,
    ordered_blocks,
    parse_tool_arguments,
    prepare_messages,
    tool_calls_from_blocks,
    usage_to_dict,
    user_content,
)
from kohakuterrarium.llm.base import ToolSchema


class _Delta:
    """Minimal stand-in for an Anthropic SDK stream delta object."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Usage:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class TestEndpointHeuristics:
    def test_minimax_base_url_uses_bearer_auth(self):
        assert looks_like_bearer_endpoint("https://api.minimax.io/v1") is True
        assert looks_like_bearer_endpoint("https://api.minimaxi.com") is True

    def test_non_minimax_endpoint_is_not_bearer(self):
        assert looks_like_bearer_endpoint("https://api.anthropic.com") is False
        assert looks_like_bearer_endpoint("") is False

    def test_anthropic_api_endpoint_detection(self):
        assert is_anthropic_api_endpoint("https://api.anthropic.com/v1") is True
        assert is_anthropic_api_endpoint("https://openrouter.ai/api") is False
        assert is_anthropic_api_endpoint("") is False


class TestAnthropicTools:
    def test_tool_schema_maps_to_anthropic_input_schema(self):
        schema = ToolSchema(
            name="bash",
            description="run a command",
            parameters={"type": "object", "properties": {"cmd": {"type": "string"}}},
        )
        out = anthropic_tools([schema])
        assert out == [
            {
                "name": "bash",
                "description": "run a command",
                "input_schema": {
                    "type": "object",
                    "properties": {"cmd": {"type": "string"}},
                },
            }
        ]

    def test_empty_parameters_becomes_empty_object_schema(self):
        schema = ToolSchema(name="ping", description="d", parameters={})
        out = anthropic_tools([schema])
        assert out[0]["input_schema"] == {"type": "object", "properties": {}}


class TestPrepareMessages:
    def test_system_messages_joined_and_split_from_body(self):
        system, body = prepare_messages(
            [
                {"role": "system", "content": "be terse"},
                {"role": "system", "content": "be kind"},
                {"role": "user", "content": "hi"},
            ]
        )
        assert system == "be terse\n\nbe kind"
        assert body == [{"role": "user", "content": "hi"}]

    def test_empty_system_text_is_dropped(self):
        system, body = prepare_messages([{"role": "system", "content": ""}])
        assert system == ""
        assert body == []

    def test_consecutive_tool_results_coalesce_into_one_user_message(self):
        # Consecutive tool messages following an assistant with two
        # tool_calls coalesce into one user message containing both
        # ``tool_result`` blocks. The preceding assistant is required
        # by Anthropic — orphan tool_results are dropped by
        # ``fix_anthropic_tool_block_pairing``.
        _system, body = prepare_messages(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"id": "a", "function": {"name": "f1", "arguments": "{}"}},
                        {"id": "b", "function": {"name": "f2", "arguments": "{}"}},
                    ],
                },
                {"role": "tool", "tool_call_id": "a", "content": "ra"},
                {"role": "tool", "tool_call_id": "b", "content": "rb"},
            ]
        )
        # assistant + one user(tool_result a, tool_result b)
        assert len(body) == 2
        assert body[0]["role"] == "assistant"
        assert body[1]["role"] == "user"
        assert [p["tool_use_id"] for p in body[1]["content"]] == ["a", "b"]

    def test_tool_result_after_user_text_starts_new_message(self):
        # An assistant.tool_call followed by user text then the tool
        # result lands the splice in: the tool_result is moved up to
        # immediately follow the assistant, and the user text stays
        # in place (just AFTER the tool_result group now).
        _system, body = prepare_messages(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"id": "x", "function": {"name": "f", "arguments": "{}"}}
                    ],
                },
                {"role": "user", "content": "question"},
                {"role": "tool", "tool_call_id": "x", "content": "answer"},
            ]
        )
        assert len(body) == 3
        assert body[0]["role"] == "assistant"
        assert body[1]["role"] == "user"
        assert body[1]["content"][0]["tool_use_id"] == "x"
        assert body[2]["role"] == "user"
        assert body[2]["content"] == "question"


class TestAssistantMessage:
    def test_text_plus_tool_calls_become_blocks(self):
        msg = {
            "role": "assistant",
            "content": "thinking",
            "tool_calls": [
                {
                    "id": "call1",
                    "function": {"name": "bash", "arguments": '{"cmd": "ls"}'},
                }
            ],
        }
        out = assistant_message(msg)
        assert out["content"][0] == {"type": "text", "text": "thinking"}
        assert out["content"][1] == {
            "type": "tool_use",
            "id": "call1",
            "name": "bash",
            "input": {"cmd": "ls"},
        }

    def test_native_content_preserved_when_present(self):
        native = [{"type": "text", "text": "raw"}]
        msg = {"role": "assistant", KT_CONTENT_KEY: native, "content": "ignored"}
        out = assistant_message(msg)
        assert out["content"] == native
        # deep-copied — mutating the result must not touch the source
        out["content"][0]["text"] = "changed"
        assert native[0]["text"] == "raw"

    def test_native_tool_use_blocks_filtered_by_valid_call_ids(self):
        native = [
            {"type": "tool_use", "id": "keep", "name": "a", "input": {}},
            {"type": "tool_use", "id": "stale", "name": "b", "input": {}},
        ]
        msg = {
            "role": "assistant",
            KT_CONTENT_KEY: native,
            "tool_calls": [{"id": "keep"}],
        }
        out = assistant_message(msg)
        assert [b["id"] for b in out["content"]] == ["keep"]

    def test_text_only_assistant_returns_plain_text_when_no_parts(self):
        out = assistant_message({"role": "assistant", "content": ""})
        assert out == {"role": "assistant", "content": ""}


class TestUserContent:
    def test_string_content_passes_through(self):
        assert user_content("hello") == "hello"

    def test_data_url_image_becomes_base64_source(self):
        part = {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,QUJD"},
        }
        out = user_content([part])
        assert out == [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": "QUJD",
                },
            }
        ]

    def test_http_image_url_becomes_url_source(self):
        out = user_content(
            [{"type": "image_url", "image_url": {"url": "https://x/y.png"}}]
        )
        assert out[0] == {
            "type": "image",
            "source": {"type": "url", "url": "https://x/y.png"},
        }

    def test_unsupported_image_url_becomes_text_placeholder(self):
        out = user_content([{"type": "image_url", "image_url": {"url": "ftp://x"}}])
        assert out[0] == {
            "type": "text",
            "text": "[image omitted: unsupported image URL]",
        }

    def test_file_part_with_content_inlined_as_text(self):
        out = user_content(
            [{"type": "file", "file": {"name": "a.txt", "content": "body"}}]
        )
        assert out[0] == {"type": "text", "text": "[file: a.txt]\nbody"}

    def test_file_part_without_content_becomes_omitted_placeholder(self):
        out = user_content([{"type": "file", "file": {"path": "/p/x"}}])
        assert out[0] == {"type": "text", "text": "[file omitted: /p/x]"}

    def test_empty_part_list_returns_empty_string(self):
        assert user_content([{"type": "unknown"}]) == ""

    def test_non_list_non_str_content_stringified(self):
        assert user_content(123) == "123"
        assert user_content(None) == ""


class TestContentText:
    def test_list_of_text_parts_joined_with_newline(self):
        assert (
            content_text([{"type": "text", "text": "a"}, {"type": "text", "text": "b"}])
            == "a\nb"
        )

    def test_multimodal_only_content_summarised(self):
        out = content_text([{"type": "image_url"}, {"type": "file"}])
        assert out == "[message multimodal content: 1 image(s), 1 file(s)]"

    def test_assistant_flag_changes_summary_label(self):
        out = content_text([{"type": "image"}], assistant=True)
        assert out == "[assistant multimodal content: 1 image(s), 0 file(s)]"

    def test_none_content_is_empty_string(self):
        assert content_text(None) == ""


class TestParseToolArguments:
    def test_valid_json_string_parsed(self):
        assert parse_tool_arguments('{"a": 1}') == {"a": 1}

    def test_dict_passes_through(self):
        assert parse_tool_arguments({"a": 1}) == {"a": 1}

    def test_invalid_json_wrapped_in_raw(self):
        assert parse_tool_arguments("not json") == {"_raw": "not json"}

    def test_non_dict_json_wrapped_in_value(self):
        assert parse_tool_arguments("[1, 2]") == {"value": [1, 2]}

    def test_non_string_non_dict_returns_empty(self):
        assert parse_tool_arguments(42) == {}


class TestBlockToDict:
    def test_dict_drops_none_values(self):
        assert block_to_dict({"type": "text", "text": None}) == {"type": "text"}

    def test_none_block_returns_empty(self):
        assert block_to_dict(None) == {}

    def test_object_with_model_dump_used(self):
        class _M:
            def model_dump(self, exclude_none=True):
                return {"type": "text", "text": "x"}

        assert block_to_dict(_M()) == {"type": "text", "text": "x"}

    def test_plain_object_attrs_extracted(self):
        obj = _Delta(type="text", text="hi")
        assert block_to_dict(obj) == {"type": "text", "text": "hi"}


class TestStreamingBlocks:
    def test_normalise_started_text_block_gets_empty_text(self):
        assert normalise_started_block({"type": "text"}) == {"type": "text", "text": ""}

    def test_normalise_started_tool_use_seeds_partial_json(self):
        out = normalise_started_block({"type": "tool_use"})
        assert out["input"] == {} and out["_partial_json"] == ""

    def test_text_delta_accumulates_and_returns_chunk(self):
        block = {}
        first = apply_delta(block, _Delta(type="text_delta", text="he"))
        second = apply_delta(block, _Delta(type="text_delta", text="llo"))
        assert first == "he" and second == "llo"
        assert block == {"type": "text", "text": "hello"}

    def test_thinking_delta_accumulates_without_returning_text(self):
        block = {}
        out = apply_delta(block, _Delta(type="thinking_delta", thinking="reason"))
        assert out == ""
        assert block == {"type": "thinking", "thinking": "reason"}

    def test_input_json_delta_accumulates_partial_json(self):
        block = {}
        apply_delta(block, _Delta(type="input_json_delta", partial_json='{"a"'))
        apply_delta(block, _Delta(type="input_json_delta", partial_json=": 1}"))
        assert block["_partial_json"] == '{"a": 1}'

    def test_finalize_tool_use_parses_accumulated_json(self):
        block = {"type": "tool_use", "_partial_json": '{"x": 2}'}
        finalize_block(block)
        assert block["input"] == {"x": 2}
        assert "_partial_json" not in block

    def test_finalize_non_tool_block_just_drops_partial_json(self):
        block = {"type": "text", "_partial_json": "junk"}
        finalize_block(block)
        assert "_partial_json" not in block

    def test_ordered_blocks_sorts_and_strips_internal_keys(self):
        blocks = {
            1: {"type": "tool_use", "_partial_json": '{"a": 1}', "id": "x"},
            0: {"type": "text", "text": "first"},
        }
        out = ordered_blocks(blocks)
        assert out[0] == {"type": "text", "text": "first"}
        assert out[1] == {"type": "tool_use", "input": {"a": 1}, "id": "x"}

    def test_ordered_blocks_drops_typeless_blocks(self):
        out = ordered_blocks({0: {"text": "no type"}})
        assert out == []


class TestToolCallsFromBlocks:
    def test_tool_use_block_becomes_native_call_with_json_arguments(self):
        calls = tool_calls_from_blocks(
            [{"type": "tool_use", "id": "c1", "name": "bash", "input": {"cmd": "ls"}}]
        )
        assert len(calls) == 1
        assert calls[0].id == "c1" and calls[0].name == "bash"
        assert calls[0].arguments == '{"cmd": "ls"}'

    def test_string_input_kept_verbatim(self):
        calls = tool_calls_from_blocks(
            [{"type": "tool_use", "id": "c", "name": "n", "input": "raw"}]
        )
        assert calls[0].arguments == "raw"

    def test_non_tool_use_blocks_ignored(self):
        assert tool_calls_from_blocks([{"type": "text", "text": "x"}]) == []


class TestUsageAccounting:
    def test_usage_to_dict_sums_prompt_side_with_cache(self):
        usage = _Usage(
            input_tokens=10,
            output_tokens=5,
            cache_creation_input_tokens=3,
            cache_read_input_tokens=2,
        )
        out = usage_to_dict(usage)
        assert out["prompt_tokens"] == 15  # 10 + 3 + 2
        assert out["completion_tokens"] == 5
        assert out["total_tokens"] == 20
        assert out["cache_creation_input_tokens"] == 3
        assert out["cache_read_input_tokens"] == 2

    def test_usage_to_dict_none_returns_empty(self):
        assert usage_to_dict(None) == {}

    def test_merge_usage_keeps_prompt_side_when_delta_only_has_output(self):
        existing = {"input_tokens": 100, "output_tokens": 0}
        # message_delta event only carries output_tokens
        merged = merge_usage(existing, _Usage(output_tokens=42))
        assert merged["completion_tokens"] == 42
        # prompt side must not be zeroed
        assert merged["prompt_tokens"] == 100

    def test_merge_usage_empty_usage_returns_copy_of_existing(self):
        existing = {"input_tokens": 7}
        merged = merge_usage(existing, None)
        assert merged == {"input_tokens": 7}
        assert merged is not existing


class TestEdgeCases:
    def test_prepare_messages_routes_assistant_role(self):
        _system, body = prepare_messages([{"role": "assistant", "content": "hello"}])
        # assistant content is structured into text blocks
        assert body == [
            {"role": "assistant", "content": [{"type": "text", "text": "hello"}]}
        ]

    def test_user_content_skips_non_dict_parts(self):
        out = user_content(["raw string", {"type": "text", "text": "kept"}])
        assert out == [{"type": "text", "text": "kept"}]

    def test_content_text_skips_non_dict_parts(self):
        assert content_text(["raw", {"type": "text", "text": "kept"}]) == "kept"

    def test_block_to_dict_model_dump_typeerror_fallback(self):
        # an SDK object whose model_dump rejects the exclude_none kwarg;
        # block_to_dict must retry with a bare model_dump() call.
        class _M:
            def model_dump(self, *args, **kwargs):
                if kwargs:
                    raise TypeError("no kwargs allowed")
                return {"type": "text"}

        assert block_to_dict(_M()) == {"type": "text"}

    def test_block_to_dict_attribute_extraction_fallback(self):
        # an object with neither model_dump nor a usable __dict__
        class _Slotted:
            __slots__ = ("type", "text")

            def __init__(self):
                self.type = "text"
                self.text = "hi"

        assert block_to_dict(_Slotted()) == {"type": "text", "text": "hi"}

    def test_normalise_started_thinking_block_seeds_fields(self):
        out = normalise_started_block({"type": "thinking"})
        assert out == {"type": "thinking", "thinking": "", "signature": ""}

    def test_signature_delta_sets_signature(self):
        block = {}
        apply_delta(block, _Delta(type="signature_delta", signature="sig123"))
        assert block == {"type": "thinking", "signature": "sig123"}

    def test_mark_last_cacheable_block_skips_non_dict_entries(self):
        blocks = ["not a dict", {"type": "text", "text": "x"}]
        assert mark_last_cacheable_block(blocks) is True
        assert blocks[1]["cache_control"] == {"type": "ephemeral"}


class TestCacheMarkers:
    def test_mark_system_cache_wraps_plain_string(self):
        out = mark_system_cache("system prompt")
        assert out == [
            {
                "type": "text",
                "text": "system prompt",
                "cache_control": {"type": "ephemeral"},
            }
        ]

    def test_mark_last_cacheable_block_marks_last_text(self):
        blocks = [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]
        assert mark_last_cacheable_block(blocks) is True
        assert "cache_control" not in blocks[0]
        assert blocks[1]["cache_control"] == {"type": "ephemeral"}

    def test_mark_last_cacheable_block_returns_false_when_nothing_cacheable(self):
        blocks = [{"type": "image"}]
        assert mark_last_cacheable_block(blocks) is False

    def test_mark_tail_cache_marks_last_n_body_messages(self):
        messages = [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
        ]
        out = mark_tail_cache(messages, slots=2)
        # first message untouched, original input not mutated
        assert messages[0]["content"] == "u1"
        assert out[0]["content"] == "u1"
        # last two converted to marked content lists
        assert out[1]["content"][0]["cache_control"] == {"type": "ephemeral"}
        assert out[2]["content"][0]["cache_control"] == {"type": "ephemeral"}

    def test_mark_tail_cache_zero_slots_returns_input_unchanged(self):
        messages = [{"role": "user", "content": "x"}]
        assert mark_tail_cache(messages, slots=0) is messages

    def test_mark_tail_cache_marks_last_block_of_list_content(self):
        messages = [
            {"role": "user", "content": [{"type": "text", "text": "only"}]},
        ]
        out = mark_tail_cache(messages, slots=1)
        assert out[0]["content"][0]["cache_control"] == {"type": "ephemeral"}


# ── fix_anthropic_tool_block_pairing ─────────────────────────────


from kohakuterrarium.llm.anthropic_format import (  # noqa: E402
    SYNTHETIC_TOOL_RESULT_TEXT,
    fix_anthropic_tool_block_pairing,
    sanitized_native_content,
)


def _asst_tool_use(*pairs):
    """Build a synthetic assistant message with N tool_use blocks."""
    content = [
        {"type": "tool_use", "id": id_, "name": name, "input": {}}
        for id_, name in pairs
    ]
    return {"role": "assistant", "content": content}


def _user_tool_results(*pairs):
    """Build a synthetic user message with N tool_result blocks."""
    content = [
        {"type": "tool_result", "tool_use_id": id_, "content": text}
        for id_, text in pairs
    ]
    return {"role": "user", "content": content}


class TestFixAnthropicToolBlockPairing:
    """Anthropic's API rejects (400) any conversation where a
    ``tool_use`` content block has no matching ``tool_result`` in the
    immediately-following user message, OR a ``tool_result`` block
    references no preceding ``tool_use``. This post-pass runs on
    every request to keep the wire shape valid even after interrupts,
    branch switches, opportunistic input injection, or compaction.
    """

    def test_well_formed_pair_passes_through_unchanged(self):
        messages = [
            _asst_tool_use(("a", "bash")),
            _user_tool_results(("a", "ok")),
        ]
        out = fix_anthropic_tool_block_pairing(messages)
        assert len(out) == 2
        assert out[0] == messages[0]
        # The spliced user message has the same single tool_result.
        assert out[1]["role"] == "user"
        assert len(out[1]["content"]) == 1
        assert out[1]["content"][0]["tool_use_id"] == "a"
        assert out[1]["content"][0]["content"] == "ok"

    def test_idempotent(self):
        # Running the pass twice yields the same shape — the second
        # call must be a no-op given the first call's output.
        messages = [
            _asst_tool_use(("a", "bash")),
            _user_tool_results(("a", "ok")),
            {"role": "assistant", "content": [{"type": "text", "text": "done"}]},
        ]
        once = fix_anthropic_tool_block_pairing(messages)
        twice = fix_anthropic_tool_block_pairing(once)
        assert once == twice

    def test_synthesises_placeholder_for_missing_tool_result(self):
        # Assistant has two tool_use blocks but only one tool_result.
        # The missing one MUST be replaced with a synthetic is_error
        # block carrying the "interrupted or removed" wording so the
        # API doesn't 400 and the model can decide whether to retry.
        messages = [
            _asst_tool_use(("a", "bash"), ("b", "edit")),
            _user_tool_results(("a", "first ok")),
        ]
        out = fix_anthropic_tool_block_pairing(messages)
        assert len(out) == 2
        user_blocks = out[1]["content"]
        assert len(user_blocks) == 2
        assert user_blocks[0]["tool_use_id"] == "a"
        assert user_blocks[0]["content"] == "first ok"
        # Synthetic placeholder for b.
        synth = user_blocks[1]
        assert synth["tool_use_id"] == "b"
        assert synth["is_error"] is True
        assert "[edit]" in synth["content"]
        assert SYNTHETIC_TOOL_RESULT_TEXT in synth["content"]

    def test_splices_tool_result_up_past_user_text(self):
        # Real user text landed between assistant.tool_use and the
        # tool_result (e.g. opportunistic input injection). Splice
        # the tool_result up so it immediately follows the assistant;
        # keep the user text exactly where it was — just now AFTER
        # the spliced tool_result group. The user explicitly OKs
        # this re-ordering as a non-logical bug, just bad ordering.
        messages = [
            _asst_tool_use(("a", "bash")),
            {"role": "user", "content": "what about Y?"},
            _user_tool_results(("a", "X done")),
        ]
        out = fix_anthropic_tool_block_pairing(messages)
        assert len(out) == 3
        assert out[0]["role"] == "assistant"
        assert out[1]["role"] == "user"
        assert out[1]["content"][0]["tool_use_id"] == "a"
        assert out[1]["content"][0]["content"] == "X done"
        assert out[2] == {"role": "user", "content": "what about Y?"}

    def test_drops_orphan_tool_result(self):
        # tool_result with no preceding tool_use — drop the block.
        # The user message that carried it becomes empty and is
        # dropped along with it.
        messages = [
            {"role": "user", "content": "hello"},
            _user_tool_results(("ghost", "leftover")),
            {"role": "assistant", "content": [{"type": "text", "text": "hi"}]},
        ]
        out = fix_anthropic_tool_block_pairing(messages)
        assert len(out) == 2
        assert out[0]["content"] == "hello"
        assert out[1]["content"][0]["text"] == "hi"

    def test_drops_orphan_tool_result_keeps_rest_of_user_message(self):
        # A user message that mixes a real text block with an orphan
        # tool_result must keep the real text and drop only the
        # orphan. (Defensive — our converter doesn't usually mix
        # these, but the pass must be robust to bad input.)
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "hi"},
                    {"type": "tool_result", "tool_use_id": "ghost", "content": "stale"},
                ],
            },
        ]
        out = fix_anthropic_tool_block_pairing(messages)
        assert len(out) == 1
        assert out[0]["content"] == [{"type": "text", "text": "hi"}]

    def test_two_consecutive_pairs_pass_through(self):
        messages = [
            _asst_tool_use(("a", "bash")),
            _user_tool_results(("a", "first")),
            _asst_tool_use(("b", "edit")),
            _user_tool_results(("b", "second")),
        ]
        out = fix_anthropic_tool_block_pairing(messages)
        assert len(out) == 4
        assert out[1]["content"][0]["tool_use_id"] == "a"
        assert out[3]["content"][0]["tool_use_id"] == "b"

    def test_synthesises_for_assistant_at_end_of_list(self):
        # Assistant.tool_use with NOTHING after — no following user
        # at all. Must synthesise a placeholder user(tool_result).
        messages = [_asst_tool_use(("a", "bash"))]
        out = fix_anthropic_tool_block_pairing(messages)
        assert len(out) == 2
        assert out[1]["role"] == "user"
        assert out[1]["content"][0]["tool_use_id"] == "a"
        assert out[1]["content"][0]["is_error"] is True

    def test_empty_message_list_passes_through(self):
        assert fix_anthropic_tool_block_pairing([]) == []


class TestSanitizedNativeContentNoneHole:
    """Regression: when ``tool_calls`` is missing from the assistant
    message (key absent, not just empty), the legacy code returned
    ALL ``_kt_anthropic_content`` blocks — including orphan
    ``tool_use`` from a previous round-trip the canonical OpenAI
    shape has since lost track of. That orphan tool_use then went
    out on the wire and Claude rejected the request. The fix
    treats missing-key and empty-list identically: any tool_use in
    native content with no announcing tool_call is an orphan.
    """

    def test_missing_tool_calls_drops_native_tool_use(self):
        msg = {
            "role": "assistant",
            "content": "",
            # NO ``tool_calls`` key at all — but the Anthropic round-
            # trip stored a tool_use in native content.
            KT_CONTENT_KEY: [
                {"type": "text", "text": "thinking"},
                {"type": "tool_use", "id": "ghost", "name": "bash", "input": {}},
            ],
        }
        out = sanitized_native_content(msg)
        # Text block kept; orphan tool_use dropped.
        assert out == [{"type": "text", "text": "thinking"}]

    def test_empty_tool_calls_list_drops_native_tool_use(self):
        # Same behaviour for empty list — already worked pre-fix but
        # the new code path is unified, so guard against regression.
        msg = {
            "role": "assistant",
            "content": "",
            "tool_calls": [],
            KT_CONTENT_KEY: [
                {"type": "tool_use", "id": "ghost", "name": "bash", "input": {}},
            ],
        }
        out = sanitized_native_content(msg)
        assert out == []

    def test_announced_tool_use_kept(self):
        # The matching id IS in tool_calls — the block survives.
        msg = {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "real", "function": {"name": "bash", "arguments": "{}"}}
            ],
            KT_CONTENT_KEY: [
                {"type": "tool_use", "id": "real", "name": "bash", "input": {}},
                {"type": "tool_use", "id": "ghost", "name": "edit", "input": {}},
            ],
        }
        out = sanitized_native_content(msg)
        ids = [b.get("id") for b in out if b.get("type") == "tool_use"]
        assert ids == ["real"]
