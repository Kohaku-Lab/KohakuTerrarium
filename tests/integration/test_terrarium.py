"""Integration test for the ``terrarium/`` package.

The comprehensive usage example of the Terrarium runtime engine,
driving it the *same way the real consumer does* —
``studio/sessions/lifecycle.py`` never pokes ``Terrarium`` internals;
it builds a :class:`Terrarium`, wraps it in
:class:`LocalTerrariumService`, and operates through the
``TerrariumService`` Protocol (``add_creature`` / ``add_channel`` /
``connect`` / ``remove_creature`` / ``status_snapshot`` / ``subscribe``
…). ``attach_session`` and ``apply_recipe`` are not on the Protocol —
lifecycle.py reaches the engine directly for those, so the test does
the same.

Each ``test_*`` method runs ONE complete workflow end-to-end. No
shape asserts — every assertion pins an exact value or side effect.
The only seam is the LLM: both ``bootstrap.llm.create_llm_provider``
and ``bootstrap.agent_init.create_llm_provider`` are monkeypatched to
a :class:`ScriptedLLM`. The engine, ``LocalTerrariumService``, real
``Agent``-backed creatures, the live channel registry, the ``group_*``
tools, topology auto-merge / auto-split, and session-store
coordination are all real.
"""

import asyncio
import json
from pathlib import Path

import pytest

from kohakuterrarium.bootstrap import agent_init as _agent_init
from kohakuterrarium.bootstrap import llm as _bootstrap_llm
from kohakuterrarium.core.channel import ChannelMessage
from kohakuterrarium.core.config_types import (
    AgentConfig,
    InputConfig,
    OutputConfig,
)
from kohakuterrarium.modules.tool.base import ToolContext
from kohakuterrarium.session.store import SessionStore
import kohakuterrarium.terrarium.session_coord as _session_coord
from kohakuterrarium.terrarium.drive.store import (
    DriveRepositoryClosedError,
    SqliteDriveRepository,
)
from kohakuterrarium.terrarium.config import (
    ChannelConfig,
    CreatureConfig,
    RootConfig,
    TerrariumConfig,
)
from kohakuterrarium.terrarium.drive.config import (
    DriveRuntimeConfig,
    default_registrations,
)
from kohakuterrarium.terrarium.drive.models import ActorRef, DriveStatus
from kohakuterrarium.terrarium.drive.registration import (
    DriveRegistrationDescriptor,
    GenericDriveRegistration,
)
from kohakuterrarium.terrarium.drive.requests import CreateDriveRequest, DriveQuery
from kohakuterrarium.testing.llm import ScriptEntry
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.terrarium.events import EventFilter, EventKind
from kohakuterrarium.terrarium.output_log import OutputLogCapture
from kohakuterrarium.terrarium.service import LocalTerrariumService
from kohakuterrarium.terrarium.tools_group_channel import GroupChannelTool
from kohakuterrarium.terrarium.tools_group_lifecycle import (
    GroupAddNodeTool,
    GroupRemoveNodeTool,
    GroupStartNodeTool,
    GroupStopNodeTool,
)
from kohakuterrarium.terrarium.tools_group_send import (
    GroupSendTool,
    SendChannelTool,
)
from kohakuterrarium.terrarium.tools_group_status import GroupStatusTool
from kohakuterrarium.terrarium.tools_group_wire import GroupWireTool
from kohakuterrarium.testing.llm import ScriptedLLM

pytestmark = pytest.mark.timeout(30)


# ---------------------------------------------------------------------------
# fixtures — the LLM seam, plus real-creature + service builders
# ---------------------------------------------------------------------------


@pytest.fixture
def patched_llm(monkeypatch):
    """Patch BOTH LLM factory import sites so every real ``Agent`` the
    engine builds gets a deterministic :class:`ScriptedLLM`.

    The default script answers every turn with a short ack — the
    workflows that need a creature to *do* something (emit a tool
    call) register their own per-creature script via the returned
    ``set_script(name, [...])`` hook before the creature is built.
    """

    class _Scripts:
        def __init__(self) -> None:
            self.by_name: dict[str, list] = {}

        def set_script(self, name: str, script: list) -> None:
            self.by_name[name] = list(script)

    scripts = _Scripts()

    def _fake_create(config, llm=None):
        return ScriptedLLM(scripts.by_name.get(config.name, ["ack"]))

    monkeypatch.setattr(_bootstrap_llm, "create_llm_provider", _fake_create)
    monkeypatch.setattr(_agent_init, "create_llm_provider", _fake_create)
    return scripts


def _agent_config(name: str, tmp_path: Path) -> AgentConfig:
    """Minimal real ``AgentConfig`` — a NoneInput creature, the shape
    ``lifecycle.start_creature`` feeds to ``add_creature(config=...)``."""
    return AgentConfig(
        name=name,
        llm_profile="test/scripted",
        model="scripted-model",
        provider="test",
        api_key_env="",
        system_prompt=f"You are {name}.",
        include_tools_in_prompt=True,
        include_hints_in_prompt=False,
        tool_format="bracket",
        agent_path=tmp_path,
        input=InputConfig(type="none"),
        output=OutputConfig(type="stdout"),
        tools=[],
    )


@pytest.fixture
async def make_service(patched_llm, tmp_path):
    """Build a real ``Terrarium`` wrapped in ``LocalTerrariumService``
    — how ``lifecycle.py`` obtains the runtime surface. ``session_dir``
    is wired so session-store merge/split coordination mints real
    files (without it merge degrades to "keep first store"). Async
    fixture so teardown can ``await engine.shutdown()`` — a safety net
    for tests that fail before their own explicit shutdown."""
    engines: list[Terrarium] = []
    sess_dir = tmp_path / "sessions"
    sess_dir.mkdir()

    def _build() -> LocalTerrariumService:
        engine = Terrarium(pwd=str(tmp_path), session_dir=str(sess_dir))
        engines.append(engine)
        return LocalTerrariumService(engine)

    yield _build

    # Cap shutdown — on Python 3.10/3.11 CI runners the kohakuvault
    # daemon threads sometimes don't drain in time and ``shutdown``
    # blocks on a thread join that exceeds pytest's per-test timeout.
    # Fixture teardown that blocks is reported as a test timeout even
    # when every assertion already passed; bounding the wait surfaces
    # the leak without losing the test result.
    for engine in engines:
        try:
            await asyncio.wait_for(engine.shutdown(), timeout=15.0)
        except (asyncio.TimeoutError, Exception):
            pass


def _ctx_for(service: LocalTerrariumService, creature_id: str) -> ToolContext:
    """The ToolContext a privileged ``group_*`` tool call carries — the
    engine resolves the caller from ``environment`` + ``agent_name``,
    exactly as the real tool-executor path does."""
    engine = service.engine
    creature = engine.get_creature(creature_id)
    env = engine._environments[creature.graph_id]
    return ToolContext(
        agent_name=creature.name,
        session=None,
        working_dir=Path("."),
        environment=env,
    )


async def _events(service: LocalTerrariumService, sink: list) -> asyncio.Task:
    """Collect engine events into ``sink`` via the Protocol's
    ``subscribe``; returns the collector task."""

    async def _collect() -> None:
        async for ev in service.subscribe():
            sink.append(ev)

    task = asyncio.create_task(_collect())
    await asyncio.sleep(0)  # let the subscriber register
    return task


async def _settle() -> None:
    """Drain the engine's deferred channel-trigger runner tasks.
    ``connect`` / ``add_channel`` wiring spawns a task that performs
    the actual channel ``subscribe()`` — it has not run yet when the
    sync call returns. A few event-loop turns let it land."""
    for _ in range(5):
        await asyncio.sleep(0)


class _TwoPartyReviewReg(GenericDriveRegistration):
    """A ``review``-kind registration whose terminal completion needs a distinct
    approver (verifier_mode ``two_party``), so a propose_transition PERSISTS a
    pending proposal and emits ``drive_proposal_pending`` (item 5 event surface)."""

    name = "review"
    kind = "review"

    def descriptor(self) -> DriveRegistrationDescriptor:
        return DriveRegistrationDescriptor(
            name=self.name,
            kind=self.kind,
            schema_version=1,
            required_roles=frozenset({"spec", "transition", "readiness"}),
            optional_roles=frozenset({"projection", "verifier", "prompt"}),
            verifier_mode="two_party",
        )


# ===========================================================================
# the workflows
# ===========================================================================


class TestTerrariumIntegration:
    """Each method is one complete terrarium workflow, driven through
    ``LocalTerrariumService`` the way ``studio/sessions`` does."""

    async def test_build_wire_and_broadcast_to_exact_listeners(
        self, make_service, tmp_path
    ):
        """Build engine + service → add three creatures into one graph
        → declare a channel → connect two of them → broadcast → assert
        the message reached the EXACT listeners (the wired receiver,
        not the unwired third creature)."""
        service = make_service()

        # First add_creature mints a fresh singleton graph; the next
        # two join it explicitly (the multi-creature session shape).
        # Explicit ``creature_id`` keeps the namespace deterministic.
        alice = await service.add_creature(
            _agent_config("alice", tmp_path), creature_id="alice"
        )
        gid = alice.graph_id
        await service.add_creature(
            _agent_config("bob", tmp_path), graph_id=gid, creature_id="bob"
        )
        await service.add_creature(
            _agent_config("carol", tmp_path), graph_id=gid, creature_id="carol"
        )
        # All three share one graph — that is one session.
        assert {c.creature_id for c in await service.list_creatures()} == {
            "alice",
            "bob",
            "carol",
        }
        graphs = await service.list_graphs()
        assert len(graphs) == 1
        assert graphs[0].creature_ids == {"alice", "bob", "carol"}

        # Declare a channel, then wire alice -> bob over it.
        chan = await service.add_channel(gid, "ops", "operations channel")
        assert chan.name == "ops"
        result = await service.connect("alice", "bob", channel="ops")
        assert result.channel == "ops"
        assert result.delta_kind == "nothing"  # same graph already
        assert result.graph_id == gid

        # connect() wires alice as sender, bob as listener; carol is
        # untouched. Verified through the Protocol's creature info.
        info_alice = await service.get_creature_info("alice")
        info_bob = await service.get_creature_info("bob")
        info_carol = await service.get_creature_info("carol")
        assert info_alice.send_channels == ("ops",)
        assert info_bob.listen_channels == ("ops",)
        assert info_carol.listen_channels == ()
        assert info_carol.send_channels == ()

        # Broadcast on the live channel and capture engine events.
        await _settle()  # let the receiver's async subscribe land
        events: list = []
        collector = await _events(service, events)
        env = service.engine._environments[gid]
        ops = env.shared_channels.get("ops")
        await ops.send(ChannelMessage(sender="alice", content="status: green"))
        await asyncio.sleep(0.05)
        collector.cancel()

        # Broadcast channel: only the wired listener is subscribed; the
        # unwired creature is not subscribed at all.
        assert ops.history[-1].content == "status: green"
        assert set(ops._subscribers) == {"bob_ops"}  # only bob listens
        # Exactly one CHANNEL_MESSAGE event fired for that send.
        channel_msgs = [ev for ev in events if ev.kind == EventKind.CHANNEL_MESSAGE]
        assert len(channel_msgs) == 1
        assert channel_msgs[0].channel == "ops"
        assert channel_msgs[0].graph_id == gid
        assert channel_msgs[0].payload["content"] == "status: green"
        assert channel_msgs[0].payload["sender"] == "alice"

        # --- the Protocol's topology / status read surface ---------------
        # ``list_channels`` reflects the declared channel; the per-graph
        # ``get_graph`` exposes the live wiring edges.
        channels = await service.list_channels(gid)
        assert {c.name for c in channels} == {"ops"}
        graph = await service.get_graph(gid)
        assert "ops" in graph.send_edges["alice"]
        assert "ops" in graph.listen_edges["bob"]
        # ``creature_status`` is the single-creature shape; ``status_snapshot``
        # is the engine-wide roll-up.
        cstat = await service.creature_status("alice")
        assert cstat["creature_id"] == "alice"
        assert cstat["running"] is True
        snap = await service.status_snapshot()
        assert set(snap["creatures"]) == {"alice", "bob", "carol"}
        # Unknown ids return None / () rather than raising — the
        # null-object contract API routes rely on for 404s.
        assert await service.get_creature_info("ghost") is None
        assert await service.get_graph("no_such_graph") is None
        assert await service.creature_status("ghost") is None
        assert await service.list_channels("no_such_graph") == ()

        # --- a second channel + disconnect path --------------------------
        # Declare a second channel and wire carol in as a listener via the
        # per-side ``wire_creature`` primitive (the cross-node connect
        # building block), then disconnect it.
        await service.add_channel(gid, "side", "side channel")
        await service.wire_creature(gid, "alice", "side", "send")
        await service.wire_creature(gid, "carol", "side", "listen")
        assert (await service.get_creature_info("carol")).listen_channels == ("side",)
        assert (await service.get_creature_info("alice")).send_channels == (
            "ops",
            "side",
        )
        # Disconnecting alice->bob on ``ops`` drops the only edge between
        # alice/bob; alice<->carol on ``side`` keeps alice+carol together,
        # so bob becomes isolated and auto-splits into its own graph.
        disc = await service.disconnect("alice", "bob", channel="ops")
        assert "ops" in disc.channels
        graphs = await service.list_graphs()
        members = sorted(sorted(g.creature_ids) for g in graphs)
        assert members == [["alice", "carol"], ["bob"]]
        # ``remove_channel`` tears ``side`` out of the alice+carol graph.
        # ``side`` is the sole bridge between alice and carol, so removing
        # it auto-splits that component into two singletons.
        alice_gid = (await service.get_creature_info("alice")).graph_id
        rm_delta = await service.remove_channel(alice_gid, "side")
        assert rm_delta.kind == "split"
        graphs = await service.list_graphs()
        # alice / bob / carol each now sit in their own singleton graph.
        members = sorted(sorted(g.creature_ids) for g in graphs)
        assert members == [["alice"], ["bob"], ["carol"]]
        # No graph carries the deleted channel any more.
        for g in graphs:
            assert "side" not in g.channels

        # --- interaction surface: inject_input + chat streaming ----------
        # ``inject_input`` pushes a turn without consuming the stream;
        # ``chat`` injects + streams. Both route through the live agent.
        await service.inject_input("alice", "injected message")
        await asyncio.sleep(0.05)  # let the injected turn finalise
        alice_hist = await service.chat_history("alice")
        assert alice_hist["creature_id"] == "alice"
        injected_joined = " ".join(
            m.get("content", "") if isinstance(m.get("content"), str) else ""
            for m in alice_hist["messages"]
        )
        assert "injected message" in injected_joined
        # ``chat`` streams the scripted "ack" reply.
        chunks: list[str] = []
        async for chunk in service.chat("bob", "hello bob"):
            chunks.append(chunk)
        assert "".join(chunks) == "ack"

        # --- per-creature control: interrupt + list_jobs -----------------
        # After a plain turn there are no jobs in flight; interrupt is a
        # no-op (idempotent), and list_jobs is empty.
        assert await service.list_jobs("alice") == []
        await service.interrupt("alice")  # no-op, must not raise
        assert await service.list_jobs("alice") == []
        # stop_job / promote_job on a non-existent job return False — never
        # a fabricated success.
        assert await service.stop_job("alice", "job_does_not_exist") is False
        assert await service.promote_job("alice", "job_does_not_exist") is False

        # --- per-creature state reads through the Protocol --------------
        # ``get_env`` surfaces pwd + redacted env; ``get_working_dir`` and
        # ``get_system_prompt`` round-trip; ``list_triggers`` is a list.
        env_info = await service.get_env("alice")
        assert "pwd" in env_info
        assert not any("API_KEY" in k.upper() for k in env_info["env"])
        assert await service.get_working_dir("alice")
        sp = await service.get_system_prompt("alice")
        assert "alice" in sp["text"]
        assert isinstance(await service.list_triggers("alice"), list)
        # native-tool inventory + options map — scripted creatures have none.
        assert await service.native_tool_inventory("alice") == []
        assert await service.get_native_tool_options("alice") == {}
        # ``list_modules`` returns only known module types.
        assert {m["type"] for m in await service.list_modules("alice")} <= {
            "plugin",
            "native_tool",
        }

        # --- output-sink wiring round-trip via the engine escape hatch ---
        # ``wire_output_sink`` attaches a secondary sink, returns its id;
        # ``unwire_output_sink`` removes it (True on hit, False on miss).
        sink_calls: list[str] = []

        class _Sink:
            async def write(self, text: str) -> None:
                sink_calls.append(text)

            async def flush(self) -> None:
                pass

        sink_id = await service.engine.wire_output_sink("alice", _Sink())
        assert sink_id
        assert await service.unwire_output_sink("alice", sink_id) is True
        assert await service.unwire_output_sink("alice", "sink_bogus") is False

        # --- filtered subscribe — EventFilter restricts the event stream.
        # Subscribe with a kind filter, fire a topology change (re-wire a
        # creature edge), and assert only the matching kind arrives.
        filtered: list = []

        async def _collect_filtered() -> None:
            flt = EventFilter(kinds={EventKind.TOPOLOGY_CHANGED})
            async for ev in service.subscribe(flt):
                filtered.append(ev)

        ftask = asyncio.create_task(_collect_filtered())
        await asyncio.sleep(0)
        # alice + carol are singletons now; declare a channel on alice's
        # graph and wire alice onto it — emits TOPOLOGY_CHANGED.
        alice_gid2 = (await service.get_creature_info("alice")).graph_id
        await service.add_channel(alice_gid2, "solo", "alice solo channel")
        await service.wire_creature(alice_gid2, "alice", "solo", "listen")
        await asyncio.sleep(0.05)
        ftask.cancel()
        assert filtered, "filtered subscriber received no TOPOLOGY_CHANGED event"
        assert all(ev.kind == EventKind.TOPOLOGY_CHANGED for ev in filtered)

        await service.shutdown()

    @pytest.mark.timeout(120)
    async def test_privileged_node_evolves_the_graph_mid_run(
        self, make_service, tmp_path
    ):
        """A privileged node uses the ``group_*`` tool surface to
        evolve its team mid-run: spawn a worker, wire a channel to it,
        confirm it is in-group via ``group_status``, then remove it.
        Mirrors how an LLM-driven privileged creature mutates topology
        — the tools route through the same engine the service wraps."""
        service = make_service()
        engine = service.engine

        # A privileged "lead" — the user-created shape (Studio "new
        # creature" and lifecycle.start_creature pass is_privileged).
        lead = await service.add_creature(
            _agent_config("lead", tmp_path),
            creature_id="lead",
            is_privileged=True,
        )
        gid = lead.graph_id
        # Privileged creatures get the full group_* tool surface.
        lead_agent = engine.get_creature("lead").agent
        assert "group_add_node" in lead_agent.registry.list_tools()
        assert "group_channel" in lead_agent.registry.list_tools()

        # group_add_node takes a config PATH — write a real file.
        worker_yaml = tmp_path / "worker.yaml"
        worker_yaml.write_text(
            "name: worker\nllm: test/scripted\nmodel: scripted-model\n"
            "provider: test\nsystem_prompt: You are worker.\n"
            "tool_format: bracket\ninput:\n  type: none\n"
            "output:\n  type: stdout\n",
            encoding="utf-8",
        )

        ctx = _ctx_for(service, "lead")

        # 1. spawn — group_add_node drops the worker into the caller's
        # graph as a non-privileged child; the tool mints the id.
        add_res = await GroupAddNodeTool()._execute(
            {"config_path": str(worker_yaml), "name": "worker"}, context=ctx
        )
        assert add_res.error is None, add_res.error
        worker_id = json.loads(add_res.output)["creature_id"]
        worker = engine.get_creature(worker_id)
        assert worker.name == "worker"
        assert worker.graph_id == gid  # joined the lead's graph
        assert worker.is_privileged is False  # tool-spawned workers are NOT privileged
        assert worker.parent_creature_id == "lead"
        assert "group_add_node" not in worker.agent.registry.list_tools()

        # 2. wire — group_channel(action="wire") gives the worker a
        # listen edge on a fresh auto-created channel.
        wire_res = await GroupChannelTool()._execute(
            {
                "action": "wire",
                "channel": "tasks",
                "creature_id": worker_id,
                "direction": "listen",
            },
            context=ctx,
        )
        assert wire_res.error is None, wire_res.error
        assert "tasks" in engine.get_graph(gid).channels
        assert "tasks" in engine.get_graph(gid).listen_edges[worker_id]
        assert "tasks" in engine.get_creature(worker_id).listen_channels

        # 3. group_status — the caller's snapshot lists itself + the
        # worker it just spawned and wired.
        status_res = await GroupStatusTool()._execute({}, context=ctx)
        assert status_res.error is None
        body = json.loads(status_res.output)
        assert body["self"]["creature_id"] == "lead"
        assert body["self"]["is_privileged"] is True
        assert {c["creature_id"] for c in body["creatures"]} == {
            "lead",
            worker_id,
        }

        # 4. group_channel(action="create") — declare a fresh broadcast
        # channel, then wire the lead as a sender on it so it can
        # broadcast through ``send_channel``.
        create_res = await GroupChannelTool()._execute(
            {"action": "create", "channel": "orders", "description": "lead orders"},
            context=ctx,
        )
        assert create_res.error is None, create_res.error
        assert json.loads(create_res.output)["created"] == "orders"
        assert "orders" in engine.get_graph(gid).channels
        # Wire lead as sender + worker as listener on ``orders``.
        await GroupChannelTool()._execute(
            {
                "action": "wire",
                "channel": "orders",
                "creature_id": "lead",
                "direction": "send",
            },
            context=ctx,
        )
        await GroupChannelTool()._execute(
            {
                "action": "wire",
                "channel": "orders",
                "creature_id": worker_id,
                "direction": "listen",
            },
            context=ctx,
        )
        await _settle()
        # 5. send_channel — the privileged lead broadcasts on the wired
        # channel; the message lands in channel history + the worker's
        # subscription queue.
        send_res = await SendChannelTool()._execute(
            {"channel": "orders", "message": "ship it"}, context=ctx
        )
        assert send_res.error is None, send_res.error
        orders = engine._environments[gid].shared_channels.get("orders")
        assert orders.history[-1].content == "ship it"
        assert orders.history[-1].sender == "lead"
        # send_channel on a channel the caller is NOT wired to is rejected.
        bad_send = await SendChannelTool()._execute(
            {"channel": "tasks", "message": "nope"}, context=ctx
        )
        assert bad_send.error is not None
        assert "not wired as sender" in bad_send.error

        # 6. group_send — fire-and-forget point-to-point to the worker.
        gs_res = await GroupSendTool()._execute(
            {"to": worker_id, "message": "ping worker"}, context=ctx
        )
        assert gs_res.error is None, gs_res.error
        gs_body = json.loads(gs_res.output)
        assert gs_body["delivered"] is True
        assert gs_body["to"] == worker_id

        # --- group_channel error paths — every rejection branch --------
        # At this point ``tasks`` + ``orders`` are both live in the graph.
        # create on an existing channel is rejected.
        dup_create = await GroupChannelTool()._execute(
            {"action": "create", "channel": "tasks"}, context=ctx
        )
        assert dup_create.error is not None
        assert "already exists" in dup_create.error
        # delete a channel that is not in the graph is rejected.
        bad_delete = await GroupChannelTool()._execute(
            {"action": "delete", "channel": "no_such_channel"}, context=ctx
        )
        assert bad_delete.error is not None
        assert "not in your graph" in bad_delete.error
        # wire with a bad direction is rejected.
        bad_dir = await GroupChannelTool()._execute(
            {
                "action": "wire",
                "channel": "tasks",
                "creature_id": worker_id,
                "direction": "sideways",
            },
            context=ctx,
        )
        assert bad_dir.error is not None
        assert "direction must be" in bad_dir.error
        # wire targeting a creature not in the group is rejected.
        bad_target = await GroupChannelTool()._execute(
            {
                "action": "wire",
                "channel": "tasks",
                "creature_id": "ghost_creature",
                "direction": "listen",
            },
            context=ctx,
        )
        assert bad_target.error is not None
        assert "not in your group" in bad_target.error
        # unwire a channel not in the graph is rejected.
        bad_unwire = await GroupChannelTool()._execute(
            {
                "action": "unwire",
                "channel": "no_such_channel",
                "creature_id": worker_id,
                "direction": "listen",
            },
            context=ctx,
        )
        assert bad_unwire.error is not None
        assert "not in your graph" in bad_unwire.error
        # action + channel are both required.
        missing_args = await GroupChannelTool()._execute(
            {"action": "create", "channel": ""}, context=ctx
        )
        assert missing_args.error is not None
        assert "required" in missing_args.error

        # --- group_wire — direct output-wire edge add / remove ----------
        # group_wire adds a round-output edge from the caller; the edge id
        # round-trips back through remove.
        gw_add = await GroupWireTool()._execute(
            {"action": "add", "to": worker_id, "with_content": True}, context=ctx
        )
        assert gw_add.error is None, gw_add.error
        gw_edge_id = json.loads(gw_add.output)["edge_id"]
        assert gw_edge_id
        assert any(e["to"] == "worker" for e in engine.list_output_wiring("lead"))
        gw_rm = await GroupWireTool()._execute(
            {"action": "remove", "edge_id": gw_edge_id}, context=ctx
        )
        assert gw_rm.error is None, gw_rm.error
        assert json.loads(gw_rm.output)["removed"] is True
        assert engine.list_output_wiring("lead") == []
        # group_wire add without ``to`` is rejected; remove without
        # ``edge_id`` is rejected; an unknown action is rejected.
        gw_no_to = await GroupWireTool()._execute({"action": "add"}, context=ctx)
        assert gw_no_to.error is not None and "required" in gw_no_to.error
        gw_no_id = await GroupWireTool()._execute({"action": "remove"}, context=ctx)
        assert gw_no_id.error is not None and "required" in gw_no_id.error
        gw_bad = await GroupWireTool()._execute({"action": "frobnicate"}, context=ctx)
        assert gw_bad.error is not None

        # --- wire_creature "root" resolution + error branches -----------
        # The service's ``wire_creature`` with creature_id="root" resolves
        # to the graph's privileged creature (lead).
        await service.add_channel(gid, "rootchan", "root channel")
        await service.wire_creature(gid, "root", "rootchan", "listen")
        assert "rootchan" in engine.get_creature("lead").listen_channels
        # An unknown direction raises ValueError.
        with pytest.raises(ValueError):
            await service.wire_creature(gid, "lead", "rootchan", "diagonal")
        # Wiring a channel not in the graph raises KeyError.
        with pytest.raises(KeyError):
            await service.wire_creature(gid, "lead", "ghost_chan", "listen")
        # Wiring a creature not in the graph raises KeyError.
        with pytest.raises(KeyError):
            await service.wire_creature(gid, "ghost_creature", "rootchan", "listen")
        # Unwire the rootchan listen edge back off — the disabled branch.
        await service.wire_creature(gid, "lead", "rootchan", "listen", enabled=False)
        assert "rootchan" not in engine.get_creature("lead").listen_channels

        # --- module-options error branches on the privileged lead -------
        # ``get_module_options`` / ``set_module_options`` for an unknown
        # module type are rejected; ``toggle_module`` on native_tool is
        # rejected (native tools don't support toggle).
        with pytest.raises(ValueError):
            await service.get_module_options("lead", "not_a_type", "x")
        with pytest.raises(ValueError):
            await service.set_module_options("lead", "bogus", "x", {})
        with pytest.raises(ValueError):
            await service.toggle_module("lead", "native_tool", "anything")
        # ``get_module_options`` for a missing native_tool raises KeyError.
        with pytest.raises(KeyError):
            await service.get_module_options("lead", "native_tool", "ghost_tool")

        # 7. group_stop_node / group_start_node — pause and resume the
        # worker without removing it from the graph.
        stop_res = await GroupStopNodeTool()._execute(
            {"creature_id": worker_id}, context=ctx
        )
        assert stop_res.error is None, stop_res.error
        assert engine.get_creature(worker_id).is_running is False
        # double-stop is rejected.
        again = await GroupStopNodeTool()._execute(
            {"creature_id": worker_id}, context=ctx
        )
        assert again.error is not None and "not running" in again.error
        start_res = await GroupStartNodeTool()._execute(
            {"creature_id": worker_id}, context=ctx
        )
        assert start_res.error is None, start_res.error
        assert engine.get_creature(worker_id).is_running is True
        # Cannot stop the privileged node via the group tool.
        deny_stop = await GroupStopNodeTool()._execute(
            {"creature_id": "lead"}, context=ctx
        )
        assert deny_stop.error is not None and "privileged" in deny_stop.error

        # 8. group_channel(action="unwire") then (action="delete").
        unwire_res = await GroupChannelTool()._execute(
            {
                "action": "unwire",
                "channel": "orders",
                "creature_id": worker_id,
                "direction": "listen",
            },
            context=ctx,
        )
        assert unwire_res.error is None, unwire_res.error
        assert "orders" not in engine.get_creature(worker_id).listen_channels
        del_res = await GroupChannelTool()._execute(
            {"action": "delete", "channel": "orders"}, context=ctx
        )
        assert del_res.error is None, del_res.error
        assert "orders" not in engine.get_graph(gid).channels

        # 9. remove — group_remove_node destroys the worker. It was the
        # only other node so the graph just shrinks back to the lead.
        rm_res = await GroupRemoveNodeTool()._execute(
            {"creature_id": worker_id}, context=ctx
        )
        assert rm_res.error is None, rm_res.error
        assert worker_id not in service.engine
        assert {c.creature_id for c in await service.list_creatures()} == {"lead"}
        # The privileged node may NOT be removed via the tool.
        deny = await GroupRemoveNodeTool()._execute(
            {"creature_id": "lead"}, context=ctx
        )
        assert deny.error is not None
        assert "privileged" in deny.error
        # removing a creature not in the group is rejected.
        deny_ghost = await GroupRemoveNodeTool()._execute(
            {"creature_id": "ghost_creature"}, context=ctx
        )
        assert deny_ghost.error is not None

        await service.shutdown()

    async def test_auto_split_on_bridge_removal_then_auto_merge(
        self, make_service, tmp_path
    ):
        """The load-bearing topology workflow. A linear graph
        alice-bob-carol where bob is the sole bridge: removing bob
        auto-splits the graph into the exact surviving components, each
        with its own fresh environment. Then connecting across two
        graphs auto-merges them — union environment, single merged
        session store, ``parent_session_ids`` lineage recorded."""
        service = make_service()
        engine = service.engine

        # Build a chain: alice <-> bob <-> carol. bob bridges the two.
        alice = await service.add_creature(
            _agent_config("alice", tmp_path), creature_id="alice"
        )
        gid = alice.graph_id
        await service.add_creature(
            _agent_config("bob", tmp_path), graph_id=gid, creature_id="bob"
        )
        await service.add_creature(
            _agent_config("carol", tmp_path), graph_id=gid, creature_id="carol"
        )
        await service.add_channel(gid, "ab", "alice-bob link")
        await service.add_channel(gid, "bc", "bob-carol link")
        await service.connect("alice", "bob", channel="ab")
        await service.connect("bob", "carol", channel="bc")
        assert len(await service.list_graphs()) == 1  # one connected graph
        env_before = engine._environments[gid]

        # Attach a session store so the split has lineage to copy.
        store = SessionStore(tmp_path / "sessions" / f"{gid}.kohakutr")
        store.init_meta(
            session_id=gid,
            config_type="terrarium",
            config_path="",
            pwd=str(tmp_path),
            agents=["alice", "bob", "carol"],
        )
        await service.engine.attach_session(gid, store)

        # --- AUTO-SPLIT: remove the bridge creature -------------------
        events: list = []
        collector = await _events(service, events)
        await service.remove_creature("bob")
        await asyncio.sleep(0.05)
        collector.cancel()

        # bob is gone; the graph fragmented into exactly two graphs:
        # {alice} and {carol}, each a singleton (their bridge left).
        graphs = await service.list_graphs()
        assert len(graphs) == 2
        members = sorted(sorted(g.creature_ids) for g in graphs)
        assert members == [["alice"], ["carol"]]
        alice_info = await service.get_creature_info("alice")
        carol_info = await service.get_creature_info("carol")
        assert alice_info.graph_id != carol_info.graph_id
        # Each surviving component got its OWN environment object: one
        # keeps the original graph_id + env, the other is fresh.
        env_alice = engine._environments[alice_info.graph_id]
        env_carol = engine._environments[carol_info.graph_id]
        assert env_alice is not env_carol
        assert env_before in (env_alice, env_carol)  # reused by one survivor
        # A split EngineEvent fired with both new graph ids.
        split_evs = [
            ev
            for ev in events
            if ev.kind == EventKind.TOPOLOGY_CHANGED
            and ev.payload.get("kind") == "split"
        ]
        assert len(split_evs) == 1
        assert set(split_evs[0].payload["new_graph_ids"]) == {
            alice_info.graph_id,
            carol_info.graph_id,
        }
        # Session-store split: each survivor's graph has its own store
        # carrying the pre-split session as a parent.
        store_alice = engine._session_stores[alice_info.graph_id]
        store_carol = engine._session_stores[carol_info.graph_id]
        assert store_alice is not store_carol
        assert store_alice.meta["parent_session_ids"] == [gid]
        assert store_carol.meta["parent_session_ids"] == [gid]

        # --- AUTO-MERGE: connect across the two graphs ----------------
        merge_res = await service.connect("alice", "carol", channel="reunite")
        assert merge_res.delta_kind == "merge"
        # Invariant: graph == connected component → back to ONE graph.
        graphs = await service.list_graphs()
        assert len(graphs) == 1
        assert graphs[0].creature_ids == {"alice", "carol"}
        # Both creatures now share one graph_id and one environment.
        a2 = await service.get_creature_info("alice")
        c2 = await service.get_creature_info("carol")
        assert a2.graph_id == c2.graph_id
        merged_env = engine._environments[a2.graph_id]
        assert engine._environments[c2.graph_id] is merged_env
        assert merged_env.shared_channels.get("reunite") is not None
        # Merged session store: one store on the surviving graph, with
        # lineage pointing back at BOTH pre-merge graphs.  The store may
        # reuse the kept-side's existing wrapper when the kept graph's
        # on-disk file IS the merge target file — opening a fresh
        # wrapper there would duplicate the kept side's rows.  Either
        # way, it must carry merge lineage in its meta.
        merged_store = engine._session_stores[a2.graph_id]
        assert set(merged_store.meta["parent_session_ids"]) == {
            store_alice.session_id,
            store_carol.session_id,
        }
        assert "merged_at" in merged_store.meta

        # --- AUTO-SPLIT via disconnect (the other split trigger) -------
        # The merged graph has exactly one channel (``reunite``) joining
        # alice and carol. ``disconnect`` removes that edge → the
        # component fragments again into two singletons, each with a
        # fresh per-side environment and a per-side session store whose
        # lineage points back at the merged store.
        disc = await service.disconnect("alice", "carol", channel="reunite")
        assert disc.delta_kind == "split"
        assert "reunite" in disc.channels
        graphs = await service.list_graphs()
        assert len(graphs) == 2
        members = sorted(sorted(g.creature_ids) for g in graphs)
        assert members == [["alice"], ["carol"]]
        a3 = await service.get_creature_info("alice")
        c3 = await service.get_creature_info("carol")
        assert a3.graph_id != c3.graph_id
        post_store_alice = engine._session_stores[a3.graph_id]
        post_store_carol = engine._session_stores[c3.graph_id]
        assert post_store_alice is not post_store_carol
        assert post_store_alice.meta["parent_session_ids"] == [merged_store.session_id]
        assert post_store_carol.meta["parent_session_ids"] == [merged_store.session_id]
        # The post-split environments are distinct, freshly minted.
        assert (
            engine._environments[a3.graph_id] is not engine._environments[c3.graph_id]
        )
        # status_snapshot still sees both creatures, now in two graphs.
        snap = await service.status_snapshot()
        assert set(snap["creatures"]) == {"alice", "carol"}
        assert len(snap["graphs"]) == 2

    async def test_split_drive_lifecycle_no_repo_race(
        self, make_service, tmp_path, monkeypatch
    ):
        """Regression: a Drive-default-on split must not open a second store at
        the parent's live path, nor let a barrier-gated reconcile race the
        repository teardown.

        Two lifecycle bugs collided here. (1) The largest split child keeps the
        original graph_id, so its target file equals the parent's still-open
        ``<gid>.kohakutr``; ``apply_split`` used to open a SECOND ``SessionStore``
        there, duplicating events and racing the live connection
        (``SQLite error: disk I/O error`` under WAL). (2) an in-flight
        ``_reconcile_when_ready`` task was not drained before the split rebound
        the graph's Drive repository, so it started a manager on a shut executor
        (``DriveRepositoryClosedError``). This drives a real split with an
        in-flight reconcile forced and asserts NEITHER escapes."""
        service = make_service()
        engine = service.engine

        alice = await service.add_creature(
            _agent_config("alice", tmp_path), creature_id="alice"
        )
        gid = alice.graph_id
        await service.add_creature(
            _agent_config("bob", tmp_path), graph_id=gid, creature_id="bob"
        )
        await service.add_creature(
            _agent_config("carol", tmp_path), graph_id=gid, creature_id="carol"
        )
        await service.add_channel(gid, "ab", "")
        await service.add_channel(gid, "bc", "")
        await service.connect("alice", "bob", channel="ab")
        await service.connect("bob", "carol", channel="bc")

        # Attach the session at exactly ``<gid>.kohakutr`` so the largest split
        # child's target path equals the parent's live path (the collision case).
        store = SessionStore(tmp_path / "sessions" / f"{gid}.kohakutr")
        store.init_meta(
            session_id=gid,
            config_type="terrarium",
            config_path="",
            pwd=str(tmp_path),
            agents=["alice", "bob", "carol"],
        )
        await engine.attach_session(gid, store)
        for _ in range(20):  # settle: managers started, initial reconciles done
            await asyncio.sleep(0)

        # Detect the double-open: a fresh split store minted at the parent's own
        # live path (would duplicate events + race the open connection).
        parent_live = str(store.path)
        collisions: list[str] = []
        orig_split = _session_coord.split_session_store

        def _probe_split(old_store, new_paths):
            for p in new_paths:
                if str(p) == parent_live:
                    collisions.append(str(p))
            return orig_split(old_store, new_paths)

        monkeypatch.setattr(_session_coord, "split_session_store", _probe_split)

        # Capture any orphaned-task crash (the reconcile racing a closed repo).
        loop_errors: list[BaseException] = []
        loop = asyncio.get_running_loop()
        prev_handler = loop.get_exception_handler()

        def _handler(lp, context):
            exc = context.get("exception")
            if exc is not None:
                loop_errors.append(exc)

        loop.set_exception_handler(_handler)

        # Widen the reconcile window: slow the Drive repo's worker hops so a
        # freshly scheduled reconcile is suspended inside ``manager.start`` when
        # the split tears its repository down.
        slow = {"on": False}
        orig_run = SqliteDriveRepository._run

        async def _slow_run(self, fn):
            if slow["on"]:
                await asyncio.sleep(0.02)
            return await orig_run(self, fn)

        monkeypatch.setattr(SqliteDriveRepository, "_run", _slow_run)

        try:
            slow["on"] = True
            engine._drive_runtime.schedule_reconcile(engine.get_creature("alice"))
            engine._drive_runtime.schedule_reconcile(engine.get_creature("carol"))
            await asyncio.sleep(0.005)  # let reconciles suspend inside start()

            await service.remove_creature("bob")

            for _ in range(30):  # let any orphaned task resume + (pre-fix) crash
                await asyncio.sleep(0.01)
        finally:
            loop.set_exception_handler(prev_handler)
            slow["on"] = False

        # Fix A: no second store opened at the parent's live path.
        assert collisions == []
        # Fix A: the child keeping the original graph_id reuses the live parent
        # store object in place (not a re-opened duplicate).
        assert engine._session_stores[gid] is store
        # Fix B: no reconcile crashed against a torn-down repository.
        closed = [e for e in loop_errors if isinstance(e, DriveRepositoryClosedError)]
        assert closed == [], f"reconcile raced repo teardown: {closed}"
        assert loop_errors == [], f"unexpected orphaned-task errors: {loop_errors}"

        # The split still produced the correct topology + lineage.
        graphs = await service.list_graphs()
        assert len(graphs) == 2
        members = sorted(sorted(g.creature_ids) for g in graphs)
        assert members == [["alice"], ["carol"]]
        alice_gid = (await service.get_creature_info("alice")).graph_id
        carol_gid = (await service.get_creature_info("carol")).graph_id
        assert engine._session_stores[alice_gid].meta["parent_session_ids"] == [gid]
        assert engine._session_stores[carol_gid].meta["parent_session_ids"] == [gid]

        await service.shutdown()

    @pytest.mark.timeout(120)
    async def test_hotplug_into_running_session_then_stop(self, make_service, tmp_path):
        """Hot-plug workflow: a session is already running with one
        creature; a second is added into the SAME graph at runtime
        (``add_creature(graph_id=...)``), wired in, used, then the
        whole session is stopped via the Protocol."""
        service = make_service()
        engine = service.engine

        # A running solo session.
        host = await service.add_creature(
            _agent_config("host", tmp_path),
            creature_id="host",
            is_privileged=True,
        )
        gid = host.graph_id
        assert engine.get_creature("host").is_running is True

        # Hot-plug a second creature into the live graph — it joins the
        # existing graph rather than minting a new one.
        helper = await service.add_creature(
            _agent_config("helper", tmp_path),
            graph_id=gid,
            creature_id="helper",
        )
        assert helper.graph_id == gid
        assert engine.get_creature("helper").is_running is True
        graphs = await service.list_graphs()
        assert len(graphs) == 1  # still ONE graph, now two creatures
        assert graphs[0].creature_ids == {"host", "helper"}

        # Wire a channel and broadcast — the hot-plugged creature is a
        # first-class graph member, its listen wiring works at once.
        await service.add_channel(gid, "relay", "host-helper relay")
        await service.connect("host", "helper", channel="relay")
        assert (await service.get_creature_info("helper")).listen_channels == ("relay",)
        await _settle()  # let the receiver's subscribe task land
        env = engine._environments[gid]
        relay = env.shared_channels.get("relay")
        await relay.send(ChannelMessage(sender="host", content="ping"))
        assert relay.history[-1].content == "ping"
        assert set(relay._subscribers) == {"helper_relay"}

        # status_snapshot — the engine-wide roll-up the Protocol
        # exposes (matches Terrarium.status() with no args).
        snap = await service.status_snapshot()
        assert snap["running"] is True
        assert set(snap["creatures"]) == {"host", "helper"}
        assert snap["graphs"][gid]["creature_ids"] == ["helper", "host"]
        assert snap["graphs"][gid]["channels"] == ["relay"]

        # stop_creature pauses the helper without removing it — it stays
        # a graph member, just not running. start_creature resumes it.
        await service.stop_creature("helper")
        assert engine.get_creature("helper").is_running is False
        assert {c.creature_id for c in await service.list_creatures()} == {
            "host",
            "helper",
        }  # still in the graph
        await service.start_creature("helper")
        assert engine.get_creature("helper").is_running is True

        # runtime_graph_snapshot — the normalized per-graph editor view.
        # One graph, both creatures, the ``relay`` channel.
        rgs = await service.runtime_graph_snapshot()
        rg_graphs = {g["graph_id"]: g for g in rgs["graphs"]}
        assert gid in rg_graphs
        assert {c["name"] for c in rg_graphs[gid]["creatures"]} == {"host", "helper"}
        assert {c["name"] for c in rg_graphs[gid]["channels"]} == {"relay"}
        # Each graph in the snapshot is tagged with the node id.
        assert rg_graphs[gid]["node_id"] == service.node_id

        # attach_policies — what live streams the host advertises. The
        # base policies are always present; with a wired channel in the
        # graph ``observer`` is added.
        host_policies = await service.attach_policies("host")
        assert "log" in host_policies and "trace" in host_policies
        assert "observer" in host_policies
        # An unknown creature still gets the safe base policy list.
        assert set(await service.attach_policies("ghost")) == {"log", "trace"}
        # session_attach_policies — graph-level; a privileged creature in
        # the graph means ``io`` is offered.
        sess_policies = await service.session_attach_policies(gid)
        assert "io" in sess_policies
        assert "observer" in sess_policies

        # stop_graph stops every creature in the graph in one call
        # (without removing them) — they remain graph members.
        await engine.stop_graph(gid)
        assert engine.get_creature("host").is_running is False
        assert engine.get_creature("helper").is_running is False
        assert len(await service.list_graphs()) == 1

        # Stop the whole session: remove every creature in the graph
        # (what lifecycle.stop_session does); the engine drops the
        # graph once its last creature leaves.
        for cid in list(graphs[0].creature_ids):
            await service.remove_creature(cid)
        assert await service.list_creatures() == ()
        assert await service.list_graphs() == ()

        await service.shutdown()

    async def test_apply_recipe_promotes_privileged_root(
        self, make_service, tmp_path, patched_llm
    ):
        """Recipe workflow: applying a ``TerrariumConfig`` with a
        ``root:`` builds every creature into one graph, auto-declares
        per-creature direct channels + ``report_to_root``, and the
        ``root`` keyword promotes its creature to a privileged node
        wired as listener on every channel — the
        ``lifecycle.start_terrarium`` path (it calls
        ``engine.apply_recipe``)."""
        service = make_service()
        engine = service.engine

        # ``CreatureConfig`` / ``RootConfig`` carry raw agent-config
        # dicts — the in-recipe shape ``load_terrarium_config`` builds.
        def _cfg(name: str) -> dict:
            return {
                "name": name,
                "llm": "test/scripted",
                "model": "scripted-model",
                "provider": "test",
                "system_prompt": f"You are {name}.",
                "tool_format": "bracket",
                "input": {"type": "none"},
                "output": {"type": "stdout"},
            }

        recipe = TerrariumConfig(
            name="research-team",
            creatures=[
                CreatureConfig(
                    name="scout",
                    config_data=_cfg("scout"),
                    base_dir=tmp_path,
                    listen_channels=["findings"],
                    send_channels=["findings"],
                ),
                CreatureConfig(
                    name="writer",
                    config_data=_cfg("writer"),
                    base_dir=tmp_path,
                    listen_channels=["findings"],
                ),
            ],
            channels=[ChannelConfig(name="findings", channel_type="broadcast")],
            root=RootConfig(config_data=_cfg("root"), base_dir=tmp_path),
        )

        graph = await engine.apply_recipe(recipe)
        gid = graph.graph_id

        # Every creature landed in ONE graph: the two workers + root.
        assert graph.creature_ids == {"scout", "writer", "root"}
        # Declared channel + per-creature direct channels + report_to_root.
        assert "findings" in graph.channels
        assert "scout" in graph.channels and "writer" in graph.channels
        assert "report_to_root" in graph.channels

        # The ``root:`` keyword promoted its creature to a privileged
        # node — it has the privilege flag AND the group_* surface.
        root_info = await service.get_creature_info("root")
        assert root_info.is_privileged is True
        root_agent = engine.get_creature("root").agent
        assert "group_channel" in root_agent.registry.list_tools()
        assert "group_add_node" in root_agent.registry.list_tools()
        # Workers are NOT privileged.
        assert (await service.get_creature_info("scout")).is_privileged is False
        assert (await service.get_creature_info("writer")).is_privileged is False

        # assign_root wired the root as listener on every channel and
        # gave every other creature a send edge on report_to_root.
        assert "findings" in graph.listen_edges["root"]
        assert "report_to_root" in graph.listen_edges["root"]
        assert "report_to_root" in graph.send_edges["scout"]
        assert "report_to_root" in graph.send_edges["writer"]
        # The recipe's own declared listen/send wiring is honored too.
        assert "findings" in graph.send_edges["scout"]
        assert "findings" in graph.listen_edges["writer"]
        # Every creature was started by the recipe.
        for cid in ("scout", "writer", "root"):
            assert engine.get_creature(cid).is_running is True

        # A broadcast on ``findings``: all three creatures listen
        # (scout + writer by recipe wiring, root by assign_root), so
        # all three hold a subscription and all three queues receive
        # the send — the per-creature ChannelTrigger, not the channel
        # layer, is what filters a creature's own sends.
        await _settle()
        env = engine._environments[gid]
        findings = env.shared_channels.get("findings")
        assert set(findings._subscribers) == {
            "scout_findings",
            "writer_findings",
            "root_findings",
        }
        await findings.send(ChannelMessage(sender="scout", content="found it"))
        assert findings.history[-1].content == "found it"
        for sub_id in ("scout_findings", "writer_findings", "root_findings"):
            queued = findings._subscribers[sub_id]
            assert queued.qsize() == 1
            assert queued.get_nowait().content == "found it"

        # --- per-creature ops the Protocol exposes to Studio -------------
        # The studio panels (scratchpad / triggers / env / system-prompt /
        # working-dir / plugins / native-tools) all route through these.
        # system prompt — exactly what the recipe declared for scout.
        sp = await service.get_system_prompt("scout")
        assert sp["text"] and "scout" in sp["text"]
        # scratchpad round-trip — patch sets a key, get reads it back,
        # patching to None deletes it.
        assert await service.get_scratchpad("scout") == {}
        patched = await service.patch_scratchpad("scout", {"focus": "area-51"})
        assert patched["focus"] == "area-51"
        assert (await service.get_scratchpad("scout"))["focus"] == "area-51"
        cleared = await service.patch_scratchpad("scout", {"focus": None})
        assert "focus" not in cleared
        # a framework-reserved (``__key__``) scratchpad key is rejected.
        with pytest.raises(ValueError):
            await service.patch_scratchpad("scout", {"__system__": "x"})
        # triggers — scout's recipe wiring (listen on ``findings`` +
        # ``report_to_root`` + its own direct channel) installed live
        # ChannelTriggers, surfaced here for the studio triggers panel.
        scout_triggers = await service.list_triggers("scout")
        assert scout_triggers  # recipe wiring installed channel triggers
        assert all(t["trigger_type"] == "ChannelTrigger" for t in scout_triggers)
        assert all(t["running"] is True for t in scout_triggers)
        assert "channel_scout_findings" in {t["trigger_id"] for t in scout_triggers}
        # env — the working dir is surfaced, secrets are redacted.
        env_info = await service.get_env("scout")
        assert "pwd" in env_info and "env" in env_info
        assert not any("API_KEY" in k.upper() for k in env_info["env"])
        # working dir — get / set round-trip.
        wd = await service.get_working_dir("scout")
        assert wd
        new_wd = await service.set_working_dir("scout", str(tmp_path))
        assert new_wd == str(tmp_path)
        assert await service.get_working_dir("scout") == str(tmp_path)
        # plugins / modules — every engine creature carries the built-in
        # cross-cutting plugins (sandbox / budget / permgate / compact).
        plugins = await service.list_plugins("scout")
        plugin_names = {p["name"] for p in plugins}
        assert "sandbox" in plugin_names
        assert "budget" in plugin_names
        modules = await service.list_modules("scout")
        assert {m["type"] for m in modules} <= {"plugin", "native_tool"}
        # toggle a real built-in plugin on, then back off — the enabled
        # flag is the observable side effect.
        toggled_on = await service.toggle_plugin("scout", "budget", True)
        assert toggled_on == {"plugin": "budget", "enabled": True}
        toggled_off = await service.toggle_plugin("scout", "budget", False)
        assert toggled_off == {"plugin": "budget", "enabled": False}
        # toggling a non-existent plugin raises KeyError (a 404, never a
        # fabricated success).
        with pytest.raises(KeyError):
            await service.toggle_plugin("scout", "ghost_plugin", True)
        # native-tool inventory is a list (scripted creatures have none).
        assert await service.native_tool_inventory("scout") == []
        # native-tool options map — empty when no native tools exist.
        assert await service.get_native_tool_options("scout") == {}
        # execute_command — a built-in slash command runs against the
        # creature; ``/status`` reports state, an unknown command raises.
        status_cmd = await service.execute_command("root", "status")
        assert status_cmd["command"] == "status"
        assert status_cmd["success"] is True
        with pytest.raises(ValueError):
            await service.execute_command("root", "definitely_not_a_command")

        # --- output-wiring round-trip + live delivery --------------------
        # Wire scout → writer as a direct round-output edge, list it.
        # ``wire_output`` returns the stable edge id.
        wired = await service.wire_output(
            "scout", {"to": "writer", "with_content": True}
        )
        edge_id = wired["edge_id"]
        assert edge_id
        listed = await service.list_output_wiring("scout")
        assert any(e["to"] == "writer" for e in listed)
        # With the edge LIVE, a full scout turn fires the wiring resolver
        # at turn-finalisation: writer receives a ``creature_output``
        # event and processes its own turn. Drive scout through chat so
        # the turn completes, then let the delivery task land.
        scout_chunks: list[str] = []
        async for chunk in service.chat("scout", "scout reporting"):
            scout_chunks.append(chunk)
        assert "".join(scout_chunks) == "ack"
        await asyncio.sleep(0.1)  # let the fire-and-forget delivery run
        # writer's conversation now carries the wired inbound turn —
        # its history grew beyond just its own earlier message.
        writer_hist = await service.chat_history("writer")
        assert len(writer_hist["messages"]) >= 2
        # Now unwire and confirm it is gone.
        assert await service.unwire_output("scout", edge_id) is True
        assert await service.list_output_wiring("scout") == []
        # unwiring an unknown edge is a clean False, never an exception.
        assert await service.unwire_output("scout", "wire_bogus") is False

        # --- chat streaming through the Protocol -------------------------
        # ``service.chat`` injects a message and streams the creature's
        # text response. The default script answers "ack".
        chunks: list[str] = []
        async for chunk in service.chat("writer", "hello writer"):
            chunks.append(chunk)
        assert "".join(chunks) == "ack"
        # ``chat_history`` then exposes that turn for replay.
        history = await service.chat_history("writer")
        assert history["creature_id"] == "writer"
        assert history["session_id"] == gid
        joined = " ".join(
            m.get("content", "") if isinstance(m.get("content"), str) else ""
            for m in history["messages"]
        )
        assert "hello writer" in joined
        # ``chat_branches`` returns the per-turn branch metadata list.
        assert isinstance(await service.chat_branches("writer"), list)

        # --- branch ops through the Protocol -----------------------------
        # ``regenerate`` re-runs the assistant tail; ``edit_message``
        # rewrites a user message and re-runs from there; ``rewind``
        # drops the tail without re-running. All route to the live agent.
        # Both regen + edit now return ``{status, turn_index, branch_id}``
        # — the freshly-opened branch_id lets the frontend's <N/M>
        # navigator promote before the post-turn resync lands. ``False``
        # is reserved for refused edits.
        regen = await service.regenerate("writer")
        assert regen["status"] == "regenerating"
        assert isinstance(regen["turn_index"], int) and regen["turn_index"] >= 1
        assert isinstance(regen["branch_id"], int) and regen["branch_id"] >= 1
        # find the user message index to edit (the "hello writer" turn).
        hist2 = await service.chat_history("writer")
        user_idx = next(
            i for i, m in enumerate(hist2["messages"]) if m.get("role") == "user"
        )
        edited = await service.edit_message("writer", user_idx, "edited writer message")
        assert isinstance(edited, dict)
        assert edited["status"] == "edited"
        assert isinstance(edited["branch_id"], int) and edited["branch_id"] >= 1
        hist3 = await service.chat_history("writer")
        joined3 = " ".join(
            m.get("content", "") if isinstance(m.get("content"), str) else ""
            for m in hist3["messages"]
        )
        assert "edited writer message" in joined3
        # rewind drops every message from index 1 onward (keeps message 0).
        await service.rewind("writer", 1)
        rewound = await service.chat_history("writer")
        assert len(rewound["messages"]) <= 1

        # --- module-options surface --------------------------------------
        # ``get_module_options`` reads one module's schema + values;
        # toggling via the module dispatcher mirrors ``toggle_plugin``.
        budget_opts = await service.get_module_options("scout", "plugin", "budget")
        assert budget_opts["type"] == "plugin"
        assert budget_opts["name"] == "budget"
        toggled_mod = await service.toggle_module("scout", "plugin", "budget")
        assert toggled_mod["name"] == "budget"
        # an unknown module type is rejected.
        with pytest.raises(ValueError):
            await service.get_module_options("scout", "bogus_type", "x")

        # --- runtime-graph prompt block ----------------------------------
        # ``RuntimeGraphPrompt.refresh_creature`` splices a live
        # ``## Live Group`` section into the creature's system prompt —
        # the lifecycle.py / explicit-recompute path. The root is
        # privileged and listens on every channel, so its block names it
        # privileged and lists its listen channels.
        root_creature = engine.get_creature("root")
        await engine._runtime_prompt.refresh_creature(root_creature)
        root_prompt = root_creature.agent.get_system_prompt()
        assert "## Live Group" in root_prompt
        assert "privileged" in root_prompt
        assert "findings" in root_prompt
        assert "<!-- runtime-graph -->" in root_prompt
        # A second refresh replaces (not stacks) the block.
        await engine._runtime_prompt.refresh_creature(root_creature)
        assert root_creature.agent.get_system_prompt().count("## Live Group") == 1
        # The non-privileged scout's block names its recipe wiring.
        scout_creature = engine.get_creature("scout")
        await engine._runtime_prompt.refresh_creature(scout_creature)
        scout_prompt = scout_creature.agent.get_system_prompt()
        assert "## Live Group" in scout_prompt
        assert "findings" in scout_prompt
        # The writer's block names its single recipe wire too.
        writer_creature = engine.get_creature("writer")
        await engine._runtime_prompt.refresh_creature(writer_creature)
        assert writer_creature.agent.get_system_prompt().count("## Live Group") == 1

        # --- output-wiring resolver: ROOT_TARGET + delivery edge cases ---
        # The resolver's ``root`` magic target resolves to the graph's
        # privileged creature. Wire scout → root, drive a scout turn, and
        # the root receives a ``creature_output`` event (its history grows).
        root_pre = len((await service.chat_history("root"))["messages"])
        root_wire = await service.wire_output(
            "scout", {"to": "root", "with_content": True}
        )
        root_edge = root_wire["edge_id"]
        scout_chunks2: list[str] = []
        async for chunk in service.chat("scout", "report to root"):
            scout_chunks2.append(chunk)
        assert "".join(scout_chunks2) == "ack"
        await asyncio.sleep(0.1)  # let the fire-and-forget delivery land
        assert len((await service.chat_history("root"))["messages"]) > root_pre
        assert await service.unwire_output("scout", root_edge) is True
        # A wire whose target creature is STOPPED is dropped at delivery:
        # stop writer, wire scout → writer, drive a scout turn — writer's
        # history must NOT grow because the resolver skips stopped targets.
        await service.stop_creature("writer")
        assert engine.get_creature("writer").is_running is False
        writer_pre = len((await service.chat_history("writer"))["messages"])
        stopped_wire = await service.wire_output(
            "scout", {"to": "writer", "with_content": True}
        )
        async for _ in service.chat("scout", "to a stopped writer"):
            pass
        await asyncio.sleep(0.1)
        assert len((await service.chat_history("writer"))["messages"]) == writer_pre
        assert await service.unwire_output("scout", stopped_wire["edge_id"]) is True
        await service.start_creature("writer")
        # A wire to a creature that does NOT exist is silently dropped
        # (the resolver warns once and skips) — the source turn still ok.
        ghost_wire = await service.wire_output(
            "scout", {"to": "no_such_target", "with_content": True}
        )
        async for _ in service.chat("scout", "to a ghost"):
            pass
        await asyncio.sleep(0.05)
        assert await service.unwire_output("scout", ghost_wire["edge_id"]) is True

        # --- OutputLogCapture — tee a creature's output into a ring buffer.
        # ``creature_host`` exposes ``output_log`` + ``get_log_entries`` /
        # ``get_log_text`` for observability; wrap writer's output module,
        # drive a turn, and read the captured text back.
        original_output = writer_creature.agent.output_router.default_output
        capture = OutputLogCapture(original_output, max_entries=50)
        writer_creature.agent.output_router.default_output = capture
        writer_creature.output_log = capture
        await capture.start()
        await capture.write("captured-line-one")
        await capture.write_stream("streamed-")
        await capture.write_stream("chunk")
        await capture.flush()
        capture.on_activity("probe", "activity-detail")
        entries = writer_creature.get_log_entries(last_n=10)
        assert any(e.content == "captured-line-one" for e in entries)
        assert any(e.entry_type == "stream_flush" for e in entries)
        assert any(e.entry_type == "activity" for e in entries)
        log_text = writer_creature.get_log_text(last_n=10)
        assert "captured-line-one" in log_text
        assert "streamed-chunk" in log_text
        assert capture.entry_count >= 3
        # ``preview`` truncates long content; ``clear`` empties the buffer.
        long_entry = entries[0]
        assert long_entry.preview(max_len=5) == long_entry.content[:5] + (
            "..." if len(long_entry.content) > 5 else ""
        )
        capture.clear()
        assert capture.entry_count == 0
        await capture.stop()
        # restore writer's original output module.
        writer_creature.agent.output_router.default_output = original_output
        writer_creature.output_log = None

        await service.shutdown()

    @pytest.mark.timeout(60)
    async def test_group_tools_visible_in_prompt_with_drive_off(
        self, patched_llm, tmp_path
    ):
        """UXI-10 blocker: the group_* tools + ``group_status`` guidance must
        reach the model even when Drive is OFF.

        Registration happens AFTER the agent is built, so without a prompt
        rebuild the tools are frozen out of ``## Available Functions`` in
        bracket/text mode. It only ever worked because the Drive plugin
        install rebuilt the prompt — so a Drive-DISABLED privileged creature
        could not spawn/wire graph creatures at all. ``_register_named`` now
        refreshes the prompt; this pins that it holds with Drive off.
        """
        engine = Terrarium(
            pwd=str(tmp_path), drive_config=DriveRuntimeConfig(enabled=False)
        )
        # Prove Drive is genuinely off (so the prompt refresh is NOT coming
        # from the Drive plugin install).
        assert engine.drives is None
        async with engine:
            root = await engine.add_creature(
                _agent_config("root", tmp_path),
                creature_id="root",
                is_privileged=True,
            )
            prompt = root.agent.get_system_prompt()
            # The privileged surface AND the paradigm guidance are live.
            assert "group_spawn_child" in prompt
            assert "send_channel" in prompt
            assert "unbounded sub-agent" in prompt
            # A non-privileged creature gets the comm tools but not the
            # graph-mutating surface.
            worker = await engine.add_creature(
                _agent_config("worker", tmp_path),
                graph=root.graph_id,
                creature_id="worker",
            )
            wprompt = worker.agent.get_system_prompt()
            assert "send_channel" in wprompt
            assert "group_spawn_child" not in wprompt

    @pytest.mark.timeout(120)
    async def test_drive_runtime_local_mvp(self, patched_llm, tmp_path):
        """M1 Drive workflow through explicit constructor args.

        A creature calls ``drive_create`` via real LLM tool syntax; the
        engine delivers ``drive_ready`` as an ordinary ``TriggerEvent`` and
        the creature processes it. Stopping the creature defers a
        newly-assigned drive with no admission; restarting reconciles and
        admits it after the restoration barrier. A terminal proposal moves
        the first drive to COMPLETED in the repository. A Drive-disabled
        engine remains Drive-free — no recipe/config change is involved."""
        patched_llm.set_script(
            "worker",
            [
                ScriptEntry(
                    "[/drive_create]\n@@title=deploywatch\n[drive_create/]",
                    match="create please",
                ),
                "drive is created",
                "acknowledged the drive",
            ],
        )
        engine = Terrarium(
            pwd=str(tmp_path),
            drive_config=DriveRuntimeConfig(enabled=True),
            drive_registrations=list(default_registrations()) + [_TwoPartyReviewReg()],
        )
        events: list = []
        async with engine:
            worker = await engine.add_creature(
                _agent_config("worker", tmp_path), creature_id="worker"
            )
            manager = engine.drives.manager

            # Injection: the five self-service tools + generic prompt landed.
            tools = worker.agent.registry.list_tools()
            for name in (
                "drive_create",
                "drive_status",
                "drive_update",
                "drive_report",
                "drive_transition",
            ):
                assert name in tools, f"{name} not injected"
            assert "Drive kind: generic" in worker.agent.get_system_prompt()

            async def _collect():
                async for ev in engine.subscribe():
                    events.append(ev)

            collector = asyncio.create_task(_collect())

            # A subscriber filtered to worker's graph must see only THIS graph's
            # Drive events (Defect 5: Drive events must carry their graph_id so
            # graph-filtered subscribers match and events stay isolated).
            gid_a = worker.graph_id
            graph_a_events: list = []

            async def _collect_graph_a():
                async for ev in engine.subscribe(EventFilter(graph_ids={gid_a})):
                    graph_a_events.append(ev)

            collector_a = asyncio.create_task(_collect_graph_a())
            await asyncio.sleep(0)

            # 1) A real LLM tool turn creates a caller-owned ACTIVE drive.
            await worker.inject_input("create please")
            drive_id = None
            for _ in range(150):
                drives = await manager.list_drives(DriveQuery())
                if drives:
                    drive_id = drives[0].drive_id
                    break
                await asyncio.sleep(0.03)
            assert drive_id is not None, "drive_create tool turn produced no drive"
            record = await manager.get_drive(drive_id)
            assert record.owner == ActorRef("creature", "worker")
            assert record.scope_type == "creature"
            assert record.status is DriveStatus.ACTIVE

            # 2) drive_ready delivered as an ORDINARY TriggerEvent + processed.
            for _ in range(150):
                deliveries = await manager.list_deliveries(drive_id)
                if any(d.state == "acknowledged" for d in deliveries):
                    break
                await asyncio.sleep(0.03)
            deliveries = await manager.list_deliveries(drive_id)
            admitted = [
                d for d in deliveries if d.state in ("admitted", "acknowledged")
            ]
            # Exactly one logical admission reached the creature (§5.3 dedupe).
            assert len(admitted) == 1, [d.state for d in deliveries]
            joined = " ".join(
                m.get("content", "") if isinstance(m.get("content"), str) else ""
                for m in worker.agent.conversation_history
            )
            assert "acknowledged the drive" in joined
            await asyncio.sleep(0.05)
            assert any(ev.kind == EventKind.DRIVE_READY for ev in events)
            assert any(ev.kind == EventKind.DRIVE_CREATED for ev in events)
            # The graph-A-filtered subscriber received graph A's Drive events,
            # each stamped with graph A's id (this collected NOTHING before the
            # fix, when every Drive event carried graph_id=None).
            assert any(ev.kind == EventKind.DRIVE_CREATED for ev in graph_a_events)
            assert graph_a_events and all(ev.graph_id == gid_a for ev in graph_a_events)

            # 3) Stop the worker; a drive assigned while stopped is deferred.
            await engine.stop("worker")
            actor = ActorRef("user", "alice")
            drive2 = await manager.create_drive(
                CreateDriveRequest(
                    kind="generic",
                    title="watch2",
                    scope_type="graph",
                    scope_id=worker.graph_id,
                    owner=actor,
                    owner_scope="graph",
                    created_by=actor,
                    assignee_creature_id="worker",
                ),
                actor=actor,
                graph_id=worker.graph_id,
                is_privileged=True,
            )
            for _ in range(20):
                await asyncio.sleep(0.03)
            d2 = await manager.list_deliveries(drive2.drive_id)
            assert d2, "no delivery enqueued for the stopped-worker drive"
            assert not any(x.state in ("admitted", "acknowledged") for x in d2), [
                x.state for x in d2
            ]

            # 4) Restart -> reconcile admits it after the restoration barrier.
            await engine.start("worker")
            for _ in range(200):
                d2 = await manager.list_deliveries(drive2.drive_id)
                if any(x.state == "acknowledged" for x in d2):
                    break
                await asyncio.sleep(0.03)
            d2 = await manager.list_deliveries(drive2.drive_id)
            assert any(x.state == "acknowledged" for x in d2), [x.state for x in d2]

            # 5) Terminal proposal -> repository shows COMPLETED.
            record = await manager.get_drive(drive_id)
            await manager.propose_transition(
                drive_id,
                DriveStatus.COMPLETED,
                actor=ActorRef("creature", "worker"),
                evidence={"ok": True},
                expected_revision=record.revision,
            )
            final = await manager.get_drive(drive_id)
            assert final.status is DriveStatus.COMPLETED

            # 6) Structural events reach a real subscriber (item 5). Stop the
            # worker first so these manager calls don't spawn LLM turns; the
            # observation->EngineEvent bridge is independent of delivery.
            await engine.stop("worker")
            await manager.report_progress(
                drive2.drive_id, summary="halfway", evidence=None, actor=actor
            )
            review = await manager.create_drive(
                CreateDriveRequest(
                    kind="review",
                    title="review-me",
                    scope_type="graph",
                    scope_id=worker.graph_id,
                    owner=actor,
                    owner_scope="graph",
                    created_by=actor,
                    assignee_creature_id="worker",
                ),
                actor=actor,
                graph_id=worker.graph_id,
                is_privileged=True,
            )
            proposal = await manager.propose_transition(
                review.drive_id,
                DriveStatus.COMPLETED,
                actor=actor,
                evidence={"ok": True},
                expected_revision=review.revision,
            )
            await _settle()
            await asyncio.sleep(0.05)
            # drive_progress and drive_proposal_pending must arrive as EngineEvents
            # (the frontend depends on both; they were silently dropped before).
            assert any(ev.kind == EventKind.DRIVE_PROGRESS for ev in events)
            pending = [
                ev for ev in events if ev.kind == EventKind.DRIVE_PROPOSAL_PENDING
            ]
            assert pending, "drive_proposal_pending never reached the subscriber"
            assert pending[0].payload["proposal_id"] == proposal.proposal_id

            # 7) Graph isolation: a Drive in a SECOND graph must not leak into
            # the graph-A-filtered subscriber. A fresh creature (no graph_id)
            # lands in its own singleton graph; it is stopped so its Drive never
            # spawns an (unscripted) LLM turn.
            worker_b = await engine.add_creature(
                _agent_config("worker_b", tmp_path), creature_id="worker_b"
            )
            gid_b = worker_b.graph_id
            assert gid_b != gid_a
            await engine.stop("worker_b")
            drive_b = await engine.drives.manager_for(gid_b).create_drive(
                CreateDriveRequest(
                    kind="generic",
                    title="watch-b",
                    scope_type="graph",
                    scope_id=gid_b,
                    owner=actor,
                    owner_scope="graph",
                    created_by=actor,
                ),
                actor=actor,
                graph_id=gid_b,
                is_privileged=True,
            )
            await _settle()
            await asyncio.sleep(0.05)
            # The unfiltered subscriber sees graph B's event, stamped with gid_b.
            b_created = [
                ev
                for ev in events
                if ev.kind == EventKind.DRIVE_CREATED
                and ev.payload.get("drive_id") == drive_b.drive_id
            ]
            assert b_created and b_created[0].graph_id == gid_b
            # The graph-A-filtered subscriber never saw any graph B event.
            assert all(ev.graph_id == gid_a for ev in graph_a_events)
            assert not any(
                ev.payload.get("drive_id") == drive_b.drive_id for ev in graph_a_events
            )

            collector_a.cancel()
            collector.cancel()

        # Bare engines now enable the generic + goal Drive runtime by default.
        assert Terrarium().drives is not None

    @pytest.mark.timeout(60)
    async def test_drive_topology_merge_then_split_moves_rows(
        self, patched_llm, tmp_path
    ):
        """A real-engine graph merge then split moves Drive rows between the
        per-graph repositories (design §6.6-6.7): ``connect`` merges A + B so the
        survivor's manager holds BOTH graphs' Drives (B's rehomed); ``disconnect``
        splits so each assigned Drive follows its assignee's child graph. WAITING
        Drives keep this deterministic — no delivery, pure row movement."""
        svc = ActorRef("service", "ops")

        def _req(graph_id: str, assignee: str) -> CreateDriveRequest:
            return CreateDriveRequest(
                kind="generic",
                title=f"watch-{assignee}",
                scope_type="graph",
                scope_id=graph_id,
                owner=svc,
                owner_scope="service",
                created_by=svc,
                assignee_creature_id=assignee,
                spec={},
            )

        engine = Terrarium(
            pwd=str(tmp_path),
            drive_config=DriveRuntimeConfig(enabled=True),
            drive_registrations=default_registrations(),
        )
        async with engine:
            alice = await engine.add_creature(
                _agent_config("alice", tmp_path), creature_id="alice"
            )
            bob = await engine.add_creature(
                _agent_config("bob", tmp_path), creature_id="bob"
            )
            # Separate singleton graphs -> two isolated Drive repositories.
            alice_gid0, bob_gid0 = alice.graph_id, bob.graph_id
            assert alice_gid0 != bob_gid0
            da = await engine.drives.manager_for(alice_gid0).create_drive(
                _req(alice_gid0, "alice"),
                actor=svc,
                graph_id=alice_gid0,
                is_privileged=True,
                initial_status=DriveStatus.WAITING,
            )
            db = await engine.drives.manager_for(bob_gid0).create_drive(
                _req(bob_gid0, "bob"),
                actor=svc,
                graph_id=bob_gid0,
                is_privileged=True,
                initial_status=DriveStatus.WAITING,
            )

            # MERGE: the real engine drains B's Drive rows into the survivor.
            result = await engine.connect("alice", "bob", channel="ab")
            assert result.delta_kind == "merge"
            survivor = result.graph_id
            smgr = engine.drives.manager_for(survivor)
            assert await smgr.get_drive(da.drive_id) is not None
            moved = await smgr.get_drive(db.drive_id)
            assert moved is not None and moved.scope_id == survivor
            # The non-survivor source graph's manager was dropped (§6.6).
            other = bob_gid0 if survivor == alice_gid0 else alice_gid0
            assert engine.drives.peek_manager(other) is None

            # SPLIT: each assigned Drive follows its assignee's child graph.
            disc = await engine.disconnect("alice", "bob", channel="ab")
            assert disc.delta_kind == "split"
            agid = engine.get_creature("alice").graph_id
            bgid = engine.get_creature("bob").graph_id
            assert agid != bgid
            amgr = engine.drives.manager_for(agid)
            bmgr = engine.drives.manager_for(bgid)
            assert await amgr.get_drive(da.drive_id) is not None
            assert await amgr.get_drive(db.drive_id) is None
            assert await bmgr.get_drive(db.drive_id) is not None
            assert await bmgr.get_drive(da.drive_id) is None

    @pytest.mark.timeout(120)
    async def test_drive_delivery_waits_for_startup_trigger(
        self, patched_llm, tmp_path
    ):
        """Restoration barrier (§6.5): a drive assigned to a creature that has
        a startup trigger is NOT delivered until the startup turn settles — the
        startup response precedes the drive response in the conversation."""
        patched_llm.set_script(
            "worker",
            [
                ScriptEntry("STARTUP-DONE-MARKER", match="startup wake"),
                "DRIVE-DONE-MARKER",
            ],
        )
        engine = Terrarium(
            pwd=str(tmp_path),
            drive_config=DriveRuntimeConfig(enabled=True),
            drive_registrations=default_registrations(),
        )
        async with engine:
            cfg = _agent_config("worker", tmp_path)
            cfg.startup_trigger = {"prompt": "startup wake"}
            # start=False so a drive is seeded BEFORE the barrier is crossed.
            worker = await engine.add_creature(cfg, creature_id="worker", start=False)
            actor = ActorRef("user", "alice")
            record = await engine.drives.manager.create_drive(
                CreateDriveRequest(
                    kind="generic",
                    title="watch",
                    scope_type="graph",
                    scope_id=worker.graph_id,
                    owner=actor,
                    owner_scope="graph",
                    created_by=actor,
                    assignee_creature_id="worker",
                ),
                actor=actor,
                graph_id=worker.graph_id,
                is_privileged=True,
            )
            # Start: the startup trigger fires; the drive is deferred behind
            # the barrier and delivered only after startup settles.
            await engine.start("worker")
            for _ in range(200):
                deliveries = await engine.drives.manager.list_deliveries(
                    record.drive_id
                )
                if any(d.state == "acknowledged" for d in deliveries):
                    break
                await asyncio.sleep(0.03)
            assistant_text = " || ".join(
                m.get("content", "") if isinstance(m.get("content"), str) else ""
                for m in worker.agent.conversation_history
                if m.get("role") == "assistant"
            )
            assert "STARTUP-DONE-MARKER" in assistant_text
            assert "DRIVE-DONE-MARKER" in assistant_text
            # Ordering: startup settled BEFORE the drive turn ran.
            assert assistant_text.index("STARTUP-DONE-MARKER") < assistant_text.index(
                "DRIVE-DONE-MARKER"
            )
