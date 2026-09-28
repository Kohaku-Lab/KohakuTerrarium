"""Recovery contracts for Responses WebSocket requests."""

import asyncio
from copy import deepcopy

import pytest
import httpx
from openai import APIConnectionError

from kohakuterrarium.bootstrap.tools import create_tool
from kohakuterrarium.builtins.tools.image_gen import ImageGenTool
from kohakuterrarium.core.config_types import ToolConfigItem
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
    await h.run(
        [USER1, ASSIST1, USER2],
        base={
            "model": "m",
            "background": True,
            "tools": [{"type": "image_generation", "request_replay": "forbid"}],
        },
    )
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


async def test_delivered_text_blocks_replay(provider, caplog):
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
    stopped = next(
        r for r in caplog.records if r.msg == "Responses WS recovery stopped"
    )
    assert stopped.stop_reason == "content_delivered"


@pytest.mark.parametrize("kind", ["image_generation", "mcp", "future_tool", "function"])
@pytest.mark.parametrize("policy", [None, "allow", "forbid"])
async def test_explicit_policy_controls_only_server_tool_replay(
    provider, kind, policy, caplog
):
    spec = {"type": kind, "name": "test_tool"}
    if policy is not None:
        spec["request_replay"] = policy
    provider.extra_body["tools"] = [spec]
    connection = provider._client.responses.connection
    connection.scripts = [[ConnectionError("lost")], [completed("ok")]]
    if policy == "forbid" and kind != "function":
        with pytest.raises(ResponsesWSError):
            _ = [x async for x in provider.chat(MESSAGES)]
        assert len(connection.sent) == 1
        stopped = next(
            r for r in caplog.records if r.msg == "Responses WS recovery stopped"
        )
        assert stopped.stop_reason == "tool_replay_forbidden"
        assert stopped.blocked_tools == ["test_tool"]
        assert stopped.uncertain_replays == 0
    else:
        assert [x async for x in provider.chat(MESSAGES)] == []
        assert len(connection.sent) == 2
    assert all("request_replay" not in req["tools"][0] for req in connection.sent)
    assert provider.extra_body["tools"] == [spec]


@pytest.mark.parametrize("policy", [True, "maybe", None, {"allow": True}])
async def test_invalid_wire_replay_policy_fails_before_submission(provider, policy):
    provider.extra_body["tools"] = [
        {"type": "image_generation", "request_replay": policy}
    ]
    with pytest.raises(ValueError, match="request_replay"):
        _ = [x async for x in provider.chat(MESSAGES)]
    assert provider._client.responses.connection.sent == []


@pytest.mark.parametrize("policy", ["allow", "forbid"])
async def test_configured_native_tool_replay_policy(provider, policy):
    if not isinstance(provider, CodexOAuthProvider):
        return
    tool = create_tool(
        ToolConfigItem(
            name="image_gen", type="builtin", options={"request_replay": policy}
        ),
        None,
        strict=True,
    )
    tool.request_replay = "forbid" if policy == "allow" else "allow"
    connection = provider._client.responses.connection
    connection.scripts = [[ConnectionError("lost")], [completed("ok")]]
    if policy == "forbid":
        with pytest.raises(ResponsesWSError):
            _ = [x async for x in provider.chat(MESSAGES, provider_native_tools=[tool])]
        assert len(connection.sent) == 1
    else:
        assert [
            x async for x in provider.chat(MESSAGES, provider_native_tools=[tool])
        ] == []
        assert len(connection.sent) == 2
    assert "request_replay" not in connection.sent[0]["tools"][0]


async def test_image_results_are_private_until_successful_attempt(provider):
    if not isinstance(provider, CodexOAuthProvider):
        return
    connection = provider._client.responses.connection
    observed = []

    def image_event(name):
        return Ev(
            type="response.output_item.done",
            item=Ev(
                type="image_generation_call",
                id=name,
                result=name,
            ),
        )

    async def events():
        yield image_event("abandoned")
        observed.append(list(provider.last_assistant_content_parts or []))
        raise ConnectionError("lost")

    original_iter = type(connection).__aiter__

    # Observe the public result getter while the first attempt is suspended.
    class ObservedConnection(type(connection)):
        def __aiter__(self):
            if len(self.sent) == 1:
                self.scripts.pop(0)
                return events()
            return original_iter(self)

    connection.__class__ = ObservedConnection
    connection.scripts = [[], [image_event("kept"), completed("ok")]]
    # The registered native list wins over raw extra_body tools on this path.
    provider.extra_body["tools"] = [{"type": "mcp", "request_replay": "forbid"}]
    assert [
        x async for x in provider.chat(MESSAGES, provider_native_tools=[ImageGenTool()])
    ] == []
    assert observed == [[]]
    assert [part.source_name for part in provider.last_assistant_content_parts] == [
        "kept"
    ]
    assert len(connection.sent) == 2
    assert connection.sent[0]["tools"][0]["type"] == "image_generation"


async def test_http_strips_framework_tool_metadata(provider):
    provider._websocket_mode = False
    spec = {"type": "image_generation", "request_replay": "forbid"}
    provider.extra_body["tools"] = [spec]
    assert [x async for x in provider.chat(MESSAGES)] == []
    target = (
        provider._client.responses
        if isinstance(provider, CodexOAuthProvider)
        else provider._client.chat.completions
    )
    assert target.kwargs["extra_body"]["tools"] == [{"type": "image_generation"}]
    assert provider.extra_body["tools"] == [spec]


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
    provider.extra_body["tools"] = [
        {"type": "image_generation", "request_replay": "forbid"}
    ]
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
    assert all(
        call["extra_body"]["tools"] == [{"type": "image_generation"}]
        for call in creates
    )
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


async def test_tool_policy_mutation_only_affects_next_request(provider):
    spec = {"type": "image_generation", "request_replay": "allow"}
    provider.extra_body["tools"] = [spec]
    connection = provider._client.responses.connection
    connection.scripts = [
        [ConnectionError("lost")],
        [completed("ok")],
        [ConnectionError("lost again")],
    ]

    async def mutate(payload):
        if payload["phase"]:
            spec["request_replay"] = "forbid"

    with observe_model_recovery(mutate):
        assert [x async for x in provider.chat(MESSAGES)] == []
    assert len(connection.sent) == 2
    with pytest.raises(ResponsesWSError):
        _ = [x async for x in provider.chat(MESSAGES)]
    assert len(connection.sent) == 3


async def test_allowed_server_tool_still_has_one_uncertain_replay(provider, caplog):
    provider.extra_body["tools"] = [{"type": "image_generation"}]
    connection = provider._client.responses.connection
    connection.scripts = [
        [ConnectionError("lost")],
        [ConnectionError("lost again")],
        [completed("unused")],
    ]
    with pytest.raises(ResponsesWSError):
        _ = [x async for x in provider.chat(MESSAGES)]
    assert len(connection.sent) == 2
    stopped = next(
        r for r in caplog.records if r.msg == "Responses WS recovery stopped"
    )
    assert stopped.stop_reason == "uncertain_replay_exhausted"
    assert stopped.uncertain_replays == 1


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
