"""Unit tests for ``llm/codex/image/gen.py``: the ImagePart built from a Codex
``image_generation_call`` item (including the no-result → None rule).
"""

from kohakuterrarium.llm.codex.image.gen import build_image_part
from kohakuterrarium.llm.message import ImagePart


class _ImageItem:
    def __init__(self, result=None, item_id="img-1", revised_prompt=None):
        self.result = result
        self.id = item_id
        self.revised_prompt = revised_prompt


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
