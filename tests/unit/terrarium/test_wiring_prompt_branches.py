"""Branch-coverage tests for ``output_wiring`` and ``runtime_prompt``:
the cross-node forwarder path, root-creature resolution by id/name, the
activity-notify arms, and the runtime-graph prompt listener loop.
"""

import asyncio
from types import SimpleNamespace

from kohakuterrarium.core.output_wiring import ROOT_TARGET, OutputWiringEntry
from kohakuterrarium.terrarium import runtime_prompt as rp
from kohakuterrarium.terrarium.events import EngineEvent, EventKind
from kohakuterrarium.terrarium.output_wiring import TerrariumOutputWiringResolver
from kohakuterrarium.testing.terrarium import TestTerrariumBuilder

# ---------------------------------------------------------------------------
# output_wiring — fakes
# ---------------------------------------------------------------------------


class _FakeAgent:
    def __init__(self, name="alice", running=True, creature_id=None):
        self.config = SimpleNamespace(name=name)
        self._running = running
        self._creature_id = creature_id or name
        self.output_router = None
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


# ---------------------------------------------------------------------------
# _resolve_graph_root_agent — privileged-creature precedence
# ---------------------------------------------------------------------------


class TestResolveGraphRootAgent:
    def test_root_by_creature_id_among_privileged(self):
        """A privileged creature whose ``creature_id`` is ``"root"`` is
        chosen over other privileged peers in the same graph."""
        root = _FakeCreature(
            "alpha", is_privileged=True, graph_id="g", creature_id=ROOT_TARGET
        )
        other = _FakeCreature(
            "beta", is_privileged=True, graph_id="g", creature_id="cid-beta"
        )
        r = TerrariumOutputWiringResolver({ROOT_TARGET: root, "cid-beta": other})
        assert r._resolve_target(ROOT_TARGET) is root.agent

    def test_root_by_name_among_privileged(self):
        """With no id-``root``, the privileged creature *named* ``root``
        wins."""
        named = _FakeCreature(
            ROOT_TARGET, is_privileged=True, graph_id="g", creature_id="cid-a"
        )
        other = _FakeCreature(
            "beta", is_privileged=True, graph_id="g", creature_id="cid-b"
        )
        r = TerrariumOutputWiringResolver({"cid-a": named, "cid-b": other})
        assert r._resolve_target(ROOT_TARGET) is named.agent


# ---------------------------------------------------------------------------
# emit — cross-node forwarder fallback for unknown local targets
# ---------------------------------------------------------------------------


class _Forwarder:
    def __init__(self, peer):
        self._peer = peer
        self.forwarded = []

    def peer_for_target(self, name):
        return self._peer

    async def forward_event(self, peer, payload):
        self.forwarded.append((peer, payload))


class TestEmitCrossNodeForward:
    async def test_unknown_local_target_forwarded_to_peer(self):
        """When a target isn't a local creature but a remote forwarder
        knows a peer for it, the emission is forwarded over the wire."""
        forwarder = _Forwarder(peer="node-2")
        engine = SimpleNamespace(_output_wire_adapter=forwarder)
        r = TerrariumOutputWiringResolver({}, engine=engine)
        await r.emit(
            source="alice",
            content="payload",
            source_event_type="text",
            turn_index=3,
            entries=[OutputWiringEntry(to="remote_bob", with_content=True)],
        )
        await asyncio.sleep(0.05)
        assert forwarder.forwarded
        peer, payload = forwarder.forwarded[0]
        assert peer == "node-2"
        assert payload["target_name"] == "remote_bob"
        assert payload["content"] == "payload"
        assert payload["turn_index"] == 3

    async def test_unknown_target_no_peer_is_skipped(self):
        """When the forwarder has no peer for the name, the emission is
        dropped — the target is recorded so it isn't re-warned."""
        forwarder = _Forwarder(peer=None)
        engine = SimpleNamespace(_output_wire_adapter=forwarder)
        r = TerrariumOutputWiringResolver({}, engine=engine)
        await r.emit(
            source="alice",
            content="x",
            source_event_type="text",
            turn_index=1,
            entries=[OutputWiringEntry(to="ghost")],
        )
        await asyncio.sleep(0.02)
        assert forwarder.forwarded == []
        assert "ghost" in r._warned_missing


# ---------------------------------------------------------------------------
# emit — activity-notify preview truncation + exception tolerance
# ---------------------------------------------------------------------------


class TestEmitActivityNotify:
    async def test_long_content_preview_is_truncated(self):
        """The ``wire_inbound`` activity preview is capped at 240 chars
        with an ellipsis."""

        class _Router:
            def __init__(self):
                self.activities = []

            def notify_activity(self, type_, detail, metadata=None):
                self.activities.append((type_, detail, metadata))

        bob = _FakeCreature("bob")
        bob.agent.output_router = _Router()
        r = TerrariumOutputWiringResolver({"bob": bob})
        long_content = "x" * 500
        await r.emit(
            source="alice",
            content=long_content,
            source_event_type="text",
            turn_index=1,
            entries=[OutputWiringEntry(to="bob", with_content=True)],
        )
        await asyncio.sleep(0.05)
        meta = bob.agent.output_router.activities[0][2]
        preview = meta["content_preview"]
        assert preview.endswith("…")
        assert len(preview) <= 240

    async def test_router_notify_exception_is_swallowed(self):
        """A receiver router whose ``notify_activity`` raises does not
        block the actual delivery — the event still reaches the
        receiver."""

        class _BadRouter:
            def notify_activity(self, *a, **kw):
                raise RuntimeError("router exploded")

        bob = _FakeCreature("bob")
        bob.agent.output_router = _BadRouter()
        r = TerrariumOutputWiringResolver({"bob": bob})
        await r.emit(
            source="alice",
            content="still delivered",
            source_event_type="text",
            turn_index=1,
            entries=[OutputWiringEntry(to="bob", with_content=True)],
        )
        await asyncio.sleep(0.05)
        # Delivery proceeded despite the router blowing up.
        assert len(bob.agent.events) == 1
        assert bob.agent.events[0].content == "still delivered"


# ---------------------------------------------------------------------------
# runtime_prompt — listener loop + descriptions + refresh failure
# ---------------------------------------------------------------------------


class TestRuntimeGraphPromptLoop:
    async def test_run_loop_refreshes_on_real_event(self):
        """The ``_run`` listener loop, fed a real refresh-kind event
        through ``engine.subscribe``, schedules a debounced refresh for
        the affected creature."""
        t = await TestTerrariumBuilder().with_creature("alice").build()
        try:
            prompt = rp.RuntimeGraphPrompt(t)
            prompt.attach()
            await asyncio.sleep(0)  # let _run start subscribing
            # Emit a refresh-kind event; the loop should pick it up.
            t._emit(EngineEvent(kind=EventKind.CREATURE_STARTED, creature_id="alice"))
            # Give the loop a tick to consume + schedule.
            await asyncio.sleep(0.02)
            assert "alice" in prompt._pending
            prompt.detach()
        finally:
            await t.shutdown()

    async def test_do_refresh_swallows_build_failure(self, monkeypatch):
        """A failure inside ``build_runtime_graph_section`` during a
        debounced refresh is caught — the refresh is a no-op, not a
        crash."""
        t = await TestTerrariumBuilder().with_creature("alice").build()
        try:
            prompt = rp.RuntimeGraphPrompt(t)

            def _boom(engine, creature):
                raise RuntimeError("section build failed")

            monkeypatch.setattr(rp, "build_runtime_graph_section", _boom)
            # Must not raise.
            prompt._do_refresh("alice")
        finally:
            await t.shutdown()

    async def test_section_renders_channel_descriptions(self):
        """``build_runtime_graph_section`` renders each listen/send
        channel with its topology description."""
        t = await (
            TestTerrariumBuilder()
            .with_creature("alice")
            .with_creature("bob")
            .with_channel("chat", description="team chat")
            .with_connection("alice", "bob", channel="chat")
            .build()
        )
        try:
            # alice sends on "chat", bob listens — both carry the edge.
            alice = t.get_creature("alice")
            bob = t.get_creature("bob")
            alice_section = rp.build_runtime_graph_section(t, alice)
            bob_section = rp.build_runtime_graph_section(t, bob)
            # The channel description shows up in the rendered block.
            assert "team chat" in alice_section + bob_section
            # alice's section shows the send channel, bob's the listen.
            assert "chat" in alice_section
            assert "chat" in bob_section
        finally:
            await t.shutdown()
