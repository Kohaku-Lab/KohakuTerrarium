"""Unit tests for ``llm/codex_image_gen.py`` — image-gen translation.

Behavior-first: assert the exact wire-format spec produced for an
image_gen tool, the None return for non-image-gen tools, the minimal
fallback path, and the ImagePart built from a Codex
``image_generation_call`` item (including the no-result → None rule).
"""

from kohakuterrarium.llm.codex_image_gen import (
    build_image_part,
    translate_image_gen_tool,
)
from kohakuterrarium.llm.message import ImagePart


class _ToolWithOptions:
    tool_name = "image_gen"

    @staticmethod
    def provider_native_option_schema():
        return {
            "output_format": {"type": "string"},
            "size": {"type": "string"},
        }

    def provider_native_options(self):
        return {"output_format": "webp", "size": "1024x1024"}


class _BareTool:
    """An image_gen tool subclassed without the options helper."""

    tool_name = "image_gen"


class _OtherTool:
    tool_name = "bash"


class _ImageItem:
    def __init__(self, result=None, item_id="img-1", revised_prompt=None):
        self.result = result
        self.id = item_id
        self.revised_prompt = revised_prompt


class TestTranslateImageGenTool:
    def test_non_image_gen_tool_returns_none(self):
        assert translate_image_gen_tool(_OtherTool()) is None

    def test_image_gen_tool_options_merged_into_spec(self):
        spec = translate_image_gen_tool(_ToolWithOptions())
        assert spec["type"] == "image_generation"
        assert spec["output_format"] == "webp"
        assert spec["size"] == "1024x1024"

    def test_bare_tool_falls_back_to_png(self):
        spec = translate_image_gen_tool(_BareTool())
        assert spec == {"type": "image_generation", "output_format": "png"}

    def test_tool_without_tool_name_attr_uses_name_attr(self):
        class _NameOnly:
            name = "bash"

        assert translate_image_gen_tool(_NameOnly()) is None


class TestBuildImagePart:
    def test_no_result_returns_none(self):
        assert build_image_part(_ImageItem(result=None), "png") is None

    def test_result_becomes_data_url_image_part(self):
        part = build_image_part(_ImageItem(result="QUJD"), "png")
        assert isinstance(part, ImagePart)
        assert part.url == "data:image/png;base64,QUJD"
        assert part.source_type == "image_gen"
        assert part.source_name == "img-1"

    def test_webp_output_format_sets_webp_mime(self):
        part = build_image_part(_ImageItem(result="X"), "webp")
        assert part.url.startswith("data:image/webp;base64,")

    def test_unknown_format_defaults_to_png_mime(self):
        part = build_image_part(_ImageItem(result="X"), "tiff")
        assert part.url.startswith("data:image/png;base64,")

    def test_revised_prompt_attached_dynamically(self):
        part = build_image_part(
            _ImageItem(result="X", revised_prompt="a better prompt"), "png"
        )
        assert getattr(part, "revised_prompt") == "a better prompt"

    def test_no_revised_prompt_means_no_attribute(self):
        part = build_image_part(_ImageItem(result="X"), "png")
        assert not hasattr(part, "revised_prompt")
