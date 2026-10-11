"""Request-wide Anthropic image limits using only synthetic in-memory fixtures."""

import base64
import io
import json
import random
from copy import deepcopy

import pytest
from PIL import Image

from kohakuterrarium.llm.anthropic_images import (
    prepare_anthropic_images,
    prepare_anthropic_request,
)


def image_block(size, *, format="PNG", mode="RGB"):
    color = (80, 120, 160, 0) if mode == "RGBA" else (80, 120, 160)
    with Image.new(mode, size, color) as image:
        if mode == "RGBA":
            image.paste((80, 120, 160, 255), (size[0] // 2, 0, *size))
        data = io.BytesIO()
        image.save(data, format=format)
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": Image.MIME[format],
            "data": base64.b64encode(data.getvalue()).decode("ascii"),
        },
    }


def remote_images(count):
    return [
        {
            "type": "image",
            "source": {"type": "url", "url": f"https://example.invalid/{i}.png"},
        }
        for i in range(count)
    ]


def decode(block):
    return Image.open(io.BytesIO(base64.b64decode(block["source"]["data"])))


@pytest.mark.parametrize("count", [20, 21])
@pytest.mark.parametrize(
    "size, expected", [((3000, 1500), (2000, 1000)), ((1500, 3000), (1000, 2000))]
)
def test_request_threshold_counts_history_and_nested_tool_results(
    count, size, expected
):
    block = image_block(size)
    block["cache_control"] = {"type": "ephemeral"}
    block["metadata"] = {"filename": "synthetic.png"}
    tool_use = {"type": "tool_use", "id": "read1", "name": "read", "input": {}}
    result = {
        "type": "tool_result",
        "tool_use_id": "read1",
        "content": [block, *remote_images(count - 11)],
    }
    messages = [
        {"role": "user", "content": remote_images(10)},
        {"role": "assistant", "content": [tool_use]},
        {"role": "user", "content": [result]},
    ]
    saved = deepcopy(messages)
    prepared = prepare_anthropic_images(messages)
    resized = prepared[2]["content"][0]["content"][0]
    with decode(resized) as image:
        assert image.size == (size if count == 20 else expected)
    assert messages == saved
    if count == 20:
        assert prepared is messages
    else:
        assert prepared is not messages
        assert prepared[0] is messages[0]
        assert prepared[1] is messages[1]
        assert resized["metadata"] == block["metadata"]
        assert resized["cache_control"] == block["cache_control"]
        assert prepared[2]["content"][0]["tool_use_id"] == "read1"


def test_remote_file_and_document_blocks_count_without_fetching():
    block = image_block((3000, 6))
    other = [
        *remote_images(7),
        *[
            {"type": "image", "source": {"type": "file", "file_id": f"file-{i}"}}
            for i in range(7)
        ],
        *[
            {"type": "document", "source": {"type": "file", "file_id": f"doc-{i}"}}
            for i in range(6)
        ],
    ]
    prepared = prepare_anthropic_images([{"role": "user", "content": [block, *other]}])
    with decode(prepared[0]["content"][0]) as image:
        assert image.size == (2000, 4)
    assert prepared[0]["content"][1:] == other


@pytest.mark.parametrize(
    "size, extras",
    [((8000, 2), 0), ((2, 8000), 0), ((2000, 2), 20), ((2, 2000), 20), ((100, 80), 20)],
)
def test_boundary_and_smaller_images_keep_exact_source_and_list_identity(size, extras):
    messages = [
        {"role": "user", "content": [image_block(size), *remote_images(extras)]}
    ]
    saved = deepcopy(messages)
    assert prepare_anthropic_images(messages) is messages
    assert messages == saved


@pytest.mark.parametrize(
    "size, expected", [((8001, 3), (8000, 3)), ((3, 8001), (3, 8000))]
)
def test_single_image_limit_is_8000(size, expected):
    messages = [{"role": "user", "content": [image_block(size)]}]
    prepared = prepare_anthropic_images(messages)
    with decode(prepared[0]["content"][0]) as image:
        assert image.size == expected


@pytest.mark.parametrize(
    "format, mode",
    [("JPEG", "RGB"), ("PNG", "RGBA"), ("GIF", "RGBA"), ("WEBP", "RGBA")],
)
def test_resize_preserves_format_and_alpha_and_sets_actual_mime(format, mode):
    block = image_block((2001, 20), format=format, mode=mode)
    block["source"]["media_type"] = "image/incorrect"
    prepared = prepare_anthropic_images(
        [{"role": "user", "content": [block, *remote_images(20)]}]
    )
    resized = prepared[0]["content"][0]
    with decode(resized) as image:
        assert image.format == format
        assert image.size == (2000, 20)
        assert resized["source"]["media_type"] == Image.MIME[format]
        if mode == "RGBA":
            rgba = image.convert("RGBA")
            assert rgba.getpixel((0, 10))[3] == 0
            assert rgba.getpixel((1999, 10))[3] == 255


def test_oversized_animation_uses_first_frame():
    with (
        Image.new("RGB", (2001, 2), "red") as first,
        Image.new("RGB", (2001, 2), "blue") as second,
    ):
        data = io.BytesIO()
        first.save(
            data,
            format="GIF",
            save_all=True,
            append_images=[second],
            duration=100,
            loop=0,
        )
    block = {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/gif",
            "data": base64.b64encode(data.getvalue()).decode("ascii"),
        },
    }
    prepared = prepare_anthropic_images(
        [{"role": "user", "content": [block, *remote_images(20)]}]
    )
    with decode(prepared[0]["content"][0]) as image:
        assert image.size == (2000, 2)
        assert not getattr(image, "is_animated", False)
        assert image.convert("RGB").getpixel((0, 0)) == (255, 0, 0)


def test_resized_jpeg_applies_exif_orientation():
    with Image.new("RGB", (3000, 1500), "red") as image:
        exif = Image.Exif()
        exif[274] = 6
        data = io.BytesIO()
        image.save(data, format="JPEG", exif=exif)
    block = {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/jpeg",
            "data": base64.b64encode(data.getvalue()).decode("ascii"),
        },
    }
    prepared = prepare_anthropic_images(
        [{"role": "user", "content": [block, *remote_images(20)]}]
    )
    with decode(prepared[0]["content"][0]) as image:
        assert image.size == (1000, 2000)
        assert image.getexif().get(274, 1) == 1


@pytest.mark.parametrize(
    "source",
    [
        None,
        {},
        {"type": "base64", "data": "not base64!"},
        {"type": "base64", "data": "bm90IGFuIGltYWdl"},
        {"type": "base64", "data": None},
    ],
)
def test_invalid_sources_pass_through_unchanged(source):
    messages = [
        {
            "role": "user",
            "content": [{"type": "image", "source": source}, *remote_images(20)],
        }
    ]
    saved = deepcopy(messages)
    assert prepare_anthropic_images(messages) is messages
    assert messages == saved


def test_image_shaped_tool_arguments_and_metadata_are_not_media_blocks():
    image = image_block((8001, 2))
    messages = [
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "write1", "name": "write", "input": image}
            ],
            "metadata": image,
        }
    ]
    assert prepare_anthropic_images(messages) is messages


def test_text_only_and_empty_messages_are_unchanged():
    for messages in ([], [{"role": "user", "content": "text"}]):
        assert prepare_anthropic_images(messages) is messages


def test_request_budget_keeps_all_individually_small_images_and_json_overhead():
    data = io.BytesIO()
    pixels = random.Random(4).randbytes(384 * 384 * 3)
    Image.frombytes("RGB", (384, 384), pixels).save(data, "PNG")
    assert len(data.getvalue()) < 512_000
    block = {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.b64encode(data.getvalue()).decode(),
        },
    }
    body = {
        "model": "test",
        "system": "界" * 10_000,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "read1",
                        "content": [
                            {"type": "text", "text": "Preserve the caption"},
                            *[deepcopy(block) for _ in range(12)],
                        ],
                    }
                ],
            }
        ],
        "tools": [{"name": "unchanged", "input_schema": {"type": "object"}}],
    }
    saved = deepcopy(body)
    result, measure = prepare_anthropic_request(body, max_bytes=1_000_000)
    assert (
        len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode())
        <= 1_000_000
    )
    content = result["messages"][0]["content"][0]["content"]
    assert len(content) == 13
    assert content[0] == saved["messages"][0]["content"][0]["content"][0]
    assert result["system"] == body["system"] and result["tools"] == body["tools"]
    assert body == saved
    for image in content[1:]:
        with decode(image) as prepared:
            assert prepared.size == (384, 384)
            assert prepared.format == "JPEG"
    assert measure.image_count == 12
    assert measure.bytes == len(
        json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode()
    )
    assert prepare_anthropic_request(body, max_bytes=1_000_000) == (result, measure)


def test_budget_preserves_uncompressible_content_and_ignores_transport_options():
    image = {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"},
    }
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "large" * 500}, image]}
    ]
    kwargs = {
        "messages": [],
        "extra_body": {"messages": messages},
        "extra_headers": {"private": object()},
        "timeout": object(),
    }
    prepared, _ = prepare_anthropic_request(kwargs, max_bytes=1000)
    assert prepared["extra_body"]["messages"] == messages
    assert prepared["extra_headers"] is kwargs["extra_headers"]
    assert prepared["timeout"] is kwargs["timeout"]
    assert kwargs["messages"] == []


def test_text_only_request_identity_and_tool_argument_images_are_preserved():
    kwargs = {
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "a",
                        "name": "store",
                        "input": image_block((8001, 2)),
                    }
                ],
            }
        ]
    }
    prepared, measure = prepare_anthropic_request(kwargs)
    assert prepared is kwargs
    assert measure.image_count == 0


def _tinted_photo(size, tint, seed):
    noise = random.Random(seed).randbytes(size[0] * size[1] * 3)
    base = Image.frombytes("RGB", size, noise)
    return Image.blend(base, Image.new("RGB", size, tint), 0.6)


def mpo_block(size):
    data = io.BytesIO()
    with _tinted_photo(size, (255, 0, 0), 1) as first:
        with _tinted_photo(size, (0, 255, 0), 2) as second:
            first.save(data, "MPO", save_all=True, append_images=[second])
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/jpeg",
            "data": base64.b64encode(data.getvalue()).decode("ascii"),
        },
    }


def test_many_image_request_resizes_camera_mpo_photo_to_jpeg():
    photo = mpo_block((4032, 3024))
    messages = [
        {"role": "user", "content": [image_block((10, 10)) for _ in range(20)]},
        {"role": "user", "content": [photo]},
    ]

    result = prepare_anthropic_images(messages)

    resized = result[1]["content"][0]
    with decode(resized) as image:
        assert image.format == "JPEG"
        assert image.size == (2000, 1500)
        red, green, _ = (sum(c) for c in zip(*image.resize((8, 8)).getdata()))
        assert red > green
    assert resized["source"]["media_type"] == "image/jpeg"


def test_request_budget_compresses_large_mpo_photo():
    body = {
        "model": "test",
        "messages": [{"role": "user", "content": [mpo_block((4032, 3024))]}],
    }

    result, _ = prepare_anthropic_request(body)

    block = result["messages"][0]["content"][0]
    with decode(block) as image:
        assert image.format == "JPEG"
        assert max(image.size) <= 2000
    assert block["source"]["media_type"] == "image/jpeg"
