import json
import threading
import time
from pathlib import Path

import httpx
import pytest

from kohakuterrarium.core.compact import CompactManager
from kohakuterrarium.llm import antigravity_auth as auth
from kohakuterrarium.llm.antigravity_auth import AntigravityError, BorrowedCredential
from kohakuterrarium.llm.antigravity_provider import AntigravityProvider
from kohakuterrarium.llm.base import ToolSchema
from kohakuterrarium.llm.media_reference import session_media_scope

pytestmark = pytest.mark.real_llm_provider

AGY_CLI_USER_AGENT = (
    "antigravity/cli/1.3.4 (aidev_client; os_type=windows; arch=amd64; "
    "cl=998275202; auth_method=consumer)"
)


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


async def test_local_image_read_runs_off_loop_with_session_context(
    tmp_path, monkeypatch
):
    session = tmp_path / "own.kohakutr"
    artifacts = session.with_suffix(".artifacts")
    artifacts.mkdir()
    (artifacts / "pic.png").write_bytes(b"PNG")
    loop_thread = threading.get_ident()
    original_read = Path.read_bytes
    reads = []

    def read(path):
        if path.suffix == ".png":
            reads.append(threading.get_ident())
            assert threading.get_ident() != loop_thread
        return original_read(path)

    def respond(request):
        if request.url.path.endswith("loadCodeAssist"):
            return httpx.Response(200, json={"cloudaicompanionProject": "test-project"})
        payload = json.loads(request.content)
        assert payload["request"]["contents"][0]["parts"] == [
            {"inlineData": {"mimeType": "image/png", "data": "UE5H"}}
        ]
        return httpx.Response(200, text=frame([{"text": "ok"}]))

    monkeypatch.setattr(Path, "read_bytes", read)
    provider = AntigravityProvider(
        "gemini-3.8-flash", transport=httpx.MockTransport(respond)
    )
    with session_media_scope(session):
        response = await provider.chat_complete(
            [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": "/api/sessions/own/artifacts/pic.png"},
                        }
                    ],
                }
            ]
        )
    assert response.content == "ok"
    assert len(reads) == 1


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
        "gemini-3.8-flash", transport=httpx.MockTransport(respond)
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
    assert provider.with_model("claude-sonnet-5-5").config.model == "claude-sonnet-5-5"


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
        "gemini-3.8-flash",
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
            "gemini-3.8-flash",
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
        "gemini-3.8-flash", transport=httpx.MockTransport(respond)
    )
    assert (
        await provider.chat_complete([{"role": "user", "content": "test"}])
    ).content == "ok"
    assert projects == ["project-b"]


# The agy 1.3.4 CLI agent catalog: every wire ID KT may request.
AGY_AGENT_MODELS = {
    *(f"gemini-4-argon-{effort}" for effort in ("low", "medium", "high")),
    *(f"gemini-3.8-flash-{effort}" for effort in ("low", "medium", "high")),
    *(f"claude-opus-5-5-{effort}" for effort in ("low", "medium", "high")),
    *(f"claude-sonnet-5-5-{effort}" for effort in ("low", "medium", "high")),
    "gpt-oss-120b-medium",
}
FLASH_BUDGETS = {"low": 1000, "medium": 4000, "high": -1}


@pytest.mark.parametrize(
    "model,effort,wire,thinking,limit",
    [
        (
            family,
            effort,
            f"{family}-{effort or 'medium'}",
            {"includeThoughts": True, "thinkingLevel": (effort or "medium").upper()},
            limit,
        )
        for family, limit in (
            ("gemini-4-argon", 65536),
            ("claude-opus-5-5", 128000),
            ("claude-sonnet-5-5", 128000),
        )
        for effort in ("", "low", "medium", "high")
    ]
    + [
        (
            "gemini-3.8-flash",
            effort,
            f"gemini-3.8-flash-{effort or 'medium'}",
            {
                "includeThoughts": True,
                "thinkingBudget": FLASH_BUDGETS[effort or "medium"],
            },
            65536,
        )
        for effort in ("", "low", "medium", "high")
    ]
    + [
        (
            "gpt-oss-120b",
            "",
            "gpt-oss-120b-medium",
            {"includeThoughts": True, "thinkingBudget": 8192},
            32768,
        ),
        (
            "gemini-4-argon-high",
            "",
            "gemini-4-argon-high",
            {"includeThoughts": True, "thinkingLevel": "HIGH"},
            65536,
        ),
    ],
)
async def test_agy_effort_reaches_wire_request(model, effort, wire, thinking, limit):
    assert wire in AGY_AGENT_MODELS
    requests = []

    def respond(request):
        if request.url.path.endswith("loadCodeAssist"):
            return httpx.Response(200, json={"cloudaicompanionProject": "test-project"})
        requests.append(json.loads(request.content))
        assert request.headers["user-agent"] == AGY_CLI_USER_AGENT
        assert "client-metadata" not in request.headers
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
        ("gemini-4-argon", "minimal"),
        ("gemini-4-argon", "xhigh"),
        ("gemini-3.8-flash", "none"),
        ("gemini-3.8-flash-low", "high"),
        ("claude-sonnet-5-5", "max"),
        ("claude-opus-5-5-low", "medium"),
        ("gpt-oss-120b", "high"),
        ("gemini-4-argon-tiered", ""),
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
    "model,replacement",
    [
        ("gemini-3-flash", "gemini-3.8-flash"),
        ("gemini-3.6-flash-low", "gemini-3.8-flash"),
        ("gemini-3.7-flash", "gemini-3.8-flash"),
        ("gemini-3.8-flash-tiered", "gemini-3.8-flash"),
        ("gemini-3.1-pro-high", "gemini-4-argon"),
        ("gemini-pro-agent", "gemini-4-argon"),
        ("claude-sonnet-4-6", "claude-sonnet-5-5"),
        ("claude-opus-4-6-thinking", "claude-opus-5-5"),
    ],
)
def test_retired_models_fail_before_authentication_naming_successor(model, replacement):
    with pytest.raises(AntigravityError) as caught:
        AntigravityProvider(model)
    assert caught.value.code == f"deprecated_model, use {replacement}"


@pytest.mark.parametrize(
    "model,limit",
    [
        ("gemini-3.8-flash", 65537),
        ("gemini-4-argon", 65537),
        ("claude-sonnet-5-5", 128001),
        ("gpt-oss-120b", 32769),
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

    def thinking(provider):
        return provider._request(messages, "scope", None, None, {})["generationConfig"][
            "thinkingConfig"
        ]

    assert thinking(AntigravityProvider("gemini-3.8-flash-low")) == {
        "includeThoughts": True,
        "thinkingBudget": 1000,
    }
    provider = AntigravityProvider("gemini-3.8-flash", reasoning_effort="low")
    sibling = provider.with_model("gemini-4-argon")
    assert thinking(sibling) == {"includeThoughts": True, "thinkingLevel": "LOW"}
    assert sibling._settings.wire_model == "gemini-4-argon-low"
    assert sibling.config.max_tokens == 65536
    claude = provider.with_model("claude-sonnet-5-5")
    assert claude.config.max_tokens == 65536
    assert claude._profile_max_context == 256000
    gpt = provider.with_model("gpt-oss-120b")
    assert gpt.reasoning_effort == "medium"
    assert gpt._profile_max_context == 80000
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
    for other in ("gemini-3.8-flash-low", "gemini-4-argon-high"):
        with pytest.raises(AntigravityError, match="history_requires_new_session"):
            await AntigravityProvider(other, transport=transport).chat_complete(
                messages
            )
    assert len(requests) == 2


@pytest.mark.parametrize(
    "target,context",
    [
        ("gemini-3.8-flash-low", 256000),
        ("gemini-4-argon-high", 256000),
        ("claude-opus-5-5", 256000),
        ("gemini-unknown", 120000),
    ],
)
def test_with_model_preserves_explicit_output_limit(target, context):
    provider = AntigravityProvider("gemini-3.8-flash", max_tokens=4096)
    sibling = provider.with_model(target)
    assert sibling.config.max_tokens == 4096
    assert sibling._profile_max_context == context


def test_with_model_rejects_a_kept_limit_below_the_target_thinking_budget():
    provider = AntigravityProvider("gemini-3.8-flash", max_tokens=4096)
    with pytest.raises(
        AntigravityError, match="max_output_tokens_must_exceed_thinking_budget"
    ):
        provider.with_model("gpt-oss-120b")
    roomy = AntigravityProvider("gemini-3.8-flash", max_tokens=16384)
    assert roomy.with_model("gpt-oss-120b")._profile_max_context == 80000


@pytest.mark.parametrize(
    "family", ["gemini-4-argon", "gemini-3.8-flash", "claude-opus-5-5", "gpt-oss-120b"]
)
@pytest.mark.parametrize("effort", ["", "low", "medium", "high"])
async def test_only_advertised_catalog_skus_are_requested(family, effort):
    requests = []

    def respond(request):
        if request.url.path.endswith("loadCodeAssist"):
            return httpx.Response(200, json={"cloudaicompanionProject": "test-project"})
        body = json.loads(request.content)
        requests.append(body)
        if body["model"] not in AGY_AGENT_MODELS:
            return httpx.Response(404)
        return httpx.Response(200, text=frame([{"text": "OK"}]))

    try:
        provider = AntigravityProvider(
            family,
            reasoning_effort=effort,
            transport=httpx.MockTransport(respond),
            retry_policy={"max_retries": 0},
        )
    except AntigravityError as exc:
        assert exc.code == "unsupported_reasoning_effort"
        assert family == "gpt-oss-120b" and effort in ("low", "high")
        return
    assert (
        await provider.chat_complete([{"role": "user", "content": "Reply exactly OK."}])
    ).content == "OK"
    assert [body["model"] for body in requests] == [provider._settings.wire_model]


@pytest.mark.parametrize(
    "model,summary_limit,summary_budget",
    [
        ("gemini-4-argon", 4000, None),
        ("gemini-3.8-flash", 4000, 2000),
        ("gemini-3.8-flash-low", 4000, 1000),
        ("gemini-3.8-flash-high", 4000, -1),
        ("claude-sonnet-5-5", 4000, None),
        ("gpt-oss-120b", 1250, 625),
    ],
)
async def test_compact_summary_uses_per_call_limit_without_mutating_profile(
    model, summary_limit, summary_budget
):
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
    assert submitted[0]["maxOutputTokens"] == summary_limit
    assert submitted[0]["thinkingConfig"].get("thinkingBudget") == summary_budget
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
    "model,limit", [("gemini-3.8-flash-low", 128), ("gpt-oss-120b", 128)]
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
    provider = AntigravityProvider("gemini-4-argon")
    args = ([{"role": "user", "content": "test"}], "scope", None, None)
    assert provider._request(*args, {"max_tokens": None}) == provider._request(
        *args, {}
    )
    with pytest.raises(AntigravityError, match="unsupported_generation_option"):
        provider._request(*args, {"max_tokens": 4096, "typo": 1})


@pytest.mark.parametrize(
    "limit,budget", [(512, 256), (129, 128), (8192, 4096), (9000, 8192)]
)
def test_per_call_numeric_budget_respects_output_cap_and_minimum(limit, budget):
    provider = AntigravityProvider("gpt-oss-120b")
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
    assert provider._settings.thinking_config["thinkingBudget"] == 8192


def test_dynamic_thinking_budget_is_never_shrunk_by_a_small_limit():
    provider = AntigravityProvider("gemini-3.8-flash-high")
    request = provider._request(
        [{"role": "user", "content": "test"}], "scope", None, None, {"max_tokens": 64}
    )
    assert request["generationConfig"] == {
        "maxOutputTokens": 64,
        "thinkingConfig": {"includeThoughts": True, "thinkingBudget": -1},
    }


@pytest.mark.asyncio
async def test_malformed_success_response_is_redacted_and_not_retried():
    attempts = []

    def respond(request):
        if request.url.path.endswith("loadCodeAssist"):
            return httpx.Response(200, json={"cloudaicompanionProject": "p"})
        attempts.append(1)
        return httpx.Response(
            200,
            text='data: {"response":{"candidates":[{"content":null}],'
            '"private":"secret-canary"}}\n\n',
        )

    provider = AntigravityProvider(
        "gemini-3.8-flash",
        retry_policy={"base_delay": 0, "max_retries": 2},
        transport=httpx.MockTransport(respond),
    )
    with pytest.raises(AntigravityError) as caught:
        await provider.chat_complete([{"role": "user", "content": "test"}])
    assert str(caught.value) == "Antigravity: malformed_response"
    assert attempts == [1]
    assert provider.last_tool_calls == []
    assert provider.last_assistant_extra_fields == {}
