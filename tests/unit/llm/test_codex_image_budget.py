"""Keep Codex image requests bounded while retaining saved media references."""

from copy import deepcopy
import json

import httpx
import pytest
from openai import BadRequestError
from websockets import serve

from kohakuterrarium.llm.codex_format import fix_tool_call_pairing, to_responses_input
from kohakuterrarium.llm import artifact_resolve
from kohakuterrarium.llm.codex_image_budget import (
    limit_codex_images,
    reported_image_limit,
)
from kohakuterrarium.llm.codex_provider import CodexOAuthProvider
from kohakuterrarium.llm.responses_ws import ResponsesWSSession, ResponsesWSError


def _image(index):
    return {
        "type": "image_url",
        "image_url": {"url": f"https://example.invalid/{index}"},
    }


def _images(messages):
    return [
        part
        for message in messages
        for part in message.get("content", [])
        if isinstance(part, dict) and part.get("type") == "image_url"
    ]


@pytest.mark.parametrize("count", [0, 50, 51, 65])
def test_latest_message_keeps_newest_images_and_explicit_notice(count):
    images = [_image(index) for index in range(count)]
    messages = [{"role": "user", "content": images}]
    original = deepcopy(messages)
    result = limit_codex_images(messages)
    assert _images(result) == images[-50:]
    assert messages == original
    if count <= 50:
        assert result is messages
    else:
        notice = result[0]["content"][0]["text"]
        assert f"{count - 50} image(s) omitted" in notice
        assert "were not seen" in notice and "smaller batches" in notice


def test_distributed_user_and_tool_images_preserve_text_and_call_pairing():
    messages = [
        {"role": "user", "content": [_image(index) for index in range(4)]},
        {
            "role": "assistant",
            "content": "Reading more",
            "tool_calls": [
                {"id": "read1", "function": {"name": "read", "arguments": "{}"}}
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "read1",
            "content": [
                {"type": "text", "text": "before"},
                _image(4),
                {"type": "text", "text": "between"},
                _image(5),
                {"type": "text", "text": "after"},
                *[_image(index) for index in range(6, 56)],
            ],
        },
        {"role": "user", "content": "Compare them"},
    ]
    original = deepcopy(messages)
    result = limit_codex_images(messages)
    assert _images(result) == [_image(index) for index in range(6, 56)]
    assert "4 image(s) omitted" in result[0]["content"][0]["text"]
    assert "2 image(s) omitted" in result[2]["content"][1]["text"]
    text = [p["text"] for p in result[2]["content"] if p["type"] == "text"]
    assert text[0] == "before" and text[2:] == ["between", "after"]
    wire = fix_tool_call_pairing(to_responses_input(result))
    assert [item.get("call_id") for item in wire if item.get("call_id")] == [
        "read1",
        "read1",
    ]
    assert messages == original


def test_non_input_images_and_empty_references_do_not_consume_budget():
    messages = [
        {"role": "assistant", "content": [_image(index) for index in range(100)]},
        {"role": "user", "content": [{"type": "image_url", "image_url": ""}]},
        {"role": "user", "content": [_image(index) for index in range(50)]},
    ]
    assert limit_codex_images(messages) is messages


def test_dropped_file_references_never_reach_artifact_resolution(tmp_path, monkeypatch):
    dropped = (tmp_path / "never-read.png").as_uri()
    resolved = []
    original_resolver = artifact_resolve.resolve_artifact_url

    def observe(url):
        assert url != dropped
        resolved.append(url)
        return original_resolver(url)

    monkeypatch.setattr(artifact_resolve, "resolve_artifact_url", observe)
    messages = [
        {"role": "user", "content": [{"type": "image_url", "image_url": dropped}]},
        {"role": "user", "content": [_image(index) for index in range(50)]},
    ]
    wire = to_responses_input(limit_codex_images(messages))
    assert "omitted" in wire[0]["content"][0]["text"]
    assert resolved == [f"https://example.invalid/{i}" for i in range(50)]
    assert len(wire[1]["content"]) == 50


def test_shared_responses_converter_has_no_codex_limit():
    messages = [{"role": "user", "content": [_image(i) for i in range(51)]}]
    assert len(to_responses_input(messages)[0]["content"]) == 51


def test_rotating_image_budget_prevents_stale_websocket_continuation():
    messages = [{"role": "user", "content": [_image(i) for i in range(50)]}]
    session = ResponsesWSSession(lambda: None)
    session._prev_id = "previous"
    session._sent_items = to_responses_input(limit_codex_images(messages))
    session._assistant_echo = []
    next_text = messages + [{"role": "user", "content": "continue"}]
    assert session._compute_delta(
        to_responses_input(limit_codex_images(next_text))
    ) == [{"role": "user", "content": [{"type": "input_text", "text": "continue"}]}]
    next_image = messages + [{"role": "user", "content": [_image(50)]}]
    projected = to_responses_input(limit_codex_images(next_image))
    assert session._compute_delta(projected) is None
    assert (
        sum(p["type"] == "input_image" for m in projected for p in m["content"]) == 50
    )


@pytest.fixture
async def custom_backend():
    requests = []
    failures = []

    def respond(request):
        requests.append(json.loads(request.content))
        if failures:
            return httpx.Response(400, json={"error": {"message": failures.pop(0)}})
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                'data: {"type":"response.output_text.delta","delta":"ok"}\n\n'
                'data: {"type":"response.completed","response":{"id":"ok","output":[]}}\n\n'
            ),
        )

    provider = CodexOAuthProvider(
        model="synthetic", api_key="test", base_url="https://example.invalid/v1"
    )
    await provider.ensure_authenticated()
    initial = provider._client
    provider._client = initial.with_options(
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond))
    )
    await initial.close()
    try:
        yield provider, requests, failures
    finally:
        await provider.close()


async def test_custom_backend_has_no_unverified_fifty_image_limit(custom_backend):
    provider, requests, _ = custom_backend
    messages = [{"role": "user", "content": [_image(i) for i in range(56)]}]
    assert (await provider.chat_complete(messages)).content == "ok"
    assert len(requests[0]["input"][0]["content"]) == 56


async def test_custom_backend_learns_rejected_limit_and_bounds_future_calls(
    custom_backend,
):
    provider, requests, failures = custom_backend
    failures.append("Exceeded maximum number of images (50) allowed in the request")
    messages = [{"role": "user", "content": [_image(i) for i in range(56)]}]
    original = deepcopy(messages)
    assert (await provider.chat_complete(messages)).content == "ok"
    assert len(requests) == 2
    sent = requests[-1]["input"][0]["content"]
    assert [p["image_url"] for p in sent if p["type"] == "input_image"] == [
        f"https://example.invalid/{i}" for i in range(6, 56)
    ]
    assert (await provider.chat_complete(messages)).content == "ok"
    assert requests[-1]["input"] == requests[-2]["input"]
    assert messages == original
    clone = provider.with_model("another-model")
    assert clone._request_image_limit is None
    failures.append("Exceeded maximum number of images (25) allowed in the request")
    assert (await clone.chat_complete(messages)).content == "ok"
    assert clone._request_image_limit == 25
    assert provider._request_image_limit == 50
    assert len(requests[-2]["input"][0]["content"]) == 56
    assert (
        sum(p["type"] == "input_image" for p in requests[-1]["input"][0]["content"])
        == 25
    )


async def test_unrelated_rejection_is_not_retried(custom_backend):
    provider, requests, pending = custom_backend
    pending.append("invalid field")
    messages = [{"role": "user", "content": [_image(i) for i in range(56)]}]
    with pytest.raises(BadRequestError):
        await provider.chat_complete(messages)
    assert len(requests) == 1


@pytest.mark.parametrize(
    "second",
    [
        "Exceeded maximum number of images (50) allowed in the request",
        "Exceeded maximum number of images (25) allowed in the request",
    ],
)
async def test_repeated_image_rejection_removes_the_images_and_says_so(
    custom_backend, second
):
    provider, requests, pending = custom_backend
    pending.extend(
        ["Exceeded maximum number of images (50) allowed in the request", second]
    )
    repairs = []
    provider.on_context_repair(repairs.append)
    messages = [{"role": "user", "content": [_image(i) for i in range(56)]}]
    assert (await provider.chat_complete(messages)).content == "ok"
    assert len(requests) == 3
    final = requests[-1]["input"][0]["content"]
    assert not [p for p in final if p["type"] == "input_image"]
    assert "56 image(s) removed" in final[-1]["text"]
    assert "maximum number of images" in final[-1]["text"]
    assert [(r.kind, r.scope) for r in repairs] == [("strip_media", "newest")]


async def test_endless_image_rejection_stops_once_the_ladder_is_spent(custom_backend):
    provider, requests, pending = custom_backend
    pending.extend(
        ["Exceeded maximum number of images (50) allowed in the request"] * 10
    )
    messages = [{"role": "user", "content": [_image(i) for i in range(56)]}]
    with pytest.raises(BadRequestError):
        await provider.chat_complete(messages)
    # limit learned, then newest media stripped; nothing else is left to remove.
    assert len(requests) == 3


@pytest.mark.parametrize(
    "status, mid_stream, transport, existing, reported, expected",
    [
        (400, False, False, None, 50, 50),
        (400, False, False, 50, 25, 25),
        (400, False, False, 50, 50, None),
        (400, False, False, 50, 75, None),
        (400, False, False, None, 0, None),
        (500, False, False, None, 50, None),
        (None, False, False, None, 50, None),
        (400, True, False, None, 50, None),
        (400, False, True, None, 50, None),
    ],
)
def test_only_explicit_rejected_request_can_lower_the_limit(
    status, mid_stream, transport, existing, reported, expected
):
    error = ResponsesWSError(
        f"Exceeded maximum number of images ({reported}) allowed in the request",
        status_code=status,
        mid_stream=mid_stream,
        transport=transport,
    )
    assert reported_image_limit(error, existing) == expected


def test_only_known_oauth_endpoint_defaults_to_fifty_images():
    provider = CodexOAuthProvider(model="synthetic")
    assert provider._request_image_limit == 50
    assert provider.with_model("another")._request_image_limit == 50
    assert CodexOAuthProvider(api_key="test")._request_image_limit is None
    assert (
        CodexOAuthProvider(base_url="https://example.invalid")._request_image_limit
        is None
    )


@pytest.mark.parametrize("budget, partial", [(0, False), (1, False), (1, True)])
async def test_websocket_learning_respects_output_budget_and_continuation(
    budget, partial
):
    requests = []

    async def respond(socket):
        async for payload in socket:
            requests.append(json.loads(payload))
            if len(requests) == 1:
                if partial:
                    await socket.send(
                        json.dumps(
                            {"type": "response.output_text.delta", "delta": "partial"}
                        )
                    )
                await socket.send(
                    json.dumps(
                        {
                            "type": "error",
                            "status": 400,
                            "code": "invalid_request_error",
                            "message": "Exceeded maximum number of images (50) allowed in the request",
                        }
                    )
                )
            else:
                await socket.send(
                    json.dumps(
                        {
                            "type": "response.completed",
                            "response": {
                                "id": f"response-{len(requests)}",
                                "output": [],
                            },
                        }
                    )
                )

    async with serve(respond, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        provider = CodexOAuthProvider(
            api_key="test",
            model="synthetic",
            websocket_mode=True,
            base_url=f"http://127.0.0.1:{port}/v1",
            retry_policy={"max_retries": budget},
        )
        messages = [{"role": "user", "content": [_image(i) for i in range(56)]}]
        original = deepcopy(messages)
        chunks = []
        try:
            if partial:
                with pytest.raises(ResponsesWSError):
                    async for chunk in provider.chat(messages):
                        chunks.append(chunk)
                assert len(requests) == 1
                assert chunks == ["partial"]
                assert provider._request_image_limit is None
                return
            assert (await provider.chat_complete(messages)).content == ""
            assert len(requests) == 2
            assert len(requests[0]["input"][0]["content"]) == 56
            assert provider._request_image_limit == 50
            if budget == 0:
                # No replay budget: the rejection is answered by removing the
                # refused images, a reduced request rather than a retry.
                sent = requests[-1]["input"][0]["content"]
                assert [p["type"] for p in sent] == ["input_text"]
                assert "56 image(s) removed" in sent[0]["text"]
                assert messages == original
                return
            expected = fix_tool_call_pairing(
                to_responses_input(limit_codex_images(messages))
            )
            assert requests[-1]["input"] == expected
            assert "previous_response_id" not in requests[-1]
            followup = messages + [{"role": "user", "content": "continue"}]
            await provider.chat_complete(followup)
            assert "previous_response_id" in requests[-1]
            extended = followup + [{"role": "user", "content": [_image(56)]}]
            await provider.chat_complete(extended)
            assert "previous_response_id" not in requests[-1]
            assert requests[-1]["input"] == fix_tool_call_pairing(
                to_responses_input(limit_codex_images(extended))
            )
            assert messages == original
        finally:
            await provider.close()
