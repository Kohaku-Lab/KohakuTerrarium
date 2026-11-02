"""Read bounded Codex V1/V2 pet archives without extracting archive paths."""

import hashlib
import io
import json
import stat
import warnings
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import PurePosixPath

from PIL import Image, UnidentifiedImageError

MAX_ARCHIVE_BYTES = 8 * 1024 * 1024
MAX_EXPANDED_BYTES = 16 * 1024 * 1024
MAX_MANIFEST_BYTES = 16 * 1024
CELL_WIDTH = 192
CELL_HEIGHT = 208


class PetFormatError(ValueError):
    """A pet archive does not satisfy the supported data-only format."""


@dataclass(frozen=True)
class Pet:
    """Validated metadata, frame layout, and original sprite bytes."""

    key: str
    metadata: dict
    image: bytes
    mime: str


def _path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if (
        not name
        or "\\" in name
        or ":" in name
        or path.is_absolute()
        or ".." in path.parts
        or "\x00" in name
    ):
        raise PetFormatError("Archive paths must be relative and stay inside the pet.")
    return path


def _text(value: object, field: str, limit: int, *, required: bool = True) -> str:
    if (
        not isinstance(value, str)
        or len(value) > limit
        or (required and not value.strip())
    ):
        raise PetFormatError(f"Invalid {field} (maximum {limit} characters).")
    return value.strip()


def _read_member(z: zipfile.ZipFile, info: zipfile.ZipInfo, limit: int) -> bytes:
    with z.open(info) as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise PetFormatError("Expanded ZIP member exceeds its size limit.")
    return data


def parse_pet(archive: bytes) -> Pet:
    """Validate a ZIP and return a portable atlas descriptor and sprite image."""
    if not archive or len(archive) > MAX_ARCHIVE_BYTES:
        raise PetFormatError("Pet ZIP must be between 1 byte and 8 MiB.")
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as z:
            entries = z.infolist()
            if (
                len(entries) > 32
                or sum(i.file_size for i in entries) > MAX_EXPANDED_BYTES
            ):
                raise PetFormatError("Pet ZIP exceeds 32 entries or 16 MiB expanded.")
            if any(
                i.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                for i in entries
            ):
                raise PetFormatError(
                    "Pet ZIP supports only stored or Deflate compression."
                )
            paths = [_path(i.filename) for i in entries]
            if len(set(paths)) != len(paths):
                raise PetFormatError("Duplicate archive paths are not supported.")
            if any(
                stat.S_ISLNK(i.external_attr >> 16) or i.flag_bits & 1 for i in entries
            ):
                raise PetFormatError(
                    "Symlinks and encrypted ZIP entries are not supported."
                )
            manifests = [i for i in entries if _path(i.filename).name == "pet.json"]
            if len(manifests) != 1:
                raise PetFormatError("Pet ZIP must contain exactly one pet.json.")
            manifest = manifests[0]
            if manifest.file_size > MAX_MANIFEST_BYTES:
                raise PetFormatError("pet.json exceeds 16 KiB.")
            data = json.loads(
                _read_member(z, manifest, MAX_MANIFEST_BYTES).decode("utf-8-sig")
            )
            if not isinstance(data, dict):
                raise PetFormatError("pet.json must be a JSON object.")
            pet_id = _text(data.get("id"), "id", 128)
            name = _text(data.get("displayName"), "displayName", 128)
            description = _text(
                data.get("description", ""), "description", 2048, required=False
            )
            version = data.get("spriteVersionNumber", 1)
            if type(version) is not int or version not in (1, 2):
                raise PetFormatError("Only spriteVersionNumber 1 and 2 are supported.")
            sprite = _path(_text(data.get("spritesheetPath"), "spritesheetPath", 256))
            target = _path(manifest.filename).parent / sprite
            files = {p: i for p, i in zip(paths, entries)}
            info = files.get(target)
            if info is None or info.is_dir() or info.file_size > MAX_ARCHIVE_BYTES:
                raise PetFormatError("Missing or oversized spritesheetPath image.")
            image = _read_member(z, info, MAX_ARCHIVE_BYTES)
        layout, mime = _image_layout(image, version)
        metadata = {
            "id": pet_id,
            "displayName": name,
            "description": description,
            "spriteVersionNumber": version,
            **layout,
        }
        digest = hashlib.sha256(
            json.dumps(metadata, sort_keys=True).encode() + image
        ).hexdigest()
        metadata["interactions"] = interaction_frames(image)
        return Pet(digest, metadata, image, mime)
    except PetFormatError:
        raise
    except (
        zipfile.BadZipFile,
        zlib.error,
        UnicodeError,
        json.JSONDecodeError,
        RuntimeError,
        NotImplementedError,
        OSError,
        ValueError,
        KeyError,
    ) as exc:
        raise PetFormatError(f"Invalid pet ZIP: {exc}") from exc


def interaction_frames(image: bytes) -> dict[str, int]:
    """Detect complete optional action prefixes without requiring those rows."""
    result = {}
    with Image.open(io.BytesIO(image)) as im:
        if im.size not in ((1536, 1872), (1536, 2288)):
            raise PetFormatError("Interaction sheet has invalid dimensions.")
        alpha = im.convert("RGBA").getchannel("A")
        visible = alpha.point(lambda a: 255 if a >= 16 else 0)
        for row, action, maximum in (
            (1, "running-right", 8),
            (2, "running-left", 8),
            (3, "waving", 4),
            (4, "jumping", 5),
        ):
            occupied = [
                col
                for col in range(maximum)
                if visible.crop(
                    (
                        col * CELL_WIDTH,
                        row * CELL_HEIGHT,
                        (col + 1) * CELL_WIDTH,
                        (row + 1) * CELL_HEIGHT,
                    )
                ).getbbox()
            ]
            if occupied and occupied == list(range(len(occupied))):
                result[action] = len(occupied)
    return result


def _image_layout(image: bytes, version: int) -> tuple[dict, str]:
    rows = 11 if version == 2 else 9
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(image)) as im:
                if im.format not in ("PNG", "WEBP") or getattr(im, "n_frames", 1) != 1:
                    raise PetFormatError("Spritesheet must be a still PNG or WebP.")
                if im.size != (8 * CELL_WIDTH, rows * CELL_HEIGHT):
                    raise PetFormatError(
                        f"V{version} requires a 1536 × {rows * CELL_HEIGHT} sheet."
                    )
                rgba = im.convert("RGBA")
                alpha = rgba.getchannel("A")
                if alpha.getextrema()[0] == 255:
                    raise PetFormatError(
                        "Spritesheet must have a transparent background."
                    )
                visible = alpha.point(lambda a: 255 if a >= 16 else 0)
                frames = []
                for row in (0, 7):
                    occupied = [
                        col
                        for col in range(8)
                        if visible.crop(
                            (
                                col * CELL_WIDTH,
                                row * CELL_HEIGHT,
                                (col + 1) * CELL_WIDTH,
                                (row + 1) * CELL_HEIGHT,
                            )
                        ).getbbox()
                    ]
                    if not occupied or occupied != list(range(len(occupied))):
                        raise PetFormatError(
                            f"Row {row} must have consecutive visible frames from column 0."
                        )
                    frames.append(len(occupied))
                return {
                    "width": im.width,
                    "height": im.height,
                    "cellWidth": CELL_WIDTH,
                    "cellHeight": CELL_HEIGHT,
                    "columns": 8,
                    "rows": rows,
                    "frames": dict(zip(("idle", "running"), frames)),
                }, Image.MIME[im.format]
    except (
        UnidentifiedImageError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise PetFormatError("Invalid or oversized sprite image.") from exc
