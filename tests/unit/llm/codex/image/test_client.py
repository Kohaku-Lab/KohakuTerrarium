"""Unit tests for ``kohakuterrarium.llm.codex.image.client``."""

import json
import time

import httpx
import pytest

from kohakuterrarium.errors import LLMNotConfiguredError
from kohakuterrarium.llm.codex.auth import CodexTokens
from kohakuterrarium.llm.codex.image.client import (
    CodexImageClient,
    CodexImageError,
    image_request,
)

OK = {
    "data": [{"b64_json": "QUJD", "generation_id": "g-1"}],
    "output_format": "png",
}


def _login(access="tok", expires=None):
    CodexTokens(
        access_token=access,
        refresh_token="r",
        expires_at=time.time() + 3600 if expires is None else expires,
    ).save()


def _client(handler):
    return CodexImageClient(
        base_url="https://codex.test/api/codex",
        transport=httpx.MockTransport(handler),
    )


class TestImageRequest:
    def test_generation_body_matches_the_codex_extension(self):
        assert image_request("a frog") == {
            "prompt": "a frog",
            "background": "opaque",
            "model": "gpt-image-2",
            "quality": "auto",
            "size": "auto",
        }

    def test_edit_body_carries_inline_images_first_and_a_transparent_background(self):
        body = image_request(
            "cut out",
            transparent_background=True,
            images=["data:image/png;base64,AA"],
        )
        assert list(body)[0] == "images"
        assert body["images"] == [{"image_url": "data:image/png;base64,AA"}]
        assert body["background"] == "transparent"

    def test_gif_options_are_flat_siblings_of_the_format(self):
        body = image_request("loop", gif={"frame_count": 4, "frame_duration_ms": 100})
        assert body["output_format"] == "gif"
        assert (body["frame_count"], body["frame_duration_ms"]) == (4, 100)
        assert "frame_count" not in image_request("x", gif={})

    @pytest.mark.parametrize(
        "gif", [{"frame_count": 1}, {"frame_count": 17}, {"frame_duration_ms": 10}]
    )
    def test_out_of_range_gif_options_are_rejected(self, gif):
        with pytest.raises(ValueError):
            image_request("x", gif=gif)


class TestRequests:
    @pytest.mark.asyncio
    async def test_generate_posts_to_the_images_endpoint_with_the_login(self):
        _login()
        seen = []

        def handler(request):
            seen.append(request)
            return httpx.Response(200, json=OK)

        parts = await _client(handler).generate("a frog", transparent_background=True)

        request = seen[0]
        assert str(request.url) == "https://codex.test/api/codex/images/generations"
        assert request.headers["authorization"] == "Bearer tok"
        assert json.loads(request.content)["background"] == "transparent"
        assert parts[0].url == "data:image/png;base64,QUJD"
        assert parts[0].source_name == "g-1"

    @pytest.mark.asyncio
    async def test_edit_posts_images_to_the_edits_endpoint(self):
        _login()
        seen = []

        def handler(request):
            seen.append(request)
            return httpx.Response(200, json={**OK, "output_format": "gif"})

        parts = await _client(handler).edit("hat", ["data:image/png;base64,AA"])

        assert seen[0].url.path.endswith("/images/edits")
        assert json.loads(seen[0].content)["images"][0]["image_url"].startswith("data:")
        assert parts[0].url.startswith("data:image/gif;base64,")

    @pytest.mark.asyncio
    async def test_edit_takes_one_to_five_images(self):
        _login()
        client = _client(lambda request: httpx.Response(200, json=OK))
        for images in ([], ["u"] * 6):
            with pytest.raises(ValueError, match="1 to 5"):
                await client.edit("x", images)

    @pytest.mark.asyncio
    async def test_an_empty_prompt_is_rejected_before_any_request(self):
        _login()

        def handler(request):
            raise AssertionError("no request expected")

        with pytest.raises(ValueError, match="prompt"):
            await _client(handler).generate("  ")

    @pytest.mark.asyncio
    async def test_a_backend_refusal_surfaces_its_message_and_request_id(self):
        _login()

        def handler(request):
            return httpx.Response(
                400,
                json={"detail": "GIF output is not enabled for this request."},
                headers={"x-codex-imagegen-request-id": "req-9"},
            )

        with pytest.raises(CodexImageError) as info:
            await _client(handler).generate("x", gif={})
        assert "GIF output is not enabled" in str(info.value)
        assert "req-9" in str(info.value)

    @pytest.mark.asyncio
    async def test_a_response_without_an_image_is_an_error(self):
        _login()
        client = _client(lambda request: httpx.Response(200, json={"data": []}))
        with pytest.raises(CodexImageError, match="no image"):
            await client.generate("x")

    @pytest.mark.asyncio
    async def test_a_401_refreshes_the_login_and_retries_once(self, monkeypatch):
        _login(access="old")
        calls = []

        async def refresh(tokens):
            fresh = CodexTokens("new", "r", time.time() + 3600)
            fresh.save()
            return fresh

        monkeypatch.setattr(
            "kohakuterrarium.llm.codex.image.client.refresh_tokens", refresh
        )

        def handler(request):
            calls.append(request.headers["authorization"])
            return httpx.Response(401 if len(calls) == 1 else 200, json=OK)

        await _client(handler).generate("x")
        assert calls == ["Bearer old", "Bearer new"]

    @pytest.mark.asyncio
    async def test_no_login_names_the_command_to_run(self):
        with pytest.raises(LLMNotConfiguredError, match="kt login codex"):
            await _client(lambda request: httpx.Response(200, json=OK)).generate("x")


class TestCustomBackend:
    @pytest.mark.asyncio
    async def test_an_api_key_and_base_url_replace_the_chatgpt_login(self):
        seen = []

        def handler(request):
            seen.append(request)
            return httpx.Response(200, json=OK)

        client = CodexImageClient(
            base_url="https://corp.example/codex/",
            api_key="sk-private",
            transport=httpx.MockTransport(handler),
        )
        await client.generate("x")

        assert str(seen[0].url) == "https://corp.example/codex/images/generations"
        assert seen[0].headers["authorization"] == "Bearer sk-private"

    @pytest.mark.asyncio
    async def test_a_rejected_api_key_is_reported_without_a_refresh(self):
        client = CodexImageClient(
            base_url="https://corp.example/codex",
            api_key="bad",
            transport=httpx.MockTransport(lambda request: httpx.Response(401, json={})),
        )
        with pytest.raises(CodexImageError, match="HTTP 401"):
            await client.generate("x")
