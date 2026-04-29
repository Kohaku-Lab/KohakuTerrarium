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
    assert classify_openai_error(_Error("mystery")) is ErrorClass.UNKNOWN


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
