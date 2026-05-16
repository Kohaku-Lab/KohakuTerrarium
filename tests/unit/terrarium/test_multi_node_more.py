"""Coverage tests for the uncovered branches of
:mod:`kohakuterrarium.terrarium.multi_node_service`.

Reuses :class:`_FakeService` from the sibling ``test_multi_node_service``
module by constructing the service via ``__new__`` and substituting
fake *worker* services for ``_remotes``.  The lab-host runs no agents —
there is no host-local service.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from kohakuterrarium.terrarium import multi_node_service as mns_mod
from kohakuterrarium.terrarium.events import EventKind
from kohakuterrarium.terrarium.topology import ChannelInfo, GraphTopology

# Re-import fakes from the sibling test module.
from tests.unit.terrarium.test_multi_node_service import (
    _FakeService,
    _info,
    _make_service,
)

# ── add_remote / drop_remote / membership ────────────────────


class TestMembershipMore:
    async def test_add_remote_creates_and_caches(self, monkeypatch):
        svc = _make_service()
        # Skip the real RemoteTerrariumService init by patching the
        # class in the module under test.
        captured = {}

        class _FakeRemote(_FakeService):
            def __init__(self, host, node_id, *, demux=None):
                super().__init__(node_id=node_id)
                captured["host"] = host
                captured["demux"] = demux

        monkeypatch.setattr(mns_mod, "RemoteTerrariumService", _FakeRemote)
        out = svc.add_remote("worker-1")
        assert out.node_id == "worker-1"
        assert "worker-1" in svc._remotes
        # Adding again returns the same instance.
        again = svc.add_remote("worker-1")
        assert again is out

    def test_add_remote_no_loop_skips_warm(self, monkeypatch):
        svc = _make_service()

        class _FakeRemote(_FakeService):
            def __init__(self, host, node_id, *, demux=None):
                super().__init__(node_id=node_id)

        monkeypatch.setattr(mns_mod, "RemoteTerrariumService", _FakeRemote)
        # No running loop in this sync test → warm task isn't scheduled,
        # but add_remote should still return.
        svc.add_remote("worker-x")
        assert "worker-x" in svc._remotes

    async def test_warm_caches_swallows_failure(self, monkeypatch):
        svc = _make_service()

        async def _boom():
            raise RuntimeError("bad")

        # Replace list_creatures with a raising version.
        svc.list_creatures = _boom
        # Should swallow and not raise.
        await svc._warm_caches_on_join("worker-x")


# ── reads with errors ────────────────────────────────────────


class TestReadsErrorPaths:
    async def test_list_graphs_remote_failure_swallowed(self):
        svc = _make_service()

        class _BadSvc:
            async def list_graphs(self):
                raise RuntimeError("nope")

        svc._remotes["w1"] = _BadSvc()
        out = await svc.list_graphs()
        # The one worker raised — got an empty tuple, no crash.
        assert isinstance(out, tuple)

    async def test_status_snapshot_marks_unreachable(self):
        svc = _make_service()

        class _BadSvc:
            async def status_snapshot(self):
                raise RuntimeError("down")

        svc._remotes["w1"] = _BadSvc()
        out = await svc.status_snapshot()
        assert out["w1"] == {"error": "unreachable"}

    async def test_get_graph_remote_hit(self):
        svc = _make_service()
        g = GraphTopology(graph_id="g1", creature_ids=set(), channels={})
        svc._remotes["w1"] = _FakeService(node_id="w1", graphs=[g])
        out = await svc.get_graph("g1")
        assert out is g

    async def test_get_graph_miss(self):
        svc = _make_service()
        svc._remotes["w1"] = _FakeService(node_id="w1")
        out = await svc.get_graph("ghost")
        assert out is None

    async def test_list_channels_falls_back_to_remote(self):
        svc = _make_service()
        ch = ChannelInfo(name="chat")
        svc._remotes["w1"] = _FakeService(node_id="w1", channels_by_graph={"g1": [ch]})
        out = await svc.list_channels("g1")
        assert out == (ch,)

    async def test_list_channels_total_miss(self):
        svc = _make_service()
        svc._remotes["w1"] = _FakeService(node_id="w1")
        out = await svc.list_channels("g1")
        assert out == ()

    async def test_cf4_list_channels_unions_cluster_members(self):
        """CF-4: cluster channels exist on every member's worker engine.

        ``list_channels`` must walk every cluster member and union the
        per-member channel set by name, not return the first hit and
        stop.  Otherwise a channel that exists only on the peer (or has
        diverged across members) is invisible to the route caller.
        """
        svc = _make_service()
        ch_a = ChannelInfo(name="shared", description="from-a")
        ch_only_b = ChannelInfo(name="peer-only", description="from-b")
        svc._remotes["w1"] = _FakeService(
            node_id="w1", channels_by_graph={"g_a": [ch_a]}
        )
        svc._remotes["w2"] = _FakeService(
            node_id="w2", channels_by_graph={"g_b": [ch_a, ch_only_b]}
        )
        # Record a cluster link between g_a (w1) and g_b (w2).
        svc._cluster_links.add(frozenset({("w1", "g_a"), ("w2", "g_b")}))
        names = {ch.name for ch in await svc.list_channels("g_a")}
        assert {"shared", "peer-only"} <= names, names
        # Same union must be visible from the non-primary sid too.
        names_b = {ch.name for ch in await svc.list_channels("g_b")}
        assert {"shared", "peer-only"} <= names_b, names_b

    async def test_cf4_channel_history_merges_cluster_sides(self):
        """CF-4: channel history is recorded per-member-side.

        Each worker's engine stores its own copy of broadcasts sent on
        its side of the cluster.  ``channel_history`` must merge the
        per-member streams (sorted by timestamp) instead of routing to
        a single resolved home; otherwise the UI shows only half of the
        conversation.
        """
        svc = _make_service()

        class _HistSvc(_FakeService):
            def __init__(self, *, node_id, hist):
                super().__init__(node_id=node_id)
                self._hist = hist

            async def channel_history(self, gid, name, *, limit=None):
                rows = list(self._hist.get((gid, name), []))
                if limit is not None and limit >= 0:
                    rows = rows[-limit:]
                return rows

        svc._remotes["w1"] = _HistSvc(
            node_id="w1",
            hist={
                ("g_a", "ch1"): [
                    {
                        "message_id": "m1",
                        "sender": "u",
                        "content": "a-side",
                        "timestamp": 1.0,
                    }
                ]
            },
        )
        svc._remotes["w2"] = _HistSvc(
            node_id="w2",
            hist={
                ("g_b", "ch1"): [
                    {
                        "message_id": "m2",
                        "sender": "u",
                        "content": "b-side",
                        "timestamp": 2.0,
                    }
                ]
            },
        )
        svc._cluster_links.add(frozenset({("w1", "g_a"), ("w2", "g_b")}))
        merged = await svc.channel_history("g_a", "ch1")
        contents = [m["content"] for m in merged]
        assert contents == ["a-side", "b-side"], merged
        # Non-primary sid yields the same merged stream.
        merged_b = await svc.channel_history("g_b", "ch1")
        assert [m["content"] for m in merged_b] == ["a-side", "b-side"]
        # Dedup by message_id when the same message_id surfaces on both
        # sides (broadcast cross-sub can produce that under replication).
        svc._remotes["w2"]._hist[("g_b", "ch1")] = [
            {"message_id": "m1", "sender": "u", "content": "a-side", "timestamp": 1.0},
            {"message_id": "m2", "sender": "u", "content": "b-side", "timestamp": 2.0},
        ]
        merged = await svc.channel_history("g_a", "ch1")
        assert [m["message_id"] for m in merged] == ["m1", "m2"], merged


# ── connect / disconnect with same-worker routing ────────────


class TestConnectDisconnectRouting:
    async def test_connect_same_worker_routes(self):
        # Both creatures live on one worker — connect routes there.
        svc = _make_service(remote_specs={"w1": [_info("a"), _info("b")]})
        await svc.list_creatures()
        result = await svc.connect("a", "b", channel="chat")
        assert result.channel == "chat"

    async def test_connect_unknown_sender_raises(self):
        svc = _make_service()
        with pytest.raises(KeyError):
            await svc.connect("ghost", "b")

    async def test_connect_unknown_receiver_raises(self):
        svc = _make_service(remote_specs={"w1": [_info("a")]})
        await svc.list_creatures()
        with pytest.raises(KeyError):
            await svc.connect("a", "ghost")

    async def test_disconnect_same_worker_routes(self):
        svc = _make_service(remote_specs={"w1": [_info("a"), _info("b")]})
        await svc.list_creatures()
        result = await svc.disconnect("a", "b", channel="chat")
        assert result.channels == ["chat"]

    async def test_disconnect_unknown_sender(self):
        svc = _make_service()
        with pytest.raises(KeyError):
            await svc.disconnect("a", "b")

    async def test_disconnect_unknown_receiver(self):
        svc = _make_service(remote_specs={"w1": [_info("a")]})
        await svc.list_creatures()
        with pytest.raises(KeyError):
            await svc.disconnect("a", "b")


# ── _resolve_graph_home ──────────────────────────────────────


class TestResolveGraphHome:
    async def test_first_worker_hit(self):
        g = GraphTopology(graph_id="g1", creature_ids=set(), channels={})
        svc = _make_service()
        svc._remotes["w1"] = _FakeService(node_id="w1", graphs=[g])
        svc._remotes["w2"] = _FakeService(node_id="w2")
        out = await svc._resolve_graph_home("g1")
        assert out == "w1"

    async def test_remote_hit(self):
        g = GraphTopology(graph_id="g1", creature_ids=set(), channels={})
        svc = _make_service()
        svc._remotes["w1"] = _FakeService(node_id="w1")
        svc._remotes["w2"] = _FakeService(node_id="w2", graphs=[g])
        out = await svc._resolve_graph_home("g1")
        assert out == "w2"

    async def test_miss_raises(self):
        svc = _make_service()
        with pytest.raises(KeyError, match="not found"):
            await svc._resolve_graph_home("ghost")


# ── routing through _route_per_creature ──────────────────────


class TestRoutingDelegates:
    async def test_remove_creature_drops_home(self):
        svc = _make_service(remote_specs={"w1": [_info("c1")]})
        await svc.remove_creature("c1")
        assert "c1" not in svc._home

    async def test_start_stop_creature(self):
        svc = _make_service(remote_specs={"w1": [_info("c1")]})
        await svc.start_creature("c1")
        await svc.stop_creature("c1")
        assert ("start_creature", "c1") in svc._remotes["w1"].calls
        assert ("stop_creature", "c1") in svc._remotes["w1"].calls

    async def test_inject_input(self):
        svc = _make_service(remote_specs={"w1": [_info("c1")]})
        await svc.inject_input("c1", "hi", source="api")
        assert ("inject_input", "c1", "hi", "api") in svc._remotes["w1"].calls

    async def test_add_channel(self):
        g = GraphTopology(graph_id="g1", creature_ids=set(), channels={})
        svc = _make_service()
        svc._remotes["w1"] = _FakeService(node_id="w1", graphs=[g])
        info = await svc.add_channel("g1", "chat", "desc")
        assert info.name == "chat"

    async def test_remove_channel(self):
        g = GraphTopology(graph_id="g1", creature_ids=set(), channels={})
        svc = _make_service()
        svc._remotes["w1"] = _FakeService(node_id="w1", graphs=[g])
        delta = await svc.remove_channel("g1", "chat")
        assert delta.kind == "nothing"

    async def test_shutdown_is_noop(self):
        # The host runs no agent engine — shutdown() is a no-op and
        # never reaches into workers (separate-process lifecycles).
        svc = _make_service(remote_specs={"w1": []})
        await svc.shutdown()
        assert ("shutdown",) not in svc._remotes["w1"].calls


# ── _route_per_creature retry path ───────────────────────────


class TestRoutingRetry:
    async def test_creature_not_hosted_here_retries(self):
        from kohakuterrarium.terrarium.remote_service import (
            CreatureNotHostedHere,
        )

        # ``_home`` is stale — points at w1, but c1 actually lives on
        # w2.  The first routed call hits w1, gets CreatureNotHostedHere,
        # re-resolves via a list_creatures fan-out, and retries on w2.
        svc = _make_service(remote_specs={"w1": [], "w2": [_info("c1")]})
        svc._home["c1"] = "w1"

        async def _boom(cid):
            raise CreatureNotHostedHere("not here")

        svc._remotes["w1"].creature_status = _boom
        out = await svc.creature_status("c1")
        assert out == {"running": True}

    async def test_unknown_creature_returns_none(self):
        # creature_status returns None (swallows KeyError).
        svc = _make_service()
        out = await svc.creature_status("ghost")
        assert out is None

    async def test_remove_unknown_raises(self):
        svc = _make_service()
        with pytest.raises(KeyError):
            await svc.remove_creature("ghost")


# ── runtime_graph_snapshot fan-out ───────────────────────────


class TestRuntimeGraphSnapshot:
    async def test_fans_out_and_merges(self):
        svc = _make_service()

        async def _snap_a():
            return {"version": 5, "graphs": [{"graph_id": "g-a"}]}

        async def _snap_b():
            return {"version": 10, "graphs": [{"graph_id": "g-b"}]}

        rem_a = _FakeService(node_id="w1")
        rem_a.runtime_graph_snapshot = _snap_a
        rem_b = _FakeService(node_id="w2")
        rem_b.runtime_graph_snapshot = _snap_b
        svc._remotes["w1"] = rem_a
        svc._remotes["w2"] = rem_b
        out = await svc.runtime_graph_snapshot()
        assert out["version"] == 10
        assert len(out["graphs"]) == 2

    async def test_swallows_worker_failure(self):
        svc = _make_service()

        async def _boom():
            raise RuntimeError("nope")

        rem = _FakeService(node_id="w1")
        rem.runtime_graph_snapshot = _boom
        svc._remotes["w1"] = rem
        out = await svc.runtime_graph_snapshot()
        assert "graphs" in out

    async def test_enriches_with_meta_lookup(self):
        # The injected studio meta-lookup annotates each worker graph
        # with its name / kind so the graph editor renders labels.
        svc = _make_service()

        async def _snap():
            return {"version": 1, "graphs": [{"graph_id": "g1"}]}

        rem = _FakeService(node_id="w1")
        rem.runtime_graph_snapshot = _snap
        svc._remotes["w1"] = rem
        svc.set_runtime_graph_meta_lookup(
            lambda gid: {"name": "labelled", "kind": "agent"} if gid == "g1" else {}
        )
        out = await svc.runtime_graph_snapshot()
        assert out["graphs"][0]["name"] == "labelled"
        assert out["graphs"][0]["kind"] == "agent"


# ── chat + subscribe streaming ───────────────────────────────


class TestChatSubscribeStreams:
    async def test_chat_unknown_creature(self):
        svc = _make_service()

        async def _consume():
            async for _ in svc.chat("ghost", "hi"):
                pass

        with pytest.raises(KeyError):
            await _consume()

    async def test_chat_routes_to_worker(self):
        svc = _make_service(remote_specs={"w1": [_info("c1")]})
        await svc.list_creatures()

        async def _chat_gen(cid, msg):
            yield "hello"

        svc._remotes["w1"].chat = _chat_gen
        chunks = []
        async for c in svc.chat("c1", "hi"):
            chunks.append(c)
        assert chunks == ["hello"]

    async def test_subscribe_fans_out(self):
        from kohakuterrarium.terrarium.events import EngineEvent

        svc = _make_service(remote_specs={"w1": []})

        async def _worker_subscribe(filter=None):
            yield EngineEvent(kind=EventKind.CREATURE_STARTED, creature_id="x")

        svc._remotes["w1"].subscribe = _worker_subscribe
        events = []
        async for ev in svc.subscribe():
            events.append(ev)
            if len(events) >= 1:
                break
        assert events

    async def test_subscribe_no_workers_is_empty(self):
        # No workers connected → nothing to stream, clean finish.
        svc = _make_service()
        events = [ev async for ev in svc.subscribe()]
        assert events == []


# ── session_attach_policies + wire_creature root ─────────────


class TestWireCreatureAndPolicies:
    async def test_wire_creature_root_routes_to_graph_home(self):
        g = GraphTopology(graph_id="g1", creature_ids=set(), channels={})
        svc = _make_service()
        worker = _FakeService(node_id="w1", graphs=[g])
        worker.wire_creature = AsyncMock()
        svc._remotes["w1"] = worker
        await svc.wire_creature("g1", "root", "chat", "listen")
        worker.wire_creature.assert_awaited_once()

    async def test_session_attach_policies_unknown_returns_empty(self):
        # No connected worker hosts the graph — there are no policies
        # to report.  Empty list, not a 500.
        svc = _make_service()
        out = await svc.session_attach_policies("ghost")
        assert out == []

    async def test_session_attach_policies_routes(self):
        g = GraphTopology(graph_id="g1", creature_ids=set(), channels={})
        svc = _make_service()
        worker = _FakeService(node_id="w1", graphs=[g])

        async def _worker_policies(sid):
            return ["log", "trace"]

        worker.session_attach_policies = _worker_policies
        svc._remotes["w1"] = worker
        out = await svc.session_attach_policies("g1")
        assert "trace" in out

    async def test_session_attach_policies_unions_cluster_members(self):
        """CF-10: cluster sessions span multiple worker engines; each
        member may advertise its own subset of policies (e.g. one has
        the input module → IO, another has channels → OBSERVER). The
        service MUST union the per-member policy lists rather than
        report only the primary's slice — pre-CF-10 the lab-host UI
        hid toggles the worker actually supported because the host
        engine had nothing to introspect."""
        g_a = GraphTopology(graph_id="ga", creature_ids=set(), channels={})
        g_b = GraphTopology(graph_id="gb", creature_ids=set(), channels={})
        svc = _make_service()
        worker_a = _FakeService(node_id="w1", graphs=[g_a])
        worker_b = _FakeService(node_id="w2", graphs=[g_b])

        async def _policies_a(sid):
            # Worker-A advertises IO + LOG (has input module).
            return ["io", "log", "trace"]

        async def _policies_b(sid):
            # Worker-B advertises OBSERVER + LOG (channels).
            return ["log", "observer", "trace"]

        worker_a.session_attach_policies = _policies_a
        worker_b.session_attach_policies = _policies_b
        svc._remotes["w1"] = worker_a
        svc._remotes["w2"] = worker_b
        # Record the cluster link between the two members so
        # cluster_members_for resolves both.
        svc._cluster_links.add(frozenset({("w1", "ga"), ("w2", "gb")}))

        primary = min("ga", "gb")
        out = await svc.session_attach_policies(primary)
        # Every policy advertised by ANY member must surface — IO
        # (worker-A only) AND OBSERVER (worker-B only). Without CF-10
        # only one side's slice came through.
        assert "io" in out
        assert "observer" in out
        assert "log" in out
        assert "trace" in out


# ── cross-sub bookkeeping ────────────────────────────────────


class TestCrossSubBookkeeping:
    def test_record_increments(self):
        svc = _make_service()
        svc._record_cross_sub("a", "b", "g", "c")
        svc._record_cross_sub("a", "b", "g", "c")
        assert svc._cross_subs[("a", "b", "g", "c")] == 2

    def test_drop_decrements_then_removes(self):
        svc = _make_service()
        svc._record_cross_sub("a", "b", "g", "c")
        svc._record_cross_sub("a", "b", "g", "c")
        svc._drop_cross_sub("a", "b", "g", "c")
        assert svc._cross_subs[("a", "b", "g", "c")] == 1
        svc._drop_cross_sub("a", "b", "g", "c")
        assert ("a", "b", "g", "c") not in svc._cross_subs

    def test_drop_missing_silent(self):
        svc = _make_service()
        svc._drop_cross_sub("a", "b", "g", "c")  # no error


# ── _local_broadcast_adapter ─────────────────────────────────


class TestLocalBroadcastAdapter:
    async def test_no_coordination_engine_returns_none(self):
        # No coordination engine wired → no broadcast adapter.
        svc = _make_service()
        assert svc._coordination_engine is None
        out = await svc._local_broadcast_adapter()
        assert out is None

    async def test_returns_adapter_from_coordination_engine(self):
        svc = _make_service()
        svc._coordination_engine = SimpleNamespace(_broadcast_adapter="adapter")
        out = await svc._local_broadcast_adapter()
        assert out == "adapter"


# ── cross-node lazy channel replication on wire_creature ─────────────


class TestCrossNodeChannelReplication:
    """The 'VERY BAD' bug: wiring a creature to a user-named channel that
    lives on a different worker's graph must replicate the channel on
    the target's graph + cross-subscribe — otherwise the channel name
    is meaningless across worker boundaries and the graph editor falls
    back to auto-named ``a_to_b`` channels."""

    async def test_wire_creature_replicates_channel_from_peer_graph(self):
        # w1 hosts graph_a with a channel ``my_channel``; w2 hosts
        # graph_b which does NOT have that channel.  Wiring a creature
        # on w2 to ``my_channel`` should:
        #   1. call w2.add_channel(graph_b, "my_channel")
        #   2. cross-subscribe via the broadcast adapter
        #   3. call w2.wire_creature(graph_b, b, "my_channel", "listen")
        graph_a = GraphTopology(graph_id="g_a", creature_ids={"a"}, channels={})
        graph_b = GraphTopology(graph_id="g_b", creature_ids={"b"}, channels={})
        w1 = _FakeService(
            node_id="w1",
            graphs=[graph_a],
            channels_by_graph={"g_a": [ChannelInfo(name="my_channel")]},
        )
        w2 = _FakeService(
            node_id="w2",
            graphs=[graph_b],
            channels_by_graph={"g_b": []},
        )
        w2.wire_creature = AsyncMock()
        svc = _make_service()
        svc._remotes = {"w1": w1, "w2": w2}
        svc._home = {"a": "w1", "b": "w2"}

        # Fake broadcast adapter to capture the cross-subscribe call.
        proxy_calls: list[dict] = []

        class _FakeBcast:
            async def proxy_subscribe(self, **kw):
                proxy_calls.append(kw)

        svc._coordination_engine = SimpleNamespace(_broadcast_adapter=_FakeBcast())

        await svc.wire_creature("g_b", "b", "my_channel", "listen")

        # 1. channel was replicated on w2's graph_b
        assert any(
            call[:3] == ("add_channel", "g_b", "my_channel") for call in w2.calls
        ), f"add_channel not called on w2 for replication; calls: {w2.calls}"
        # 2. cross-subscribe: w2 (target) subscribes to w1 (source)'s sends
        assert proxy_calls, "no cross-subscribe issued"
        sub = proxy_calls[0]
        assert sub["proxy_node"] == "w2"
        assert sub["peer_node"] == "w1"
        assert sub["graph_id"] == "g_a"
        assert sub["channel"] == "my_channel"
        # 3. cross-sub bookkeeping recorded
        assert svc._cross_subs.get(("w2", "w1", "g_a", "my_channel"), 0) == 1
        # 4. the actual wire call still went to the target node
        w2.wire_creature.assert_awaited_once_with(
            "g_b", "b", "my_channel", "listen", enabled=True
        )

    async def test_wire_creature_no_replication_when_channel_already_local(self):
        # The channel is ALREADY on the target's graph — no replication,
        # no cross-subscribe, just the normal wire.
        graph_b = GraphTopology(graph_id="g_b", creature_ids={"b"}, channels={})
        w2 = _FakeService(
            node_id="w2",
            graphs=[graph_b],
            channels_by_graph={"g_b": [ChannelInfo(name="my_channel")]},
        )
        w2.wire_creature = AsyncMock()
        svc = _make_service()
        svc._remotes = {"w2": w2}
        svc._home = {"b": "w2"}

        proxy_calls: list[dict] = []

        class _FakeBcast:
            async def proxy_subscribe(self, **kw):
                proxy_calls.append(kw)

        svc._coordination_engine = SimpleNamespace(_broadcast_adapter=_FakeBcast())

        await svc.wire_creature("g_b", "b", "my_channel", "listen")

        # No add_channel for replication, no proxy_subscribe.
        assert not any(
            call[:1] == ("add_channel",) for call in w2.calls
        ), "should not replicate when channel is already on target graph"
        assert proxy_calls == []
        assert svc._cross_subs == {}
        w2.wire_creature.assert_awaited_once()

    async def test_wire_creature_no_replication_when_channel_nowhere(self):
        # Channel doesn't exist on any worker → no replication, let the
        # canonical "channel not found" propagate from the wire call.
        graph_b = GraphTopology(graph_id="g_b", creature_ids={"b"}, channels={})
        w2 = _FakeService(
            node_id="w2",
            graphs=[graph_b],
            channels_by_graph={"g_b": []},
        )
        w2.wire_creature = AsyncMock()
        svc = _make_service()
        svc._remotes = {"w2": w2}
        svc._home = {"b": "w2"}
        svc._coordination_engine = SimpleNamespace(_broadcast_adapter=object())

        await svc.wire_creature("g_b", "b", "ghost", "listen")

        # No replication attempted (channel exists nowhere to find).
        assert not any(call[:1] == ("add_channel",) for call in w2.calls)
        assert svc._cross_subs == {}
        # Wire still attempted — the worker raises the canonical error.
        w2.wire_creature.assert_awaited_once()

    async def test_wire_creature_send_direction_subscribes_reverse(self):
        # direction='send' on the cross-node side: the SOURCE node
        # should subscribe to the TARGET node's sends so peer listeners
        # actually hear the new sender.
        graph_a = GraphTopology(graph_id="g_a", creature_ids={"a"}, channels={})
        graph_b = GraphTopology(graph_id="g_b", creature_ids={"b"}, channels={})
        w1 = _FakeService(
            node_id="w1",
            graphs=[graph_a],
            channels_by_graph={"g_a": [ChannelInfo(name="my_channel")]},
        )
        w2 = _FakeService(
            node_id="w2",
            graphs=[graph_b],
            channels_by_graph={"g_b": []},
        )
        w2.wire_creature = AsyncMock()
        svc = _make_service()
        svc._remotes = {"w1": w1, "w2": w2}
        svc._home = {"a": "w1", "b": "w2"}

        proxy_calls: list[dict] = []

        class _FakeBcast:
            async def proxy_subscribe(self, **kw):
                proxy_calls.append(kw)

        svc._coordination_engine = SimpleNamespace(_broadcast_adapter=_FakeBcast())

        await svc.wire_creature("g_b", "b", "my_channel", "send")

        # ONE subscribe — and its direction is REVERSED: source listens
        # to target's sends.
        assert len(proxy_calls) == 1, proxy_calls
        sub = proxy_calls[0]
        assert sub["proxy_node"] == "w1"
        assert sub["peer_node"] == "w2"
        assert sub["graph_id"] == "g_b"
        assert sub["channel"] == "my_channel"

    async def test_find_channel_elsewhere_skips_excluded_node(self):
        # Even if a worker hosts the channel, the helper must skip the
        # ``exclude`` node (the caller has already established absence
        # there).
        graph_a = GraphTopology(graph_id="g_a", creature_ids=set(), channels={})
        w1 = _FakeService(
            node_id="w1",
            graphs=[graph_a],
            channels_by_graph={"g_a": [ChannelInfo(name="X")]},
        )
        svc = _make_service()
        svc._remotes = {"w1": w1}
        out = await svc._find_channel_elsewhere("X", exclude="w1")
        assert out is None
        out2 = await svc._find_channel_elsewhere("X", exclude="other")
        assert out2 == ("w1", "g_a")

    async def test_find_channel_elsewhere_swallows_worker_failure(self):
        # A failed list_graphs / list_channels on one worker must not
        # break the search — keep looking on other workers.
        graph_a = GraphTopology(graph_id="g_a", creature_ids=set(), channels={})

        class _Broken(_FakeService):
            async def list_graphs(self):
                raise RuntimeError("rpc stall")

        broken = _Broken(node_id="bad")
        good = _FakeService(
            node_id="w1",
            graphs=[graph_a],
            channels_by_graph={"g_a": [ChannelInfo(name="X")]},
        )
        svc = _make_service()
        svc._remotes = {"bad": broken, "w1": good}
        out = await svc._find_channel_elsewhere("X", exclude="other")
        assert out == ("w1", "g_a")


# ── cluster-graph view: cross-node connection = ONE graph in snapshot ─


class TestClusterGraphSnapshot:
    """The "Laboratory makes N terrariums look like 1" UX invariant.

    When two engine graphs on different workers have been linked by a
    cross-node channel wire, the cluster-wide
    ``runtime_graph_snapshot`` must return them as ONE graph (with a
    ``members`` list naming both halves), not two.  Single un-linked
    graphs surface unchanged so the host-only path still works.
    """

    def test_two_engine_graphs_no_links_unchanged(self):
        svc = _make_service()
        engine_graphs = [
            {"node_id": "w1", "graph_id": "g_a", "creature_ids": ["a"], "channels": []},
            {"node_id": "w2", "graph_id": "g_b", "creature_ids": ["b"], "channels": []},
        ]
        out = svc._fold_clusters(engine_graphs)
        # No cluster links → both engine graphs pass through.
        assert len(out) == 2
        gids = {g["graph_id"] for g in out}
        assert gids == {"g_a", "g_b"}
        # No is_cluster flag on un-linked graphs.
        assert all(not g.get("is_cluster") for g in out)

    def test_linked_engine_graphs_fold_into_one_cluster(self):
        svc = _make_service()
        svc._cluster_links.add(frozenset({("w1", "g_a"), ("w2", "g_b")}))
        engine_graphs = [
            {
                "node_id": "w1",
                "graph_id": "g_a",
                "creature_ids": ["alpha"],
                "channels": [{"name": "my_channel"}],
            },
            {
                "node_id": "w2",
                "graph_id": "g_b",
                "creature_ids": ["bravo"],
                "channels": [{"name": "my_channel"}],
            },
        ]
        out = svc._fold_clusters(engine_graphs)
        assert len(out) == 1, f"expected ONE cluster graph; got {out!r}"
        cluster = out[0]
        assert cluster["is_cluster"] is True
        # Union of creatures from both engine graphs.
        assert set(cluster["creature_ids"]) == {"alpha", "bravo"}
        # Dedup of channels by name — ``my_channel`` appears once.
        ch_names = [c["name"] for c in cluster["channels"]]
        assert ch_names == ["my_channel"]
        # Members carry the per-node graph_ids so the frontend can
        # issue ops to the right engine graph.
        members = {(m["node_id"], m["graph_id"]) for m in cluster["members"]}
        assert members == {("w1", "g_a"), ("w2", "g_b")}

    def test_cluster_id_is_lexicographic_smallest_for_stability(self):
        # Snapshot identity must not flip every call — if the same
        # cluster were assigned a different ``graph_id`` per request,
        # the frontend would render a fresh node each refresh.
        svc = _make_service()
        svc._cluster_links.add(frozenset({("w2", "g_z"), ("w1", "g_a")}))
        engine_graphs = [
            {"node_id": "w2", "graph_id": "g_z", "creature_ids": [], "channels": []},
            {"node_id": "w1", "graph_id": "g_a", "creature_ids": [], "channels": []},
        ]
        out = svc._fold_clusters(engine_graphs)
        # Lex-smallest (w1, g_a) wins → cluster graph_id == "g_a".
        assert len(out) == 1
        assert out[0]["graph_id"] == "g_a"
        assert out[0]["node_id"] == "w1"

    def test_three_engine_graphs_two_linked_one_alone(self):
        # w1.g_a ↔ w2.g_b are linked.  w3.g_c is alone.  Expect ONE
        # cluster (a+b) + ONE pass-through (c) = 2 entries.
        svc = _make_service()
        svc._cluster_links.add(frozenset({("w1", "g_a"), ("w2", "g_b")}))
        engine_graphs = [
            {"node_id": "w1", "graph_id": "g_a", "creature_ids": ["a"], "channels": []},
            {"node_id": "w2", "graph_id": "g_b", "creature_ids": ["b"], "channels": []},
            {"node_id": "w3", "graph_id": "g_c", "creature_ids": ["c"], "channels": []},
        ]
        out = svc._fold_clusters(engine_graphs)
        assert len(out) == 2
        cluster = next(g for g in out if g.get("is_cluster"))
        loner = next(g for g in out if not g.get("is_cluster"))
        assert set(cluster["creature_ids"]) == {"a", "b"}
        assert loner["creature_ids"] == ["c"]

    def test_transitive_links_form_one_cluster(self):
        # w1.g_a ↔ w2.g_b AND w2.g_b ↔ w3.g_c → all three in one cluster.
        svc = _make_service()
        svc._cluster_links.add(frozenset({("w1", "g_a"), ("w2", "g_b")}))
        svc._cluster_links.add(frozenset({("w2", "g_b"), ("w3", "g_c")}))
        engine_graphs = [
            {"node_id": "w1", "graph_id": "g_a", "creature_ids": ["a"], "channels": []},
            {"node_id": "w2", "graph_id": "g_b", "creature_ids": ["b"], "channels": []},
            {"node_id": "w3", "graph_id": "g_c", "creature_ids": ["c"], "channels": []},
        ]
        out = svc._fold_clusters(engine_graphs)
        assert len(out) == 1
        cluster = out[0]
        assert set(cluster["creature_ids"]) == {"a", "b", "c"}
        assert len(cluster["members"]) == 3

    async def test_wire_creature_rewrites_cluster_graph_id_to_creature_actual(self):
        # Frontend posts ``wire`` on the cluster's primary graph_id —
        # which is graph_a on worker-1.  Creature b lives on worker-2's
        # graph_b.  The service must rewrite ``graph_id`` to graph_b
        # before the worker call, else worker-2's engine raises
        # "graph not found".  The channel-replication call must also
        # use the rewritten graph_id so the channel lands on b's
        # actual graph.
        graph_a = GraphTopology(graph_id="g_a", creature_ids={"a"}, channels={})
        graph_b = GraphTopology(graph_id="g_b", creature_ids={"b"}, channels={})
        w1 = _FakeService(
            node_id="w1",
            graphs=[graph_a],
            creatures=[_info("a", name="alpha", graph_id="g_a")],
            channels_by_graph={"g_a": [ChannelInfo(name="ch2")]},
        )
        w2 = _FakeService(
            node_id="w2",
            graphs=[graph_b],
            creatures=[_info("b", name="bravo", graph_id="g_b")],
            channels_by_graph={"g_b": []},
        )
        w2.wire_creature = AsyncMock()
        svc = _make_service()
        svc._remotes = {"w1": w1, "w2": w2}
        svc._home = {"a": "w1", "b": "w2"}
        svc._coordination_engine = SimpleNamespace(
            _broadcast_adapter=SimpleNamespace(
                proxy_subscribe=AsyncMock(return_value=None)
            )
        )

        # Frontend passes the cluster's primary graph_id ("g_a") —
        # creature b actually lives on g_b.
        await svc.wire_creature("g_a", "b", "ch2", "send")

        # The wire call MUST go to w2 with g_b (b's actual graph_id),
        # not g_a.
        w2.wire_creature.assert_awaited_once_with(
            "g_b", "b", "ch2", "send", enabled=True
        )

    async def test_ensure_channel_replicated_records_cluster_link(self):
        # End-to-end at the unit tier: ``_ensure_channel_replicated``
        # populating ``_cluster_links`` is what makes the snapshot
        # fold the two graphs.
        graph_a = GraphTopology(graph_id="g_a", creature_ids={"a"}, channels={})
        graph_b = GraphTopology(graph_id="g_b", creature_ids={"b"}, channels={})
        w1 = _FakeService(
            node_id="w1",
            graphs=[graph_a],
            channels_by_graph={"g_a": [ChannelInfo(name="my_channel")]},
        )
        w2 = _FakeService(
            node_id="w2",
            graphs=[graph_b],
            channels_by_graph={"g_b": []},
        )
        w2.wire_creature = AsyncMock()
        svc = _make_service()
        svc._remotes = {"w1": w1, "w2": w2}
        svc._home = {"a": "w1", "b": "w2"}

        class _FakeBcast:
            async def proxy_subscribe(self, **kw):
                return None

        svc._coordination_engine = SimpleNamespace(_broadcast_adapter=_FakeBcast())

        await svc.wire_creature("g_b", "b", "my_channel", "listen")

        assert frozenset({("w2", "g_b"), ("w1", "g_a")}) in svc._cluster_links, (
            "wire_creature cross-replication did not record a cluster link — "
            "the snapshot will render TWO separate graphs instead of one"
        )


# ── list_creatures resilience — the "offline cascade" bug ─────────────


class TestListCreaturesResilience:
    """When one worker's ``list_creatures`` fails or stalls, the fan-out
    must NOT (a) wipe the cluster name-cache of the failing worker's
    prior entries, nor (b) hold up returning the healthy workers'
    creatures.  Pre-fix the controller would log
    ``list_creatures failed on worker-1`` and the UI flipped every
    creature on worker-1 to "offline" because the resolver's cache
    lookup miss-fell-through to None.
    """

    async def test_failing_worker_does_not_wipe_prior_cache_entries(self):
        # Two workers: w1 raises (RPC stalled or transient error), w2
        # returns a creature.  w1 had a creature in the cache from an
        # earlier successful list — that entry must SURVIVE the failed
        # fan-out, otherwise downstream "is this creature online?"
        # checks return None and the UI flips it offline.

        class _RaisingService(_FakeService):
            async def list_creatures(self):
                raise RuntimeError("rpc stall")

        bad = _RaisingService(node_id="w1")
        good = _FakeService(
            node_id="w2",
            creatures=[_info("c_good", name="bravo", graph_id="g_w2")],
        )
        svc = _make_service()
        svc._remotes = {"w1": bad, "w2": good}
        # Pre-populate the cache as if a prior list_creatures had
        # succeeded for both workers.
        svc._creature_name_cache = {
            "alpha": ("w1", "c_alpha"),
            "c_alpha": ("w1", "c_alpha"),
        }
        svc._home = {"c_alpha": "w1"}

        listed = await svc.list_creatures()

        # Healthy worker's creature came through.
        listed_ids = {c.creature_id for c in listed}
        assert "c_good" in listed_ids
        # The bug: pre-fix the cache was atomic-swapped to a fresh dict
        # built only from successful workers — w1's "alpha" entry was
        # wiped, cross-node output wiring couldn't resolve it, the UI
        # treated w1 creatures as offline.
        cache = svc._creature_name_cache
        assert cache.get("alpha") == ("w1", "c_alpha"), (
            "failed worker's prior name-cache entry was wiped — this "
            "is the offline cascade.  Cache: " + repr(cache)
        )
        assert cache.get("c_alpha") == ("w1", "c_alpha")
        # And the new worker's entry was merged in.
        assert cache.get("bravo") == ("w2", "c_good")

    async def test_fan_out_runs_in_parallel(self):
        # If listed sequentially, a slow worker holds up every fast one.
        # The user reported w1 stuck "a dozen sec" when w2 spawned —
        # that's the sequential fan-out blocking on a stalled RPC.
        import time

        class _SlowService(_FakeService):
            async def list_creatures(self):
                await asyncio.sleep(0.3)
                return tuple(self._creatures)

        slow = _SlowService(
            node_id="w1", creatures=[_info("slow_cid", name="slow", graph_id="g_w1")]
        )
        fast = _FakeService(
            node_id="w2", creatures=[_info("fast_cid", name="fast", graph_id="g_w2")]
        )
        # Add a SECOND slow worker — sequential cost would be 0.6s.
        slow2 = _SlowService(
            node_id="w3", creatures=[_info("slow2", name="slow2", graph_id="g_w3")]
        )
        svc = _make_service()
        svc._remotes = {"w1": slow, "w2": fast, "w3": slow2}

        t0 = time.monotonic()
        listed = await svc.list_creatures()
        elapsed = time.monotonic() - t0

        # 3 workers × 0.3s sleep = 0.9s sequential vs ~0.3s parallel.
        # 0.55s gives generous CI slack while still failing the
        # sequential-fan-out implementation.
        assert elapsed < 0.55, (
            f"list_creatures took {elapsed:.2f}s for 3 workers (0.3s each) — "
            "expected parallel fan-out (~0.3s).  Sequential fan-out blocks "
            "every UI render on the slowest worker."
        )
        # All workers' creatures still came through.
        ids = {c.creature_id for c in listed}
        assert {"slow_cid", "fast_cid", "slow2"} <= ids
