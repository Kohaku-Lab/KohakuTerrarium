"""Codex pet ZIP validation."""

import hashlib
import io
import json
import stat
import struct
import tracemalloc
import zipfile
import zlib

import pytest

from examples.packages.virtual_pet.kt_virtual_pet.atlas import (
    MAX_ARCHIVE_BYTES,
    PetFormatError,
    parse_pet,
)
from tests.helpers.pet_archive import pet_archive


@pytest.mark.parametrize("version,rows", [(1, 9), (2, 11)])
@pytest.mark.parametrize("image_format", ["PNG", "WEBP"])
def test_atlas_layout_and_content_identity(version, rows, image_format):
    archive = pet_archive(
        version=version,
        prefix="wrapped/",
        image_format=image_format,
        cells=[6, 0, 0, 0, 0, 0, 0, 6] + [0] * (rows - 8),
    )
    pet = parse_pet(archive)
    assert pet.metadata["rows"] == rows
    assert pet.metadata["frames"] == {"idle": 6, "running": 6}
    assert pet.metadata["interactions"] == {}
    rich = parse_pet(pet_archive(version=version, image_format=image_format))
    assert rich.metadata["interactions"] == {
        "running-right": 8,
        "running-left": 8,
        "waving": 4,
        "jumping": 5,
    }
    legacy = {
        key: value for key, value in rich.metadata.items() if key != "interactions"
    }
    assert (
        rich.key
        == hashlib.sha256(
            json.dumps(legacy, sort_keys=True).encode() + rich.image
        ).hexdigest()
    )
    partial = parse_pet(
        pet_archive(
            version=version, cells=[6, [0, 2], 0, 2, 1, 0, 0, 6] + [0] * (rows - 8)
        )
    )
    assert partial.metadata["interactions"] == {"waving": 2, "jumping": 1}
    assert pet.mime == f"image/{image_format.lower()}"
    assert parse_pet(archive).key == pet.key
    assert (
        parse_pet(pet_archive(version=version, metadata={"displayName": "Other"})).key
        != pet.key
    )


@pytest.mark.parametrize(
    "metadata",
    [
        {"spritesheetPath": "../secret.png"},
        {"spritesheetPath": "C:/secret.png"},
        {"spritesheetPath": "\\\\host\\share"},
        {"spritesheetPath": "missing.png"},
        {"spriteVersionNumber": True},
        {"spriteVersionNumber": 3},
        {"id": ""},
        {"displayName": 2},
        {"description": "x" * 2049},
    ],
)
def test_rejects_invalid_manifest(metadata):
    with pytest.raises(PetFormatError):
        parse_pet(pet_archive(metadata=metadata))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"opaque": True},
        {"size": (192, 208)},
        {"cells": [0] * 11},
        {"extra": {"../outside.txt": "bad"}},
        {"extra": {"other/pet.json": "{}"}},
        {"extra": {f"file-{i}": "" for i in range(32)}},
    ],
)
def test_rejects_bad_layout_and_unsafe_archives(kwargs):
    with pytest.raises(PetFormatError):
        parse_pet(pet_archive(**kwargs))


@pytest.mark.parametrize(
    "content",
    [b"", b"not a zip", b"x" * (MAX_ARCHIVE_BYTES + 1)],
    ids=["empty", "not-zip", "oversized"],
)
def test_rejects_bad_archive_bytes(content):
    with pytest.raises(PetFormatError):
        parse_pet(content)


def test_corrupt_deflate_is_a_format_error():
    archive = bytearray(pet_archive())
    name_length, extra_length = struct.unpack_from("<HH", archive, 26)
    archive[30 + name_length + extra_length] = 7
    with pytest.raises(PetFormatError):
        parse_pet(bytes(archive))


def test_forged_manifest_size_cannot_expand_unbounded_memory():
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("pet.json", b"{}" + b" " * (24 * 1024 * 1024))
    forged = bytearray(archive.getvalue())
    central = forged.index(b"PK\x01\x02")
    struct.pack_into("<I", forged, central + 24, 2)
    struct.pack_into("<I", forged, central + 16, zlib.crc32(b"{}"))
    tracemalloc.start()
    try:
        with pytest.raises(PetFormatError):
            parse_pet(bytes(forged))
        _, peak = tracemalloc.get_traced_memory()
        assert peak < 1024 * 1024
    finally:
        tracemalloc.stop()


def test_rejects_duplicate_symlink_and_oversized_entries():
    for kind in ("duplicate", "symlink", "expanded", "json"):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
            if kind == "duplicate":
                z.writestr("pet.json", "{}")
                with pytest.warns(UserWarning):
                    z.writestr("pet.json", "{}")
            elif kind == "symlink":
                info = zipfile.ZipInfo("pet.json")
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                z.writestr(info, "target")
            elif kind == "expanded":
                z.writestr("bomb", b"0" * (17 * 1024 * 1024))
            else:
                z.writestr("pet.json", json.dumps([]))
        with pytest.raises(PetFormatError):
            parse_pet(archive.getvalue())
