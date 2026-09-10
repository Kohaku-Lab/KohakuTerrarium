"""Tests for xAI Images API request and ImagePart conversion."""

import base64

import pytest

from kohakuterrarium.llm import artifact_resolve
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
    @pytest.mark.parametrize("reference_kind", ["file", "artifact"])
    async def test_edit_inlines_local_image(
        self, tmp_path, monkeypatch, reference_kind
    ):
        data = b"local image"
        if reference_kind == "file":
            image = tmp_path / "image%20name.png"
            reference = image.resolve().as_uri()
        else:
            image = tmp_path / "sid.artifacts" / "source.png"
            reference = "/api/sessions/sid/artifacts/source.png"
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(data)
        monkeypatch.setattr(artifact_resolve, "_session_dir", lambda: tmp_path)
        media = _Media()
        args = {"prompt": "add a hat", "image_url": reference}

        parts = await GrokImageClient(media=media).edit(args)

        assert media.calls[0][1] == "images/edits"
        assert media.calls[0][2]["image"] == {
            "url": "data:image/png;base64," + base64.b64encode(data).decode(),
            "type": "image_url",
        }
        assert (
            parts[0].url
            == "data:image/jpeg;base64," + base64.b64encode(b"jpeg").decode()
        )
        assert args["image_url"] == reference

    @pytest.mark.parametrize(
        "reference",
        ["file://remote/share/source.png", "/api/sessions/sid/artifacts/missing.png"],
    )
    async def test_edit_rejects_unresolved_local_reference(
        self, tmp_path, monkeypatch, reference
    ):
        monkeypatch.setattr(artifact_resolve, "_session_dir", lambda: tmp_path)
        media = _Media()

        with pytest.raises(ValueError, match="image_url"):
            await GrokImageClient(media=media).edit(
                {"prompt": "edit", "image_url": reference}
            )
        assert media.calls == []

    async def test_edit_rejects_missing_local_file(self, tmp_path):
        media = _Media()
        reference = (tmp_path / "missing.png").resolve().as_uri()

        with pytest.raises(ValueError, match="image_url"):
            await GrokImageClient(media=media).edit(
                {"prompt": "edit", "image_url": reference}
            )
        assert media.calls == []

    async def test_edit_rejects_non_image_local_file(self, tmp_path):
        source = tmp_path / "source.txt"
        source.write_text("not an image", encoding="utf-8")
        media = _Media()

        with pytest.raises(ValueError, match="image_url"):
            await GrokImageClient(media=media).edit(
                {"prompt": "edit", "image_url": source.resolve().as_uri()}
            )
        assert media.calls == []

    @pytest.mark.parametrize(
        "reference", ["https://img.test/a", "data:image/png;base64,QUJD"]
    )
    async def test_edit_preserves_remote_and_inline_images(self, reference):
        media = _Media()

        await GrokImageClient(media=media).edit(
            {"prompt": "edit", "image_url": reference}
        )

        assert media.calls[0][2]["image"]["url"] == reference

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
