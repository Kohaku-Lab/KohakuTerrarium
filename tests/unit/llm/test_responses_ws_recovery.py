"""Recovery contracts for Responses WebSocket requests."""

import asyncio
from copy import deepcopy

import pytest
import httpx
from openai import APIConnectionError

from kohakuterrarium.llm.codex_provider import CodexOAuthProvider
from kohakuterrarium.llm.model_recovery_status import observe_model_recovery
from kohakuterrarium.llm.responses_ws import ResponsesWSError
from tests.unit.llm.test_codex_provider import _FakeWSClient
from tests.unit.llm.test_openai_ws import MESSAGES, make_provider
from tests.unit.llm.test_responses_ws import (
    ASSIST1,
    USER1,
    USER2,
    Ev,
    Harness,
    completed,
)


@pytest.mark.parametrize("shape", ["dict", "object", "flat", "mixed"])
async def test_cache_rejection_shapes_resend_full_history(shape):
    fields = {"code": "previous_response_not_found", "message": "Missing response"}
    event = Ev(type="error", status=400)
    if shape == "flat":
        event.__dict__.update(fields)
    else:
        event.error = Ev(**fields) if shape == "object" else fields
        if shape == "mixed":
            event.code = None
            event.message = None
    h = Harness()
    h.conn.scripts = [[completed("r1")], [event], [completed("r2")]]
    await h.run([USER1])
    h.session.record_assistant_echo([ASSIST1])
    await h.run([USER1, ASSIST1, USER2])
    assert h.conn.sent[-1]["input"] == ["PAIRED", USER1, ASSIST1, USER2]
    assert "previous_response_id" not in h.conn.sent[-1]
    assert len(h.conn.sent) == 3


async def test_expired_connection_is_retired_even_when_request_cannot_replay():
    h = Harness()
    event = Ev(
        type="error",
        status=400,
        error={
            "code": "websocket_connection_limit_reached",
            "message": "Connection expired",
        },
    )
    h.conn.scripts = [[event]]
    with pytest.raises(ResponsesWSError) as caught:
        await h.run([USER1], base={"model": "m", "background": True})
    assert h.conn.closed
    assert h.session._connection is None
    assert caught.value.code == "websocket_connection_limit_reached"
    assert caught.value.status_code == 400
    assert caught.value.raw_event is event
    assert h.conn.send_attempts == 1


@pytest.fixture(params=["openai", "codex"])
def provider(request):
    policy = {"max_retries": 3, "base_delay": 0, "jitter": 0}
    if request.param == "openai":
        result = make_provider(websocket_mode=True, retry_policy=policy)
    else:
        result = CodexOAuthProvider(
            api_key="test", websocket_mode=True, retry_policy=policy
        )
        result._client = _FakeWSClient()
    factory = result._client.responses.connect

    def connect(**kwargs):
        manager = factory(**kwargs)
        result._client.responses.connection.closed = False
        return manager

    result._client.responses.connect = connect
    return result


@pytest.mark.parametrize("failure", ["transport", "expiry"])
async def test_uncommitted_attempt_is_discarded_before_replay(provider, failure):
    connection = provider._client.responses.connection
    terminal = (
        ConnectionError("socket dropped")
        if failure == "transport"
        else Ev(
            type="error",
            status=400,
            error={"code": "websocket_connection_limit_reached"},
        )
    )
    connection.scripts = [
        [
            Ev(type="response.created"),
            Ev(type="response.reasoning_text.delta", delta="discarded"),
            Ev(
                type="response.output_item.done",
                item=Ev(
                    type="function_call",
                    call_id="old",
                    name="must_not_run",
                    arguments="{}",
                ),
            ),
            terminal,
        ],
        [Ev(type="response.output_text.delta", delta="answer"), completed("recovered")],
    ]
    chunks = [chunk async for chunk in provider.chat(MESSAGES)]
    assert chunks == ["answer"]
    assert provider.last_tool_calls == []
    assert "discarded" not in str(provider.last_assistant_extra_fields)
    assert len(connection.sent) == 2
    assert connection.sent[0] == connection.sent[1]
    assert provider._ws_session._prev_id == "recovered"


async def test_delivered_text_blocks_replay(provider):
    connection = provider._client.responses.connection
    connection.scripts = [
        [
            Ev(type="response.output_text.delta", delta="partial"),
            ConnectionError("lost"),
        ]
    ]
    chunks = []
    with pytest.raises(ResponsesWSError):
        async for chunk in provider.chat(MESSAGES):
            chunks.append(chunk)
    assert chunks == ["partial"]
    assert len(connection.sent) == 1


async def test_zero_budget_disables_replay(provider):
    provider._retry_policy = type(provider._retry_policy)(max_retries=0)
    connection = provider._client.responses.connection
    connection.scripts = [[ConnectionError("lost")], [completed("unused")]]
    with pytest.raises(ResponsesWSError):
        async for _ in provider.chat(MESSAGES):
            pass
    assert len(connection.sent) == 1


async def test_cache_rejection_and_uncertain_replay_share_budget(provider):
    connection = provider._client.responses.connection
    connection.scripts = [
        [Ev(type="response.output_text.delta", delta="first"), completed("r1")],
        [Ev(type="error", error={"code": "previous_response_not_found"})],
        [Ev(type="response.created"), ConnectionError("lost")],
        [Ev(type="response.output_text.delta", delta="second"), completed("r2")],
        [completed("r3")],
    ]
    assert [x async for x in provider.chat(MESSAGES)] == ["first"]
    history = [
        *MESSAGES,
        {"role": "assistant", "content": "first"},
        {"role": "user", "content": "next"},
    ]
    assert [x async for x in provider.chat(history)] == ["second"]
    assert connection.sent[1]["previous_response_id"] == "r1"
    assert "previous_response_id" not in connection.sent[2]
    assert connection.sent[2] == connection.sent[3]
    history += [
        {"role": "assistant", "content": "second"},
        {"role": "user", "content": "last"},
    ]
    assert [x async for x in provider.chat(history)] == []
    assert connection.sent[4]["previous_response_id"] == "r2"


async def test_http_fallback_keeps_one_budget_and_does_not_return_to_ws(provider):
    client = provider._client
    connects = []
    creates = []
    options = []
    statuses = []

    def connect(**kwargs):
        connects.append(kwargs)
        raise ConnectionError("handshake failed")

    def with_options(**kwargs):
        options.append(kwargs)
        return client

    async def create(**kwargs):
        creates.append(kwargs)
        raise APIConnectionError(request=httpx.Request("POST", "http://localhost"))

    async def notify(payload):
        statuses.append(payload)

    client.responses.connect = connect
    client.with_options = with_options
    target = (
        client.responses
        if isinstance(provider, CodexOAuthProvider)
        else client.chat.completions
    )
    target.create = create
    with observe_model_recovery(notify), pytest.raises(APIConnectionError):
        async for _ in provider.chat(MESSAGES):
            pass
    assert len(creates) == 4
    assert len(connects) == 2
    assert statuses[-1]["phase"] is None
    if isinstance(provider, CodexOAuthProvider):
        assert all(option == {"max_retries": 0} for option in options)


async def test_retry_uses_frozen_request_despite_external_mutation(provider):
    messages = deepcopy(MESSAGES)
    provider.extra_body["metadata"] = {"tag": "original"}
    connection = provider._client.responses.connection
    connection.scripts = [[ConnectionError("lost")], [completed("ok")]]

    async def mutate(payload):
        if payload["phase"]:
            messages[-1]["content"] = "mutated"
            provider.extra_body["metadata"]["tag"] = "mutated"
            provider.config.model = "different"

    with observe_model_recovery(mutate):
        _ = [chunk async for chunk in provider.chat(messages)]
    assert len(connection.sent) == 2
    assert connection.sent[0] == connection.sent[1]
    assert connection.sent[1]["metadata"] == {"tag": "original"}


async def test_cancel_during_backoff_clears_status_and_does_not_send_again(provider):
    provider._retry_policy = type(provider._retry_policy)(
        max_retries=3, base_delay=60, jitter=0
    )
    connection = provider._client.responses.connection
    connection.scripts = [[Ev(type="response.created"), ConnectionError("lost")]]
    waiting = asyncio.Event()
    phases = []

    async def notify(payload):
        phases.append(payload["phase"])
        if payload["phase"] == "waiting":
            waiting.set()

    async def run():
        with observe_model_recovery(notify):
            _ = [chunk async for chunk in provider.chat(MESSAGES)]

    task = asyncio.create_task(run())
    await asyncio.wait_for(waiting.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert phases == ["waiting", None]
    assert len(connection.sent) == 1
    assert connection.closed
    assert not provider._ws_session.busy


@pytest.mark.parametrize("kind", ["failed", "incomplete"])
async def test_terminal_response_preserves_structured_reason(provider, kind):
    connection = provider._client.responses.connection
    event = Ev(
        type=f"response.{kind}",
        response=(
            {"error": {"code": "invalid_request", "message": "rejected", "status": 400}}
            if kind == "failed"
            else {"incomplete_details": {"reason": "max_output_tokens"}}
        ),
    )
    connection.scripts = [[Ev(type="response.created"), event]]
    with pytest.raises(ResponsesWSError) as caught:
        _ = [chunk async for chunk in provider.chat(MESSAGES)]
    assert caught.value.raw_event is event
    assert caught.value.last_event_type == f"response.{kind}"
    assert caught.value.code == (
        "invalid_request" if kind == "failed" else "max_output_tokens"
    )
    assert caught.value.status_code == (400 if kind == "failed" else None)
    assert len(connection.sent) == 1
