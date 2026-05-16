"""Branch-coverage tests for the engine-side helper modules:
``resume``, ``root``, ``observer``, ``config``, ``recipe``, ``wire``,
``channel_lifecycle`` and ``creature_ops`` — the defensive arms and
rarely-hit paths the happy-path suites don't reach.

All engine flows run through a real :class:`Terrarium` via
``TestTerrariumBuilder`` so the asserted side effects are real.
"""

import asyncio
from dataclasses import is_dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.terrarium import channel_lifecycle as cl
from kohakuterrarium.terrarium import config as config_mod
from kohakuterrarium.terrarium import creature_ops as co
from kohakuterrarium.terrarium import recipe as recipe_mod
from kohakuterrarium.terrarium import resume as resume_mod
from kohakuterrarium.terrarium import root as root_mod
from kohakuterrarium.terrarium import wire as wire_mod
from kohakuterrarium.terrarium.config import ChannelConfig, CreatureConfig
from kohakuterrarium.terrarium.events import EventKind
from kohakuterrarium.testing.terrarium import TestTerrariumBuilder, _FakeAgent
from kohakuterrarium.terrarium.creature_host import Creature

# ---------------------------------------------------------------------------
# resume._resolve_store_path — real SessionStore instance
# ---------------------------------------------------------------------------


class TestResolveStorePathRealStore:
    def test_real_session_store_uses_its_path(self, tmp_path):
        """A genuine :class:`SessionStore` resolves via its ``.path``
        attribute — exercises the ``isinstance(store, SessionStore)``
        branch the SimpleNamespace test can't reach."""
        store_path = tmp_path / "real.kohakutr"
        store = SessionStore(str(store_path))
        try:
            resolved = resume_mod._resolve_store_path(store)
            assert isinstance(resolved, Path)
            assert resolved == Path(store.path)
        finally:
            store.close()


# ---------------------------------------------------------------------------
# resume._resume_terrarium_into_engine — name-match branches
# ---------------------------------------------------------------------------


class TestTerrariumResumeNameMatch:
    async def test_fresh_name_in_saved_set_used_directly(self, monkeypatch, tmp_path):
        """When the rebuilt creature's name is already in the saved
        agents list, that name is used directly (no positional pull)."""
        monkeypatch.setattr(resume_mod, "detect_session_type", lambda p: "terrarium")
        fake_store = SimpleNamespace(
            load_meta=lambda: {
                "config_path": "/tmp/recipe.yaml",
                "pwd": ".",
                "agents": ["alice"],
            },
            update_status=lambda s: None,
        )
        monkeypatch.setattr(
            resume_mod, "_open_store_with_migration", lambda p: fake_store
        )
        from kohakuterrarium.terrarium.config import TerrariumConfig

        monkeypatch.setattr(
            resume_mod,
            "load_terrarium_config",
            lambda p: TerrariumConfig(name="t", creatures=[], channels=[]),
        )
        injected = []
        monkeypatch.setattr(
            resume_mod,
            "inject_saved_state",
            lambda agent, store, name: injected.append(name),
        )

        engine_holder = {}

        async def _fake_apply_recipe(config, pwd=None, **_):
            t = engine_holder["t"]
            agent = _FakeAgent(name="alice")
            agent.config = SimpleNamespace(name="alice")
            agent.attach_session_store = lambda s: None
            c = Creature(
                creature_id="alice", name="alice", agent=agent, config=agent.config
            )
            await t.add_creature(c, start=False)
            return t._topology.graphs[c.graph_id]

        t = await TestTerrariumBuilder().build()
        engine_holder["t"] = t
        t.apply_recipe = _fake_apply_recipe
        from unittest.mock import AsyncMock

        t.attach_session = AsyncMock()
        try:
            await resume_mod.resume_into_engine(t, tmp_path / "saved.kohakutr")
            # ``alice`` matched the saved set → injected under "alice".
            assert injected == ["alice"]
            assert t.get_creature("alice").name == "alice"
        finally:
            await t.shutdown()

    async def test_missing_creature_in_graph_is_skipped(self, monkeypatch, tmp_path):
        """A creature_id present in the graph but absent from
        ``engine._creatures`` is skipped during per-creature injection
        rather than crashing the resume."""
        monkeypatch.setattr(resume_mod, "detect_session_type", lambda p: "terrarium")
        fake_store = SimpleNamespace(
            load_meta=lambda: {
                "config_path": "/tmp/recipe.yaml",
                "pwd": ".",
                "agents": ["alice"],
            },
            update_status=lambda s: None,
        )
        monkeypatch.setattr(
            resume_mod, "_open_store_with_migration", lambda p: fake_store
        )
        from kohakuterrarium.terrarium.config import TerrariumConfig

        monkeypatch.setattr(
            resume_mod,
            "load_terrarium_config",
            lambda p: TerrariumConfig(name="t", creatures=[], channels=[]),
        )
        monkeypatch.setattr(resume_mod, "inject_saved_state", lambda *a, **kw: None)

        engine_holder = {}

        async def _fake_apply_recipe(config, pwd=None, **_):
            t = engine_holder["t"]
            agent = _FakeAgent(name="alice")
            agent.config = SimpleNamespace(name="alice")
            agent.attach_session_store = lambda s: None
            c = Creature(
                creature_id="alice", name="alice", agent=agent, config=agent.config
            )
            await t.add_creature(c, start=False)
            graph = t._topology.graphs[c.graph_id]
            # Inject a phantom id into graph membership that has no
            # backing creature — the resume loop must skip it.
            graph.creature_ids.add("phantom")
            return graph

        t = await TestTerrariumBuilder().build()
        engine_holder["t"] = t
        t.apply_recipe = _fake_apply_recipe
        from unittest.mock import AsyncMock

        t.attach_session = AsyncMock()
        try:
            # Must not raise despite the phantom membership entry.
            gid = await resume_mod.resume_into_engine(t, tmp_path / "saved.kohakutr")
            assert gid
        finally:
            await t.shutdown()


# ---------------------------------------------------------------------------
# root.assign_root_to — defensive None branches
# ---------------------------------------------------------------------------


class TestAssignRootDefensive:
    async def test_missing_graph_raises(self):
        """A creature whose ``graph_id`` is not in the topology raises
        a clean ``KeyError`` instead of an obscure crash."""
        t = await TestTerrariumBuilder().with_creature("alice").build()
        try:
            alice = t.get_creature("alice")
            alice.graph_id = "ghost-graph"
            with pytest.raises(KeyError, match="graph"):
                await root_mod.assign_root_to(t, alice)
        finally:
            await t.shutdown()

    async def test_phantom_member_skipped_when_wiring_senders(self):
        """``assign_root`` skips a creature_id in graph membership that
        has no backing creature while wiring report-channel senders."""
        t = await (
            TestTerrariumBuilder().with_creature("root").with_creature("bob").build()
        )
        try:
            root = t.get_creature("root")
            graph = t.get_graph(root.graph_id)
            # Phantom id: in membership, no creature object.
            graph.creature_ids.add("phantom")
            result = await root_mod.assign_root_to(t, root)
            # The real peer "bob" is a sender; "phantom" was skipped.
            assert "bob" in result.senders_added
            assert "phantom" not in result.senders_added
            assert t.get_creature("root").is_privileged is True
        finally:
            await t.shutdown()


# ---------------------------------------------------------------------------
# config.build_channel_topology_prompt — broadcast-channel branch
# ---------------------------------------------------------------------------


class TestConfigBroadcastChannel:
    def test_broadcast_channel_added_to_relevant_names(self):
        """A recipe channel declared ``channel_type='broadcast'`` is
        surfaced in the team-communication prompt even when the creature
        has no explicit listen/send edge on it."""
        creature = CreatureConfig(
            name="alice",
            config_data={"name": "alice"},
            base_dir=Path("."),
            listen_channels=[],
            send_channels=[],
        )
        config = SimpleNamespace(
            channels=[
                ChannelConfig(
                    name="townhall",
                    description="all hands",
                    channel_type="broadcast",
                )
            ],
            creatures=[creature],
        )
        # townhall is broadcast → it must appear in the rendered block
        # despite alice having no edge on it.
        block = config_mod.build_channel_topology_prompt(config, creature)
        assert "townhall" in block


# ---------------------------------------------------------------------------
# recipe — path-string load + custom-builder env binding + bad send edge
# ---------------------------------------------------------------------------


class TestRecipeBranches:
    def test_resolve_recipe_loads_from_path(self, monkeypatch):
        """``_resolve_recipe`` of a path string routes through
        ``load_terrarium_config``."""
        sentinel = object()
        monkeypatch.setattr(recipe_mod, "load_terrarium_config", lambda p: sentinel)
        assert recipe_mod._resolve_recipe("some/recipe.yaml") is sentinel

    def test_build_recipe_creature_custom_builder_binds_env(self):
        """A custom builder's creature has its agent + executor
        repointed at the graph environment."""
        from kohakuterrarium.core.environment import Environment

        env = Environment(env_id="env-x")
        executor = SimpleNamespace(_environment=None)
        agent = SimpleNamespace(environment=None, executor=executor)
        creature = SimpleNamespace(agent=agent)

        def _builder(cfg, *, creature_id, pwd):
            return creature

        cfg = CreatureConfig(
            name="alice", config_data={"name": "alice"}, base_dir=Path(".")
        )
        out = recipe_mod._build_recipe_creature(
            _builder,
            cfg,
            creature_id="alice",
            pwd=None,
            llm_override=None,
            env=env,
            use_default_builder=False,
        )
        assert out is creature
        assert agent.environment is env
        assert executor._environment is env

    async def test_apply_recipe_skips_undeclared_send_channel(self):
        """A recipe creature whose ``send_channels`` names a channel
        that was never declared is wired skipping that channel — no
        crash, the creature still lands in the graph."""
        from kohakuterrarium.terrarium.config import TerrariumConfig

        cfg = TerrariumConfig(
            name="t",
            creatures=[
                CreatureConfig(
                    name="alice",
                    config_data={"name": "alice"},
                    base_dir=Path("."),
                    listen_channels=[],
                    send_channels=["never_declared"],
                )
            ],
            channels=[],
        )
        engine_holder = {}

        def _fake_builder(c, *, creature_id=None, pwd=None, **_):
            agent = _FakeAgent(name=c.name)
            agent.config = SimpleNamespace(name=c.name)
            return Creature(
                creature_id=creature_id or c.name,
                name=c.name,
                agent=agent,
                config=c,
            )

        t = await TestTerrariumBuilder().build()
        engine_holder["t"] = t
        try:
            graph = await recipe_mod.apply_recipe(
                t, cfg, creature_builder=_fake_builder
            )
            # alice landed in the graph; the undeclared send channel was
            # silently skipped (not added to her send_channels).
            assert "alice" in graph.creature_ids
            assert "never_declared" not in t.get_creature("alice").send_channels
        finally:
            await t.shutdown()


# ---------------------------------------------------------------------------
# wire.pack_creature_build_input — CreatureConfig rejection
# ---------------------------------------------------------------------------


class TestWireCreatureConfigRejected:
    def test_creature_config_rejected_for_remote(self):
        """A ``CreatureConfig`` carries a controller-local ``base_dir``
        Path — it cannot be packed for a remote ``add_creature`` and is
        rejected with a clear error."""
        cfg = CreatureConfig(
            name="alice", config_data={"name": "alice"}, base_dir=Path(".")
        )
        assert is_dataclass(cfg)
        with pytest.raises(wire_mod.RemoteAddCreatureError, match="CreatureConfig"):
            wire_mod.pack_creature_build_input(cfg)


# ---------------------------------------------------------------------------
# channel_lifecycle.apply_split_bookkeeping — multi-channel split with
# surviving listen edges on both new components
# ---------------------------------------------------------------------------


class TestSplitBookkeepingRewires:
    async def test_split_repoints_and_reinjects_listen_triggers(self):
        """A 4-creature graph A-B + C-D + B-C(bridge): removing the
        bridge channel splits it into {A,B} and {C,D}. The split
        bookkeeping must allocate fresh envs, register each component's
        surviving channels, repoint creatures, and re-inject the
        still-live listen triggers."""
        t = await (
            TestTerrariumBuilder()
            .with_creature("a")
            .with_creature("b")
            .with_creature("c")
            .with_creature("d")
            .with_channel("ch_ab")
            .with_channel("ch_cd")
            .with_channel("ch_bc")
            .with_connection("a", "b", channel="ch_ab")
            .with_connection("c", "d", channel="ch_cd")
            .with_connection("b", "c", channel="ch_bc")
            .build()
        )
        try:
            # One connected component before the bridge removal.
            assert len(t.list_graphs()) == 1
            gid = t.get_creature("b").graph_id
            delta = await cl.remove_channel_from_graph(t, gid, "ch_bc")
            assert delta.kind == "split"
            # Two components now; each keeps its intra-component channel.
            assert len(t.list_graphs()) == 2
            a_gid = t.get_creature("a").graph_id
            c_gid = t.get_creature("c").graph_id
            assert a_gid != c_gid
            # The surviving channels were registered into the new envs.
            a_env = t._environments[a_gid]
            c_env = t._environments[c_gid]
            assert "ch_ab" in a_env.shared_channels.list_channels()
            assert "ch_cd" in c_env.shared_channels.list_channels()
            # The still-live listen edges survived the split.
            assert "ch_ab" in t.get_creature("b").listen_channels
            assert "ch_cd" in t.get_creature("d").listen_channels
        finally:
            await t.shutdown()


# ---------------------------------------------------------------------------
# creature_ops.wire_creature_on_engine — the engine-level wire primitive
# ---------------------------------------------------------------------------


class TestWireCreatureOnEngine:
    async def test_wire_listen_injects_trigger_and_emits(self):
        """``wire_creature_on_engine`` toggling a listen edge registers
        the channel trigger, updates ``listen_channels`` and emits
        TOPOLOGY_CHANGED."""
        t = await (
            TestTerrariumBuilder().with_creature("alice").with_channel("chat").build()
        )
        try:
            alice = t.get_creature("alice")
            gid = alice.graph_id
            events = []

            async def _collect():
                async for ev in t.subscribe():
                    events.append(ev)

            collector = asyncio.create_task(_collect())
            await asyncio.sleep(0)

            co.wire_creature_on_engine(t, gid, "alice", "chat", "listen")
            await asyncio.sleep(0.02)
            collector.cancel()

            assert "chat" in alice.listen_channels
            assert "chat" in t.get_graph(gid).listen_edges.get("alice", set())
            assert any(ev.kind == EventKind.TOPOLOGY_CHANGED for ev in events)
        finally:
            await t.shutdown()

    async def test_wire_send_updates_send_channels(self):
        t = await (
            TestTerrariumBuilder().with_creature("alice").with_channel("chat").build()
        )
        try:
            alice = t.get_creature("alice")
            gid = alice.graph_id
            co.wire_creature_on_engine(t, gid, "alice", "chat", "send")
            assert "chat" in alice.send_channels
            # Unwiring removes it again.
            co.wire_creature_on_engine(t, gid, "alice", "chat", "send", enabled=False)
            assert "chat" not in alice.send_channels
        finally:
            await t.shutdown()

    async def test_wire_unknown_creature_raises(self):
        t = await (
            TestTerrariumBuilder().with_creature("alice").with_channel("chat").build()
        )
        try:
            gid = t.get_creature("alice").graph_id
            with pytest.raises(KeyError, match="creature"):
                co.wire_creature_on_engine(t, gid, "ghost", "chat", "listen")
        finally:
            await t.shutdown()

    async def test_wire_unknown_channel_raises(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        try:
            gid = t.get_creature("alice").graph_id
            with pytest.raises(KeyError, match="channel"):
                co.wire_creature_on_engine(t, gid, "alice", "ghost", "listen")
        finally:
            await t.shutdown()

    async def test_wire_invalid_direction_raises(self):
        t = await (
            TestTerrariumBuilder().with_creature("alice").with_channel("chat").build()
        )
        try:
            gid = t.get_creature("alice").graph_id
            with pytest.raises(ValueError, match="direction must be"):
                co.wire_creature_on_engine(t, gid, "alice", "chat", "sideways")
        finally:
            await t.shutdown()

    async def test_wire_root_keyword_resolves_to_privileged_creature(self):
        """``creature_id='root'`` resolves to the graph's privileged
        creature."""
        t = await (
            TestTerrariumBuilder().with_creature("root").with_channel("chat").build()
        )
        t.get_creature("root").is_privileged = True
        try:
            gid = t.get_creature("root").graph_id
            co.wire_creature_on_engine(t, gid, "root", "chat", "listen")
            assert "chat" in t.get_creature("root").listen_channels
        finally:
            await t.shutdown()

    async def test_wire_root_keyword_without_privileged_raises(self):
        """When no creature in the graph is privileged, the ``root``
        keyword cannot resolve and a ``KeyError`` is raised."""
        t = await (
            TestTerrariumBuilder().with_creature("alice").with_channel("chat").build()
        )
        try:
            gid = t.get_creature("alice").graph_id
            with pytest.raises(KeyError, match="no privileged creature"):
                co.wire_creature_on_engine(t, gid, "root", "chat", "listen")
        finally:
            await t.shutdown()

    async def test_unwire_listen_removes_trigger_and_channel(self):
        """Disabling a listen edge via ``wire_creature_on_engine``
        removes the channel trigger and the ``listen_channels`` entry."""
        t = await (
            TestTerrariumBuilder().with_creature("alice").with_channel("chat").build()
        )
        try:
            alice = t.get_creature("alice")
            gid = alice.graph_id
            co.wire_creature_on_engine(t, gid, "alice", "chat", "listen")
            assert "chat" in alice.listen_channels
            co.wire_creature_on_engine(t, gid, "alice", "chat", "listen", enabled=False)
            assert "chat" not in alice.listen_channels
        finally:
            await t.shutdown()


# ---------------------------------------------------------------------------
# creature_ops.agent_execute_command — builtin command happy path
# ---------------------------------------------------------------------------


class TestAgentExecuteCommand:
    async def test_help_command_returns_normalized_dict(self):
        """A known builtin command runs and its result is normalised to
        the JSON-friendly ``{command, output, error, success}`` dict."""
        agent = _FakeAgent(name="alice")
        agent.session = None
        result = await co.agent_execute_command(agent, "help")
        assert result["command"] == "help"
        assert result["success"] is True
        assert result["output"]


# ---------------------------------------------------------------------------
# creature_ops._channels_for_graph — channel-with-history serialisation
# ---------------------------------------------------------------------------


class TestChannelsForGraph:
    async def test_channel_history_count_reflected(self):
        """A channel that has carried messages reports a non-zero
        ``message_count`` in the graph snapshot."""
        t = await (
            TestTerrariumBuilder()
            .with_creature("alice")
            .with_creature("bob")
            .with_channel("chat")
            .with_connection("alice", "bob", channel="chat")
            .build()
        )
        try:
            from kohakuterrarium.core.channel import ChannelMessage

            alice = t.get_creature("alice")
            env = t._environments[alice.graph_id]
            ch = env.shared_channels.get("chat")
            await ch.send(ChannelMessage(sender="alice", content="hi"))
            await ch.send(ChannelMessage(sender="alice", content="again"))
            graph = t.get_graph(alice.graph_id)
            channels = co._channels_for_graph(t, graph)
            chat = next(c for c in channels if c["name"] == "chat")
            assert chat["type"] == "broadcast"
            assert chat["message_count"] == 2
        finally:
            await t.shutdown()
