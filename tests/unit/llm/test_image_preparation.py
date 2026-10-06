"""Router image preparation parity using generated in-memory pixels only."""

import base64
import copy
import io
import random

import pytest
from PIL import Image, PngImagePlugin

from kohakuterrarium.llm.image_preparation import ImagePreparer


def encoded(image, format="PNG", **kwargs):
    stream = io.BytesIO()
    image.save(stream, format, **kwargs)
    mime = "image/jpeg" if format == "JPEG" else "image/" + format.lower()
    return "data:" + mime + ";base64," + base64.b64encode(stream.getvalue()).decode()


def decoded(url):
    return Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1])))


def request(url):
    return {
        "messages": [
            {
                "role": "user",
                "content": [{"type": "image_url", "image_url": {"url": url}}],
            }
        ]
    }


def output(body):
    return body["messages"][0]["content"][0]["image_url"]["url"]


def test_large_photo_shrinks_with_stable_cached_copy_and_original_preserved():
    pixels = random.Random(42).randbytes(960 * 1280 * 3)
    original = encoded(Image.frombytes("RGB", (960, 1280), pixels))
    body = request(original)
    saved = copy.deepcopy(body)
    preparer = ImagePreparer()
    first, stats = preparer.prepare(body)
    second, cached = preparer.prepare(body)
    assert body == saved
    assert first == second
    assert len(output(first)) < len(original) / 3
    assert decoded(output(first)).size == (960, 1280)
    assert stats.changed_count == 1 and stats.cache_hits == 0
    assert cached.cache_hits == 1
    assert stats.prepared_bytes <= 512_000


def test_small_text_and_transparency_are_not_reencoded():
    for mode, color in [("RGB", "white"), ("RGBA", (0, 60, 255, 64))]:
        original = encoded(Image.new(mode, (96, 64), color))
        body = request(original)
        result, stats = ImagePreparer().prepare(body)
        assert output(result) == original
        assert stats.changed_count == 0


def test_resizing_preserves_aspect_ratio_and_alpha_without_enlarging():
    original = encoded(Image.new("RGBA", (2400, 1200), (40, 70, 90, 80)))
    result, _ = ImagePreparer().prepare(request(original))
    image = decoded(output(result))
    assert image.size == (2000, 1000)
    assert image.mode == "RGBA"
    assert image.getpixel((100, 100))[3] == 80


def test_changed_exif_image_keeps_display_orientation():
    image = Image.new("RGB", (2400, 1200), "red")
    exif = Image.Exif()
    exif[274] = 6
    result, _ = ImagePreparer().prepare(request(encoded(image, "JPEG", exif=exif)))
    prepared = decoded(output(result))
    assert prepared.size == (1000, 2000)
    assert prepared.getexif().get(274, 1) == 1


def test_small_png_stays_lossless_when_only_dimensions_need_reduction():
    gradient = Image.linear_gradient("L")
    image = Image.merge(
        "RGB",
        [
            gradient.resize((2400, 1200)),
            gradient.transpose(Image.Transpose.ROTATE_90).resize((2400, 1200)),
            Image.new("L", (2400, 1200), 80),
        ],
    )
    original = encoded(image)
    assert len(original) < 512_000
    result, _ = ImagePreparer().prepare(request(original))
    prepared = decoded(output(result))
    assert prepared.size == (2000, 1000)
    assert prepared.format == "PNG"


@pytest.mark.parametrize(
    "value",
    [
        "https://example.invalid/image.png",
        "data:image/png;base64,not valid!",
        "data:image/svg+xml;base64,PHN2Zy8+",
        "data:image/png;base64,YQ==",
    ],
)
def test_unsupported_or_invalid_images_are_preserved(value):
    body = request(value)
    result, _ = ImagePreparer().prepare(body)
    assert result == body


def test_animated_image_is_not_flattened():
    original = encoded(
        Image.new("RGB", (32, 32), "red"),
        "GIF",
        save_all=True,
        append_images=[Image.new("RGB", (32, 32), "blue")],
        duration=100,
        loop=0,
    )
    result, _ = ImagePreparer().prepare(request(original))
    assert output(result) == original


def test_metadata_is_removed_without_mutating_text_or_tool_results():
    info = PngImagePlugin.PngInfo()
    info.add_text("padding", "x" * 600_000)
    original = encoded(Image.new("RGB", (64, 64), "blue"), pnginfo=info)
    body = {
        "model": "unchanged",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "lookup",
                        "content": [
                            {"type": "text", "text": "exact tool result"},
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": original.split(",", 1)[1],
                                },
                            },
                        ],
                    }
                ],
            }
        ],
        "extra": {"image": original},
    }
    before = copy.deepcopy(body)
    result, stats = ImagePreparer().prepare(body)
    assert body == before and result["extra"] == body["extra"]
    tool = result["messages"][0]["content"][0]
    assert tool["tool_use_id"] == "lookup"
    assert tool["content"][0] == {"type": "text", "text": "exact tool result"}
    assert len(tool["content"][1]["source"]["data"]) < 1000
    assert stats.changed_count == 1


def test_responses_images_share_preparation_without_changing_history_or_extensions():
    info = PngImagePlugin.PngInfo()
    info.add_text("padding", "x" * 600_000)
    original = encoded(Image.new("RGB", (64, 64), "blue"), pnginfo=info)
    image = {"type": "input_image", "image_url": original, "detail": "high"}
    body = {
        "type": "response.create",
        "model": "fly/test",
        "previous_response_id": "resp_previous",
        "input": [
            {
                "role": "user",
                "content": [{"type": "input_text", "text": "exact"}, image],
            },
            {
                "type": "reasoning",
                "summary": [],
                "content": [{"text": "retain reasoning"}],
            },
            {
                "type": "function_call",
                "call_id": "call1",
                "name": "lookup",
                "arguments": "{}",
            },
            {"type": "function_call_output", "call_id": "call1", "output": [image]},
        ],
        "extension": {"image_url": original},
    }
    before = copy.deepcopy(body)
    preparer = ImagePreparer()
    prepared, stats = preparer.prepare(body)
    image_url = prepared["input"][0]["content"][1]["image_url"]
    assert len(image_url) < 1000
    assert decoded(image_url).getpixel((0, 0)) == (0, 0, 255)
    expected = copy.deepcopy(before)
    expected["input"][0]["content"][1]["image_url"] = image_url
    expected["input"][3]["output"][0]["image_url"] = image_url
    assert prepared == expected and body == before
    assert stats.image_count == stats.changed_count == 2
    assert stats.cache_hits == 1
    repeated, cached = preparer.prepare(body)
    assert repeated == prepared and cached.cache_hits == 2
    assert preparer.prepare({"input": "plain text"})[0] == {"input": "plain text"}


def test_cache_evicts_by_entries_and_byte_budget():
    preparer = ImagePreparer(max_entries=1, max_cache_bytes=2048)
    bodies = [
        request(encoded(Image.new("RGB", (32, 32), color))) for color in ["red", "blue"]
    ]
    preparer.prepare(bodies[0])
    preparer.prepare(bodies[1])
    _, stats = preparer.prepare(bodies[0])
    assert stats.cache_hits == 0
    assert preparer.cache_bytes <= 2048
    assert len(preparer.entries) <= 1


def test_prepared_image_larger_than_cache_budget_is_not_retained():
    info = PngImagePlugin.PngInfo()
    info.add_text("padding", "x" * 600_000)
    body = request(encoded(Image.new("RGB", (96, 64), "blue"), pnginfo=info))
    preparer = ImagePreparer(max_cache_bytes=64)
    first, _ = preparer.prepare(body)
    again, stats = preparer.prepare(body)
    assert first == again
    assert output(first) != output(body)
    assert stats.cache_hits == 0
    assert preparer.cache_bytes == 0


def test_tighter_target_is_cached_separately_and_does_not_change_dimensions():
    pixels = random.Random(5).randbytes(384 * 384 * 3)
    original = encoded(Image.frombytes("RGB", (384, 384), pixels))
    body = request(original)
    preparer = ImagePreparer()
    normal, _ = preparer.prepare(body)
    assert output(normal) == original
    smaller, first = preparer.prepare(body, max_image_bytes=60_000)
    assert first.prepared_bytes <= 60_000
    assert decoded(output(smaller)).size == (384, 384)
    repeated, cached = preparer.prepare(body, max_image_bytes=60_000)
    assert repeated == smaller and cached.cache_hits == 1
    assert preparer.prepare(body)[0] == normal


def test_invalid_target_fails_without_touching_images():
    with pytest.raises(ValueError, match="must be positive"):
        ImagePreparer().prepare({}, max_image_bytes=0)


def test_tight_budget_compresses_grayscale_instead_of_forcing_larger_png():
    original = encoded(
        Image.frombytes("L", (384, 384), random.Random(6).randbytes(384 * 384)),
        "JPEG",
        quality=95,
    )
    result, stats = ImagePreparer().prepare(request(original), max_image_bytes=40_000)
    assert stats.prepared_bytes <= 40_000
    assert decoded(output(result)).size == (384, 384)
    assert decoded(output(result)).format == "JPEG"


def test_default_budget_does_not_expand_grayscale_jpeg_to_oversized_png():
    image = Image.frombytes("L", (1600, 1600), random.Random(8).randbytes(1600 * 1600))
    original = encoded(image, "JPEG", quality=95)
    assert len(base64.b64decode(original.split(",", 1)[1])) < 5_000_000
    prepared, stats = ImagePreparer().prepare(request(original))
    assert stats.prepared_bytes < stats.original_bytes < 5_000_000
    assert decoded(output(prepared)).format == "JPEG"
    assert decoded(output(prepared)).size == image.size


def test_high_bit_depth_png_is_preserved_without_rgb_clipping():
    pixels = (
        b"".join((1000 + x % 1000).to_bytes(2, "little") for x in range(2400)) * 1000
    )
    original = encoded(Image.frombytes("I;16", (2400, 1000), pixels))
    prepared, _ = ImagePreparer().prepare(request(original))
    assert output(prepared) == original
    assert decoded(output(prepared)).getextrema() == (1000, 1999)


@pytest.mark.parametrize("size", [(512, 512), (2400, 1200)])
def test_transparent_webp_does_not_expand_to_a_larger_png(size):
    rgb = Image.frombytes(
        "RGB", size, random.Random(9).randbytes(size[0] * size[1] * 3)
    )
    image = rgb.convert("RGBA")
    image.putalpha(100)
    original = encoded(image, "WEBP", quality=80)
    prepared, stats = ImagePreparer().prepare(request(original), max_image_bytes=10_000)
    assert stats.prepared_bytes <= stats.original_bytes
    assert decoded(output(prepared)).getchannel("A").getextrema() == (100, 100)


def _tinted_photo(size, tint, seed):
    noise = random.Random(seed).randbytes(size[0] * size[1] * 3)
    base = Image.frombytes("RGB", size, noise)
    return Image.blend(base, Image.new("RGB", size, tint), 0.6)


def test_camera_mpo_photo_shrinks_to_its_primary_jpeg_frame():
    original = io.BytesIO()
    with _tinted_photo((4032, 3024), (255, 0, 0), 1) as first:
        with _tinted_photo((4032, 3024), (0, 255, 0), 2) as second:
            first.save(original, "MPO", save_all=True, append_images=[second])

    data, mime = ImagePreparer._encode(original.getvalue())

    assert mime == "image/jpeg"
    with Image.open(io.BytesIO(data)) as prepared:
        assert prepared.format == "JPEG"
        assert prepared.size == (2000, 1500)
        red, green, _ = (sum(c) for c in zip(*prepared.resize((8, 8)).getdata()))
        assert red > green
