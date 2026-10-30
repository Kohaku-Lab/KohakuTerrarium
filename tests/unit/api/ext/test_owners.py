"""Unit tests for ``api/ext/owners.py`` — owner resolution and the push bus."""

import asyncio
import importlib
import sys
import threading
from types import SimpleNamespace

import pytest

from kohakuterrarium.api.auth.config import AuthConfig
from kohakuterrarium.api.ext.owners import (
    BUS,
    OwnerEventBus,
    OwnerUnsupportedError,
    resolve_owner,
)
from kohakuterrarium.errors import InvalidRequestError
from kohakuterrarium.modules.ui_owner import OwnerHasNoUIError, OwnerNotLoadedError
from kohakuterrarium.packages.slots.ui import get_package_ui, invalidate_ui_cache
from kohakuterrarium.studio.identity import ui_extensions


async def test_bus_delivers_on_subscriber_loop_from_any_thread():
    bus = OwnerEventBus()
    got: list = []
    done = asyncio.Event()

    def deliver(event, data):
        got.append((event, data, threading.current_thread() is threading.main_thread()))
        done.set()

    token = bus.subscribe(("k",), deliver)
    assert bus.subscriber_count(("k",)) == 1
    thread = threading.Thread(target=bus.publish, args=(("k",), "tick", 1))
    thread.start()
    thread.join()
    await asyncio.wait_for(done.wait(), 2)
    assert got == [("tick", 1, True)]

    bus.publish(("other",), "tick", 2)
    bus.unsubscribe(("k",), token)
    bus.publish(("k",), "tick", 3)
    await asyncio.sleep(0)
    assert len(got) == 1
    assert bus.subscriber_count(("k",)) == 0
    bus.unsubscribe(("k",), token)


async def test_package_owner_is_one_instance_rebuilt_with_the_slot(ui_package):
    ui_package("alpha")
    auth = AuthConfig()
    first = await resolve_owner(None, auth, "package:alpha")
    again = await resolve_owner(None, auth, "package:alpha")
    assert first.instance is again.instance
    assert first.key == ("package", "alpha")

    pushes = []
    first.instance.ui_emit("hello", 1)
    token = BUS.subscribe(first.key, lambda event, data: pushes.append((event, data)))
    try:
        first.instance.ui_emit("hello", 2)
        await asyncio.sleep(0)
    finally:
        BUS.unsubscribe(first.key, token)
    assert pushes == [("hello", 2)]

    ui_extensions.set_enabled("alpha", False)
    with pytest.raises(OwnerNotLoadedError):
        await resolve_owner(None, auth, "package:alpha")
    ui_extensions.set_enabled("alpha", None)
    assert get_package_ui("alpha") is not None


async def test_package_owner_failures(ui_package):
    ui_package("nohandler", ui={"handler": None})
    auth = AuthConfig()
    with pytest.raises(OwnerHasNoUIError):
        await resolve_owner(None, auth, "package:nohandler")
    with pytest.raises(OwnerNotLoadedError):
        await resolve_owner(None, auth, "package:ghost")
    with pytest.raises(InvalidRequestError):
        await resolve_owner(None, auth, "gadget:x")


async def test_runtime_owner_needs_scope_and_a_local_service():
    auth = AuthConfig()
    with pytest.raises(InvalidRequestError):
        await resolve_owner(None, auth, "plugin:budget")
    with pytest.raises(OwnerUnsupportedError):
        await resolve_owner(SimpleNamespace(), auth, "plugin:budget", "s", "c")


async def test_installing_another_package_keeps_existing_handlers(
    ui_package, packages_dir
):
    module = ui_package("alpha")
    auth = AuthConfig()
    first = await resolve_owner(None, auth, "package:alpha")
    handler_cls = type(first.instance)
    ui_package("beta")
    again = await resolve_owner(None, auth, "package:alpha")
    assert again.instance is first.instance
    assert handler_cls.instances == 1
    assert sys._kt_ui_loads.count(module) == 1

    # Changing alpha's own manifest replaces (and closes) its handler.
    manifest = packages_dir / "alpha" / "kohaku.yaml"
    manifest.write_text(manifest.read_text().replace("0.3.0", "0.4.0"))
    invalidate_ui_cache()
    replaced = await resolve_owner(None, auth, "package:alpha")
    assert replaced.instance is not first.instance
    assert importlib.import_module(module).CLOSED == [id(first.instance)]


async def test_async_close_hook_is_awaited(ui_package, packages_dir):
    module = ui_package("alpha")
    first = await resolve_owner(None, AuthConfig(), "package:alpha")
    seen = []

    async def aclose():
        seen.append("aclosed")

    first.instance.aclose = aclose
    manifest = packages_dir / "alpha" / "kohaku.yaml"
    manifest.write_text(manifest.read_text().replace("0.3.0", "0.5.0"))
    invalidate_ui_cache()
    await resolve_owner(None, AuthConfig(), "package:alpha")
    assert seen == ["aclosed"]
    assert importlib.import_module(module).CLOSED == []


async def test_bus_targets_users_for_shared_keys():
    bus = OwnerEventBus()
    got = []
    key = ("package", "p")
    bus.subscribe(key, lambda e, d: got.append(("a", d)), user_id=1, multi_user=True)
    bus.subscribe(key, lambda e, d: got.append(("b", d)), user_id=2, multi_user=True)
    bus.subscribe(key, lambda e, d: got.append(("solo", d)))
    bus.publish(key, "x", "for-a", user_id=1)
    bus.publish(key, "x", "untargeted")
    await asyncio.sleep(0)
    assert sorted(got) == [("a", "for-a"), ("solo", "untargeted")]
    # Runtime keys live in one user's engine: untargeted pushes still arrive.
    runtime = (1, "plugin", "x", "c")
    bus.subscribe(
        runtime, lambda e, d: got.append(("rt", d)), user_id=1, multi_user=True
    )
    bus.publish(runtime, "x", "rt-push")
    await asyncio.sleep(0)
    assert ("rt", "rt-push") in got
