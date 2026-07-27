"""Unit tests for :mod:`kohakuterrarium.terrarium.output_wiring`."""

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace


from kohakuterrarium.core.output_wiring import ROOT_TARGET, OutputWiringEntry
from kohakuterrarium.terrarium.output_wiring import (
    TerrariumOutputWiringResolver,
    _log_task_error,
    _safe_deliver,
)
from kohakuterrarium.terrarium.topology import GraphTopology, TopologyState

# ── fakes ─────────────────────────────────────────────────────────


@dataclass
class _FakeConfig:
    name: str = "alice"


class _FakeRouter:
    def __init__(self):
        self.activities = []

    def notify_activity(self, type_, detail, metadata=None):
        self.activities.append((type_, detail, metadata))


class _FakeAgent:
    def __init__(self, name="alice", running=True, creature_id=None):
        self.config = _FakeConfig(name=name)
        self._running = running
        self._creature_id = creature_id or name
        self.output_router = _FakeRouter()
        self.events = []

    async def _process_event(self, event):
        self.events.append(event)


class _FakeCreature:
    def __init__(self, name, *, is_privileged=False, graph_id=None, creature_id=None):
        self.name = name
        self.is_privileged = is_privileged
        self.graph_id = graph_id
        self.creature_id = creature_id or name
        self.agent = _FakeAgent(name=name, creature_id=self.creature_id)


# ── _resolve_target ──────────────────────────────────────────────


class TestResolveTarget:
    def test_known_name(self):
        creatures = {"alice": _FakeCreature("alice")}
        r = TerrariumOutputWiringResolver(creatures)
        assert r._resolve_target("alice") is creatures["alice"].agent

    def test_unknown_name(self):
        r = TerrariumOutputWiringResolver({})
        assert r._resolve_target("ghost") is None

    def test_root_target_with_explicit_root_agent(self):
        root_agent = _FakeAgent(name="root")
        r = TerrariumOutputWiringResolver({}, root_agent=root_agent)
        # No privileged creatures → falls back to root_agent.
        assert r._resolve_target(ROOT_TARGET) is root_agent

    def test_root_target_picks_privileged_creature(self):
        priv = _FakeCreature("priv", is_privileged=True, graph_id="g")
        r = TerrariumOutputWiringResolver({"priv": priv})
        out = r._resolve_target(ROOT_TARGET)
        assert out is priv.agent

    def test_root_target_no_root_returns_none(self):
        r = TerrariumOutputWiringResolver({})
        assert r._resolve_target(ROOT_TARGET) is None

    def test_root_per_graph(self):
        # Two privileged roots in different graphs; the source's graph
        # decides which one wins.
        ra = _FakeCreature("ra", is_privileged=True, graph_id="g1")
        rb = _FakeCreature("rb", is_privileged=True, graph_id="g2")
        source = _FakeCreature("src", graph_id="g2")
        r = TerrariumOutputWiringResolver({"ra": ra, "rb": rb, "src": source})
        out = r._resolve_target(ROOT_TARGET, source="src")
        assert out is rb.agent

    def test_engine_backed_root_uses_topology_not_stale_handle_graph(self):
        ra = _FakeCreature("ra", is_privileged=True, graph_id="stale")
        rb = _FakeCreature("rb", is_privileged=True, graph_id="g2")
        source = _FakeCreature("src", graph_id="g2")
        creatures = {"ra": ra, "rb": rb, "src": source}
        topology = TopologyState(
            graphs={
                "g1": GraphTopology(graph_id="g1", creature_ids={"ra", "src"}),
                "g2": GraphTopology(graph_id="g2", creature_ids={"rb"}),
            },
            creature_to_graph={"ra": "g1", "rb": "g2", "src": "g1"},
        )
        engine = SimpleNamespace(_topology=topology)
        r = TerrariumOutputWiringResolver(creatures, engine=engine)

        assert r._resolve_target(ROOT_TARGET, source="src") is ra.agent

    def test_engine_backed_root_rejects_unknown_source(self):
        root = _FakeCreature("root", is_privileged=True, graph_id="g")
        topology = TopologyState(
            graphs={"g": GraphTopology(graph_id="g", creature_ids={"root"})},
            creature_to_graph={"root": "g"},
        )
        r = TerrariumOutputWiringResolver(
            {"root": root}, engine=SimpleNamespace(_topology=topology)
        )

        assert r._resolve_target(ROOT_TARGET, source="forged") is None


# ── _warn_once ───────────────────────────────────────────────────


class TestWarnOnce:
    def test_only_first_logs(self):
        r = TerrariumOutputWiringResolver({})
        # First call adds to set; second is silent.
        r._warn_once("X", "reason")
        assert "X" in r._warned_missing
        r._warn_once("X", "reason")  # no-op


# ── _resolve_handle by name fallback ─────────────────────────────


class TestResolveHandle:
    def test_match_by_name_field(self):
        # Store under a different key than ``name``.
        c = _FakeCreature("alice")
        r = TerrariumOutputWiringResolver({"some-key": c})
        assert r._resolve_handle("alice") is c

    def test_match_by_config_name(self):
        c = _FakeCreature("alice")
        c.agent.config.name = "alpha"
        c.name = "other"
        r = TerrariumOutputWiringResolver({"some-key": c})
        # ``alpha`` matches via the agent.config.name field.
        assert r._resolve_handle("alpha") is c

    def test_no_match(self):
        r = TerrariumOutputWiringResolver({})
        assert r._resolve_handle("nope") is None


# ── _target_identity ─────────────────────────────────────────────


class TestTargetIdentity:
    def test_root_uses_creature_id(self):
        ag = _FakeAgent(name="r", creature_id="cr1")
        r = TerrariumOutputWiringResolver({})
        assert r._target_identity(ROOT_TARGET, ag) == "cr1"

    def test_normal_target(self):
        ag = _FakeAgent(name="alice", creature_id="cid-alice")
        r = TerrariumOutputWiringResolver({})
        assert r._target_identity("alice", ag) == "cid-alice"


# ── emit ─────────────────────────────────────────────────────────


class TestEmit:
    async def test_remote_event_preserves_standard_target_context(self):
        forwarded = asyncio.Event()

        class _Forwarder:
            payload = None

            def peer_for_target(self, target, *, graph_id):
                return "_host"

            async def forward_event(self, **kwargs):
                self.payload = kwargs
                forwarded.set()
                return True

        source = _FakeCreature("source", creature_id="source", graph_id="g")
        topology = TopologyState(
            graphs={"g": GraphTopology(graph_id="g", creature_ids={"source"})},
            creature_to_graph={"source": "g"},
        )
        forwarder = _Forwarder()
        engine = SimpleNamespace(
            _creatures={"source": source},
            _topology=topology,
            _output_wire_adapter=forwarder,
        )
        resolver = TerrariumOutputWiringResolver(engine._creatures, engine=engine)

        await resolver.emit(
            source="source",
            content="hello",
            source_event_type="turn_end",
            turn_index=7,
            entries=[OutputWiringEntry(to="remote-target")],
        )
        await asyncio.wait_for(forwarded.wait(), timeout=1)

        assert forwarder.payload["event"]["context"]["target"] == "remote-target"

    async def test_unknown_target_skipped(self):
        r = TerrariumOutputWiringResolver({})
        await r.emit(
            source="alice",
            content="hi",
            source_event_type="text",
            turn_index=1,
            entries=[OutputWiringEntry(to="ghost")],
        )
        # The unresolved target is recorded so it isn't re-warned every
        # turn — that's the observable side effect of "skipped".
        assert "ghost" in r._warned_missing

    async def test_emits_to_running_target(self):
        creatures = {"bob": _FakeCreature("bob")}
        r = TerrariumOutputWiringResolver(creatures)
        await r.emit(
            source="alice",
            content="hello",
            source_event_type="text",
            turn_index=1,
            entries=[OutputWiringEntry(to="bob", with_content=True)],
        )
        # Wait for fire-and-forget task.
        await asyncio.sleep(0.05)
        events = creatures["bob"].agent.events
        assert len(events) == 1
        # The delivered event carries the source's content and identity.
        assert events[0].content == "hello"
        assert events[0].context["source"] == "alice"

    async def test_blocks_self_trigger(self):
        alice = _FakeCreature("alice")
        r = TerrariumOutputWiringResolver({"alice": alice})
        await r.emit(
            source="alice",
            content="hi",
            source_event_type="text",
            turn_index=1,
            entries=[OutputWiringEntry(to="alice")],
        )
        await asyncio.sleep(0.05)
        # Self-trigger blocked; agent received no events.
        assert alice.agent.events == []

    async def test_allow_self_trigger(self):
        alice = _FakeCreature("alice")
        r = TerrariumOutputWiringResolver({"alice": alice})
        await r.emit(
            source="alice",
            content="hi",
            source_event_type="text",
            turn_index=1,
            entries=[OutputWiringEntry(to="alice", allow_self_trigger=True)],
        )
        await asyncio.sleep(0.05)
        assert len(alice.agent.events) == 1

    async def test_skips_stopped_target(self):
        bob = _FakeCreature("bob")
        bob.agent._running = False
        r = TerrariumOutputWiringResolver({"bob": bob})
        await r.emit(
            source="alice",
            content="hi",
            source_event_type="text",
            turn_index=1,
            entries=[OutputWiringEntry(to="bob")],
        )
        await asyncio.sleep(0.05)
        assert bob.agent.events == []

    async def test_with_content_false_strips(self):
        bob = _FakeCreature("bob")
        r = TerrariumOutputWiringResolver({"bob": bob})
        await r.emit(
            source="alice",
            content="should be stripped",
            source_event_type="text",
            turn_index=1,
            entries=[OutputWiringEntry(to="bob", with_content=False)],
        )
        await asyncio.sleep(0.05)
        # with_content=False → the event is delivered but its content
        # field is emptied (metadata ping, not a content delivery).
        assert len(bob.agent.events) == 1
        assert bob.agent.events[0].content == ""

    async def test_with_content_true_delivers_content(self):
        bob = _FakeCreature("bob")
        r = TerrariumOutputWiringResolver({"bob": bob})
        await r.emit(
            source="alice",
            content="real payload",
            source_event_type="text",
            turn_index=1,
            entries=[OutputWiringEntry(to="bob", with_content=True)],
        )
        await asyncio.sleep(0.05)
        # with_content=True → the source's text reaches the receiver.
        assert len(bob.agent.events) == 1
        assert bob.agent.events[0].content == "real payload"

    async def test_notifies_activity_on_receiver(self):
        bob = _FakeCreature("bob")
        r = TerrariumOutputWiringResolver({"bob": bob})
        await r.emit(
            source="alice",
            content="hello",
            source_event_type="text",
            turn_index=1,
            entries=[OutputWiringEntry(to="bob", with_content=True)],
        )
        await asyncio.sleep(0.05)
        # Receiver router got a wire_inbound activity.
        types = [t for t, _, _ in bob.agent.output_router.activities]
        assert "wire_inbound" in types
        # Sender turn travels as source_turn_index; a plain turn_index
        # would collide with receiver-timeline frame/row stamps and the
        # frontend branch gate drops such mixed-coordinate frames.
        meta = bob.agent.output_router.activities[types.index("wire_inbound")][2]
        assert meta["source_turn_index"] == 1
        assert "turn_index" not in meta


# ── _safe_deliver ────────────────────────────────────────────────


class TestSafeDeliver:
    async def test_no_error_passes_through(self):
        a = _FakeAgent()
        await _safe_deliver(a, {"x": 1})
        assert a.events == [{"x": 1}]

    async def test_swallow_exception(self):
        class _Bad(_FakeAgent):
            async def _process_event(self, event):
                raise RuntimeError("boom")

        a = _Bad()
        # Doesn't raise.
        await _safe_deliver(a, {"x": 1})


# ── _log_task_error ──────────────────────────────────────────────


class TestGraphLocalOutputResolution:
    def test_same_name_in_other_graph_does_not_win(self):
        from types import SimpleNamespace

        from kohakuterrarium.terrarium.topology import GraphTopology, TopologyState

        source = _FakeCreature("source", creature_id="source")
        local = _FakeCreature("worker", creature_id="local")
        foreign = _FakeCreature("worker", creature_id="foreign")
        creatures = {
            source.creature_id: source,
            local.creature_id: local,
            foreign.creature_id: foreign,
        }
        topology = TopologyState(
            graphs={
                "g-local": GraphTopology(
                    graph_id="g-local", creature_ids={"source", "local"}
                ),
                "g-foreign": GraphTopology(
                    graph_id="g-foreign", creature_ids={"foreign"}
                ),
            },
            creature_to_graph={
                "source": "g-local",
                "local": "g-local",
                "foreign": "g-foreign",
            },
        )
        engine = SimpleNamespace(_creatures=creatures, _topology=topology)
        resolver = TerrariumOutputWiringResolver(creatures, engine=engine)

        assert resolver._resolve_target("worker", source="source") is local.agent
        assert resolver._resolve_target("foreign", source="source") is None


class TestLogTaskError:
    async def test_cancelled_task_no_log(self):
        async def coro():
            await asyncio.sleep(100)

        task = asyncio.create_task(coro())
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        _log_task_error(task, "alice", "bob")  # no raise

    async def test_successful_task_no_log(self):
        async def coro():
            return 1

        task = asyncio.create_task(coro())
        await task
        _log_task_error(task, "alice", "bob")  # no raise

    async def test_errored_task_logs(self):
        async def coro():
            raise RuntimeError("boom")

        task = asyncio.create_task(coro())
        try:
            await task
        except RuntimeError:
            pass
        # Doesn't raise; just logs.
        _log_task_error(task, "alice", "bob")
