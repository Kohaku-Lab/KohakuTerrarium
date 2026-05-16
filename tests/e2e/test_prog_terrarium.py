"""End-to-end journey: programmatic multi-creature terrarium usage.

``e2e = whole project → a fat journey test``. Each method here is a
single function simulating an *entire* user session driving a
terrarium programmatically — the way ``studio/sessions/lifecycle.py``
and ``cli/resume.py`` drive the real :class:`Terrarium` engine. No
operation-per-test granularity: every method walks a whole workflow
and asserts observable state at each milestone.

The only seam is the LLM: both ``bootstrap.llm.create_llm_provider``
and ``bootstrap.agent_init.create_llm_provider`` are monkeypatched to
a deterministic :class:`ScriptedLLM`. Everything else is real — the
engine, ``LocalTerrariumService``, real ``Agent``-backed creatures,
the live channel registry, the ``group_*`` privileged tools, topology
auto-merge / auto-split, real ``SessionStore`` files on ``tmp_path``,
and the engine resume path.
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
from kohakuterrarium.terrarium.config import load_terrarium_config
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.terrarium.events import EventKind
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
# fixtures — the single LLM seam + a real-engine service builder
# ---------------------------------------------------------------------------


@pytest.fixture
def patched_llm(monkeypatch):
    """Patch BOTH LLM factory import sites so every real ``Agent`` the
    engine builds (including ones rebuilt on resume) gets a
    deterministic :class:`ScriptedLLM`."""

    def _fake_create(config, llm_override=None):
        return ScriptedLLM(["ack"])

    monkeypatch.setattr(_bootstrap_llm, "create_llm_provider", _fake_create)
    monkeypatch.setattr(_agent_init, "create_llm_provider", _fake_create)


@pytest.fixture
async def make_service(patched_llm, tmp_path):
    """Build a real ``Terrarium`` wrapped in ``LocalTerrariumService`` —
    the runtime surface ``studio/sessions/lifecycle.py`` operates
    through. ``session_dir`` is wired so merge/split coordination mints
    real store files. Async fixture so teardown can shut every engine
    down even if a test fails before its explicit shutdown."""
    engines: list[Terrarium] = []
    sess_dir = tmp_path / "sessions"
    sess_dir.mkdir()

    def _build() -> LocalTerrariumService:
        engine = Terrarium(pwd=str(tmp_path), session_dir=str(sess_dir))
        engines.append(engine)
        return LocalTerrariumService(engine)

    yield _build

    for engine in engines:
        await engine.shutdown()


# ---------------------------------------------------------------------------
# helpers — recipe authoring, tool-context minting, event collection
# ---------------------------------------------------------------------------


def _agent_yaml_block(name: str) -> str:
    """One creature entry in a terrarium recipe — a self-contained
    agent config (inline ``system_prompt``, ``input: none``) so no
    on-disk ``system.md`` is needed."""
    return (
        f"    - name: {name}\n"
        "      llm: test/scripted\n"
        "      model: scripted-model\n"
        "      provider: test\n"
        f"      system_prompt: You are {name}.\n"
        "      tool_format: bracket\n"
        "      input:\n"
        "        type: none\n"
        "      output:\n"
        "        type: stdout\n"
    )


def _write_recipe(tmp_path: Path) -> Path:
    """Author a real terrarium recipe on disk: two workers + a
    ``root:`` privileged node + one declared broadcast channel. Written
    as a file so ``load_terrarium_config`` can resolve it on resume —
    the recipe path is the source of truth the engine rebuilds from."""
    recipe = tmp_path / "team.yaml"
    recipe.write_text(
        "terrarium:\n"
        "  name: research-team\n"
        "  channels:\n"
        "    findings:\n"
        "      type: broadcast\n"
        "      description: shared findings channel\n"
        "  creatures:\n" + _agent_yaml_block("scout") + "      channels:\n"
        "        listen: [findings]\n"
        "        can_send: [findings]\n"
        + _agent_yaml_block("writer")
        + "      channels:\n"
        "        listen: [findings]\n"
        "  root:\n"
        "    name: root\n"
        "    llm: test/scripted\n"
        "    model: scripted-model\n"
        "    provider: test\n"
        "    system_prompt: You are the privileged lead.\n"
        "    tool_format: bracket\n"
        "    input:\n"
        "      type: none\n"
        "    output:\n"
        "      type: stdout\n",
        encoding="utf-8",
    )
    return recipe


def _agent_config(name: str, tmp_path: Path) -> AgentConfig:
    """A minimal real ``AgentConfig`` — the NoneInput creature shape
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
    """Drain the engine's deferred channel-trigger runner tasks — the
    actual channel ``subscribe()`` runs a few event-loop turns after a
    ``connect`` / ``add_channel`` sync call returns."""
    for _ in range(5):
        await asyncio.sleep(0)


# ===========================================================================
# the journeys
# ===========================================================================


class TestProgTerrariumJourney:
    """Programmatic multi-creature terrarium usage, end to end."""

    async def test_recipe_to_broadcast_to_privileged_mutation_journey(
        self, make_service, tmp_path
    ):
        """JOURNEY 1 — recipe apply → start → topology check → channel
        broadcast to exact listeners → privileged node mutates the team
        (spawn + wire + status + remove) → hot-plug a creature in.

        One continuous session: a user writes a recipe, brings the
        terrarium up, watches a creature broadcast, then lets the
        privileged ``lead`` evolve the team mid-run and hot-plugs a
        helper into the live graph.
        """
        service = make_service()
        engine = service.engine

        # --- 1. apply a real on-disk recipe -------------------------------
        recipe_path = _write_recipe(tmp_path)
        recipe = load_terrarium_config(recipe_path)
        graph = await engine.apply_recipe(recipe)
        gid = graph.graph_id

        # --- 2. every creature running, topology exactly as declared ------
        assert graph.creature_ids == {"scout", "writer", "root"}
        for cid in ("scout", "writer", "root"):
            assert engine.get_creature(cid).is_running is True
        # Declared channel + per-creature direct channels + report_to_root.
        assert "findings" in graph.channels
        assert "report_to_root" in graph.channels
        # The ``root:`` keyword promoted ``lead`` to a privileged node
        # with the full group_* surface; the workers stay unprivileged.
        root_info = await service.get_creature_info("root")
        assert root_info.is_privileged is True
        root_tools = engine.get_creature("root").agent.registry.list_tools()
        assert "group_add_node" in root_tools
        assert "group_channel" in root_tools
        assert (await service.get_creature_info("scout")).is_privileged is False
        assert (await service.get_creature_info("writer")).is_privileged is False
        # Recipe wiring honored: scout sends+listens, writer listens,
        # root listens on every channel.
        assert "findings" in graph.send_edges["scout"]
        assert "findings" in graph.listen_edges["scout"]
        assert "findings" in graph.listen_edges["writer"]
        assert "findings" in graph.listen_edges["root"]
        assert "report_to_root" in graph.send_edges["scout"]
        assert "report_to_root" in graph.send_edges["writer"]

        # --- 3. broadcast → exact listeners receive it --------------------
        await _settle()
        events: list = []
        collector = await _events(service, events)
        env = engine._environments[gid]
        findings = env.shared_channels.get("findings")
        # All three creatures listen on ``findings`` (scout+writer by
        # recipe, lead by root assignment) → three live subscriptions.
        assert set(findings._subscribers) == {
            "scout_findings",
            "writer_findings",
            "root_findings",
        }
        await findings.send(ChannelMessage(sender="scout", content="found it"))
        # The send fans the message into every listener's queue before
        # returning — check the broadcast landed in all three queues
        # *before* yielding to the running ChannelTriggers (which would
        # legitimately drain them).
        for sub_id in ("scout_findings", "writer_findings", "root_findings"):
            queued = findings._subscribers[sub_id]
            assert queued.qsize() == 1
            assert queued.get_nowait().content == "found it"
        await asyncio.sleep(0.05)  # let the engine event fan out
        collector.cancel()
        assert findings.history[-1].content == "found it"
        # Exactly one CHANNEL_MESSAGE engine event fired for that send.
        channel_msgs = [ev for ev in events if ev.kind == EventKind.CHANNEL_MESSAGE]
        assert len(channel_msgs) == 1
        assert channel_msgs[0].channel == "findings"
        assert channel_msgs[0].graph_id == gid
        assert channel_msgs[0].payload["sender"] == "scout"
        assert channel_msgs[0].payload["content"] == "found it"

        # --- 4. privileged node evolves the team mid-run ------------------
        worker_yaml = tmp_path / "analyst.yaml"
        worker_yaml.write_text(
            "name: analyst\nllm: test/scripted\nmodel: scripted-model\n"
            "provider: test\nsystem_prompt: You are analyst.\n"
            "tool_format: bracket\ninput:\n  type: none\n"
            "output:\n  type: stdout\n",
            encoding="utf-8",
        )
        ctx = _ctx_for(service, "root")
        # spawn — group_add_node drops the analyst into lead's graph as
        # a non-privileged child.
        add_res = await GroupAddNodeTool()._execute(
            {"config_path": str(worker_yaml), "name": "analyst"}, context=ctx
        )
        assert add_res.error is None, add_res.error
        analyst_id = json.loads(add_res.output)["creature_id"]
        analyst = engine.get_creature(analyst_id)
        assert analyst.name == "analyst"
        assert analyst.graph_id == gid
        assert analyst.is_privileged is False
        assert analyst.parent_creature_id == "root"
        # wire — group_channel(action="wire") gives the analyst a listen
        # edge on a fresh auto-created channel.
        wire_res = await GroupChannelTool()._execute(
            {
                "action": "wire",
                "channel": "analysis",
                "creature_id": analyst_id,
                "direction": "listen",
            },
            context=ctx,
        )
        assert wire_res.error is None, wire_res.error
        assert "analysis" in engine.get_graph(gid).channels
        assert "analysis" in engine.get_creature(analyst_id).listen_channels
        # status — lead's snapshot now lists all four creatures.
        status_res = await GroupStatusTool()._execute({}, context=ctx)
        assert status_res.error is None
        body = json.loads(status_res.output)
        assert body["self"]["creature_id"] == "root"
        assert body["self"]["is_privileged"] is True
        assert {c["creature_id"] for c in body["creatures"]} == {
            "scout",
            "writer",
            "root",
            analyst_id,
        }
        # messaging tools — the privileged root broadcasts on a wired
        # channel and fires a point-to-point message at the analyst.
        # First wire root as a sender on ``findings`` (it only listens by
        # default), then broadcast.
        await GroupChannelTool()._execute(
            {
                "action": "wire",
                "channel": "findings",
                "creature_id": "root",
                "direction": "send",
            },
            context=ctx,
        )
        await _settle()
        send_res = await SendChannelTool()._execute(
            {"channel": "findings", "message": "team sync"}, context=ctx
        )
        assert send_res.error is None, send_res.error
        findings_ch = engine._environments[gid].shared_channels.get("findings")
        assert findings_ch.history[-1].content == "team sync"
        assert findings_ch.history[-1].sender == "root"
        # group_send — fire-and-forget direct message to the analyst.
        gs_res = await GroupSendTool()._execute(
            {"to": analyst_id, "message": "analyse this"}, context=ctx
        )
        assert gs_res.error is None, gs_res.error
        assert json.loads(gs_res.output)["delivered"] is True
        # group_stop_node / group_start_node — pause + resume the analyst.
        stop_res = await GroupStopNodeTool()._execute(
            {"creature_id": analyst_id}, context=ctx
        )
        assert stop_res.error is None, stop_res.error
        assert engine.get_creature(analyst_id).is_running is False
        start_res = await GroupStartNodeTool()._execute(
            {"creature_id": analyst_id}, context=ctx
        )
        assert start_res.error is None, start_res.error
        assert engine.get_creature(analyst_id).is_running is True
        # group_wire — add a direct output-wire edge root → analyst, then
        # remove it by the returned edge id.
        gw_add = await GroupWireTool()._execute(
            {"action": "add", "to": analyst_id, "with_content": True}, context=ctx
        )
        assert gw_add.error is None, gw_add.error
        wire_edge_id = json.loads(gw_add.output)["edge_id"]
        assert wire_edge_id
        assert any(e["to"] == "analyst" for e in engine.list_output_wiring("root"))
        gw_rm = await GroupWireTool()._execute(
            {"action": "remove", "edge_id": wire_edge_id}, context=ctx
        )
        assert gw_rm.error is None, gw_rm.error
        assert json.loads(gw_rm.output)["removed"] is True
        assert engine.list_output_wiring("root") == []

        # remove — group_remove_node destroys the analyst; the graph
        # stays connected (it was a leaf) so no split.
        rm_res = await GroupRemoveNodeTool()._execute(
            {"creature_id": analyst_id}, context=ctx
        )
        assert rm_res.error is None, rm_res.error
        assert analyst_id not in engine
        assert {c.creature_id for c in await service.list_creatures()} == {
            "scout",
            "writer",
            "root",
        }
        # The privileged node itself may NOT be removed via the tool.
        deny = await GroupRemoveNodeTool()._execute(
            {"creature_id": "root"}, context=ctx
        )
        assert deny.error is not None
        assert "privileged" in deny.error

        # --- 5. hot-plug a creature into the running session --------------
        helper = await service.add_creature(
            _agent_config("helper", tmp_path),
            graph_id=gid,
            creature_id="helper",
        )
        assert helper.graph_id == gid  # joined the live graph, no new one
        assert engine.get_creature("helper").is_running is True
        graphs = await service.list_graphs()
        assert len(graphs) == 1
        assert graphs[0].creature_ids == {"scout", "writer", "root", "helper"}
        # The hot-plugged creature is a first-class member: wire it and
        # its listen subscription lands immediately.
        await service.connect("scout", "helper", channel="findings")
        await _settle()
        assert "findings" in (await service.get_creature_info("helper")).listen_channels
        findings = engine._environments[gid].shared_channels.get("findings")
        assert "helper_findings" in findings._subscribers

        # --- 6. chat with the hot-plugged creature, then read it back -----
        # ``service.chat`` injects + streams the creature's text reply;
        # the default script answers "ack". ``chat_history`` then exposes
        # the turn for replay (the studio chat-tab read path).
        chunks: list[str] = []
        async for chunk in service.chat("helper", "status check"):
            chunks.append(chunk)
        assert "".join(chunks) == "ack"
        helper_history = await service.chat_history("helper")
        assert helper_history["creature_id"] == "helper"
        joined = " ".join(
            m.get("content", "") if isinstance(m.get("content"), str) else ""
            for m in helper_history["messages"]
        )
        assert "status check" in joined
        # per-creature studio ops on the live graph: scratchpad round-trip
        # + system-prompt read + working-dir read.
        patched = await service.patch_scratchpad("helper", {"note": "hot-plugged"})
        assert patched["note"] == "hot-plugged"
        assert (await service.get_scratchpad("helper"))["note"] == "hot-plugged"
        assert "helper" in (await service.get_system_prompt("helper"))["text"]
        assert await service.get_working_dir("helper")

        # --- 7. runtime-graph snapshot reflects the whole evolved team ----
        rgs = await service.runtime_graph_snapshot()
        rg = {g["graph_id"]: g for g in rgs["graphs"]}[gid]
        assert {c["name"] for c in rg["creatures"]} == {
            "scout",
            "writer",
            "root",
            "helper",
        }
        # exactly one creature is flagged as the privileged root.
        assert sum(1 for c in rg["creatures"] if c["is_root"]) == 1
        assert next(c for c in rg["creatures"] if c["is_root"])["name"] == "root"

        # --- 8. runtime-graph prompt block on the evolved team -----------
        # ``RuntimeGraphPrompt.refresh_creature`` splices the live
        # ``## Live Group`` section into a creature's system prompt. The
        # root has the analyst as a child + listens on every channel.
        root_creature = engine.get_creature("root")
        await engine._runtime_prompt.refresh_creature(root_creature)
        root_prompt = root_creature.agent.get_system_prompt()
        assert "## Live Group" in root_prompt
        assert "privileged" in root_prompt
        assert "<!-- runtime-graph -->" in root_prompt
        # The hot-plugged helper's block reflects its single recipe wire.
        helper_creature = engine.get_creature("helper")
        await engine._runtime_prompt.refresh_creature(helper_creature)
        assert "findings" in helper_creature.agent.get_system_prompt()

        # --- 9. attach-policy surface on the evolved team ----------------
        # The graph has wired channels + a privileged root, so the full
        # policy set is advertised. ``attach_policies`` is per-creature;
        # ``session_attach_policies`` is graph-level.
        root_policies = await service.attach_policies("root")
        assert "log" in root_policies and "trace" in root_policies
        assert "observer" in root_policies
        sess_policies = await service.session_attach_policies(gid)
        assert "io" in sess_policies  # privileged root in the graph
        assert "observer" in sess_policies
        # An unknown creature still gets the safe base list.
        assert set(await service.attach_policies("ghost")) == {"log", "trace"}

        # --- 10. output-wiring self-trigger + sink lifecycle -------------
        # Wire scout → scout with allow_self_trigger off (the default):
        # the edge exists but a self-emission is blocked at delivery time.
        self_wire = await service.wire_output(
            "scout", {"to": "scout", "with_content": True}
        )
        self_edge = self_wire["edge_id"]
        assert self_edge
        listed = await service.list_output_wiring("scout")
        assert any(e["to"] == "scout" for e in listed)
        # Drive a scout turn — the self-trigger resolver path runs and
        # blocks (no infinite loop); the turn still streams "ack".
        sc_chunks: list[str] = []
        async for chunk in service.chat("scout", "self report"):
            sc_chunks.append(chunk)
        assert "".join(sc_chunks) == "ack"
        await asyncio.sleep(0.05)
        assert await service.unwire_output("scout", self_edge) is True
        assert await service.list_output_wiring("scout") == []
        # A secondary output sink round-trips through the service surface.
        sink_seen: list[str] = []

        class _Sink:
            async def write(self, text: str) -> None:
                sink_seen.append(text)

            async def flush(self) -> None:
                pass

        sink_id = await engine.wire_output_sink("scout", _Sink())
        assert sink_id
        assert await service.unwire_output_sink("scout", sink_id) is True
        assert await service.unwire_output_sink("scout", "sink_missing") is False

        await service.shutdown()

    async def test_auto_split_then_auto_merge_journey(self, make_service, tmp_path):
        """JOURNEY 2 — the load-bearing topology workflow. Build a
        linear graph alice-bob-carol where bob is the sole bridge →
        attach a session store → remove bob → auto-split into the exact
        surviving components with fresh per-side environments and
        per-side session stores carrying ``parent_session_ids`` lineage
        → connect across the two graphs → auto-merge into one graph with
        a union environment and a merged store carrying ``merged_at``
        lineage back to both pre-merge stores.
        """
        service = make_service()
        engine = service.engine

        # Build a chain alice <-> bob <-> carol; bob bridges the two ends.
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
        assert len(await service.list_graphs()) == 1
        env_before = engine._environments[gid]

        # Attach a real session store so the split has lineage to copy.
        store = SessionStore(tmp_path / "sessions" / f"{gid}.kohakutr")
        store.init_meta(
            session_id=gid,
            config_type="terrarium",
            config_path="",
            pwd=str(tmp_path),
            agents=["alice", "bob", "carol"],
        )
        await engine.attach_session(gid, store)

        # --- AUTO-SPLIT: remove the bridge creature -----------------------
        events: list = []
        collector = await _events(service, events)
        await service.remove_creature("bob")
        await asyncio.sleep(0.05)
        collector.cancel()

        # bob gone → graph fragmented into exactly {alice} and {carol}.
        graphs = await service.list_graphs()
        assert len(graphs) == 2
        members = sorted(sorted(g.creature_ids) for g in graphs)
        assert members == [["alice"], ["carol"]]
        alice_info = await service.get_creature_info("alice")
        carol_info = await service.get_creature_info("carol")
        assert alice_info.graph_id != carol_info.graph_id
        # Each survivor got its OWN environment object.
        env_alice = engine._environments[alice_info.graph_id]
        env_carol = engine._environments[carol_info.graph_id]
        assert env_alice is not env_carol
        assert env_before in (env_alice, env_carol)  # one survivor reuses it
        # A split EngineEvent fired naming both new graph ids.
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
        # Per-side session stores, each carrying the pre-split session
        # as a parent.
        store_alice = engine._session_stores[alice_info.graph_id]
        store_carol = engine._session_stores[carol_info.graph_id]
        assert store_alice is not store_carol
        assert store_alice.meta["parent_session_ids"] == [gid]
        assert store_carol.meta["parent_session_ids"] == [gid]

        # --- AUTO-MERGE: connect across the two graphs --------------------
        merge_res = await service.connect("alice", "carol", channel="reunite")
        assert merge_res.delta_kind == "merge"
        # Invariant: graph == connected component → back to ONE graph.
        graphs = await service.list_graphs()
        assert len(graphs) == 1
        assert graphs[0].creature_ids == {"alice", "carol"}
        a2 = await service.get_creature_info("alice")
        c2 = await service.get_creature_info("carol")
        assert a2.graph_id == c2.graph_id
        # Union environment: both creatures share one env carrying the
        # new channel.
        merged_env = engine._environments[a2.graph_id]
        assert engine._environments[c2.graph_id] is merged_env
        assert merged_env.shared_channels.get("reunite") is not None
        # Merged session store carries lineage back to BOTH pre-merge
        # stores.  When the merge target's path is the kept graph's
        # existing file, ``apply_merge`` reuses the live kept store and
        # copies the OTHER side's events into it — opening a second
        # ``SessionStore`` at the same path would duplicate every row.
        # So ``merged_store`` may be identity-equal to whichever side
        # ``new_graph_ids[0]`` selected; what we assert here is the
        # *contract*: lineage and merge marker are stamped onto the
        # surviving store regardless of which physical handle survived.
        merged_store = engine._session_stores[a2.graph_id]
        assert set(merged_store.meta["parent_session_ids"]) == {
            store_alice.session_id,
            store_carol.session_id,
        }
        assert "merged_at" in merged_store.meta
        # The dropped side's store handle is no longer referenced by
        # the engine — only one store survives per graph.
        surviving_stores = set(engine._session_stores.values())
        assert merged_store in surviving_stores
        assert not (
            store_alice in surviving_stores and store_carol in surviving_stores
        ), "merge must drop one of the two pre-merge store handles"
        merged_gid = a2.graph_id

        # --- RE-MERGE then SPLIT-via-remove_channel -----------------------
        # Add a third creature into the merged graph, wire it onto the
        # ``reunite`` channel, then remove that channel. ``reunite`` is
        # the sole bridge holding the component together, so removing it
        # auto-splits the graph back into per-creature singletons.
        await service.add_creature(
            _agent_config("dave", tmp_path), graph_id=merged_gid, creature_id="dave"
        )
        await service.wire_creature(merged_gid, "alice", "reunite", "send")
        await service.wire_creature(merged_gid, "carol", "reunite", "listen")
        await service.wire_creature(merged_gid, "dave", "reunite", "listen")
        # one connected graph, three creatures.
        graphs = await service.list_graphs()
        assert len(graphs) == 1
        assert graphs[0].creature_ids == {"alice", "carol", "dave"}
        rm_delta = await service.remove_channel(merged_gid, "reunite")
        assert rm_delta.kind == "split"
        graphs = await service.list_graphs()
        members = sorted(sorted(g.creature_ids) for g in graphs)
        assert members == [["alice"], ["carol"], ["dave"]]
        # Each post-split singleton has its own session store with the
        # merged session recorded as its parent.
        for cid in ("alice", "carol", "dave"):
            sgid = (await service.get_creature_info(cid)).graph_id
            sstore = engine._session_stores[sgid]
            assert sstore.meta["parent_session_ids"] == [merged_store.session_id]
        # No surviving graph carries the deleted channel.
        for g in graphs:
            assert "reunite" not in g.channels

        # --- DISCONNECT-driven split (the other split trigger) -----------
        # Reconnect alice + carol over a fresh channel, then disconnect
        # them — ``disconnect`` removes the only edge so the merged graph
        # fragments straight back into two singletons.
        reconn = await service.connect("alice", "carol", channel="bridge2")
        assert reconn.delta_kind == "merge"
        joined_gid = (await service.get_creature_info("alice")).graph_id
        assert (await service.get_creature_info("carol")).graph_id == joined_gid
        disc = await service.disconnect("alice", "carol", channel="bridge2")
        assert disc.delta_kind == "split"
        assert "bridge2" in disc.channels
        graphs = await service.list_graphs()
        members = sorted(sorted(g.creature_ids) for g in graphs)
        assert members == [["alice"], ["carol"], ["dave"]]

        await service.shutdown()

    async def test_recipe_run_stop_resume_journey(self, make_service, tmp_path):
        """JOURNEY 3 — full lifecycle: apply a recipe → run → attach a
        session store keyed to the recipe path → stop the whole
        terrarium → resume it into a fresh engine straight from the
        saved store → assert topology, privilege, channel wiring and
        creature run-state are all restored. Resume reconstructs the
        graph from the recipe path stored in session metadata, not from
        a frozen snapshot — so the rebuilt graph must match the recipe.
        """
        service = make_service()
        engine = service.engine
        sess_dir = tmp_path / "sessions"

        # --- run: apply the on-disk recipe --------------------------------
        recipe_path = _write_recipe(tmp_path)
        recipe = load_terrarium_config(recipe_path)
        graph = await engine.apply_recipe(recipe)
        gid = graph.graph_id
        assert graph.creature_ids == {"scout", "writer", "root"}
        for cid in ("scout", "writer", "root"):
            assert engine.get_creature(cid).is_running is True

        # --- attach a session store keyed to the recipe path -------------
        # ``config_path`` is the source of truth resume rebuilds from.
        store = SessionStore(sess_dir / f"{gid}.kohakutr")
        store.init_meta(
            session_id=gid,
            config_type="terrarium",
            config_path=str(recipe_path),
            pwd=str(tmp_path),
            agents=["scout", "writer", "root"],
            terrarium_name=recipe.name,
        )
        await engine.attach_session(gid, store)
        store.update_status("running")

        # --- chat a turn so the session has real content to resume -------
        # Drive the creature through the same ``LocalTerrariumService``
        # surface lifecycle.py uses; the default script answers "ack".
        chunks: list[str] = []
        async for chunk in service.chat("writer", "pre-stop message"):
            chunks.append(chunk)
        assert "".join(chunks) == "ack"
        pre_history = await service.chat_history("writer")
        pre_joined = " ".join(
            m.get("content", "") if isinstance(m.get("content"), str) else ""
            for m in pre_history["messages"]
        )
        assert "pre-stop message" in pre_joined
        # Stamp scratchpad state too — it is part of the session store.
        await service.patch_scratchpad("writer", {"phase": "pre-resume"})

        # --- stop the whole terrarium -------------------------------------
        await engine.shutdown()
        for cid in ("scout", "writer", "root"):
            assert engine.get_creature(cid).is_running is False
        store.close()

        # --- resume from the saved store into a FRESH engine --------------
        resumed_engine = await Terrarium.resume(
            sess_dir / f"{gid}.kohakutr", pwd=str(tmp_path)
        )
        try:
            resumed_graphs = resumed_engine.list_graphs()
            assert len(resumed_graphs) == 1
            resumed_graph = resumed_graphs[0]
            # Topology restored: same three creatures rebuilt from the
            # recipe path, every one started again.
            assert resumed_graph.creature_ids == {"scout", "writer", "root"}
            for cid in ("scout", "writer", "root"):
                assert resumed_engine.get_creature(cid).is_running is True
            # Privilege restored: the ``root:`` keyword re-promotes lead.
            root = resumed_engine.get_creature("root")
            assert root.is_privileged is True
            assert "group_add_node" in root.agent.registry.list_tools()
            assert resumed_engine.get_creature("scout").is_privileged is False
            # Channel wiring restored from the recipe.
            rgid = resumed_graph.graph_id
            assert "findings" in resumed_graph.channels
            assert "report_to_root" in resumed_graph.channels
            assert "findings" in resumed_graph.send_edges["scout"]
            assert "findings" in resumed_graph.listen_edges["writer"]
            assert "findings" in resumed_graph.listen_edges["root"]
            # The resumed graph carries a live session store.
            assert rgid in resumed_engine._session_stores

            # --- drive the resumed engine through the service surface ---
            # lifecycle.py wraps the resumed engine in a fresh service;
            # mirror that and confirm the runtime is fully operational.
            resumed_service = LocalTerrariumService(resumed_engine)
            infos = await resumed_service.list_creatures()
            assert {c.creature_id for c in infos} == {"scout", "writer", "root"}
            # The pre-stop conversation survives the round-trip — resume
            # replays it into the rebuilt agent's history/events.
            resumed_history = await resumed_service.chat_history("writer")
            assert resumed_history["creature_id"] == "writer"
            events_text = " ".join(str(ev) for ev in resumed_history.get("events", []))
            messages_text = " ".join(
                m.get("content", "") if isinstance(m.get("content"), str) else ""
                for m in resumed_history.get("messages", [])
            )
            assert "pre-stop message" in (events_text + " " + messages_text)
            # A fresh chat turn on the resumed creature still streams.
            new_chunks: list[str] = []
            async for chunk in resumed_service.chat("writer", "post-resume turn"):
                new_chunks.append(chunk)
            assert "".join(new_chunks) == "ack"
            # Topology mutation still works post-resume: hot-plug a
            # creature into the resumed graph.
            plugged = await resumed_service.add_creature(
                _agent_config("late", tmp_path),
                graph_id=rgid,
                creature_id="late",
            )
            assert plugged.graph_id == rgid
            assert resumed_engine.get_creature("late").is_running is True
            assert {c.creature_id for c in await resumed_service.list_creatures()} == {
                "scout",
                "writer",
                "root",
                "late",
            }
            # Privileged mutation still works post-resume: the root spawns
            # a worker into the resumed graph via group_add_node.
            late_yaml = tmp_path / "postresume.yaml"
            late_yaml.write_text(
                "name: postresume\nllm: test/scripted\nmodel: scripted-model\n"
                "provider: test\nsystem_prompt: You are postresume.\n"
                "tool_format: bracket\ninput:\n  type: none\n"
                "output:\n  type: stdout\n",
                encoding="utf-8",
            )
            resumed_ctx = _ctx_for(resumed_service, "root")
            spawn_res = await GroupAddNodeTool()._execute(
                {"config_path": str(late_yaml), "name": "postresume"},
                context=resumed_ctx,
            )
            assert spawn_res.error is None, spawn_res.error
            spawned_id = json.loads(spawn_res.output)["creature_id"]
            assert resumed_engine.get_creature(spawned_id).graph_id == rgid
            assert resumed_engine.get_creature(spawned_id).parent_creature_id == "root"
            # The resumed engine's status roll-up sees the whole evolved team.
            resumed_snap = await resumed_service.status_snapshot()
            assert {"scout", "writer", "root", "late", spawned_id} <= set(
                resumed_snap["creatures"]
            )

            # --- adopt_session — hot-resume a SECOND saved store into
            # the SAME running engine. Build an independent solo-creature
            # session, save it, then adopt it: the running engine now
            # hosts two graphs.
            solo_store_path = sess_dir / "solo.kohakutr"
            solo_store = SessionStore(solo_store_path)
            solo_recipe = tmp_path / "solo.yaml"
            solo_recipe.write_text(
                "name: solo\nllm: test/scripted\nmodel: scripted-model\n"
                "provider: test\nsystem_prompt: You are solo.\n"
                "tool_format: bracket\ninput:\n  type: none\n"
                "output:\n  type: stdout\n",
                encoding="utf-8",
            )
            solo_store.init_meta(
                session_id="solo",
                config_type="agent",
                config_path=str(solo_recipe),
                pwd=str(tmp_path),
                agents=["solo"],
            )
            solo_store.close()
            adopted_gid = await resumed_engine.adopt_session(
                solo_store_path, pwd=str(tmp_path)
            )
            # The running engine now hosts the original recipe graph + the
            # freshly-adopted solo graph.
            adopted_graph_ids = {g.graph_id for g in resumed_engine.list_graphs()}
            assert rgid in adopted_graph_ids
            assert adopted_gid in adopted_graph_ids
            # The adopted creature is rebuilt under a minted id; resolve it
            # by name on the adopted graph and confirm it is running.
            adopted_graph = resumed_engine.get_graph(adopted_gid)
            solo_creatures = [
                resumed_engine.get_creature(cid) for cid in adopted_graph.creature_ids
            ]
            assert len(solo_creatures) == 1
            assert solo_creatures[0].name == "solo"
            assert solo_creatures[0].is_running is True
        finally:
            await resumed_engine.shutdown()
