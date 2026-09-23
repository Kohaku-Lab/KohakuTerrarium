import json
import time

import httpx
import pytest

from kohakuterrarium.llm import antigravity_auth as auth
from kohakuterrarium.llm.antigravity_auth import AntigravityError, BorrowedCredential
from kohakuterrarium.llm.antigravity_provider import AntigravityProvider
from kohakuterrarium.llm.base import ToolSchema


def frame(parts, reason="STOP"):
    return (
        "data: "
        + json.dumps(
            {
                "response": {
                    "candidates": [
                        {"content": {"parts": parts}, "finishReason": reason}
                    ]
                }
            }
        )
        + "\n\n"
    )


@pytest.fixture(autouse=True)
def credentials(monkeypatch):
    monkeypatch.setattr(
        auth,
        "read_sources",
        lambda: [BorrowedCredential("test-access", time.time() + 3600, "test")],
    )


@pytest.mark.asyncio
async def test_complete_and_streaming_tool_roundtrip_and_signed_history():
    requests = []

    def respond(request):
        assert request.url.host == "daily-cloudcode-pa.googleapis.com"
        assert request.headers["authorization"] == "Bearer test-access"
        data = json.loads(request.content)
        if request.url.path.endswith("loadCodeAssist"):
            return httpx.Response(200, json={"cloudaicompanionProject": "test-project"})
        requests.append(data)
        if len(requests) == 1:
            return httpx.Response(
                200,
                text=frame(
                    [
                        {
                            "functionCall": {"name": "echo", "args": {"value": "ok"}},
                            "thoughtSignature": "test-signature",
                        }
                    ]
                ),
            )
        assert (
            data["request"]["contents"][1]["parts"][0]["thoughtSignature"]
            == "test-signature"
        )
        return httpx.Response(200, text=frame([{"text": "ok"}]))

    provider = AntigravityProvider(
        "gemini-3-flash", transport=httpx.MockTransport(respond)
    )
    messages = [{"role": "user", "content": "echo"}]
    assert [
        chunk
        async for chunk in provider.chat(
            messages, stream=False, tools=[ToolSchema("echo", "echo")]
        )
    ] == [""]
    assert (
        requests[0]["request"]["tools"][0]["functionDeclarations"][0]["name"] == "echo"
    )
    call = provider.last_tool_calls[0]
    messages.extend(
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {"name": call.name, "arguments": call.arguments},
                    }
                ],
                **provider.last_assistant_extra_fields,
            },
            {"role": "tool", "tool_call_id": call.id, "content": "ok"},
        ]
    )
    assert (await provider.chat_complete(messages)).content == "ok"
    assert provider.last_tool_calls == []
    assert provider.with_model("claude-sonnet-4-6").config.model == "claude-sonnet-4-6"


@pytest.mark.asyncio
async def test_retry_before_output_but_never_after_thinking():
    attempts = []

    def respond(request):
        if request.url.path.endswith("loadCodeAssist"):
            return httpx.Response(200, json={"cloudaicompanionProject": "p"})
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(503, text="test-access must not escape")
        return httpx.Response(
            200, text=frame([{"text": "thinking", "thought": True}], reason="")
        )

    provider = AntigravityProvider(
        "gemini-3-flash",
        retry_policy={"base_delay": 0},
        transport=httpx.MockTransport(respond),
    )
    with pytest.raises(AntigravityError, match="incomplete_stream"):
        await provider.chat_complete([{"role": "user", "content": "test"}])
    assert attempts == [1, 1]
    assert provider.last_assistant_extra_fields == {}


@pytest.mark.asyncio
async def test_redirects_and_forbidden_errors_are_redacted():
    for status in (302, 403):
        provider = AntigravityProvider(
            "gemini-3-flash",
            transport=httpx.MockTransport(
                lambda req: httpx.Response(
                    status,
                    headers={"location": "https://example.org/steal"},
                    text="test-access",
                )
            ),
        )
        with pytest.raises(AntigravityError) as caught:
            await provider.chat_complete([{"role": "user", "content": "test"}])
        assert "test-access" not in str(caught.value)


@pytest.mark.asyncio
async def test_account_change_during_project_discovery_never_uses_old_project(
    monkeypatch,
):
    current = [BorrowedCredential("account-a", time.time() + 3600, "test")]
    monkeypatch.setattr(auth, "read_sources", lambda: current)
    projects = []

    def respond(request):
        if request.url.path.endswith("loadCodeAssist"):
            if request.headers["authorization"] == "Bearer account-a":
                current[0] = BorrowedCredential("account-b", time.time() + 3600, "test")
                return httpx.Response(
                    200, json={"cloudaicompanionProject": "project-a"}
                )
            return httpx.Response(200, json={"cloudaicompanionProject": "project-b"})
        body = json.loads(request.content)
        projects.append(body["project"])
        return httpx.Response(200, text=frame([{"text": "ok"}]))

    provider = AntigravityProvider(
        "gemini-3-flash", transport=httpx.MockTransport(respond)
    )
    assert (
        await provider.chat_complete([{"role": "user", "content": "test"}])
    ).content == "ok"
    assert projects == ["project-b"]
