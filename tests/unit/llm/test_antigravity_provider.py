import json
import time

import httpx
import pytest

from kohakuterrarium.core.compact import CompactManager
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
@pytest.mark.parametrize("upstream_id", [None, "server-1", "agy_server-1"])
async def test_complete_and_streaming_tool_roundtrip_and_signed_history(upstream_id):
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
                            "functionCall": {
                                "name": "echo",
                                "args": {"value": "ok"},
                                **({"id": upstream_id} if upstream_id else {}),
                            },
                            "thoughtSignature": "test-signature",
                        }
                    ]
                ),
            )
        assert (
            data["request"]["contents"][1]["parts"][0]["thoughtSignature"]
            == "test-signature"
        )
        assert data["request"]["contents"][-1]["parts"] == [
            {
                "functionResponse": {
                    "name": "echo",
                    "response": {"output": "ok"},
                    **({"id": upstream_id} if upstream_id else {}),
                }
            }
        ]
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


@pytest.mark.parametrize(
    "model,effort,wire,thinking,limit",
    [
        (
            f"gemini-{version}-flash",
            effort,
            (
                f"gemini-{version}-flash-{effort}"
                if version == "3.6"
                else f"gemini-{version}-flash-tiered"
            ),
            {"includeThoughts": True, "thinkingLevel": effort.upper()},
            65536,
        )
        for version in ("3.6", "3.7", "3.8")
        for effort in ("low", "medium", "high")
    ]
    + [
        (
            "gemini-3.1-pro",
            "low",
            "gemini-3.1-pro-low",
            {"includeThoughts": True, "thinkingBudget": 1001},
            65535,
        ),
        (
            "gemini-3.1-pro",
            "high",
            "gemini-pro-agent",
            {"includeThoughts": True, "thinkingBudget": 10001},
            65535,
        ),
        (
            "claude-sonnet-4-6",
            "",
            "claude-sonnet-4-6",
            {"includeThoughts": True, "thinkingBudget": 1024},
            64000,
        ),
        (
            "claude-opus-4-6-thinking",
            "",
            "claude-opus-4-6-thinking",
            {"includeThoughts": True, "thinkingBudget": 1024},
            64000,
        ),
    ],
)
async def test_agy_effort_reaches_wire_request(model, effort, wire, thinking, limit):
    requests = []

    def respond(request):
        if request.url.path.endswith("loadCodeAssist"):
            return httpx.Response(200, json={"cloudaicompanionProject": "test-project"})
        requests.append(json.loads(request.content))
        assert request.headers["user-agent"].startswith("antigravity-cli/1.2.9/")
        if model.startswith("claude-"):
            assert (
                request.headers["anthropic-beta"] == "interleaved-thinking-2025-05-14"
            )
        else:
            assert "anthropic-beta" not in request.headers
        return httpx.Response(200, text=frame([{"text": "ok"}]))

    provider = AntigravityProvider(
        model, reasoning_effort=effort, transport=httpx.MockTransport(respond)
    )
    assert (
        await provider.chat_complete([{"role": "user", "content": "test"}])
    ).content == "ok"
    assert requests[0]["model"] == wire
    assert requests[0]["request"]["generationConfig"] == {
        "maxOutputTokens": limit,
        "thinkingConfig": thinking,
    }
    assert (
        provider.last_assistant_extra_fields["_kt_antigravity_content"]["model"] == wire
    )


@pytest.mark.parametrize(
    "model,effort",
    [
        ("gemini-3.1-pro", "medium"),
        ("gemini-3.8-flash", "xhigh"),
        ("gemini-3.8-flash", "none"),
        ("gemini-3.8-flash-low", "high"),
        ("claude-sonnet-4-6", "high"),
        ("claude-opus-4-6-thinking", "low"),
        ("gemini-unknown", "high"),
    ],
)
def test_invalid_effort_fails_before_authentication(model, effort):
    with pytest.raises(
        AntigravityError,
        match="unsupported_reasoning_effort|conflicting_reasoning_effort",
    ):
        AntigravityProvider(model, reasoning_effort=effort)


@pytest.mark.parametrize(
    "model,limit",
    [
        ("gemini-3.8-flash", 65537),
        ("gemini-3.1-pro", 65536),
        ("claude-sonnet-4-6", 64001),
        ("gemini-3.8-flash", 0),
        ("gemini-3.8-flash", -1),
        ("gemini-3.8-flash", True),
    ],
)
def test_invalid_output_limit_fails_before_authentication(model, limit):
    with pytest.raises(AntigravityError, match="invalid_max_output_tokens"):
        AntigravityProvider(model, max_tokens=limit)


def test_explicit_sku_defaults_and_with_model_effort():
    messages = [{"role": "user", "content": "test"}]
    low = AntigravityProvider("gemini-3.8-flash-low")
    assert (
        low._request(messages, "scope", None, None, {})["generationConfig"][
            "thinkingConfig"
        ]["thinkingLevel"]
        == "LOW"
    )
    provider = AntigravityProvider("gemini-3.8-flash", reasoning_effort="low")
    sibling = provider.with_model("gemini-3.7-flash")
    assert (
        sibling._request(messages, "scope", None, None, {})["generationConfig"][
            "thinkingConfig"
        ]["thinkingLevel"]
        == "LOW"
    )
    assert sibling.config.max_tokens == 65536
    claude = provider.with_model("claude-sonnet-4-6")
    assert claude.config.max_tokens == 64000
    assert claude._profile_max_context == 250000
    assert provider.with_model(provider.config.model) is provider


async def test_signed_round_requires_matching_wire_model():
    requests = []

    def respond(request):
        if request.url.path.endswith("loadCodeAssist"):
            return httpx.Response(200, json={"cloudaicompanionProject": "test-project"})
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(
                200,
                text=frame(
                    [
                        {
                            "functionCall": {"name": "echo", "args": {}},
                            "thoughtSignature": "opaque",
                        }
                    ]
                ),
            )
        assert (
            requests[-1]["request"]["contents"][1]["parts"][0]["thoughtSignature"]
            == "opaque"
        )
        return httpx.Response(200, text=frame([{"text": "ok"}]))

    transport = httpx.MockTransport(respond)
    high = AntigravityProvider(
        "gemini-3.8-flash", reasoning_effort="high", transport=transport
    )
    messages = [{"role": "user", "content": "test"}]
    await high.chat_complete(messages)
    call = high.last_tool_calls[0]
    messages += [
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
            **high.last_assistant_extra_fields,
        },
        {"role": "tool", "tool_call_id": call.id, "content": "ok"},
    ]
    same_wire = AntigravityProvider("gemini-3.8-flash-high", transport=transport)
    assert (await same_wire.chat_complete(messages)).content == "ok"
    low = AntigravityProvider(
        "gemini-3.8-flash", reasoning_effort="low", transport=transport
    )
    assert (await low.chat_complete(messages)).content == "ok"
    other_model = AntigravityProvider("gemini-3.7-flash", transport=transport)
    with pytest.raises(AntigravityError, match="history_requires_new_session"):
        await other_model.chat_complete(messages)
    assert len(requests) == 3


@pytest.mark.parametrize(
    "target", ["gemini-3.8-flash-low", "gemini-3.7-flash-tiered", "gemini-unknown"]
)
def test_with_model_preserves_explicit_output_limit(target):
    provider = AntigravityProvider("gemini-3.8-flash", max_tokens=4096)
    sibling = provider.with_model(target)
    assert sibling.config.max_tokens == 4096
    assert sibling._profile_max_context == (
        120000 if target == "gemini-unknown" else 1048576
    )


@pytest.mark.parametrize("version", ["3.7", "3.8"])
@pytest.mark.parametrize("effort", ["", "low", "medium", "high"])
async def test_tiered_only_catalog_routes_do_not_request_missing_effort_skus(
    version, effort
):
    requests = []
    available = {"gemini-3.7-flash-tiered", "gemini-3.8-flash-tiered"}

    def respond(request):
        if request.url.path.endswith("loadCodeAssist"):
            return httpx.Response(200, json={"cloudaicompanionProject": "test-project"})
        body = json.loads(request.content)
        requests.append(body)
        if body["model"] not in available:
            return httpx.Response(404)
        return httpx.Response(200, text=frame([{"text": "OK"}]))

    provider = AntigravityProvider(
        f"gemini-{version}-flash",
        reasoning_effort=effort,
        transport=httpx.MockTransport(respond),
        retry_policy={"max_retries": 0},
    )
    assert (
        await provider.chat_complete([{"role": "user", "content": "Reply exactly OK."}])
    ).content == "OK"
    assert len(requests) == 1
    assert (
        requests[0]["request"]["generationConfig"]["thinkingConfig"]["thinkingLevel"]
        == (effort or "high").upper()
    )


@pytest.mark.parametrize(
    "model",
    [
        "gemini-3.6-flash",
        "gemini-3.7-flash",
        "gemini-3.8-flash",
        "gemini-3.1-pro",
        "gemini-3.1-pro-low",
        "claude-sonnet-4-6",
        "claude-opus-4-6-thinking",
    ],
)
async def test_compact_summary_uses_per_call_limit_without_mutating_profile(model):
    submitted = []

    def respond(request):
        if request.url.path.endswith("loadCodeAssist"):
            return httpx.Response(200, json={"cloudaicompanionProject": "test-project"})
        submitted.append(json.loads(request.content)["request"]["generationConfig"])
        return httpx.Response(200, text=frame([{"text": "Summary retained."}]))

    provider = AntigravityProvider(model, transport=httpx.MockTransport(respond))
    original = provider._request(
        [{"role": "user", "content": "test"}], "scope", None, None, {}
    )["generationConfig"]
    manager = CompactManager()
    manager._llm = provider
    manager.config.max_tokens = provider._profile_max_context
    assert (
        await manager._summarize("User: test. Assistant: done.") == "Summary retained."
    )
    assert manager._last_summary_error == ""
    assert submitted[0]["maxOutputTokens"] == (
        3906 if model.startswith("claude-") else 4096
    )
    budget = submitted[0]["thinkingConfig"].get("thinkingBudget")
    if model == "gemini-3.1-pro":
        assert budget == 2048
    elif budget is not None:
        assert budget == (1001 if model.endswith("pro-low") else 1024)
    await provider.chat_complete([{"role": "user", "content": "test"}])
    assert submitted[1] == original


@pytest.mark.parametrize("limit", [0, -1, True, 1.5, "4096", 65537])
def test_invalid_per_call_limit_is_rejected(limit):
    provider = AntigravityProvider("gemini-3.8-flash")
    with pytest.raises(AntigravityError, match="invalid_max_output_tokens"):
        provider._request(
            [{"role": "user", "content": "test"}],
            "scope",
            None,
            None,
            {"max_tokens": limit},
        )


@pytest.mark.parametrize(
    "model,limit", [("gemini-3.1-pro", 128), ("claude-sonnet-4-6", 1024)]
)
def test_per_call_limit_must_fit_minimum_thinking_budget(model, limit):
    provider = AntigravityProvider(model)
    with pytest.raises(
        AntigravityError, match="max_output_tokens_must_exceed_thinking_budget"
    ):
        provider._request(
            [{"role": "user", "content": "test"}],
            "scope",
            None,
            None,
            {"max_tokens": limit},
        )


def test_none_override_and_unknown_generation_options():
    provider = AntigravityProvider("gemini-3.1-pro")
    args = ([{"role": "user", "content": "test"}], "scope", None, None)
    assert provider._request(*args, {"max_tokens": None}) == provider._request(
        *args, {}
    )
    with pytest.raises(AntigravityError, match="unsupported_generation_option"):
        provider._request(*args, {"max_tokens": 4096, "typo": 1})


@pytest.mark.parametrize(
    "limit,budget", [(512, 256), (129, 128), (10001, 5000), (12000, 10001)]
)
def test_per_call_numeric_budget_respects_output_cap_and_minimum(limit, budget):
    provider = AntigravityProvider("gemini-3.1-pro")
    request = provider._request(
        [{"role": "user", "content": "test"}],
        "scope",
        None,
        None,
        {"max_tokens": limit},
    )
    assert request["generationConfig"] == {
        "maxOutputTokens": limit,
        "thinkingConfig": {"includeThoughts": True, "thinkingBudget": budget},
    }
    assert provider._settings.thinking_config["thinkingBudget"] == 10001
