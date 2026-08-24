"""Tests for the ordinary Grok image built-in tool."""

import pytest

from kohakuterrarium.builtins.tools.grok_image_gen import GrokImageGenTool
from kohakuterrarium.llm.message import ImagePart


class _Client:
    def __init__(self):
        self.generated = []

    async def generate(self, args):
        self.generated.append(args)
        return [ImagePart(url="data:image/jpeg;base64,abc")]

    async def edit(self, args):
        raise AssertionError("not expected")


class TestGrokImageGenTool:
    @pytest.mark.asyncio
    async def test_default_model_is_applied_and_multimodal_output_is_returned(self):
        client = _Client()
        tool = GrokImageGenTool(client=client)

        result = await tool.execute({"prompt": "cat"})

        assert result.success
        assert client.generated[0]["model"] == "grok-imagine-image-2.0"
        assert result.has_images()
        assert result.metadata["image_count"] == 1
        assert result.metadata["_image_artifact_subdir"] == "generated_images"
