"""Branch-coverage tests for the ``group_*`` tool surface.

Covers the error-handling arms and metadata accessors the
engine-driven happy-path tests in ``test_group_tools_engine`` don't
reach: resolution-error propagation, exception handling around engine
calls, and the tool-schema property bodies.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from kohakuterrarium.modules.tool.base import ToolContext
from kohakuterrarium.terrarium import tools_group as tg
from kohakuterrarium.terrarium import tools_group_channel as channel_mod
from kohakuterrarium.terrarium import tools_group_lifecycle as lifecycle_mod
from kohakuterrarium.terrarium import tools_group_status as status_mod
from kohakuterrarium.terrarium import tools_group_wire as wire_mod

# ---------------------------------------------------------------------------
# tools_group._register_named — exception + executor branches
# ---------------------------------------------------------------------------


class TestRegisterNamedBranches:
    def test_register_exception_is_swallowed(self):
        """A registry whose ``register_tool`` raises does not abort the
        whole registration pass — the loop continues."""

        class _Reg:
            def __init__(self):
                self.attempts = 0

            def register_tool(self, tool):
                self.attempts += 1
                raise RuntimeError("registry full")

            def get_tool(self, name):
                return None

        class _Agent:
            registry = _Reg()
            executor = None

        agent = _Agent()
        # Must not raise; both basic tools attempted.
        tg._register_named(agent, tg.ENGINE_BASIC_TOOL_NAMES)
        assert agent.registry.attempts == len(tg.ENGINE_BASIC_TOOL_NAMES)

    def test_executor_also_receives_registered_tool(self):
        """When the agent carries an executor, the tool is mirrored
        into it too."""
        registered = []

        class _Reg:
            def register_tool(self, tool):
                pass

            def get_tool(self, name):
                return None

        class _Exec:
            def register_tool(self, tool):
                registered.append(tool.tool_name)

        class _Agent:
            registry = _Reg()
            executor = _Exec()

        tg._register_named(_Agent(), ("send_channel",))
        assert registered == ["send_channel"]

    def test_unknown_tool_name_skipped(self):
        """A name absent from the builtin catalog is silently skipped —
        the registry never sees a ``None`` tool."""

        class _Reg:
            def __init__(self):
                self.registered = []

            def register_tool(self, tool):
                self.registered.append(tool)

            def get_tool(self, name):
                return None

        class _Agent:
            registry = _Reg()
            executor = None

        agent = _Agent()
        tg._register_named(agent, ("definitely_not_a_real_tool_xyz",))
        assert agent.registry.registered == []

    def test_executor_register_failure_swallowed(self):
        """An executor that rejects the tool doesn't break registration."""

        class _Reg:
            def register_tool(self, tool):
                pass

            def get_tool(self, name):
                return None

        class _Exec:
            def register_tool(self, tool):
                raise RuntimeError("executor closed")

        class _Agent:
            registry = _Reg()
            executor = _Exec()

        # Must not raise.
        tg._register_named(_Agent(), ("send_channel",))


# ---------------------------------------------------------------------------
# group_channel — error arms
# ---------------------------------------------------------------------------


class TestGroupChannelErrorArms:
    def test_metadata(self):
        tool = channel_mod.GroupChannelTool()
        assert tool.tool_name == "group_channel"
        assert tool.description
        assert tool.execution_mode.name == "DIRECT"
        schema = tool.get_parameters_schema()
        assert "action" in schema["properties"]

    async def test_resolution_error_propagates(self):
        """No environment on the context → clean error, not a crash."""
        tool = channel_mod.GroupChannelTool()
        result = await tool._execute(
            {"action": "create", "channel": "x"},
            context=ToolContext(
                agent_name="x", session=None, working_dir=Path("."), environment=None
            ),
        )
        assert result.error
        assert "environment" in result.error

    async def test_cross_graph_wire_connect_failure(self, monkeypatch):
        """An ``engine.connect`` failure on a cross-graph wire surfaces
        as a 'cross-graph wire failed' error."""
        caller = SimpleNamespace(
            creature_id="root", name="root", graph_id="g1", is_privileged=True
        )
        target = SimpleNamespace(
            creature_id="bob", name="bob", graph_id="g2", is_privileged=False
        )
        engine = SimpleNamespace(connect=AsyncMock(side_effect=RuntimeError("boom")))
        gctx = SimpleNamespace(
            engine=engine,
            caller=caller,
            graph=SimpleNamespace(graph_id="g1", channels={}),
        )
        monkeypatch.setattr(
            channel_mod, "resolve_or_error", lambda c, **_: (gctx, None)
        )
        monkeypatch.setattr(channel_mod, "resolve_group_target", lambda g, n: target)
        result = await channel_mod.GroupChannelTool()._execute(
            {
                "action": "wire",
                "channel": "bridge",
                "creature_id": "bob",
                "direction": "listen",
            }
        )
        assert "cross-graph wire failed" in result.error

    async def test_intra_graph_wire_autocreate_failure(self, monkeypatch):
        """A failure auto-creating the channel during an intra-graph
        wire surfaces as a 'channel auto-create failed' error."""
        caller = SimpleNamespace(
            creature_id="root", name="root", graph_id="g1", is_privileged=True
        )
        target = SimpleNamespace(
            creature_id="bob", name="bob", graph_id="g1", is_privileged=False
        )
        engine = SimpleNamespace(
            add_channel=AsyncMock(side_effect=RuntimeError("disk full")),
        )
        gctx = SimpleNamespace(
            engine=engine,
            caller=caller,
            graph=SimpleNamespace(graph_id="g1", channels={}),
        )
        monkeypatch.setattr(
            channel_mod, "resolve_or_error", lambda c, **_: (gctx, None)
        )
        monkeypatch.setattr(channel_mod, "resolve_group_target", lambda g, n: target)
        result = await channel_mod.GroupChannelTool()._execute(
            {
                "action": "wire",
                "channel": "fresh",
                "creature_id": "bob",
                "direction": "listen",
            }
        )
        assert "channel auto-create failed" in result.error


# ---------------------------------------------------------------------------
# group_status — metadata + degraded-history arms
# ---------------------------------------------------------------------------


class TestGroupStatusBranches:
    def test_metadata(self):
        tool = status_mod.GroupStatusTool()
        assert tool.tool_name == "group_status"
        assert tool.description
        assert tool.execution_mode.name == "DIRECT"
        schema = tool.get_parameters_schema()
        assert "include_history" in schema["properties"]

    async def test_output_edge_lookup_failure_is_tolerated(self, monkeypatch):
        """When ``list_output_wiring`` raises for a creature, that
        creature simply contributes no output edges — no crash."""
        caller = SimpleNamespace(
            creature_id="root", name="root", graph_id="g1", is_privileged=True
        )
        graph = SimpleNamespace(
            graph_id="g1",
            creature_ids={"root"},
            channels={},
            listen_edges={},
            send_edges={},
        )
        engine = SimpleNamespace(
            _environments={},
            _creatures={"root": caller},
            list_output_wiring=lambda cid: (_ for _ in ()).throw(RuntimeError("x")),
        )
        caller.listen_channels = []
        caller.send_channels = []
        caller.status = "idle"
        caller.parent_creature_id = None
        gctx = SimpleNamespace(engine=engine, caller=caller, graph=graph)
        monkeypatch.setattr(status_mod, "resolve_or_error", lambda c, **_: (gctx, None))
        monkeypatch.setattr(status_mod, "compute_group", lambda g: {"root": caller})
        monkeypatch.setattr(status_mod, "_list_spawnable_for_caller", lambda g: [])
        result = await status_mod.GroupStatusTool()._execute(
            {"include_spawnable": False}
        )
        import json

        body = json.loads(result.output)
        assert body["output_edges"] == []


# ---------------------------------------------------------------------------
# group_status output shape — ``status`` enum replaces ``running`` bool
# ---------------------------------------------------------------------------


class TestGroupStatusOutputShape:
    """The snapshot must carry the new ``status`` field per creature
    and must NOT carry the legacy ``running`` field. The old boolean
    was structurally broken (flipped to ``False`` after one round
    even for a healthy idle worker) so callers cannot fall back to
    it without re-introducing the bug.
    """

    def _build_gctx(self, status_value: str):
        caller = SimpleNamespace(
            creature_id="root",
            name="root",
            graph_id="g1",
            is_privileged=True,
            listen_channels=[],
            send_channels=[],
            status=status_value,
            parent_creature_id=None,
        )
        graph = SimpleNamespace(
            graph_id="g1",
            creature_ids={"root"},
            channels={},
            listen_edges={},
            send_edges={},
        )
        engine = SimpleNamespace(
            _environments={},
            _creatures={"root": caller},
            list_output_wiring=lambda cid: [],
        )
        return SimpleNamespace(engine=engine, caller=caller, graph=graph), caller

    async def _run(self, monkeypatch, status_value: str):
        gctx, caller = self._build_gctx(status_value)
        monkeypatch.setattr(status_mod, "resolve_or_error", lambda c, **_: (gctx, None))
        monkeypatch.setattr(status_mod, "compute_group", lambda g: {"root": caller})
        monkeypatch.setattr(status_mod, "_list_spawnable_for_caller", lambda g: [])
        result = await status_mod.GroupStatusTool()._execute(
            {"include_spawnable": False}
        )
        import json

        return json.loads(result.output)

    async def test_emits_status_field(self, monkeypatch):
        body = await self._run(monkeypatch, "idle")
        creature = body["creatures"][0]
        assert creature["status"] == "idle"

    async def test_does_not_emit_legacy_running_field(self, monkeypatch):
        """If ``running`` came back, callers would still read the
        broken signal. The whole point of the migration is to make
        the legacy key unreachable."""
        body = await self._run(monkeypatch, "idle")
        creature = body["creatures"][0]
        assert "running" not in creature

    async def test_propagates_each_status_value(self, monkeypatch):
        """The snapshot must surface each enum value verbatim — not
        coerce to a bool, not normalise to ``alive``/``dead``."""
        for value in ("not_started", "idle", "busy", "stopped", "error"):
            body = await self._run(monkeypatch, value)
            assert body["creatures"][0]["status"] == value


# ---------------------------------------------------------------------------
# group_status prompt_contribution — team-building paradigm hint
# ---------------------------------------------------------------------------


class TestGroupStatusPromptContribution:
    """``group_status`` owns the paradigm prose for the whole
    privileged ``group_*`` surface. The aggregator inlines it into
    the ``## Tool guidance`` block once per session, only for
    creatures that actually have the tool (privileged ones)."""

    def test_contribution_is_non_empty(self):
        tool = status_mod.GroupStatusTool()
        contribution = tool.prompt_contribution()
        assert isinstance(contribution, str)
        assert contribution.strip()

    def test_bucket_is_first(self):
        """``first`` bucket guarantees this prose lands ahead of any
        future alphabetical neighbour that opts in to the same
        section — important because this hint frames the whole
        team-building workflow."""
        tool = status_mod.GroupStatusTool()
        assert tool.prompt_contribution_bucket == "first"

    def test_mentions_each_group_tool_in_workflow(self):
        """The paradigm hint must walk through the actual privileged
        toolset (status → add_node → channel → wire → send). If any
        of these names drift out of the prose, the agent loses the
        recipe."""
        tool = status_mod.GroupStatusTool()
        contribution = tool.prompt_contribution()
        for name in (
            "group_add_node",
            "group_channel",
            "group_wire",
            "group_remove_node",
            "send_channel",
            "group_send",
        ):
            assert name in contribution, f"prompt_contribution missing {name!r}"

    def test_mentions_status_enum_values(self):
        """Reading the snapshot is meaningless without knowing what
        the enum values mean — the contribution must enumerate them
        so the model can interpret ``busy`` vs ``idle`` vs ``error``."""
        contribution = status_mod.GroupStatusTool().prompt_contribution()
        for value in ("idle", "busy", "stopped", "error", "not_started"):
            assert value in contribution

    def test_mentions_team_vs_subagent_distinction(self):
        """The contribution must call out when teams beat sub-agents —
        otherwise the model defaults to sub-agent dispatch for heavy
        work where a team would be the right shape."""
        contribution = status_mod.GroupStatusTool().prompt_contribution()
        assert "sub-agent" in contribution
        assert "team" in contribution

    def test_lands_in_assembled_tool_guidance_section(self):
        """End-to-end check: when the tool is registered on an agent
        and ``build_tool_guidance_section`` runs, the contribution
        actually appears in the output (and in the ``first`` bucket,
        i.e. before any other bucket)."""
        from kohakuterrarium.core.registry import Registry
        from kohakuterrarium.prompt.tool_contributions import (
            build_tool_guidance_section,
        )

        registry = Registry()
        registry.register_tool(status_mod.GroupStatusTool())
        section = build_tool_guidance_section(registry)
        assert section
        # The contribution arrived under the ``group_status`` bullet.
        assert "group_status" in section
        # And a key phrase from it survived assembly.
        assert "team-building workflow" in section.lower()


# ---------------------------------------------------------------------------
# group_wire — metadata + error arms
# ---------------------------------------------------------------------------


class TestGroupWireBranches:
    def test_metadata(self):
        tool = wire_mod.GroupWireTool()
        assert tool.tool_name == "group_wire"
        assert tool.description
        assert tool.execution_mode.name == "DIRECT"
        assert "action" in tool.get_parameters_schema()["properties"]

    async def test_resolution_error_propagates(self):
        tool = wire_mod.GroupWireTool()
        result = await tool._execute(
            {"action": "add"},
            context=ToolContext(
                agent_name="x", session=None, working_dir=Path("."), environment=None
            ),
        )
        assert result.error
        assert "environment" in result.error

    async def test_cross_graph_merge_failure(self, monkeypatch):
        """A failed ``ensure_same_graph`` during a cross-graph wire
        surfaces as 'cross-graph merge failed'."""
        caller = SimpleNamespace(creature_id="root", name="root", graph_id="g1")
        from_c = SimpleNamespace(creature_id="root", name="root", graph_id="g1")
        to_c = SimpleNamespace(creature_id="bob", name="bob", graph_id="g2")
        gctx = SimpleNamespace(engine=SimpleNamespace(), caller=caller)
        monkeypatch.setattr(wire_mod, "resolve_or_error", lambda c, **_: (gctx, None))
        seq = iter([from_c, to_c])
        monkeypatch.setattr(wire_mod, "resolve_group_target", lambda g, n: next(seq))

        async def _boom(engine, a, b):
            raise RuntimeError("merge denied")

        monkeypatch.setattr(wire_mod._channels, "ensure_same_graph", _boom)
        result = await wire_mod.GroupWireTool()._execute({"action": "add", "to": "bob"})
        assert "cross-graph merge failed" in result.error


# ---------------------------------------------------------------------------
# group lifecycle tools — resolution-error propagation
# ---------------------------------------------------------------------------


class TestLifecycleResolutionErrors:
    async def test_remove_node_resolution_error(self):
        tool = lifecycle_mod.GroupRemoveNodeTool()
        result = await tool._execute(
            {"creature_id": "x"},
            context=ToolContext(
                agent_name="x", session=None, working_dir=Path("."), environment=None
            ),
        )
        assert result.error
        assert "environment" in result.error

    async def test_start_node_resolution_error(self):
        tool = lifecycle_mod.GroupStartNodeTool()
        result = await tool._execute(
            {"creature_id": "x"},
            context=ToolContext(
                agent_name="x", session=None, working_dir=Path("."), environment=None
            ),
        )
        assert result.error

    async def test_stop_node_resolution_error(self):
        tool = lifecycle_mod.GroupStopNodeTool()
        result = await tool._execute(
            {"creature_id": "x"},
            context=ToolContext(
                agent_name="x", session=None, working_dir=Path("."), environment=None
            ),
        )
        assert result.error
