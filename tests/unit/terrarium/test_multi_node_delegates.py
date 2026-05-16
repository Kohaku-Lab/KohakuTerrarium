"""Cover the per-creature delegate routing in
:mod:`kohakuterrarium.terrarium.multi_node_service`.

Every delegate just calls ``_route_per_creature``; we drive each one
through a fake service and assert the underlying method was invoked
with the expected arguments.
"""

from unittest.mock import AsyncMock

import pytest

from kohakuterrarium.terrarium import multi_node_service as mns_mod
from kohakuterrarium.terrarium.multi_node_service import (
    HOST_NODE,
    MultiNodeTerrariumService,
)

from tests.unit.terrarium.test_multi_node_service import (
    _FakeService,
    _info,
    _make_service,
)

# ── Per-creature delegates — exercise each routing pass-through ──


class TestPerCreatureDelegatesLocal:
    """Routes a single creature to local; asserts delegate fired."""

    async def test_interrupt_routes(self):
        svc = _make_service(remote_specs={"w1": [_info("c1")]})
        await svc.interrupt("c1")
        assert ("interrupt", "c1") in svc._remotes["w1"].calls

    async def test_list_jobs(self):
        svc = _make_service(remote_specs={"w1": [_info("c1")]})
        out = await svc.list_jobs("c1")
        assert out == [{"id": "j1"}]

    async def test_stop_job(self):
        svc = _make_service(remote_specs={"w1": [_info("c1")]})
        out = await svc.stop_job("c1", "jid")
        assert out is True

    async def test_promote_job(self):
        svc = _make_service(remote_specs={"w1": [_info("c1")]})
        out = await svc.promote_job("c1", "jid")
        assert out is False

    async def test_chat_history(self):
        svc = _make_service(remote_specs={"w1": [_info("c1")]})
        out = await svc.chat_history("c1")
        assert out == {"messages": []}

    async def test_chat_branches(self):
        svc = _make_service(remote_specs={"w1": [_info("c1")]})
        out = await svc.chat_branches("c1")
        assert out == [{"t": 1}]

    async def test_regenerate(self):
        svc = _make_service(remote_specs={"w1": [_info("c1")]})
        out = await svc.regenerate("c1", turn_index=2)
        assert out == {"ok": True}

    async def test_edit_message(self):
        svc = _make_service(remote_specs={"w1": [_info("c1")]})
        out = await svc.edit_message("c1", 0, "new")
        assert out is True

    async def test_rewind(self):
        svc = _make_service(remote_specs={"w1": [_info("c1")]})
        await svc.rewind("c1", 1)
        assert ("rewind", "c1", 1) in svc._remotes["w1"].calls

    async def test_get_scratchpad(self):
        svc = _make_service(remote_specs={"w1": [_info("c1")]})
        out = await svc.get_scratchpad("c1")
        assert out == {"k": "v"}

    async def test_patch_scratchpad(self):
        svc = _make_service(remote_specs={"w1": [_info("c1")]})
        out = await svc.patch_scratchpad("c1", {"k": "v"})
        assert out == {"k": "v"}

    async def test_list_triggers(self):
        svc = _make_service(remote_specs={"w1": [_info("c1")]})
        out = await svc.list_triggers("c1")
        assert out == [{"id": "t1"}]

    async def test_get_env(self):
        svc = _make_service(remote_specs={"w1": [_info("c1")]})
        out = await svc.get_env("c1")
        assert out == {"X": "1"}

    async def test_get_system_prompt(self):
        svc = _make_service(remote_specs={"w1": [_info("c1")]})
        out = await svc.get_system_prompt("c1")
        assert out == {"text": "sys"}

    async def test_get_working_dir(self):
        svc = _make_service(remote_specs={"w1": [_info("c1")]})
        out = await svc.get_working_dir("c1")
        assert out == "/cwd"

    async def test_set_working_dir(self):
        svc = _make_service(remote_specs={"w1": [_info("c1")]})
        out = await svc.set_working_dir("c1", "/new")
        assert out == "/new"

    async def test_native_tool_inventory(self):
        svc = _make_service(remote_specs={"w1": [_info("c1")]})
        out = await svc.native_tool_inventory("c1")
        assert out == []

    async def test_get_native_tool_options(self):
        svc = _make_service(remote_specs={"w1": [_info("c1")]})
        out = await svc.get_native_tool_options("c1")
        assert out == {}

    async def test_set_native_tool_options(self):
        svc = _make_service(remote_specs={"w1": [_info("c1")]})
        out = await svc.set_native_tool_options("c1", "tool", {"k": "v"})
        assert out == {"k": "v"}

    async def test_switch_model(self):
        svc = _make_service(remote_specs={"w1": [_info("c1")]})
        out = await svc.switch_model("c1", "m")
        assert out == "m"

    async def test_list_plugins(self):
        svc = _make_service(remote_specs={"w1": [_info("c1")]})
        out = await svc.list_plugins("c1")
        assert out == [{"name": "p"}]

    async def test_toggle_plugin(self):
        svc = _make_service(remote_specs={"w1": [_info("c1")]})
        out = await svc.toggle_plugin("c1", "p1", False)
        assert out == {"enabled": False}


# ── Module catalog routing ──────────────────────────────────


class TestModuleCatalogRouting:
    async def test_list_modules(self):
        svc = _make_service(remote_specs={"w1": [_info("c1")]})

        # _FakeService doesn't ship list_modules; install one.
        async def _list_modules(cid):
            return [{"name": "m1"}]

        svc._remotes["w1"].list_modules = _list_modules
        out = await svc.list_modules("c1")
        assert out == [{"name": "m1"}]

    async def test_get_module_options(self):
        svc = _make_service(remote_specs={"w1": [_info("c1")]})

        async def _get(cid, mtype, name):
            return {"type": mtype, "name": name}

        svc._remotes["w1"].get_module_options = _get
        out = await svc.get_module_options("c1", "plugin", "p1")
        assert out == {"type": "plugin", "name": "p1"}

    async def test_set_module_options(self):
        svc = _make_service(remote_specs={"w1": [_info("c1")]})

        async def _set(cid, mtype, name, values):
            return values

        svc._remotes["w1"].set_module_options = _set
        out = await svc.set_module_options("c1", "plugin", "p1", {"k": "v"})
        assert out == {"k": "v"}

    async def test_toggle_module(self):
        svc = _make_service(remote_specs={"w1": [_info("c1")]})

        async def _toggle(cid, mtype, name):
            return {"enabled": True}

        svc._remotes["w1"].toggle_module = _toggle
        out = await svc.toggle_module("c1", "plugin", "p1")
        assert out == {"enabled": True}

    async def test_execute_command(self):
        svc = _make_service(remote_specs={"w1": [_info("c1")]})

        async def _exec(cid, cmd, args):
            return {"command": cmd, "success": True}

        svc._remotes["w1"].execute_command = _exec
        out = await svc.execute_command("c1", "help")
        assert out["command"] == "help"


# ── Output wiring routing ───────────────────────────────────


class TestOutputWiringRouting:
    async def test_list_output_wiring(self):
        svc = _make_service(remote_specs={"w1": [_info("c1")]})

        async def _list(cid):
            return [{"edge_id": "e1"}]

        svc._remotes["w1"].list_output_wiring = _list
        out = await svc.list_output_wiring("c1")
        assert out == [{"edge_id": "e1"}]

    async def test_wire_output(self):
        svc = _make_service(remote_specs={"w1": [_info("c1")]})

        async def _wire(cid, target):
            return {"edge_id": "new"}

        svc._remotes["w1"].wire_output = _wire
        out = await svc.wire_output("c1", "target-name")
        assert out == {"edge_id": "new"}

    async def test_unwire_output(self):
        svc = _make_service(remote_specs={"w1": [_info("c1")]})

        async def _unwire(cid, edge):
            return True

        svc._remotes["w1"].unwire_output = _unwire
        out = await svc.unwire_output("c1", "e1")
        assert out is True

    async def test_unwire_output_sink(self):
        svc = _make_service(remote_specs={"w1": [_info("c1")]})

        async def _unwire(cid, sink):
            return True

        svc._remotes["w1"].unwire_output_sink = _unwire
        out = await svc.unwire_output_sink("c1", "sink")
        assert out is True


# ── wire_creature ────────────────────────────────────────────


class TestWireCreatureRouting:
    async def test_normal_creature(self):
        svc = _make_service(remote_specs={"w1": [_info("c1")]})
        svc._remotes["w1"].wire_creature = AsyncMock()
        await svc.wire_creature("g1", "c1", "chat", "listen")
        svc._remotes["w1"].wire_creature.assert_awaited_once()


# ── attach_policies ─────────────────────────────────────────


class TestAttachPoliciesRouting:
    async def test_attach_policies(self):
        svc = _make_service(remote_specs={"w1": [_info("c1")]})

        async def _ap(cid):
            return ["log", "trace"]

        svc._remotes["w1"].attach_policies = _ap
        out = await svc.attach_policies("c1")
        assert out == ["log", "trace"]


# ── _route_per_creature retry paths (896-904) ────────────────


class TestRoutePerCreatureRetry:
    async def test_retry_to_different_node_succeeds(self):
        from kohakuterrarium.terrarium.remote_service import (
            CreatureNotHostedHere,
        )

        # ``_home`` is stale — it points at w1, but c1 actually moved to
        # w2.  The first routed call hits w1, gets ``CreatureNotHostedHere``,
        # re-resolves via a ``list_creatures`` fan-out (which finds c1 on
        # w2), and retries there.
        svc = _make_service(remote_specs={"w1": [], "w2": [_info("c1")]})
        svc._home["c1"] = "w1"

        async def _boom(cid):
            raise CreatureNotHostedHere("nope")

        svc._remotes["w1"].creature_status = _boom
        out = await svc.creature_status("c1")
        # Retry path returns the worker (w2) that genuinely hosts it.
        assert out == {"running": True}

    async def test_retry_to_same_node_raises_key_error(self):
        from kohakuterrarium.terrarium.remote_service import (
            CreatureNotHostedHere,
        )

        svc = _make_service(remote_specs={"w1": [_info("c1")]})

        async def _always_boom(cid):
            raise CreatureNotHostedHere("nope")

        svc._remotes["w1"].creature_status = _always_boom
        # Re-resolve finds the same (only) node → retry stalls → KeyError,
        # which ``creature_status`` swallows to ``None``.
        out = await svc.creature_status("c1")
        assert out is None

    async def test_retry_eventually_raises_when_no_other_home(self):
        from kohakuterrarium.terrarium.remote_service import (
            CreatureNotHostedHere,
        )

        svc = _make_service(remote_specs={"w1": [_info("c1")]})

        async def _boom(cid):
            raise CreatureNotHostedHere("nope")

        svc._remotes["w1"].creature_status = _boom
        # interrupt re-raises KeyError instead of swallowing.
        svc._remotes["w1"].interrupt = _boom
        with pytest.raises(KeyError):
            await svc.interrupt("c1")


# ── Subscribe stream failure paths (842-843, 850-851, 857-858) ──


class TestSubscribeStreamErrors:
    async def test_pump_swallows_exception(self):
        svc = _make_service(remote_specs={"w1": []})

        async def _boom(filter=None):
            raise RuntimeError("subscribe failed")
            yield  # pragma: no cover

        svc._remotes["w1"].subscribe = _boom

        # Stream completes naturally even though the worker pump raised.
        events = []
        try:
            async for ev in svc.subscribe():
                events.append(ev)
                if len(events) >= 1:
                    break
        except RuntimeError:
            pytest.fail("Stream should swallow internal pump exceptions")
        assert events == []


# ── runtime_graph_snapshot remote error path ──────────────────


class TestRuntimeGraphRemoteFailure:
    async def test_remote_snapshot_failure_swallowed(self):
        # One worker's snapshot raises; the other's still lands.
        svc = _make_service(remote_specs={"w1": []})

        async def _good_snap():
            return {"version": 1, "graphs": []}

        async def _remote_boom():
            raise RuntimeError("snap failed")

        svc._remotes["w1"].runtime_graph_snapshot = _good_snap
        bad = _FakeService(node_id="w2")
        bad.runtime_graph_snapshot = _remote_boom
        svc._remotes["w2"] = bad
        out = await svc.runtime_graph_snapshot()
        # No crash; the healthy worker's snapshot still present.
        assert "graphs" in out


# ── Constructor + property smoke ────────────────────────────


class TestConstructorAndProps:
    def test_init_with_stub_host(self, monkeypatch):
        from kohakuterrarium.terrarium.engine import Terrarium

        # Patch HostEngine so we don't actually start a transport.
        class _StubHost:
            def register_app_extension(self, ns, handler):
                pass

        class _StubDemux:
            def __init__(self, host):
                self.host = host

        monkeypatch.setattr(mns_mod, "StreamDemux", _StubDemux)
        engine = Terrarium()
        host = _StubHost()
        # The lab-host runs no agents — the engine passed in is a
        # coordination engine, not an agent runtime.
        svc = MultiNodeTerrariumService(host=host, coordination_engine=engine)
        try:
            assert svc.node_id == HOST_NODE
            # ``engine`` raises — no host agent engine — but the
            # coordination engine is reachable for the broadcast/wire
            # forwarders.
            with pytest.raises(RuntimeError, match="no host agent engine"):
                _ = svc.engine
            assert svc.coordination_engine is engine
            assert svc.demux is not None
            assert svc.host is host
            assert svc._cross_subs == {}
            assert svc._remotes == {}
        finally:
            asyncio_loop_close(engine)


def asyncio_loop_close(engine):
    """Synchronously tear down the engine without an event loop dance."""
    try:
        engine._runtime_prompt.detach()
    except Exception:
        pass
