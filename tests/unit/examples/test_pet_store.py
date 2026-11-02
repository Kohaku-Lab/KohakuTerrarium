"""Shared library persistence and coordinated mutations."""

import asyncio
import base64
import json
import os
import subprocess
import sys
import textwrap
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from examples.packages.virtual_pet.kt_virtual_pet.atlas import PetFormatError, parse_pet
from examples.packages.virtual_pet.kt_virtual_pet.store import PetLibrary
from tests.helpers.pet_archive import pet_archive


async def test_shared_mutation_reopen_and_failure_atomicity(tmp_path):
    library = await PetLibrary(tmp_path).load()
    other = library
    first_archive = pet_archive()
    second_archive = pet_archive(metadata={"displayName": "Second"})
    await asyncio.gather(
        library.import_archive(first_archive), other.import_archive(second_archive)
    )
    assert len(library.snapshot()["pets"]) == 2
    assert other.snapshot() == library.snapshot()
    first = parse_pet(first_archive)
    second = parse_pet(second_archive)
    await other.select(first.key)
    assert library.snapshot()["selected"] == first.key
    assert (
        base64.b64decode((await library.sprite(first.key)).split(",", 1)[1])
        == first.image
    )
    initial = library.snapshot()
    initial["pets"][0]["frames"]["idle"] = 99
    assert library.snapshot()["pets"][0]["frames"]["idle"] != 99
    initial = library.snapshot()
    for key in (None, "../private", "a" * 64):
        with pytest.raises(PetFormatError):
            await other.select(key)
        with pytest.raises(PetFormatError):
            await other.sprite(key)
    with patch("os.replace", side_effect=OSError("disk full")):
        with pytest.raises(OSError, match="disk full"):
            await library.select(second.key)
    assert library.snapshot() == initial
    reopened = await PetLibrary(tmp_path).load()
    assert reopened.snapshot() == initial
    assert await reopened.sprite(first.key) == await library.sprite(first.key)
    index = json.loads((tmp_path / "index.json").read_text())
    assert "image" not in index["pets"][first.key]
    assert (tmp_path / f"{first.key}.png").read_bytes() == first.image
    legacy = json.loads((tmp_path / "index.json").read_text())
    for record in legacy["pets"].values():
        record["metadata"].pop("interactions", None)
    (tmp_path / "index.json").write_text(json.dumps(legacy))
    upgraded = await PetLibrary(tmp_path).load()
    assert upgraded.snapshot() == initial
    await upgraded.import_archive(first_archive)
    assert len(upgraded.snapshot()["pets"]) == 2
    assert upgraded.snapshot()["selected"] == first.key
    (tmp_path / f"{second.key}.png").unlink()
    (tmp_path / "index.json").write_text(json.dumps(legacy))
    damaged = await PetLibrary(tmp_path).load()
    assert damaged.snapshot()["selected"] == first.key
    assert (
        next(p for p in damaged.snapshot()["pets"] if p["key"] == second.key)[
            "interactions"
        ]
        == {}
    )
    foreign = await PetLibrary(tmp_path / "other-user").load()
    assert foreign.snapshot() == {"pets": [], "selected": None}
    with pytest.raises(PetFormatError):
        await foreign.sprite(first.key)
    assert list(tmp_path.glob("*.tmp")) == []


async def test_failed_import_and_cancelled_commit_preserve_durable_state(tmp_path):
    library = await PetLibrary(tmp_path).load()
    await library.import_archive(pet_archive())
    initial = library.snapshot()
    archive = pet_archive(metadata={"displayName": "New"})
    original_replace = os.replace

    def fail_index(source, target):
        if target.name == "index.json":
            raise OSError("index unavailable")
        original_replace(source, target)

    with patch("os.replace", side_effect=fail_index):
        with pytest.raises(OSError, match="index unavailable"):
            await library.import_archive(archive)
    assert library.snapshot() == initial
    assert (await PetLibrary(tmp_path).load()).snapshot() == initial
    first_key = initial["selected"]
    with patch("os.replace", side_effect=OSError("disk full")):
        with pytest.raises(OSError, match="disk full"):
            await library.remove(first_key)
    assert library.snapshot()["selected"] == first_key
    assert (tmp_path / f"{first_key}.png").exists()
    assert (await PetLibrary(tmp_path).load()).snapshot() == initial
    entered = threading.Event()
    release = threading.Event()

    def slow_index(source, target):
        if target.name == "index.json":
            entered.set()
            release.wait(10)
        original_replace(source, target)

    with patch("os.replace", side_effect=slow_index):
        task = asyncio.create_task(library.import_archive(archive))
        try:
            assert await asyncio.to_thread(entered.wait, 10)
            task.cancel()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert library.snapshot()["selected"] == parse_pet(archive).key
    assert (await PetLibrary(tmp_path).load()).snapshot() == library.snapshot()
    assert list(tmp_path.glob("*.tmp")) == []
    await library.remove(first_key)
    assert library.snapshot()["selected"] == parse_pet(archive).key
    await library.remove(parse_pet(archive).key)
    assert library.snapshot()["selected"] is None
    assert (await PetLibrary(tmp_path).load()).snapshot() == {
        "pets": [],
        "selected": None,
    }
    shutdown = textwrap.dedent("""
        import asyncio
        import os
        import tempfile
        import threading
        from pathlib import Path
        from unittest.mock import patch
        from examples.packages.virtual_pet.kt_virtual_pet.store import PetLibrary
        from tests.helpers.pet_archive import pet_archive

        async def main(root):
            library = await PetLibrary(root).load()
            entered, release = threading.Event(), threading.Event()
            original = os.replace
            def slow(source, target):
                if target.name == "index.json":
                    entered.set()
                    release.wait(10)
                original(source, target)
            with patch("os.replace", side_effect=slow):
                asyncio.create_task(library.import_archive(pet_archive()))
                assert await asyncio.to_thread(entered.wait, 10)
                threading.Timer(0.05, release.set).start()
        with tempfile.TemporaryDirectory() as root:
            asyncio.run(main(Path(root)))
    """)
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", shutdown],
        cwd=Path(__file__).parents[3],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


async def test_subscriber_failure_preserves_committed_library(tmp_path):
    library = await PetLibrary(tmp_path).load()
    failed = []
    healthy = []

    def fail(current):
        failed.append(current.snapshot()["selected"])
        raise RuntimeError("publisher unavailable")

    def record(current):
        healthy.append(current.snapshot()["selected"])

    unsubscribe = library.subscribe(fail)
    remove_healthy = library.subscribe(record)
    unsubscribe()
    unsubscribe()
    first = pet_archive(metadata={"displayName": "First"})
    await library.import_archive(first)
    assert failed == []
    assert healthy == [parse_pet(first).key]
    remove_healthy()
    remove_failure = library.subscribe(fail)
    library.subscribe(record)
    second = pet_archive(metadata={"displayName": "Second"})
    await library.import_archive(second)
    assert failed == [parse_pet(second).key]
    assert healthy == [parse_pet(first).key, parse_pet(second).key]
    assert (await PetLibrary(tmp_path).load()).snapshot() == library.snapshot()
    remove_failure()
    remove_failure()
    await library.select(parse_pet(first).key)
    assert failed == [parse_pet(second).key]
    assert healthy[-1] == parse_pet(first).key
