"""Unit tests for :mod:`kohakuterrarium.studio.nodes`."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from kohakuterrarium.studio import nodes as nodes_mod
from kohakuterrarium.terrarium.service import LocalTerrariumService

# ── _Pending ────────────────────────────────────────────────


class TestPending:
    def test_raises_on_attr_access(self):
        p = nodes_mod._Pending("files", "Unit B")
        with pytest.raises(NotImplementedError, match="Unit B"):
            p.read("x")


# ── _Deploy ─────────────────────────────────────────────────


class TestDeploy:
    async def test_push_creature_delegates(self, monkeypatch, tmp_path):
        sender = MagicMock()
        target = nodes_mod._Deploy(sender, "worker-1")
        captured = {}

        async def _fake_deploy(s, n, path, *, name, timeout):
            captured["sender"] = s
            captured["node"] = n
            captured["path"] = path
            captured["name"] = name
            return "/remote/path"

        monkeypatch.setattr(nodes_mod, "deploy_creature_to_node", _fake_deploy)
        out = await target.push_creature(tmp_path, name="alice")
        assert out == "/remote/path"
        assert captured["node"] == "worker-1"


# ── NodeHandle ──────────────────────────────────────────────


class TestNodeHandle:
    def test_local_runtime_no_remote_handles(self):
        from kohakuterrarium.terrarium.engine import Terrarium

        engine = Terrarium()
        try:
            local = LocalTerrariumService(engine)
            handle = nodes_mod.NodeHandle("_host", local, sender=None)
            assert handle.node_id == "_host"
            assert isinstance(handle.files, nodes_mod._Pending)
            assert isinstance(handle.deploy, nodes_mod._Pending)
            assert isinstance(handle.identity, nodes_mod._Pending)
            assert isinstance(handle.catalog, nodes_mod._Pending)
        finally:
            engine.shutdown_sync = None  # silence cleanup

    def test_remote_runtime_with_sender(self):
        # A non-LocalTerrariumService runtime + sender → real handles.
        runtime = SimpleNamespace()
        sender = MagicMock()
        handle = nodes_mod.NodeHandle("worker-1", runtime, sender=sender)
        # Files and deploy are real wrappers.
        from kohakuterrarium.studio.files import RemoteFiles

        assert isinstance(handle.files, RemoteFiles)
        assert isinstance(handle.deploy, nodes_mod._Deploy)


# ── NodeMap ────────────────────────────────────────────────


class TestNodeMap:
    def test_local_returns_handle(self):
        from kohakuterrarium.terrarium.engine import Terrarium

        engine = Terrarium()
        try:
            local = LocalTerrariumService(engine)
            service = SimpleNamespace(
                node_id="_host",
                service_for=lambda n: local,
                connected_nodes=lambda: ("_host",),
                host=None,
            )
            nm = nodes_mod.NodeMap(service)
            h = nm["_host"]
            assert h.node_id == "_host"
            # Cached on second access.
            h2 = nm["_host"]
            assert h is h2
        finally:
            pass

    def test_remote_missing_raises(self):
        service = SimpleNamespace(
            node_id="_host",
            service_for=lambda n: None,
            connected_nodes=lambda: ("_host",),
            host=None,
        )
        nm = nodes_mod.NodeMap(service)
        with pytest.raises(KeyError):
            nm["worker-ghost"]

    def test_remote_present(self):
        runtime = SimpleNamespace()
        sender = MagicMock()
        service = SimpleNamespace(
            node_id="_host",
            service_for=lambda n: runtime if n == "worker-1" else None,
            connected_nodes=lambda: ("_host", "worker-1"),
            host=sender,
        )
        nm = nodes_mod.NodeMap(service)
        h = nm["worker-1"]
        assert h.node_id == "worker-1"

    def test_contains_check(self):
        service = SimpleNamespace(
            node_id="_host",
            service_for=lambda n: None,
            connected_nodes=lambda: ("_host", "worker-1"),
            host=None,
        )
        nm = nodes_mod.NodeMap(service)
        assert "worker-1" in nm
        assert "ghost" not in nm

    def test_iter_yields_node_ids(self):
        service = SimpleNamespace(
            node_id="_host",
            service_for=lambda n: None,
            connected_nodes=lambda: ("_host", "worker-1"),
            host=None,
        )
        nm = nodes_mod.NodeMap(service)
        assert sorted(nm) == ["_host", "worker-1"]

    def test_keys(self):
        service = SimpleNamespace(
            node_id="_host",
            service_for=lambda n: None,
            connected_nodes=lambda: ("_host", "w1"),
            host=None,
        )
        nm = nodes_mod.NodeMap(service)
        assert nm.keys() == ("_host", "w1")


# ── build_node_map_if_multi_node ────────────────────────────


class TestBuildNodeMapIfMultiNode:
    def test_single_node_returns_none(self):
        # A local-only service has no connected_nodes/service_for.
        svc = object()
        assert nodes_mod.build_node_map_if_multi_node(svc) is None

    def test_multi_node_returns_nodemap(self):
        svc = SimpleNamespace(
            connected_nodes=lambda: ("_host", "worker-1"),
            service_for=lambda n: None,
        )
        nm = nodes_mod.build_node_map_if_multi_node(svc)
        # A multi-node-capable service yields a NodeMap covering every
        # connected node.
        assert isinstance(nm, nodes_mod.NodeMap)
        assert nm.keys() == ("_host", "worker-1")
