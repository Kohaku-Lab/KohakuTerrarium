"""Responses socket configuration validation and provider lifecycle wiring."""

import asyncio
import json

import pytest
from websockets import serve

from kohakuterrarium.llm import openai as openai_module
from kohakuterrarium.llm.codex_provider import CodexOAuthProvider
from kohakuterrarium.llm.openai import OpenAIProvider
from kohakuterrarium.llm.responses_ws import ResponsesWSError


@pytest.fixture(params=[OpenAIProvider, CodexOAuthProvider])
def provider_type(request):
    return request.param


def session_for(provider):
    if isinstance(provider, CodexOAuthProvider):
        return provider._ws_session_for_turn({})
    return provider._ws_session_for_turn()


@pytest.mark.parametrize(
    "options",
    [
        [],
        True,
        "default",
        {"proxy": None},
        {"additional_headers": {}},
        {"max_size": True},
        {"max_size": 0},
        {"max_size": 1.5},
        {"max_queue": False},
        {"max_queue": -1},
        {"max_queue": [16, 4]},
        {"write_limit": None},
        {"write_limit": True},
        {"write_limit": 0},
        {"open_timeout": True},
        {"open_timeout": 0},
        {"ping_interval": -1},
        {"ping_timeout": float("nan")},
        {"close_timeout": float("inf")},
        {"close_timeout": "10"},
        {"compression": False},
        {"compression": "gzip"},
    ],
)
async def test_invalid_options_fail_during_provider_construction(
    provider_type, options
):
    with pytest.raises(ValueError, match="websocket_connection_options"):
        provider = provider_type(
            api_key="test",
            model="test",
            extra_body={"websocket_connection_options": options},
        )
        await provider.close()


@pytest.mark.parametrize("options", [None, {}])
async def test_default_options_use_provider_timeout(
    provider_type, options, monkeypatch
):
    provider = provider_type(
        api_key="test",
        model="test",
        extra_body={"websocket_connection_options": options},
    )
    try:
        if isinstance(provider, CodexOAuthProvider):
            await provider.ensure_authenticated()
        calls = []
        monkeypatch.setattr(
            provider._client.responses,
            "connect",
            lambda **kw: calls.append(kw),
            raising=False,
        )
        session_for(provider)._connect_factory()
        timeout = 300.0 if isinstance(provider, CodexOAuthProvider) else 120.0
        assert calls[0]["websocket_connection_options"] == {
            "max_size": None,
            "open_timeout": timeout,
            "ping_timeout": timeout,
        }
        assert calls[0]["max_retries"] == 0
    finally:
        await provider.close()


@pytest.mark.parametrize(
    "options",
    [
        {
            "max_size": 2 * 1024 * 1024,
            "max_queue": 1,
            "write_limit": 8192,
            "open_timeout": 15,
            "ping_interval": 7.5,
            "ping_timeout": 90,
            "close_timeout": 2,
            "compression": "deflate",
        },
        {
            "max_size": None,
            "max_queue": None,
            "open_timeout": None,
            "ping_interval": None,
            "ping_timeout": None,
            "close_timeout": None,
            "compression": None,
        },
    ],
)
async def test_options_are_copied_and_preserved_by_clone(
    provider_type, options, monkeypatch
):
    options = dict(options)
    provider = provider_type(
        api_key="test",
        model="test",
        timeout=37,
        extra_body={"websocket_connection_options": options},
    )
    try:
        if isinstance(provider, CodexOAuthProvider):
            await provider.ensure_authenticated()
        calls = []
        monkeypatch.setattr(
            provider._client.responses,
            "connect",
            lambda **kw: calls.append(kw),
            raising=False,
        )
        expected = {
            "max_size": None,
            "open_timeout": 37.0,
            "ping_timeout": 37.0,
            **options,
        }
        session = session_for(provider)
        session._connect_factory()
        assert calls[-1]["websocket_connection_options"] == expected
        calls[-1]["websocket_connection_options"]["max_size"] = 1
        options["max_size"] = False
        session._connect_factory()
        assert calls[-1]["websocket_connection_options"] == expected
        clone = provider.with_model("other")
        session_for(clone)._connect_factory()
        assert calls[-1]["websocket_connection_options"] == expected
    finally:
        await provider.close()


async def test_options_survive_client_rebuild(provider_type, monkeypatch):
    provider = provider_type(
        api_key="before",
        model="test",
        timeout=37,
        extra_body={"websocket_connection_options": {"ping_timeout": None}},
    )
    try:
        if isinstance(provider, CodexOAuthProvider):
            await provider.ensure_authenticated()
            old_client = provider._client
            provider._api_key = "after"
            provider._rebuild_client()
            await old_client.close()
        else:
            provider._credential_provider = "test"
            monkeypatch.setattr(openai_module, "get_api_key", lambda _: "after")
            assert provider.reload_credentials()
            await asyncio.sleep(0)
        calls = []
        monkeypatch.setattr(
            provider._client.responses,
            "connect",
            lambda **kw: calls.append(kw),
            raising=False,
        )
        session_for(provider)._connect_factory()
        assert calls[0]["websocket_connection_options"] == {
            "max_size": None,
            "open_timeout": 37.0,
            "ping_timeout": None,
        }
    finally:
        await provider.close()


async def test_explicit_receive_limit_reaches_real_sdk(provider_type):
    submissions = []

    async def respond(socket):
        submissions.append(await socket.recv())
        await socket.send(
            json.dumps({"type": "response.output_text.delta", "delta": "x" * 513})
        )
        await socket.send(
            json.dumps({"type": "response.completed", "response": {"id": "test"}})
        )
        await socket.wait_closed()

    async with serve(respond, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        provider = provider_type(
            api_key="test",
            model="test",
            base_url=f"http://127.0.0.1:{port}/v1",
            websocket_mode=True,
            extra_body={"websocket_connection_options": {"max_size": 512}},
        )
        try:
            if isinstance(provider, CodexOAuthProvider):
                await provider.ensure_authenticated()
            if not hasattr(provider._client.responses, "connect"):
                pytest.skip("Installed OpenAI SDK has no Responses WebSocket support")
            with pytest.raises(ResponsesWSError, match="1009"):
                async for _ in provider.chat([{"role": "user", "content": "hello"}]):
                    pass
            assert len(submissions) == 1
        finally:
            await provider.close()
