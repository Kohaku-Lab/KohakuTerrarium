"""Provider-boundary recovery helpers."""

import asyncio

from kohakuterrarium.llm.recovery import (
    ErrorClass,
    RetryPolicy,
    backoff_delay,
    classify_openai_error,
    drop_last_tool_round,
    format_drop_placeholder,
)


class _Error(Exception):
    def __init__(self, message: str, status_code=None, body=None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


def test_classify_openai_error_covers_core_classes():
    assert classify_openai_error(asyncio.TimeoutError()) is ErrorClass.TRANSIENT
    assert (
        classify_openai_error(_Error("too many requests", 429)) is ErrorClass.RATE_LIMIT
    )
    assert classify_openai_error(_Error("server exploded", 502)) is ErrorClass.SERVER
    assert classify_openai_error(_Error("bad request", 400)) is ErrorClass.USER_ERROR
    assert (
        classify_openai_error(
            _Error("too long", 400, {"error": {"code": "context_length_exceeded"}})
        )
        is ErrorClass.OVERFLOW
    )
    # Status reached us but we don't have a rule for it — UNKNOWN.
    assert classify_openai_error(_Error("mystery", 302)) is ErrorClass.UNKNOWN


def test_classify_retries_full_5xx_range():
    """Every 5xx must classify as SERVER so the retry policy retries it."""
    for status in (500, 502, 503, 504, 521, 599):
        assert (
            classify_openai_error(_Error("bang", status)) is ErrorClass.SERVER
        ), f"status {status} did not classify as SERVER"


def test_classify_rate_limit_matches_status_or_marker():
    assert (
        classify_openai_error(_Error("Too Many Requests", 429)) is ErrorClass.RATE_LIMIT
    )
    # Status missing but body says quota exceeded — still RATE_LIMIT.
    assert (
        classify_openai_error(_Error("nope", body={"code": "quota_exceeded"}))
        is ErrorClass.RATE_LIMIT
    )


def test_classify_httpx_remote_protocol_error_is_transient():
    """``peer closed connection without sending complete message body
    (incomplete chunked read)`` is httpx's typical truncated-stream
    error; it must classify as TRANSIENT so the retry loop retries it.
    """
    msg = (
        "peer closed connection without sending complete message body "
        "(incomplete chunked read)"
    )
    assert classify_openai_error(_Error(msg)) is ErrorClass.TRANSIENT


def test_classify_recognises_exception_class_name():
    """Bare httpx exceptions with empty message still classify by class
    name — covers the case where a re-raise loses the stringified body.
    """

    class RemoteProtocolError(Exception):
        pass

    class ReadError(Exception):
        pass

    assert classify_openai_error(RemoteProtocolError()) is ErrorClass.TRANSIENT
    assert classify_openai_error(ReadError()) is ErrorClass.TRANSIENT


def test_retry_policy_default_includes_429_and_5xx_and_transient():
    policy = RetryPolicy()
    assert ErrorClass.RATE_LIMIT in policy.retry_classes
    assert ErrorClass.SERVER in policy.retry_classes
    assert ErrorClass.TRANSIENT in policy.retry_classes


def test_classify_no_status_error_is_transient_and_retried():
    """An error that never carried a status code (request died before
    a response) classifies as TRANSIENT — covers the long tail of
    network errors (DNS, TCP reset, proxy hiccup, mid-stream cut) that
    aren't worth enumerating as explicit markers.
    """
    # No status, message that doesn't match any marker: still TRANSIENT.
    assert classify_openai_error(_Error("something flaky")) is ErrorClass.TRANSIENT
    # And the default policy retries TRANSIENT.
    assert ErrorClass.TRANSIENT in RetryPolicy().retry_classes


def test_classify_no_status_with_explicit_overflow_still_overflow():
    """No-status fallback must NOT mask non-retryable classifications."""
    assert (
        classify_openai_error(
            _Error("too long", body={"error": {"code": "context_length_exceeded"}})
        )
        is ErrorClass.OVERFLOW
    )


def test_drop_last_tool_round_noop_for_empty_or_no_tools():
    assert drop_last_tool_round([]) == (0, [])
    messages = [{"role": "user", "content": "hi"}]
    dropped, recovered = drop_last_tool_round(messages)
    assert dropped == 0
    assert recovered is messages


def test_drop_last_tool_round_splices_parallel_tool_round():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "task"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"function": {"name": "read"}},
                {"function": {"name": "grep"}},
            ],
        },
        {"role": "tool", "name": "read", "content": "x" * 10},
        {"role": "tool", "name": "grep", "content": "y" * 20},
        {"role": "assistant", "content": "after"},
    ]

    dropped, recovered = drop_last_tool_round(messages)

    assert dropped == 2
    assert [m["role"] for m in recovered] == ["system", "user", "user", "assistant"]
    assert "read" in recovered[2]["content"]
    assert "grep" in recovered[2]["content"]
    assert "30 characters" in recovered[2]["content"]
    assert recovered[3]["content"] == "after"
    assert messages[2]["role"] == "assistant"  # original not mutated


def test_format_drop_placeholder_is_deterministic():
    text = format_drop_placeholder(2, 123, ["bash", "read"])
    assert "2 tool call(s)" in text
    assert "123 characters" in text
    assert "`bash`, `read`" in text
    assert "paginated `read`" in text


def test_retry_policy_from_dict_and_backoff_without_jitter():
    policy = RetryPolicy.from_value(
        {"max_retries": 2, "base_delay": 2, "max_delay": 5, "jitter": 0}
    )
    assert policy.max_retries == 2
    assert backoff_delay(1, policy) == 2
    assert backoff_delay(2, policy) == 4
    assert backoff_delay(3, policy) == 5
