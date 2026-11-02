"""Global pet management without running creatures."""

import asyncio
import base64
import gc
import io
import threading
import weakref
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from kohakuterrarium.api.ext import UICallContext
from kohakuterrarium.modules.ui_owner import (
    OwnerArgumentError,
    OwnerForbiddenError,
    bind_publisher,
    owner_call,
    owner_state,
)

from examples.packages.virtual_pet.kt_virtual_pet.atlas import parse_pet
from examples.packages.virtual_pet.kt_virtual_pet.settings import PetSettings
from examples.packages.virtual_pet.kt_virtual_pet.store import (
    PetLibrary,
    shared_library,
)
from examples.packages.virtual_pet.kt_virtual_pet.scope import user_root
from tests.helpers.pet_archive import pet_archive


async def test_global_management_users_pushes_and_reopen(tmp_path, monkeypatch):
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
    user = UICallContext(
        user_id=7,
        role="user",
        owner="package:virtual-pet",
        creature_id="",
        multi_user=True,
        is_admin=False,
    )
    stranger = replace(user, user_id=8)
    settings = PetSettings()
    pushes = []
    bind_publisher(
        settings, lambda event, data, user_id: pushes.append((user_id, data))
    )
    assert (await owner_state(settings, user))["pets"] == []
    assert (await owner_state(settings, stranger))["pets"] == []
    for invalid in (
        replace(user, user_id=None),
        replace(user, user_id=True),
        replace(user, user_id=0),
    ):
        with pytest.raises(OwnerForbiddenError):
            await owner_state(settings, invalid)

    def encoded(name):
        return {
            "archive": base64.b64encode(
                pet_archive(metadata={"displayName": name})
            ).decode()
        }

    first = await owner_call(settings, "import", encoded("First"), user)
    a = first["selected"]
    second = await owner_call(settings, "import", encoded("Second"), user)
    b = second["selected"]
    assert [uid for uid, state in pushes] == [7, 7]
    assert (await owner_state(settings, stranger))["pets"] == []
    for action, args in [
        ("import", {"archive": "é"}),
        ("import", {"archive": "!"}),
        ("import", {}),
        ("select", {"key": None}),
        ("unknown", {}),
    ]:
        with pytest.raises(OwnerArgumentError):
            await owner_call(settings, action, args, user)
    for action in ("sprite", "thumbnail", "select", "remove"):
        with pytest.raises(OwnerArgumentError):
            await owner_call(settings, action, {"key": a}, stranger)
    assert len(pushes) == 2
    await owner_call(settings, "select", {"key": a}, user)
    await owner_call(settings, "select", {"key": a}, user)
    assert len(pushes) == 3
    sprite = await owner_call(settings, "sprite", {"key": a}, user)
    assert (
        base64.b64decode(sprite.split(",", 1)[1])
        == (tmp_path / "users/7/virtual-pet" / f"{a}.png").read_bytes()
    )
    with patch.object(Path, "read_bytes", side_effect=OSError("disk unavailable")):
        with pytest.raises(OwnerArgumentError, match="storage"):
            await owner_call(settings, "sprite", {"key": a}, user)
    assert (await owner_state(settings, user))["selected"] == a
    thumbnail = await owner_call(settings, "thumbnail", {"key": a}, user)
    with Image.open(io.BytesIO(base64.b64decode(thumbnail.split(",", 1)[1]))) as image:
        assert image.size == (96, 104)
        assert image.getbbox() is not None
    removed = await owner_call(settings, "remove", {"key": a}, user)
    assert removed["selected"] == b
    assert not (tmp_path / "users/7/virtual-pet" / f"{a}.png").exists()
    assert (await owner_call(settings, "remove", {"key": b}, user))["selected"] is None
    await settings.aclose()
    with pytest.raises(OwnerArgumentError, match="closed"):
        await owner_state(settings, user)
    reopened = PetSettings()
    assert (await owner_state(reopened, user))["pets"] == []
    assert (await owner_state(reopened))["pets"] == []
    await reopened.aclose()


async def test_handler_handoff_keeps_one_library_and_one_push(tmp_path, monkeypatch):
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
    user = UICallContext(
        user_id=7,
        role="user",
        owner="package:virtual-pet",
        creature_id="",
        multi_user=True,
        is_admin=False,
    )
    alias = UICallContext(
        user_id=7,
        role="user",
        owner="package:pet-alias",
        creature_id="",
        multi_user=False,
        is_admin=False,
    )
    old = PetSettings()
    new = PetSettings()
    sibling = PetSettings()
    old_pushes, new_pushes, sibling_pushes = [], [], []
    bind_publisher(old, lambda event, data, user_id: old_pushes.append(data))
    bind_publisher(new, lambda event, data, user_id: new_pushes.append(data))
    bind_publisher(sibling, lambda event, data, user_id: sibling_pushes.append(data))
    started = threading.Event()
    release = threading.Event()

    def pause_parse(archive):
        if archive != base64.b64decode(first["archive"]):
            return parse_pet(archive)
        started.set()
        assert release.wait(10)
        return parse_pet(archive)

    first = {
        "archive": base64.b64encode(
            pet_archive(metadata={"displayName": "Old"})
        ).decode()
    }
    second = {
        "archive": base64.b64encode(
            pet_archive(metadata={"displayName": "New"})
        ).decode()
    }
    with patch(
        "examples.packages.virtual_pet.kt_virtual_pet.store.parse_pet",
        side_effect=pause_parse,
    ):
        pending = asyncio.create_task(owner_call(old, "import", first, user))
        try:
            assert await asyncio.to_thread(started.wait, 10)
            await old.aclose()
            loaded = await owner_state(new, alias)
            imported = await owner_call(new, "import", second, alias)
            await owner_state(sibling, user)
        finally:
            release.set()
            await asyncio.gather(pending, return_exceptions=True)
        await pending
    assert loaded["pets"] == []
    assert {pet["displayName"] for pet in imported["pets"]} == {"New"}
    assert old_pushes == []
    assert [{pet["displayName"] for pet in push["pets"]} for push in new_pushes] == [
        {"New"},
        {"Old", "New"},
    ]
    assert [
        {pet["displayName"] for pet in push["pets"]} for push in sibling_pushes
    ] == [{"Old", "New"}]
    await sibling.aclose()
    changed = await owner_call(new, "select", {"key": imported["selected"]}, alias)
    assert changed["selected"] == imported["selected"]
    assert len(new_pushes) == 3
    assert len(sibling_pushes) == 1
    await new.aclose()
    reopened = PetSettings()
    durable = await owner_state(reopened, user)
    assert {pet["displayName"] for pet in durable["pets"]} == {"Old", "New"}
    disk = await PetLibrary(user_root(user)[1]).load()
    assert {pet["displayName"] for pet in disk.snapshot()["pets"]} == {"Old", "New"}
    tracked = weakref.ref(shared_library(user_root(user)[1]))
    await reopened.aclose()
    del disk, reopened
    gc.collect()
    assert tracked() is None

    loading, loading_release = threading.Event(), threading.Event()
    original_read = PetLibrary._read

    def paused_read(current):
        loading.set()
        assert loading_release.wait(10)
        return original_read(current)

    closing = PetSettings()
    with patch.object(PetLibrary, "_read", paused_read):
        initial = asyncio.create_task(owner_state(closing, user))
        try:
            assert await asyncio.to_thread(loading.wait, 10)
            await closing.aclose()
        finally:
            loading_release.set()
            await asyncio.gather(initial, return_exceptions=True)
        with pytest.raises(OwnerArgumentError, match="closed"):
            await initial
    assert closing._libraries == {}
    assert closing._subscriptions == {}
    after_load = PetSettings()
    assert (await owner_state(after_load, user))["pets"] == durable["pets"]
    await after_load.aclose()
