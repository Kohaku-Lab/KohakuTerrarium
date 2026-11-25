"""Unit tests for ``kohakuterrarium.browser.images``: PNG / JPEG size and type."""

from kohakuterrarium.browser.images import (
    DOWNSCALE_MIN_SIDE,
    downscale,
    image_size,
    mime_type,
)
from tests.helpers.browser_driver import jpeg_bytes, noise_image, png_bytes


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


def test_downscale_fits_the_budget_as_a_smaller_jpeg():
    data = noise_image(800, 600, "jpeg", 30)
    budget = len(data) // 3
    smaller = downscale(data, budget)
    assert smaller is not None and len(smaller) <= budget
    assert mime_type(smaller) == "image/jpeg"
    width, height = image_size(smaller)
    assert width < 800 and height < 600
    assert abs(width / height - 800 / 600) < 0.02


def test_downscale_gives_up_below_the_floor_or_on_unreadable_data():
    data = noise_image(800, 600, "jpeg", 30)
    assert downscale(data, 500) is None
    assert downscale(data, len(data) // 3, min_side=590) is None
    assert downscale(png_bytes(800, 600, 100), 50) is None
    assert DOWNSCALE_MIN_SIDE >= 100
