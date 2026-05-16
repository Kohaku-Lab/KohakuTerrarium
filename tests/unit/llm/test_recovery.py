"""Unit tests for ``llm/recovery.py`` — provider-boundary recovery.

Behavior-first: assert the exact ErrorClass an exception classifies to,
the RetryPolicy.from_value coercion rules, the capped-exponential
backoff math, and the emergency context-drop reducer's splice result
(which messages survive, the synthetic placeholder content).
"""

import asyncio

from kohakuterrarium.llm.recovery import (
    ErrorClass,
    RetryPolicy,
    backoff_delay,
    classify_openai_error,
    drop_last_tool_round,
    format_drop_placeholder,
)


class _HTTPError(Exception):
    def __init__(self, message="", status_code=None, body=None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class TestClassifyOpenAIError:
    def test_timeout_is_transient(self):
        assert classify_openai_error(asyncio.TimeoutError()) == ErrorClass.TRANSIENT
        assert classify_openai_error(TimeoutError()) == ErrorClass.TRANSIENT

    def test_413_status_is_overflow(self):
        assert classify_openai_error(_HTTPError(status_code=413)) == ErrorClass.OVERFLOW

    def test_context_length_marker_is_overflow(self):
        exc = _HTTPError("this model's maximum context length is 8192", status_code=400)
        assert classify_openai_error(exc) == ErrorClass.OVERFLOW

    def test_429_status_is_rate_limit(self):
        assert (
            classify_openai_error(_HTTPError(status_code=429)) == ErrorClass.RATE_LIMIT
        )

    def test_rate_limit_marker_is_rate_limit(self):
        exc = _HTTPError("too many requests, slow down")
        assert classify_openai_error(exc) == ErrorClass.RATE_LIMIT

    def test_5xx_status_is_server(self):
        assert classify_openai_error(_HTTPError(status_code=503)) == ErrorClass.SERVER

    def test_4xx_user_status_is_user_error(self):
        assert (
            classify_openai_error(_HTTPError(status_code=401)) == ErrorClass.USER_ERROR
        )
        assert (
            classify_openai_error(_HTTPError(status_code=404)) == ErrorClass.USER_ERROR
        )

    def test_transient_marker_is_transient(self):
        # a markered network error with NO status code lands TRANSIENT
        # (a 400 would instead be classified USER_ERROR by status alone)
        assert (
            classify_openai_error(_HTTPError("connection reset by peer"))
            == ErrorClass.TRANSIENT
        )

    def test_no_status_unknown_error_treated_as_transient(self):
        # request died before the server answered → transient long-tail
        assert classify_openai_error(_HTTPError("weird dns failure")) == (
            ErrorClass.TRANSIENT
        )

    def test_unhandled_status_code_is_unknown(self):
        # server DID respond, but with a status we have no rule for
        assert classify_openai_error(_HTTPError(status_code=302)) == ErrorClass.UNKNOWN

    def test_error_payload_code_field_classifies(self):
        exc = _HTTPError(body={"error": {"code": "context_length_exceeded"}})
        assert classify_openai_error(exc) == ErrorClass.OVERFLOW

    def test_class_name_marker_classifies_when_message_sparse(self):
        # a bare RemoteProtocolError-style exception with empty message
        class RemoteProtocolError(Exception):
            pass

        assert classify_openai_error(RemoteProtocolError()) == ErrorClass.TRANSIENT


class TestRetryPolicy:
    def test_retry_policy_instance_returned_as_is(self):
        policy = RetryPolicy(max_retries=9)
        assert RetryPolicy.from_value(policy) is policy

    def test_none_yields_defaults(self):
        policy = RetryPolicy.from_value(None)
        assert policy.max_retries == 3
        assert policy.base_delay == 1.0

    def test_dict_overrides_applied(self):
        policy = RetryPolicy.from_value(
            {"max_retries": 5, "base_delay": 2.0, "jitter": 0.0}
        )
        assert policy.max_retries == 5
        assert policy.base_delay == 2.0
        assert policy.jitter == 0.0

    def test_retry_classes_converted_from_strings(self):
        policy = RetryPolicy.from_value({"retry_classes": ["rate_limit", "server"]})
        assert policy.retry_classes == frozenset(
            {ErrorClass.RATE_LIMIT, ErrorClass.SERVER}
        )

    def test_unknown_retry_class_ignored(self):
        policy = RetryPolicy.from_value({"retry_classes": ["rate_limit", "bogus"]})
        assert policy.retry_classes == frozenset({ErrorClass.RATE_LIMIT})

    def test_non_dict_non_policy_yields_defaults(self):
        assert RetryPolicy.from_value("garbage").max_retries == 3


class TestBackoffDelay:
    def test_exponential_growth_without_jitter(self):
        policy = RetryPolicy(base_delay=1.0, max_delay=100.0, jitter=0.0)
        assert backoff_delay(1, policy) == 1.0
        assert backoff_delay(2, policy) == 2.0
        assert backoff_delay(3, policy) == 4.0

    def test_capped_at_max_delay(self):
        policy = RetryPolicy(base_delay=1.0, max_delay=5.0, jitter=0.0)
        assert backoff_delay(10, policy) == 5.0

    def test_jitter_keeps_delay_within_spread(self):
        policy = RetryPolicy(base_delay=4.0, max_delay=100.0, jitter=0.25)
        for _ in range(50):
            delay = backoff_delay(1, policy)
            # base=4.0, spread=1.0 → result in [3.0, 5.0]
            assert 3.0 <= delay <= 5.0

    def test_jitter_never_returns_negative(self):
        policy = RetryPolicy(base_delay=0.01, max_delay=100.0, jitter=10.0)
        for _ in range(50):
            assert backoff_delay(1, policy) >= 0.0


class TestFormatDropPlaceholder:
    def test_placeholder_names_dropped_tools(self):
        out = format_drop_placeholder(2, 5000, ["glob", "grep"])
        assert "`glob`" in out and "`grep`" in out
        assert "5000 characters" in out
        assert "2 tool call(s)" in out

    def test_no_tool_names_shows_unknown(self):
        out = format_drop_placeholder(1, 100, [])
        assert "(unknown)" in out


class TestDropLastToolRound:
    def test_empty_messages_returns_zero(self):
        count, recovered = drop_last_tool_round([])
        assert count == 0 and recovered == []

    def test_no_tool_round_returns_zero(self):
        messages = [{"role": "user", "content": "hi"}]
        count, recovered = drop_last_tool_round(messages)
        assert count == 0
        assert recovered is messages

    def test_last_tool_round_spliced_out(self):
        messages = [
            {"role": "user", "content": "do it"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "c1", "function": {"name": "glob", "arguments": "{}"}}
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "huge result " * 100},
        ]
        count, recovered = drop_last_tool_round(messages)
        assert count == 1
        # the assistant tool-call turn + its tool result are gone
        assert all(m.get("role") != "tool" for m in recovered)
        assert not any(m.get("tool_calls") for m in recovered)
        # a synthetic user placeholder replaced them
        assert recovered[-1]["role"] == "user"
        assert "tool-result truncated" in recovered[-1]["content"]
        assert "`glob`" in recovered[-1]["content"]

    def test_messages_after_dropped_round_preserved(self):
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "c1", "function": {"name": "read", "arguments": "{}"}}
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "result"},
            {"role": "user", "content": "follow-up"},
        ]
        count, recovered = drop_last_tool_round(messages)
        assert count == 1
        # the trailing follow-up user message survives the splice
        assert recovered[-1] == {"role": "user", "content": "follow-up"}

    def test_input_messages_not_mutated(self):
        original = {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "c1", "function": {"name": "glob", "arguments": "{}"}}
            ],
        }
        messages = [original, {"role": "tool", "tool_call_id": "c1", "content": "r"}]
        drop_last_tool_round(messages)
        assert original["tool_calls"][0]["id"] == "c1"
        assert len(messages) == 2

    def test_tool_names_recovered_from_tool_messages_when_calls_lack_names(self):
        messages = [
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]},
            {"role": "tool", "tool_call_id": "c1", "name": "bash", "content": "r"},
        ]
        _count, recovered = drop_last_tool_round(messages)
        assert "`bash`" in recovered[-1]["content"]

    def test_tool_name_read_from_flat_call_name_field(self):
        # some providers emit tool_calls with a top-level ``name`` and no
        # nested ``function`` block
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "c1", "name": "glob"}],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "r"},
        ]
        _count, recovered = drop_last_tool_round(messages)
        assert "`glob`" in recovered[-1]["content"]


class _ResponseError(Exception):
    """Exception carrying a ``response`` object with a .json() method."""

    def __init__(self, payload):
        super().__init__("error")

        class _Resp:
            def json(_self):
                return payload

        self.response = _Resp()


class TestErrorPayloadExtraction:
    def test_payload_extracted_from_response_json(self):
        exc = _ResponseError({"error": {"code": "rate_limit_exceeded"}})
        assert classify_openai_error(exc) == ErrorClass.RATE_LIMIT

    def test_response_json_without_error_key_used_directly(self):
        exc = _ResponseError({"code": "context length exceeded"})
        assert classify_openai_error(exc) == ErrorClass.OVERFLOW

    def test_response_json_raising_is_swallowed(self):
        class _BadResp(Exception):
            def __init__(self):
                super().__init__("")

                class _R:
                    def json(_s):
                        raise ValueError("not json")

                self.response = _R()

        # json() blowing up must not crash classification
        assert classify_openai_error(_BadResp()) == ErrorClass.TRANSIENT


class TestDropRoundByteAccounting:
    def test_list_content_tool_result_byte_count_in_placeholder(self):
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "c1", "function": {"name": "grep", "arguments": "{}"}}
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "c1",
                "content": [{"type": "text", "text": "abcde"}],
            },
        ]
        count, recovered = drop_last_tool_round(messages)
        assert count == 1
        # the list-shaped tool body's text length (5) is reported
        assert "5 characters" in recovered[-1]["content"]


class TestRetryPolicyDefaults:
    def test_default_retry_classes_cover_rate_limit_server_transient(self):
        policy = RetryPolicy()
        assert ErrorClass.RATE_LIMIT in policy.retry_classes
        assert ErrorClass.SERVER in policy.retry_classes
        assert ErrorClass.TRANSIENT in policy.retry_classes
        assert ErrorClass.USER_ERROR not in policy.retry_classes
