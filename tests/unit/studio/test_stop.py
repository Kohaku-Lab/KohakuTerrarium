from pathlib import Path
import pytest

from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.sessions import lifecycle, stop
from kohakuterrarium.terrarium.service import LocalTerrariumService
from kohakuterrarium.testing.terrarium import TestTerrariumBuilder


class _CapturingHook:
    def __init__(self, store: SessionStore) -> None:
        self.store = store
        self.snapshots: list[dict] = []
        self.detached = False

    def flush(self) -> None:
        self.snapshots.append(self.store.load_meta())

    def detach(self) -> None:
        self.detached = True


async def _local_stop(tmp_path: Path, *, end_conversation: bool) -> dict:
    engine = await TestTerrariumBuilder().with_creature("alice").build()
    service = LocalTerrariumService(engine)
    creature = engine.get_creature("alice")
    session_id = creature.graph_id
    path = tmp_path / "alice.kohakutr"
    store = SessionStore(path)
    store.init_meta(session_id, "agent", "/cfg", str(tmp_path), ["alice"])
    engine._session_stores[session_id] = store
    lifecycle.stores_for(service)[session_id] = store
    lifecycle.meta_for(service)[session_id] = {
        "name": "alice",
        "resumed_from": str(path),
    }
    hook = _CapturingHook(store)

    try:
        await stop.stop_session(
            service,
            session_id,
            meta=lifecycle.meta_for(service),
            session_stores=lifecycle.stores_for(service),
            mirror_dir=tmp_path / "mirror",
            index_hooks={session_id: hook},
            end_conversation=end_conversation,
        )
        reopened = SessionStore.open_readonly(path)
        try:
            meta = reopened.load_meta()
        finally:
            reopened.close(update_status=False)
        return {"hook": hook, "meta": meta}
    finally:
        await engine.shutdown()


async def test_stop_persists_open_paused_before_index_flush(tmp_path):
    result = await _local_stop(tmp_path, end_conversation=False)

    assert result["hook"].detached is True
    assert bool(result["hook"].snapshots[-1]["conversation_open"]) is True
    assert result["hook"].snapshots[-1]["status"] == "paused"
    assert bool(result["meta"]["conversation_open"]) is True
    assert result["meta"]["status"] == "paused"


async def test_end_persists_closed_completed_before_index_flush(tmp_path):
    result = await _local_stop(tmp_path, end_conversation=True)

    assert bool(result["hook"].snapshots[-1]["conversation_open"]) is False
    assert result["hook"].snapshots[-1]["status"] == "completed"
    assert bool(result["meta"]["conversation_open"]) is False
    assert result["meta"]["status"] == "completed"


async def test_teardown_failure_rolls_back_local_marker(tmp_path):
    engine = await TestTerrariumBuilder().with_creature("alice").build()
    service = LocalTerrariumService(engine)
    creature = engine.get_creature("alice")
    session_id = creature.graph_id
    path = tmp_path / "alice.kohakutr"
    store = SessionStore(path)
    store.init_meta(session_id, "agent", "/cfg", str(tmp_path), ["alice"])
    engine._session_stores[session_id] = store
    lifecycle.stores_for(service)[session_id] = store
    lifecycle.meta_for(service)[session_id] = {"name": "alice"}
    original_remove = engine.remove_creature

    async def fail_remove(creature_id):
        raise RuntimeError("teardown failed")

    engine.remove_creature = fail_remove
    try:
        with pytest.raises(RuntimeError, match="teardown failed"):
            await stop.stop_session(
                service,
                session_id,
                meta=lifecycle.meta_for(service),
                session_stores=lifecycle.stores_for(service),
                mirror_dir=tmp_path / "mirror",
                end_conversation=True,
            )
        assert bool(store.meta["conversation_open"]) is True
        assert store.meta["status"] == "running"
        assert session_id in lifecycle.meta_for(service)
    finally:
        engine.remove_creature = original_remove
        await engine.shutdown()


async def test_teardown_failure_rolls_back_mirror_marker(tmp_path):
    engine = await TestTerrariumBuilder().with_creature("alice").build()
    service = LocalTerrariumService(engine)
    creature = engine.get_creature("alice")
    session_id = creature.graph_id
    live_path = tmp_path / "live.kohakutr"
    mirror_path = tmp_path / "mirror.kohakutr"
    live = SessionStore(live_path)
    live.init_meta(session_id, "agent", "/cfg", str(tmp_path), ["alice"])
    mirror = SessionStore(mirror_path)
    mirror.init_meta(session_id, "agent", "/cfg", str(tmp_path), ["alice"])
    mirror.close(update_status=False)
    engine._session_stores[session_id] = live
    lifecycle.stores_for(service)[session_id] = live
    lifecycle.meta_for(service)[session_id] = {
        "name": "alice",
        "resumed_from": str(mirror_path),
    }
    original_remove = engine.remove_creature

    async def fail_remove(creature_id):
        raise RuntimeError("teardown failed")

    engine.remove_creature = fail_remove
    try:
        with pytest.raises(RuntimeError, match="teardown failed"):
            await stop.stop_session(
                service,
                session_id,
                meta=lifecycle.meta_for(service),
                session_stores=lifecycle.stores_for(service),
                mirror_dir=tmp_path / "mirror",
                end_conversation=True,
            )
        reopened = SessionStore.open_readonly(mirror_path)
        try:
            assert bool(reopened.meta["conversation_open"]) is True
            assert reopened.meta["status"] == "running"
        finally:
            reopened.close(update_status=False)
        assert bool(live.meta["conversation_open"]) is True
        assert live.meta["status"] == "running"
    finally:
        engine.remove_creature = original_remove
        await engine.shutdown()


async def test_remote_lifecycle_failure_keeps_runtime_registered(tmp_path):
    class _Host:
        async def request(self, **kwargs):
            raise RuntimeError("worker marker failed")

    class _Service:
        def __init__(self):
            self._host = _Host()
            self.removed: list[str] = []

        def list_graphs(self):
            return []

        async def remove_creature(self, creature_id: str):
            self.removed.append(creature_id)

    service = _Service()
    session_id = "remote-session"
    meta = {
        session_id: {
            "on_node": "worker-1",
            "creature_id": "creature-1",
            "remote_session_path": "C:/sessions/remote.kohakutr",
        }
    }

    with pytest.raises(RuntimeError, match="worker marker failed"):
        await stop.stop_session(
            service,
            session_id,
            meta=meta,
            session_stores={},
            mirror_dir=tmp_path,
        )

    assert service.removed == []
    assert session_id in meta


async def test_remote_teardown_failure_restores_running_lifecycle(tmp_path):
    class _Host:
        def __init__(self):
            self.calls: list[dict] = []

        async def request(self, **kwargs):
            self.calls.append(kwargs)
            return {"ok": True}

    class _Service:
        def __init__(self):
            self._host = _Host()

        def list_graphs(self):
            return []

        async def remove_creature(self, _creature_id: str):
            raise RuntimeError("teardown failed")

    service = _Service()
    session_id = "remote-session"
    meta = {
        session_id: {
            "on_node": "worker-1",
            "creature_id": "creature-1",
            "remote_session_path": "C:/sessions/remote.kohakutr",
        }
    }

    with pytest.raises(RuntimeError, match="teardown failed"):
        await stop.stop_session(
            service,
            session_id,
            meta=meta,
            session_stores={},
            mirror_dir=tmp_path,
            end_conversation=True,
        )

    assert session_id in meta
    assert [
        (call["body"]["conversation_open"], call["body"]["status"])
        for call in service._host.calls
    ] == [
        (False, "completed"),
        (True, "running"),
    ]


async def test_remote_end_updates_host_mirror_before_runtime_removal(tmp_path):
    class _Host:
        async def request(self, **_kwargs):
            return {"ok": True}

    class _Service:
        def __init__(self):
            self._host = _Host()
            self.removed: list[str] = []

        def list_graphs(self):
            return []

        async def remove_creature(self, creature_id: str):
            self.removed.append(creature_id)

    service = _Service()
    session_id = "remote-session"
    mirror_dir = tmp_path / "mirror"
    mirror_dir.mkdir()
    mirror_path = mirror_dir / f"{session_id}.kohakutr"
    mirror = SessionStore(mirror_path)
    mirror.init_meta(session_id, "agent", "/cfg", str(tmp_path), ["alice"])
    mirror.close(update_status=False)
    meta = {
        session_id: {
            "on_node": "worker-1",
            "creature_id": "creature-1",
            "remote_session_path": "C:/sessions/remote.kohakutr",
        }
    }

    await stop.stop_session(
        service,
        session_id,
        meta=meta,
        session_stores={},
        mirror_dir=mirror_dir,
        end_conversation=True,
    )

    reopened = SessionStore.open_readonly(mirror_path)
    try:
        assert bool(reopened.meta["conversation_open"]) is False
        assert reopened.meta["status"] == "completed"
    finally:
        reopened.close(update_status=False)
    assert service.removed == ["creature-1"]
    assert meta == {}


async def test_remote_stop_removes_every_creature_in_the_member_graph(tmp_path):
    class _Host:
        async def request(self, **_kwargs):
            return {"ok": True}

    class _Service:
        def __init__(self):
            self._host = _Host()
            self.removed: list[str] = []

        def list_graphs(self):
            return []

        async def list_creatures(self):
            return [
                type(
                    "Info", (), {"graph_id": "remote-session", "creature_id": "one"}
                )(),
                type(
                    "Info", (), {"graph_id": "remote-session", "creature_id": "two"}
                )(),
            ]

        async def remove_creature(self, creature_id: str):
            self.removed.append(creature_id)

    service = _Service()
    meta = {
        "remote-session": {
            "on_node": "worker-1",
            "creature_id": "one",
            "remote_session_path": "C:/sessions/remote.kohakutr",
        }
    }

    await stop.stop_session(
        service,
        "remote-session",
        meta=meta,
        session_stores={},
        mirror_dir=tmp_path,
    )

    assert service.removed == ["one", "two"]
    assert meta == {}


async def test_remote_stop_uses_saved_roster_when_live_enumeration_fails(tmp_path):
    class _Host:
        async def request(self, **_kwargs):
            return {"ok": True}

    class _Service:
        def __init__(self):
            self._host = _Host()
            self.removed: list[str] = []

        def list_graphs(self):
            return []

        async def list_creatures(self):
            raise RuntimeError("worker roster unavailable")

        async def remove_creature(self, creature_id: str):
            self.removed.append(creature_id)

    service = _Service()
    meta = {
        "remote-session": {
            "on_node": "worker-1",
            "creature_id": "one",
            "creature_ids": ["one", "two"],
            "remote_session_path": "C:/sessions/remote.kohakutr",
        }
    }

    await stop.stop_session(
        service,
        "remote-session",
        meta=meta,
        session_stores={},
        mirror_dir=tmp_path,
    )

    assert service.removed == ["one", "two"]
    assert meta == {}


async def test_remote_cluster_stop_rejects_a_member_without_teardown_target(tmp_path):
    class _Host:
        def __init__(self):
            self.calls = []

        async def request(self, **kwargs):
            self.calls.append(kwargs)
            return {"ok": True}

    class _Service:
        def __init__(self):
            self._host = _Host()
            self._cluster_links = {
                frozenset({("worker-1", "primary"), ("worker-2", "peer")})
            }
            self.removed: list[str] = []

        def list_graphs(self):
            return []

        async def list_creatures(self):
            return [
                type(
                    "Info",
                    (),
                    {"graph_id": "primary", "creature_id": "one"},
                )()
            ]

        async def remove_creature(self, creature_id: str):
            self.removed.append(creature_id)

    service = _Service()
    meta = {
        "primary": {
            "on_node": "worker-1",
            "creature_id": "one",
            "remote_session_path": "C:/sessions/primary.kohakutr",
        },
        "peer": {
            "on_node": "worker-2",
            "remote_session_path": "C:/sessions/peer.kohakutr",
        },
    }

    with pytest.raises(RuntimeError, match="peer"):
        await stop.stop_session(
            service,
            "primary",
            meta=meta,
            session_stores={},
            mirror_dir=tmp_path,
        )

    assert service._host.calls == []
    assert service.removed == []
    assert set(meta) == {"primary", "peer"}


async def test_cluster_marker_failure_rolls_back_updated_members(tmp_path, monkeypatch):
    class _Host:
        def __init__(self):
            self.calls: list[dict] = []

        async def request(self, **kwargs):
            self.calls.append(kwargs)
            if kwargs["to_node"] == "worker-2" and len(self.calls) == 2:
                raise RuntimeError("second marker failed")
            return {"ok": True}

    class _Service:
        def __init__(self):
            self._host = _Host()
            self._cluster_links = {
                frozenset({("worker-1", "z-primary"), ("worker-2", "member")})
            }
            self.removed: list[str] = []

        def list_graphs(self):
            return []

        async def remove_creature(self, creature_id: str):
            self.removed.append(creature_id)

    service = _Service()
    monkeypatch.setattr(
        stop.cluster_fold,
        "cluster_groups",
        lambda _service: {"z-primary": {"z-primary", "member"}},
    )
    meta = {
        "z-primary": {
            "on_node": "worker-1",
            "creature_id": "creature-1",
            "remote_session_path": "C:/sessions/primary.kohakutr",
        },
        "member": {
            "on_node": "worker-2",
            "creature_id": "creature-2",
            "remote_session_path": "C:/sessions/member.kohakutr",
        },
    }

    with pytest.raises(RuntimeError, match="second marker failed"):
        await stop.stop_session(
            service,
            "z-primary",
            meta=meta,
            session_stores={},
            mirror_dir=tmp_path,
            end_conversation=True,
        )

    assert service.removed == []
    assert set(meta) == {"z-primary", "member"}
    assert [
        (call["to_node"], call["body"]["conversation_open"], call["body"]["status"])
        for call in service._host.calls
    ] == [
        ("worker-1", False, "completed"),
        ("worker-2", False, "completed"),
        ("worker-1", True, "running"),
    ]
