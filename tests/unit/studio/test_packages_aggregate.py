"""Unit tests for :mod:`kohakuterrarium.studio.catalog.packages_aggregate`."""

from kohakuterrarium.studio.catalog import packages_aggregate as agg_mod


class _FakeSender:
    def __init__(self, responses=None, raises=None):
        self._responses = responses or {}
        self._raises = raises or {}
        self.calls = []

    async def request(self, *, to_node, namespace, type, body, timeout):
        self.calls.append(
            {
                "to": to_node,
                "namespace": namespace,
                "type": type,
                "body": body,
                "timeout": timeout,
            }
        )
        if to_node in self._raises:
            raise self._raises[to_node]
        return self._responses.get(to_node, {"packages": []})


class _FakeService:
    def __init__(self, sender=None, nodes=()):
        self.host = sender or _FakeSender()
        self._nodes = list(nodes)

    def connected_nodes(self):
        return tuple(self._nodes)


# ── aggregate_packages ───────────────────────────────────────


class TestAggregatePackages:
    async def test_no_nodes_returns_empty(self):
        svc = _FakeService(nodes=[])
        out = await agg_mod.aggregate_packages(svc)
        assert out == {}

    async def test_host_local_via_to_thread(self, monkeypatch):
        # Stub list_installed_packages so we don't touch the filesystem.
        monkeypatch.setattr(
            agg_mod,
            "list_installed_packages",
            lambda: [{"name": "demo", "version": "1.0"}],
        )
        svc = _FakeService(nodes=["_host"])
        out = await agg_mod.aggregate_packages(svc)
        assert "demo" in out
        assert out["demo"]["installations"]["_host"]["version"] == "1.0"

    async def test_host_local_failure_recorded(self, monkeypatch):
        def boom():
            raise RuntimeError("no packages dir")

        monkeypatch.setattr(agg_mod, "list_installed_packages", boom)
        svc = _FakeService(nodes=["_host"])
        out = await agg_mod.aggregate_packages(svc)
        assert "__node_errors__" in out
        assert out["__node_errors__"]["installations"]["_host"]["error"]

    async def test_remote_success(self):
        sender = _FakeSender(
            responses={
                "worker-1": {"packages": [{"name": "pkg-a"}]},
                "worker-2": {"packages": [{"name": "pkg-a"}, {"name": "pkg-b"}]},
            }
        )
        svc = _FakeService(sender=sender, nodes=["worker-1", "worker-2"])
        out = await agg_mod.aggregate_packages(svc, include_host_local=False)
        assert "pkg-a" in out
        assert "pkg-b" in out
        # pkg-a is installed on both workers.
        assert set(out["pkg-a"]["installations"]) == {"worker-1", "worker-2"}

    async def test_remote_failure_recorded(self):
        sender = _FakeSender(
            raises={"worker-bad": RuntimeError("unreachable")},
            responses={"worker-good": {"packages": [{"name": "pkg-x"}]}},
        )
        svc = _FakeService(
            sender=sender,
            nodes=["worker-bad", "worker-good"],
        )
        out = await agg_mod.aggregate_packages(svc, include_host_local=False)
        assert "__node_errors__" in out
        assert "worker-bad" in out["__node_errors__"]["installations"]
        assert "pkg-x" in out

    async def test_remote_returns_error_dict(self):
        sender = _FakeSender(
            responses={"w": {"error": {"message": "bad request"}}},
        )
        svc = _FakeService(sender=sender, nodes=["w"])
        out = await agg_mod.aggregate_packages(svc, include_host_local=False)
        # Recorded as node error.
        assert "__node_errors__" in out

    async def test_packages_without_name_skipped(self):
        sender = _FakeSender(
            responses={"w": {"packages": [{"version": "no-name"}, {"name": "x"}]}},
        )
        svc = _FakeService(sender=sender, nodes=["w"])
        out = await agg_mod.aggregate_packages(svc, include_host_local=False)
        assert "x" in out
        assert len(out) == 1

    async def test_exclude_host_local(self):
        sender = _FakeSender(responses={"worker": {"packages": [{"name": "a"}]}})
        svc = _FakeService(sender=sender, nodes=["_host", "worker"])
        out = await agg_mod.aggregate_packages(svc, include_host_local=False)
        # No host call made.
        assert "_host" not in sum(
            (list(v.get("installations", {}).keys()) for v in out.values()),
            [],
        )


# ── _MultiNodeServiceLike protocol ───────────────────────────


class TestProtocolCheck:
    def test_protocol_runtime_checkable(self):
        sender = _FakeSender()
        svc = _FakeService(sender, [])
        # The Protocol is decorated runtime_checkable.
        assert isinstance(svc, agg_mod._MultiNodeServiceLike)
