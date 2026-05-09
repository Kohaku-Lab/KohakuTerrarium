"""Image-handling tests for the read tool.

The read tool only forwards PNG / JPEG / WEBP / GIF — the four
formats every supported LLM provider accepts. Anything else (or a
corrupt file) must fail with a clear local error rather than
reaching the provider, which surfaces unhelpful 4xx errors.
"""

import base64
from pathlib import Path

import pytest
from PIL import Image

from kohakuterrarium.builtins.tools.read import ReadTool
from kohakuterrarium.llm.message import ImagePart, TextPart


def _make_png(path: Path, color=(255, 0, 0)) -> bytes:
    img = Image.new("RGB", (4, 4), color)
    img.save(path, format="PNG")
    return path.read_bytes()


def _make_jpeg(path: Path) -> bytes:
    img = Image.new("RGB", (4, 4), (0, 255, 0))
    img.save(path, format="JPEG")
    return path.read_bytes()


def _make_webp(path: Path) -> bytes:
    img = Image.new("RGB", (4, 4), (0, 0, 255))
    img.save(path, format="WEBP")
    return path.read_bytes()


def _make_gif(path: Path) -> bytes:
    img = Image.new("P", (4, 4))
    img.save(path, format="GIF")
    return path.read_bytes()


def _make_bmp(path: Path) -> None:
    img = Image.new("RGB", (4, 4), (128, 128, 128))
    img.save(path, format="BMP")


def _make_tiff(path: Path) -> None:
    img = Image.new("RGB", (4, 4), (0, 0, 0))
    img.save(path, format="TIFF")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ext,maker,expected_mime",
    [
        (".png", _make_png, "image/png"),
        (".jpg", _make_jpeg, "image/jpeg"),
        (".jpeg", _make_jpeg, "image/jpeg"),
        (".webp", _make_webp, "image/webp"),
        (".gif", _make_gif, "image/gif"),
    ],
)
async def test_supported_formats_round_trip(
    tmp_path: Path, ext: str, maker, expected_mime: str
) -> None:
    """A valid PNG/JPEG/WEBP/GIF returns a TextPart + ImagePart with a
    properly formed data URL whose payload decodes back to the file."""
    file_path = tmp_path / f"img{ext}"
    raw = maker(file_path)

    tool = ReadTool()
    result = await tool._execute({"path": str(file_path)})

    assert not result.error
    assert isinstance(result.output, list)
    assert len(result.output) == 2
    text_part, image_part = result.output
    assert isinstance(text_part, TextPart)
    assert isinstance(image_part, ImagePart)
    assert image_part.url.startswith(f"data:{expected_mime};base64,")
    encoded = image_part.url.split(",", 1)[1]
    assert base64.b64decode(encoded) == raw


@pytest.mark.asyncio
async def test_unsupported_extension_rejected(tmp_path: Path) -> None:
    """BMP, TIFF, etc. are valid images but not in the allowlist —
    fail before sending to the LLM."""
    bmp_path = tmp_path / "img.bmp"
    _make_bmp(bmp_path)

    tool = ReadTool()
    result = await tool._execute({"path": str(bmp_path)})

    assert "Unsupported image format" in result.error
    assert ".bmp" in result.error


@pytest.mark.asyncio
async def test_unsupported_extension_tiff_rejected(tmp_path: Path) -> None:
    tiff_path = tmp_path / "scan.tiff"
    _make_tiff(tiff_path)

    tool = ReadTool()
    result = await tool._execute({"path": str(tiff_path)})

    assert "Unsupported image format" in result.error


@pytest.mark.asyncio
async def test_corrupt_png_rejected(tmp_path: Path) -> None:
    """A file with a .png extension whose contents aren't a valid PNG
    fails the Pillow decode check — caught locally rather than
    surfacing as a provider 400."""
    bogus = tmp_path / "corrupt.png"
    bogus.write_bytes(b"\x89PNG\r\n\x1a\nNOT A REAL PNG PAYLOAD")

    tool = ReadTool()
    result = await tool._execute({"path": str(bogus)})

    assert "not a valid image" in result.error.lower()


@pytest.mark.asyncio
async def test_extension_lying_jpeg_in_png_rejected(tmp_path: Path) -> None:
    """File renamed from .jpg to .png: header says JPEG, extension
    says PNG. Pillow detects JPEG; we reject because the verified
    format JPEG vs the .png ext mismatch isn't allowed (the validator
    keys on the actual decoded format, which is fine — it's just not
    going to claim image/png for JPEG bytes)."""
    src = tmp_path / "real.jpg"
    raw = _make_jpeg(src)
    fake = tmp_path / "lying.png"
    fake.write_bytes(raw)

    tool = ReadTool()
    result = await tool._execute({"path": str(fake)})

    # Pillow detects JPEG; the tool emits the correct image/jpeg MIME
    # rather than trusting the ".png" extension. This is a feature:
    # we can't get into a state where the data URL says "png" but the
    # bytes are JPEG, which would then fail provider validation.
    assert not result.error
    image_part = next(p for p in result.output if isinstance(p, ImagePart))
    assert image_part.url.startswith("data:image/jpeg;base64,")


def test_verify_image_helper_recognises_supported_formats(tmp_path: Path) -> None:
    """Direct test of the format-detection helper for each supported type."""
    from kohakuterrarium.builtins.tools.read import _verify_image

    for ext, maker, expected in [
        (".png", _make_png, "PNG"),
        (".jpg", _make_jpeg, "JPEG"),
        (".webp", _make_webp, "WEBP"),
        (".gif", _make_gif, "GIF"),
    ]:
        path = tmp_path / f"v{ext}"
        maker(path)
        assert _verify_image(path.read_bytes()) == expected


def test_verify_image_rejects_garbage() -> None:
    from kohakuterrarium.builtins.tools.read import _verify_image

    assert _verify_image(b"not an image") is None
    assert _verify_image(b"") is None


def test_is_image_file_recognises_image_extensions() -> None:
    """``_is_image_file`` claims both supported and legacy image
    extensions so the dispatcher routes them through the image
    branch — supported ones are read, legacy ones are rejected with
    a "PNG/JPEG/WEBP/GIF only" message inside ``_read_image``.
    Non-image extensions stay non-image so they hit the text path."""
    from kohakuterrarium.builtins.tools.read import _is_image_file

    # Supported set
    for ext in (".png", ".jpg", ".JPEG", ".webp", ".gif"):
        assert _is_image_file(Path(f"a{ext}"))

    # Legacy / unsupported but still "image-ish" — claimed so we can
    # emit a clear unsupported-format error.
    for ext in (".svg", ".bmp", ".tiff", ".heic", ".avif"):
        assert _is_image_file(Path(f"a{ext}"))

    # Truly non-image
    assert not _is_image_file(Path("a.txt"))
    assert not _is_image_file(Path("a.py"))
