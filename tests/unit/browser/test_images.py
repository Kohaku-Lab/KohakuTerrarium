"""Unit tests for ``kohakuterrarium.browser.images``: PNG / JPEG size and type."""

from kohakuterrarium.browser.images import image_size, mime_type
from tests.helpers.browser_driver import jpeg_bytes, png_bytes


def test_png():
    data = png_bytes(640, 480)
    assert image_size(data) == (640, 480) and mime_type(data) == "image/png"


def test_jpeg_skips_segments_and_fill_bytes():
    app0 = b"\xff\xe0\x00\x10" + b"JFIF\x00" + b"\x00" * 9
    data = b"\xff\xd8" + app0 + b"\xff" + jpeg_bytes(300, 200)[2:]
    assert image_size(data) == (300, 200) and mime_type(data) == "image/jpeg"


def test_unknown_or_truncated():
    assert image_size(b"GIF89a") is None and mime_type(b"GIF89a") == (
        "application/octet-stream"
    )
    assert image_size(b"\xff\xd8\x00\x00\x00\x00\x00\x00\x00\x00\x00") is None
    assert image_size(b"\xff\xd8") is None
