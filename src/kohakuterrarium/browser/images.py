"""Pixel size and MIME type of the PNG and JPEG screenshots the driver returns."""

import struct

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
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
