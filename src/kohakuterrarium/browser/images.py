"""Pixel size, MIME type and downscaling of the screenshots the driver returns."""

import io
import math
import struct
import warnings

from PIL import Image

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
DOWNSCALE_MIN_SIDE = 200
DOWNSCALE_QUALITY = 50
_JPEG_FRAMES = frozenset({0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB})


def mime_type(data: bytes) -> str:
    """``image/png`` or ``image/jpeg``; ``application/octet-stream`` for anything else."""
    if data.startswith(PNG_SIGNATURE):
        return "image/png"
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    return "application/octet-stream"


def image_size(data: bytes) -> tuple[int, int] | None:
    """``(width, height)`` read from a PNG or JPEG header, or ``None``."""
    if data.startswith(PNG_SIGNATURE) and len(data) >= 24:
        return struct.unpack(">II", data[16:24])
    if not data.startswith(b"\xff\xd8"):
        return None
    i = 2
    while i + 9 <= len(data):
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker == 0xFF:
            i += 1
            continue
        length = struct.unpack(">H", data[i + 2 : i + 4])[0]
        if marker in _JPEG_FRAMES:
            height, width = struct.unpack(">HH", data[i + 5 : i + 9])
            return width, height
        i += 2 + length
    return None


def downscale(
    data: bytes, max_bytes: int, min_side: int = DOWNSCALE_MIN_SIDE
) -> bytes | None:
    """``data`` shrunk into a JPEG of at most ``max_bytes``, keeping its aspect ratio.

    ``None`` when it cannot be read or would need a side shorter than
    ``min_side``. CPU-bound: run it in a worker thread.
    """
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as source:
                image = source.convert("RGB")
    except (OSError, ValueError, Image.DecompressionBombError, Warning):
        return None
    width, height = image.size
    scale, size = 1.0, len(data)
    while True:
        scale *= min(0.9, max(0.5, math.sqrt(max_bytes / size)))
        shape = (round(width * scale), round(height * scale))
        if min(shape) < min_side:
            return None
        out = io.BytesIO()
        image.resize(shape, Image.Resampling.LANCZOS).save(
            out, "JPEG", quality=DOWNSCALE_QUALITY
        )
        size = out.tell()
        if size <= max_bytes:
            return out.getvalue()
