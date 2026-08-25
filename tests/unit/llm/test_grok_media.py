"""Tests for authenticated xAI media HTTP requests."""

import httpx
import pytest

from kohakuterrarium.llm.grok_auth import GrokToken
from kohakuterrarium.llm.grok_media import GrokMediaClient, GrokMediaError


class TestGrokMediaClient:
    @pytest.mark.asyncio
    async def test_chat_proxy_headers_are_not_sent_to_media_api(self, monkeypatch):
        token = GrokToken(
            access_token="secret",
            source="grok-cli",
            extra_headers={
                "X-XAI-Token-Auth": "xai-grok-cli",
                "x-authenticateresponse": "authenticate-response",
                "x-grok-client-identifier": "grok-shell",
                "x-grok-client-mode": "interactive",
                "x-grok-client-version": "1.0.5",
            },
        )
        monkeypatch.setattr(
            "kohakuterrarium.llm.grok_media.GrokTokens.load_candidates",
            lambda: [token],
        )
        seen_headers = None

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal seen_headers
            seen_headers = request.headers
            return httpx.Response(200, json={"request_id": "req-1"})

        client = GrokMediaClient(transport=httpx.MockTransport(handler))

        await client.request_json(
            "POST",
            "videos/generations",
            payload={"model": "grok-imagine-video-1.5", "prompt": "cat"},
            operation="video generation",
        )

        assert seen_headers is not None
        assert seen_headers["X-XAI-Token-Auth"] == "xai-grok-cli"
        assert "x-authenticateresponse" not in seen_headers
        assert "x-grok-client-identifier" not in seen_headers
        assert "x-grok-client-mode" not in seen_headers
        assert "x-grok-client-version" not in seen_headers

    @pytest.mark.asyncio
    async def test_auth_rejection_uses_next_source_without_leaking_tokens(
        self, monkeypatch
    ):
        tokens = [
            GrokToken(access_token="first-secret", source="grok-cli"),
            GrokToken(access_token="second-secret", source="opencode"),
        ]
        monkeypatch.setattr(
            "kohakuterrarium.llm.grok_media.GrokTokens.load_candidates",
            lambda: tokens,
        )
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append((request.url.path, request.headers["Authorization"]))
            if request.headers["Authorization"] == "Bearer first-secret":
                return httpx.Response(401, json={"error": {"message": "secret"}})
            return httpx.Response(200, json={"models": [{"id": "image-model"}]})

        client = GrokMediaClient(transport=httpx.MockTransport(handler))

        response = await client.request_json(
            "GET", "images/generations", operation="image generation"
        )
        assert response.data == {"models": [{"id": "image-model"}]}
        assert [auth for _, auth in seen] == [
            "Bearer first-secret",
            "Bearer second-secret",
        ]

    @pytest.mark.asyncio
    async def test_error_is_redacted(self, monkeypatch):
        token = GrokToken(access_token="secret-canary", source="grok-cli")
        monkeypatch.setattr(
            "kohakuterrarium.llm.grok_media.GrokTokens.load_candidates",
            lambda: [token],
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                400,
                json={
                    "error": {
                        "code": "invalid_argument",
                        "message": "secret-canary invalid",
                    }
                },
                headers={"x-request-id": "req-safe-1"},
            )

        client = GrokMediaClient(transport=httpx.MockTransport(handler))

        with pytest.raises(GrokMediaError) as exc_info:
            await client.request_json(
                "POST",
                "videos/generations",
                payload={"prompt": "private prompt"},
                operation="video generation",
            )
        assert "secret-canary" not in str(exc_info.value)
        assert "HTTP 400" in str(exc_info.value)
        assert "code=invalid_argument" in str(exc_info.value)
        assert "request_id=req-safe-1" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_request_session_reuses_one_isolated_http_client(self):
        token = GrokToken(
            access_token="secret",
            source="test",
            base_url="https://chat.invalid/v1",
            media_base_url="https://api.x.ai/v1",
        )
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.path)
            return httpx.Response(200, json={"ok": True})

        client = GrokMediaClient(transport=httpx.MockTransport(handler))
        async with client.request_session() as session:
            active = session._client
            assert active is not None
            for path in ("videos/generations", "videos/req-1"):
                await session.request_json(
                    "GET", path, token=token, operation="video polling"
                )
                assert session._client is active
        assert active.is_closed
        assert seen == ["/v1/videos/generations", "/v1/videos/req-1"]

    @pytest.mark.asyncio
    async def test_download_rejects_non_xai_host(self):
        client = GrokMediaClient()

        with pytest.raises(GrokMediaError, match="media download"):
            await client.download_bytes(
                "https://attacker.example/video.mp4", max_bytes=1024
            )

    @pytest.mark.asyncio
    async def test_download_is_bounded_even_without_content_length(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, content=b"12345", headers={"content-type": "video/mp4"}
            )

        client = GrokMediaClient(transport=httpx.MockTransport(handler))

        with pytest.raises(GrokMediaError, match="media download"):
            await client.download_bytes("https://vidgen.x.ai/video.mp4", max_bytes=4)
