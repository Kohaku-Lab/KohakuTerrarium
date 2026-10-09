"""Unit tests for :mod:`kohakuterrarium.terrarium.store_observers`."""

from kohakuterrarium.terrarium import Terrarium
from kohakuterrarium.terrarium.store_observers import (
    ObservingSessionStores,
    observe_session_stores,
)


def test_listeners_hear_new_and_replaced_stores_but_not_reassignments():
    seen = []
    stores = ObservingSessionStores({"g0": "old"})
    stores._listeners.append(lambda gid, store: seen.append((gid, store)))
    first, second = object(), object()
    stores["g1"] = first
    stores["g1"] = first
    stores["g1"] = second
    stores["g0"] = "old"
    assert seen == [("g1", first), ("g1", second)]
    assert stores == {"g0": "old", "g1": second}


def test_a_failing_listener_does_not_stop_the_others_or_the_write():
    seen = []
    stores = ObservingSessionStores()

    def boom(gid, store):
        raise RuntimeError("listener broke")

    stores._listeners.extend([boom, lambda gid, store: seen.append(gid)])
    stores["g"] = object()
    assert seen == ["g"] and "g" in stores


async def test_observe_wraps_an_engine_once_and_keeps_its_stores():
    engine = Terrarium()
    sentinel = object()
    engine._session_stores["existing"] = sentinel
    heard_a, heard_b = [], []
    listener_a = lambda gid, store: heard_a.append(gid)  # noqa: E731
    first = observe_session_stores(engine, listener_a)
    second = observe_session_stores(engine, lambda gid, store: heard_b.append(gid))
    again = observe_session_stores(engine, listener_a)
    assert first is second is again is engine._session_stores
    assert first["existing"] is sentinel
    assert len(first._listeners) == 2
    engine._session_stores["new"] = object()
    assert (heard_a, heard_b) == (["new"], ["new"])
