"""Unit tests for :mod:`kohakuterrarium.terrarium.remote_service`.

Exercises every RPC method against a fake LabSender that captures
``(namespace, type, body)`` calls and returns scripted responses.
"""

from typing import Any

import pytest

from kohakuterrarium.core.config_types import AgentConfig
from kohakuterrarium.errors import ConflictError, InvalidRequestError
from kohakuterrarium.terrarium.creature_host import Creature
from kohakuterrarium.terrarium.events import EventFilter, EventKind
from kohakuterrarium.terrarium.remote_service import (
    TURN_REQUEST_TIMEOUT,
    CreatureNotHostedHere,
    RemoteEngineError,
    RemoteTerrariumService,
    _maybe_raise,
)
from kohakuterrarium.terrarium.service import CreatureInfo
from kohakuterrarium.terrarium.topology import (
    ChannelInfo,
    TopologyDelta,
)
from kohakuterrarium.terrarium.events import ConnectionResult, DisconnectionResult

# ── fakes ─────────────────────────────────────────────────────────


class _FakeSender:
    def __init__(self, responses=None):
        self.responses: dict[str, Any] = responses or {}
        self.calls: list[tuple[str, str, dict]] = []
        self.timeouts: dict[str, float] = {}

    async def request(self, *, to_node, namespace, type, body, timeout=None):
        self.calls.append((namespace, type, dict(body)))
        self.timeouts[type] = timeout
        if type in self.responses:
            return self.responses[type]
        return {}


class _FakeDemux:
    """RemoteStream is exercised in laboratory/test_streams.py; we don't
    need a real demux for the runtime RPC surface, just a placeholder."""


def _make_service(responses=None):
    return RemoteTerrariumService(
        sender=_FakeSender(responses),
        target_node="worker-1",
        demux=_FakeDemux(),
    )


def _packed_creature_info():
    return {
        "creature_id": "cid",
        "name": "alice",
        "graph_id": "g1",
        "is_running": True,
        "is_privileged": False,
        "parent_creature_id": None,
        "listen_channels": [],
        "send_channels": [],
    }


# ── _maybe_raise / exception types ───────────────────────────────


class TestMaybeRaise:
    def test_passthrough(self):
        assert _maybe_raise({"x": 1}) == {"x": 1}

    def test_not_found_raises_keyerror(self):
        with pytest.raises(KeyError):
            _maybe_raise({"error": {"kind": "not_found", "message": "no"}})

    def test_invalid_raises_typed_error(self):
        with pytest.raises(InvalidRequestError):
            _maybe_raise({"error": {"kind": "invalid", "message": "bad"}})

    def test_conflict_raises_typed_error(self):
        with pytest.raises(ConflictError):
            _maybe_raise({"error": {"kind": "conflict", "message": "busy"}})

    def test_creature_not_hosted_specific(self):
        with pytest.raises(CreatureNotHostedHere):
            _maybe_raise({"error": {"kind": "creature_not_hosted", "message": "wrong"}})

    def test_unknown_kind_remote_engine_error(self):
        with pytest.raises(RemoteEngineError) as exc:
            _maybe_raise({"error": {"kind": "boom", "message": "x"}})
        assert exc.value.kind == "boom"
        assert exc.value.message == "x"


class TestExceptionsHierarchy:
    def test_creature_not_hosted_is_key_error(self):
        assert issubclass(CreatureNotHostedHere, KeyError)

    def test_remote_engine_error_is_runtime_error(self):
        assert issubclass(RemoteEngineError, RuntimeError)


# ── node_id / engine property ────────────────────────────────────


class TestProperties:
    def test_node_id(self):
        svc = _make_service()
        assert svc.node_id == "worker-1"

    def test_engine_property_raises(self):
        svc = _make_service()
        with pytest.raises(NotImplementedError):
            svc.engine


# ── Read RPCs ────────────────────────────────────────────────────


class TestReadRPCs:
    async def test_list_creatures(self):
        svc = _make_service(
            {"list_creatures": {"creatures": [_packed_creature_info()]}}
        )
        out = await svc.list_creatures()
        assert len(out) == 1
        # The packed dict is unpacked into a CreatureInfo with the exact
        # field values from the wire payload.
        assert out[0] == CreatureInfo(
            creature_id="cid",
            name="alice",
            graph_id="g1",
            is_running=True,
            is_privileged=False,
            parent_creature_id=None,
            listen_channels=(),
            send_channels=(),
        )

    async def test_get_creature_info_found(self):
        svc = _make_service(
            {"get_creature_info": {"creature_info": _packed_creature_info()}}
        )
        out = await svc.get_creature_info("cid")
        # The RPC body carried the creature id and name; unpack preserves them.
        assert out.creature_id == "cid"
        assert out.name == "alice"
        # The request was routed with the queried id.
        ns, typ, body = svc._sender.calls[-1]
        assert typ == "get_creature_info"
        assert body["creature_id"] == "cid"

    async def test_get_creature_info_missing(self):
        svc = _make_service({"get_creature_info": {"creature_info": None}})
        assert await svc.get_creature_info("cid") is None

    async def test_list_graphs(self):
        svc = _make_service(
            {
                "list_graphs": {
                    "graphs": [
                        {
                            "graph_id": "g1",
                            "creature_ids": [],
                            "channels": {},
                            "listen_edges": {},
                            "send_edges": {},
                        }
                    ]
                }
            }
        )
        out = await svc.list_graphs()
        assert len(out) == 1
        # The packed graph dict is unpacked into a GraphTopology that
        # preserves the wire id and (empty) membership.
        assert out[0].graph_id == "g1"
        assert out[0].creature_ids == set()

    async def test_get_graph_found(self):
        svc = _make_service(
            {
                "get_graph": {
                    "graph": {
                        "graph_id": "g1",
                        "creature_ids": [],
                        "channels": {},
                        "listen_edges": {},
                        "send_edges": {},
                    }
                }
            }
        )
        out = await svc.get_graph("g1")
        assert out.graph_id == "g1"

    async def test_get_graph_missing(self):
        svc = _make_service({"get_graph": {"graph": None}})
        assert await svc.get_graph("g1") is None

    async def test_list_channels(self):
        svc = _make_service(
            {"list_channels": {"channels": [{"name": "ch", "description": "d"}]}}
        )
        out = await svc.list_channels("g1")
        # Unpacked ChannelInfo carries the exact name + description.
        assert out == (ChannelInfo(name="ch", description="d"),)

    async def test_creature_status(self):
        svc = _make_service({"creature_status": {"status": {"running": True}}})
        out = await svc.creature_status("cid")
        assert out == {"running": True}

    async def test_status_snapshot(self):
        svc = _make_service({"status_snapshot": {"status": {"x": 1}}})
        out = await svc.status_snapshot()
        assert out == {"x": 1}


# ── Lifecycle ────────────────────────────────────────────────────


class TestLifecycle:
    async def test_apply_recipe_targets_one_worker(self):
        graph = {
            "graph_id": "g1",
            "creature_ids": ["alice", "bob"],
            "channels": {},
            "listen_edges": {},
            "send_edges": {},
        }
        info_a = _packed_creature_info()
        info_b = {**info_a, "creature_id": "bob", "name": "bob"}
        svc = _make_service(
            {"apply_recipe": {"graph": graph, "creatures": [info_a, info_b]}}
        )

        out_graph, creatures = await svc.apply_recipe(
            "recipe://team/config.yaml", start=False, persist=True
        )

        assert out_graph.creature_ids == {"alice", "bob"}
        assert [creature.creature_id for creature in creatures] == ["cid", "bob"]
        assert svc._sender.calls[-1][1:] == (
            "apply_recipe",
            {
                "recipe_path": "recipe://team/config.yaml",
                "pwd": None,
                "llm": None,
                "strict": True,
                "start": False,
                "persist": True,
            },
        )

    async def test_discard_recipe_targets_worker_graph(self):
        svc = _make_service({"discard_recipe": {}})

        await svc.discard_recipe("g1")

        assert svc._sender.calls[-1][1:] == (
            "discard_recipe",
            {"graph_id": "g1"},
        )

    async def test_add_creature_with_agent_config(self):
        from pathlib import Path

        cfg = AgentConfig(name="alice", agent_path=Path("."))
        svc = _make_service(
            {"add_creature": {"creature_info": _packed_creature_info()}}
        )
        out = await svc.add_creature(cfg)
        assert out.name == "alice"

    async def test_add_creature_rejects_creature(self):
        svc = _make_service()
        with pytest.raises(TypeError, match="Creature"):
            await svc.add_creature(Creature(creature_id="x", name="x", agent=object()))

    async def test_add_creature_wrong_on_node(self):
        svc = _make_service()
        from pathlib import Path

        cfg = AgentConfig(name="alice", agent_path=Path("."))
        with pytest.raises(ValueError, match="mismatches"):
            await svc.add_creature(cfg, on_node="other-worker")

    async def test_add_creature_rejects_llm_instance(self):
        # Provider instances can't cross node boundaries — only a
        # selector string travels on the wire (E5).
        from pathlib import Path

        from kohakuterrarium.testing.llm import ScriptedLLM

        svc = _make_service()
        cfg = AgentConfig(name="alice", agent_path=Path("."))
        with pytest.raises(TypeError, match="selector string"):
            await svc.add_creature(cfg, llm=ScriptedLLM(["x"]))

    async def test_remove_creature_returns_none(self):
        svc = _make_service({"remove_creature": {}})
        assert await svc.remove_creature("cid") is None

    async def test_start_stop_creature(self):
        svc = _make_service({"start_creature": {}, "stop_creature": {}})
        await svc.start_creature("cid")
        await svc.stop_creature("cid")

    async def test_shutdown_is_local_noop(self):
        sender = _FakeSender()
        svc = RemoteTerrariumService(sender=sender, target_node="w", demux=_FakeDemux())
        await svc.shutdown()
        # No wire call.
        assert sender.calls == []


# ── Per-creature control ────────────────────────────────────────


class TestPerCreatureControl:
    async def test_interrupt(self):
        svc = _make_service({"interrupt": {}})
        await svc.interrupt("cid")

    async def test_list_jobs(self):
        svc = _make_service({"list_jobs": {"jobs": [{"id": "j1"}]}})
        out = await svc.list_jobs("cid")
        assert out == [{"id": "j1"}]

    async def test_stop_job(self):
        svc = _make_service({"stop_job": {"cancelled": True}})
        assert await svc.stop_job("cid", "j1") is True

    async def test_promote_job(self):
        svc = _make_service({"promote_job": {"promoted": True}})
        assert await svc.promote_job("cid", "j1") is True


# ── Chat ops ────────────────────────────────────────────────────


class TestChatOps:
    async def test_chat_history(self):
        svc = _make_service({"chat_history": {"history": {"messages": []}}})
        out = await svc.chat_history("cid")
        assert out == {"messages": []}

    async def test_chat_branches(self):
        svc = _make_service({"chat_branches": {"branches": [{"t": 1}]}})
        out = await svc.chat_branches("cid")
        assert out == [{"t": 1}]

    async def test_regenerate(self):
        result = {
            "status": "completed",
            "request_id": "regen-1",
            "turn_index": 2,
            "branch_id": 3,
            "parent_branch_path": [[1, 1]],
        }
        svc = _make_service({"regenerate": result})
        out = await svc.regenerate("cid", turn_index=2, request_id="regen-1")
        assert out == result

    async def test_edit_message(self):
        result = {
            "status": "completed",
            "request_id": "edit-1",
            "turn_index": 1,
            "branch_id": 2,
            "parent_branch_path": [],
        }
        svc = _make_service({"edit_message": result})
        assert await svc.edit_message("cid", 0, "hi", request_id="edit-1") == result

    async def test_branch_mutation_rejects_legacy_response(self):
        svc = _make_service({"edit_message": {"edited": True}})
        with pytest.raises(RemoteEngineError, match="missing"):
            await svc.edit_message("cid", 0, "hi")

    async def test_rewind(self):
        svc = _make_service({"rewind": {}})
        await svc.rewind("cid", 0)

    async def test_turn_blocking_requests_use_long_timeout(self):
        svc = _make_service(
            {
                "regenerate": {
                    "status": "completed",
                    "turn_index": 2,
                    "branch_id": 2,
                    "parent_branch_path": [],
                },
                "edit_message": {
                    "status": "completed",
                    "turn_index": 1,
                    "branch_id": 2,
                    "parent_branch_path": [],
                },
                "chat_history": {"history": {}},
            }
        )
        await svc.regenerate("cid", turn_index=2)
        await svc.edit_message("cid", 0, "hi")
        await svc.chat_history("cid")
        timeouts = svc._sender.timeouts
        assert timeouts["regenerate"] == TURN_REQUEST_TIMEOUT
        assert timeouts["edit_message"] == TURN_REQUEST_TIMEOUT
        # Control-plane reads keep the default.
        assert timeouts["chat_history"] == 30.0

    async def test_timeout_fires_best_effort_interrupt_then_reraises(self):
        # The worker's handler task keeps running past the host's
        # deadline and would commit the branch later — the timeout must
        # interrupt the creature so the orphaned mutation can't land.
        import pytest

        from kohakuterrarium.laboratory._internal.client import (
            RequestTimeoutError,
        )

        svc = _make_service()

        async def request(*, to_node, namespace, type, body, timeout=None):
            svc._sender.calls.append((namespace, type, dict(body)))
            if type in ("regenerate", "edit_message"):
                raise RequestTimeoutError("deadline expired")
            return {}

        svc._sender.request = request
        with pytest.raises(RequestTimeoutError):
            await svc.regenerate("cid", turn_index=2)
        ops = [c[1] for c in svc._sender.calls]
        assert ops == ["regenerate", "interrupt"]
        assert svc._sender.calls[1][2] == {"creature_id": "cid"}

        svc._sender.calls.clear()
        with pytest.raises(RequestTimeoutError):
            await svc.edit_message("cid", 0, "hi")
        ops = [c[1] for c in svc._sender.calls]
        assert ops == ["edit_message", "interrupt"]

    async def test_dead_link_skips_interrupt(self):
        # RequestAbortedError = the link is gone; sending an interrupt
        # over it is pointless. Re-raise untouched.
        import pytest

        from kohakuterrarium.laboratory._internal.client import (
            RequestAbortedError,
        )

        svc = _make_service()

        async def request(*, to_node, namespace, type, body, timeout=None):
            svc._sender.calls.append((namespace, type, dict(body)))
            raise RequestAbortedError("client disconnected")

        svc._sender.request = request
        with pytest.raises(RequestAbortedError):
            await svc.regenerate("cid")
        assert [c[1] for c in svc._sender.calls] == ["regenerate"]


# ── State ops ────────────────────────────────────────────────────


class TestStateOps:
    async def test_get_scratchpad(self):
        svc = _make_service({"get_scratchpad": {"scratchpad": {"k": "v"}}})
        out = await svc.get_scratchpad("cid")
        assert out == {"k": "v"}

    async def test_patch_scratchpad(self):
        svc = _make_service({"patch_scratchpad": {"scratchpad": {"k": "v2"}}})
        out = await svc.patch_scratchpad("cid", {"k": "v2"})
        assert out == {"k": "v2"}

    async def test_list_triggers(self):
        svc = _make_service({"list_triggers": {"triggers": [{"id": "t1"}]}})
        out = await svc.list_triggers("cid")
        assert out == [{"id": "t1"}]

    async def test_get_env(self):
        svc = _make_service({"get_env": {"env": {"X": "1"}}})
        out = await svc.get_env("cid")
        assert out == {"X": "1"}

    async def test_get_system_prompt(self):
        svc = _make_service({"get_system_prompt": {"text": "hello"}})
        out = await svc.get_system_prompt("cid")
        assert out == {"text": "hello"}

    async def test_get_working_dir(self):
        svc = _make_service({"get_working_dir": {"working_dir": "/cwd"}})
        out = await svc.get_working_dir("cid")
        assert out == "/cwd"

    async def test_set_working_dir(self):
        svc = _make_service({"set_working_dir": {"working_dir": "/new"}})
        out = await svc.set_working_dir("cid", "/new")
        assert out == "/new"

    async def test_native_tool_inventory(self):
        svc = _make_service({"native_tool_inventory": {"inventory": [{"x": 1}]}})
        out = await svc.native_tool_inventory("cid")
        assert out == [{"x": 1}]

    async def test_get_native_tool_options(self):
        svc = _make_service({"get_native_tool_options": {"options": {"t": {"k": 1}}}})
        out = await svc.get_native_tool_options("cid")
        assert out == {"t": {"k": 1}}

    async def test_set_native_tool_options(self):
        svc = _make_service({"set_native_tool_options": {"options": {"k": 1}}})
        out = await svc.set_native_tool_options("cid", "t", {"k": 1})
        assert out == {"k": 1}


# ── Mutation ops ────────────────────────────────────────────────


class TestMutations:
    async def test_switch_model(self):
        svc = _make_service({"switch_model": {"model": "claude"}})
        out = await svc.switch_model("cid", "claude")
        assert out == "claude"

    async def test_list_plugins(self):
        svc = _make_service({"list_plugins": {"plugins": [{"n": "p"}]}})
        out = await svc.list_plugins("cid")
        assert out == [{"n": "p"}]

    async def test_toggle_plugin(self):
        svc = _make_service({"toggle_plugin": {"ok": True}})
        out = await svc.toggle_plugin("cid", "p", True)
        assert out == {"ok": True}


# ── Module catalog + slash commands ─────────────────────────────


class TestModulesAndCommands:
    async def test_list_modules(self):
        svc = _make_service({"list_modules": {"modules": [{"n": "m"}]}})
        out = await svc.list_modules("cid")
        assert out == [{"n": "m"}]

    async def test_get_module_options(self):
        svc = _make_service({"get_module_options": {"ok": True}})
        out = await svc.get_module_options("cid", "plugin", "n")
        assert out == {"ok": True}

    async def test_set_module_options(self):
        svc = _make_service({"set_module_options": {"ok": True}})
        out = await svc.set_module_options("cid", "plugin", "n", {"k": 1})
        assert out == {"ok": True}

    async def test_toggle_module(self):
        svc = _make_service({"toggle_module": {"toggled": True}})
        out = await svc.toggle_module("cid", "plugin", "n")
        assert out == {"toggled": True}

    async def test_command_inventory_and_skill_input(self):
        svc = _make_service(
            {
                "command_inventory": {"commands": [], "skills": []},
                "invoke_skill": {"accepted": True},
            }
        )

        inventory = await svc.command_inventory("cid")
        skill = await svc.invoke_skill("cid", "review", "diff")

        assert inventory == {"commands": [], "skills": []}
        assert skill == {"accepted": True}

    async def test_execute_command(self):
        svc = _make_service({"execute_command": {"ok": True}})
        out = await svc.execute_command("cid", "status")
        assert out == {"ok": True}


# ── Wiring ──────────────────────────────────────────────────────


class TestWiringRPCs:
    async def test_list_output_wiring(self):
        svc = _make_service({"list_output_wiring": {"edges": [{"id": "e"}]}})
        out = await svc.list_output_wiring("cid")
        assert out == [{"id": "e"}]

    async def test_wire_output(self):
        svc = _make_service({"wire_output": {"edge_id": "e1"}})
        out = await svc.wire_output("cid", "target")
        assert out == {"edge_id": "e1"}

    async def test_unwire_output(self):
        svc = _make_service({"unwire_output": {"unwired": True}})
        assert await svc.unwire_output("cid", "e1") is True

    async def test_wire_creature(self):
        svc = _make_service({"wire_creature": {}})
        await svc.wire_creature("g", "cid", "ch", "listen")

    async def test_unwire_output_sink(self):
        svc = _make_service({"unwire_output_sink": {"unwired": True}})
        assert await svc.unwire_output_sink("cid", "s1") is True


# ── Attach policies + runtime graph ──────────────────────────────


class TestAttachAndGraph:
    async def test_attach_policies(self):
        svc = _make_service({"attach_policies": {"policies": ["read"]}})
        out = await svc.attach_policies("cid")
        assert out == ["read"]

    async def test_session_attach_policies(self):
        svc = _make_service({"session_attach_policies": {"policies": ["x"]}})
        out = await svc.session_attach_policies("sess")
        assert out == ["x"]

    async def test_runtime_graph_snapshot(self):
        svc = _make_service({"runtime_graph_snapshot": {"snapshot": {"graphs": []}}})
        out = await svc.runtime_graph_snapshot()
        assert out == {"graphs": []}

    async def test_runtime_graph_snapshot_default(self):
        svc = _make_service({"runtime_graph_snapshot": {}})
        out = await svc.runtime_graph_snapshot()
        assert out == {"graphs": [], "version": 0}


# ── Channels (server-side topology) ─────────────────────────────


class TestChannels:
    async def test_add_channel(self):
        svc = _make_service(
            {"add_channel": {"channel": {"name": "ch", "description": "d"}}}
        )
        out = await svc.add_channel("g", "ch", "d")
        assert out == ChannelInfo(name="ch", description="d")
        # The RPC forwarded graph/name/description verbatim.
        _, typ, body = svc._sender.calls[-1]
        assert typ == "add_channel"
        assert body == {"graph_id": "g", "name": "ch", "description": "d"}

    async def test_remove_channel(self):
        svc = _make_service(
            {
                "remove_channel": {
                    "delta": {
                        "kind": "nothing",
                        "old_graph_ids": ["g"],
                        "new_graph_ids": ["g"],
                        "affected_creatures": [],
                    }
                }
            }
        )
        out = await svc.remove_channel("g", "ch")
        # The packed delta is unpacked field-for-field.
        assert out == TopologyDelta(
            kind="nothing",
            old_graph_ids=["g"],
            new_graph_ids=["g"],
            affected_creatures=set(),
        )

    async def test_connect(self):
        svc = _make_service(
            {
                "connect": {
                    "result": {
                        "channel": "ch",
                        "trigger_id": "",
                        "delta_kind": "nothing",
                        "graph_id": "g",
                    }
                }
            }
        )
        out = await svc.connect("a", "b", channel="ch")
        # The packed result is unpacked field-for-field.
        assert out == ConnectionResult(
            channel="ch", trigger_id="", delta_kind="nothing", graph_id="g"
        )
        # The RPC carried the sender/receiver/channel.
        _, typ, body = svc._sender.calls[-1]
        assert typ == "connect"
        assert body["sender_id"] == "a" and body["receiver_id"] == "b"
        assert body["channel"] == "ch"

    async def test_disconnect(self):
        svc = _make_service(
            {"disconnect": {"result": {"channels": ["ch"], "delta_kind": "nothing"}}}
        )
        out = await svc.disconnect("a", "b", channel="ch")
        assert out == DisconnectionResult(channels=["ch"], delta_kind="nothing")


# ── Interaction ─────────────────────────────────────────────────


class TestInjectInput:
    async def test_inject_input(self):
        svc = _make_service({"inject_input": {}})
        await svc.inject_input("cid", "hello")

    async def test_inject_input_multimodal(self):
        svc = _make_service({"inject_input": {}})
        await svc.inject_input("cid", [{"type": "text", "text": "hi"}])


# ── chat / subscribe — streaming-shape contract ────────────────
# Full iteration needs a real StreamDemux (covered in
# laboratory/test_streams.py). At this unit level the observable
# contract is that these are *async-iterables*, not coroutines —
# callers must ``async for`` them, never ``await`` them.


class TestStreamingHelpers:
    def test_chat_returns_async_iterable_not_coroutine(self):
        import inspect

        svc = _make_service()
        gen = svc.chat("cid", "hi")
        assert hasattr(gen, "__aiter__")
        assert not inspect.iscoroutine(gen)

    def test_subscribe_returns_async_iterable_not_coroutine(self):
        import inspect

        svc = _make_service()
        gen = svc.subscribe(EventFilter(kinds={EventKind.CHANNEL_MESSAGE}))
        assert hasattr(gen, "__aiter__")
        assert not inspect.iscoroutine(gen)
