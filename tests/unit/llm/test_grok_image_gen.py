"""Tests for xAI Images API request and ImagePart conversion."""

import base64

import pytest

from kohakuterrarium.llm.grok_auth import GrokToken
from kohakuterrarium.llm.grok_image_gen import GrokImageClient
from kohakuterrarium.llm.grok_media import GrokMediaResponse


class _Media:
    def __init__(self):
        self.calls = []

    async def request_json(self, method, path, *, payload, operation):
        self.calls.append((method, path, payload, operation))
        return GrokMediaResponse(
            data={
                "data": [
                    {
                        "b64_json": base64.b64encode(b"jpeg").decode(),
                        "mime_type": "image/jpeg",
                        "revised_prompt": "revised",
                    }
                ]
            },
            token=GrokToken(access_token="secret", source="test"),
        )


class TestGrokImageClient:
    @pytest.mark.asyncio
    async def test_generation_uses_images_endpoint_and_returns_image_part(self):
        media = _Media()
        client = GrokImageClient(media=media)

        parts = await client.generate(
            {
                "prompt": "a cat",
                "model": "grok-imagine-image-2.0",
                "resolution": "2k",
                "quality": "medium",
            }
        )

        _, path, payload, _ = media.calls[0]
        assert path == "images/generations"
        assert payload["model"] == "grok-imagine-image-2.0"
        assert payload["response_format"] == "b64_json"
        assert payload["resolution"] == "2k"
        assert parts[0].url.startswith("data:image/jpeg;base64,")
        assert parts[0].revised_prompt == "revised"

    @pytest.mark.asyncio
    async def test_edit_uses_dedicated_endpoint(self):
        media = _Media()
        client = GrokImageClient(media=media)

        await client.edit({"prompt": "add a hat", "image_url": "https://img.test/a"})

        _, path, payload, _ = media.calls[0]
        assert path == "images/edits"
        assert payload["image"] == {
            "url": "https://img.test/a",
            "type": "image_url",
        }

    @pytest.mark.asyncio
    async def test_invalid_count_is_rejected_before_request(self):
        media = _Media()
        client = GrokImageClient(media=media)

        with pytest.raises(ValueError, match="between 1 and 10"):
            await client.generate({"prompt": "x", "n": 11})
        assert media.calls == []
