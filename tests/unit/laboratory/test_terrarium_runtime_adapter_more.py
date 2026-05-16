"""Mutation / wiring / module-catalog / identity dispatch tests for
:class:`TerrariumRuntimeAdapter`.

Companion to `test_terrarium_runtime_adapter.py` (topology / lifecycle /
channel ops); split out to keep each file under the 600-line cap. Same
harness: a real :class:`Terrarium` engine via `TestTerrariumBuilder`,
a fake `LabRegistrar`, and `creature.agent` stubbed per-op where the
test-builder's `_FakeAgent` lacks the production agent surface.
"""

from types import SimpleNamespace


from kohakuterrarium.laboratory._internal.app import AppMessage
from kohakuterrarium.laboratory.adapters.terrarium_runtime import (
    TerrariumRuntimeAdapter,
)
from kohakuterrarium.modules.plugin.base import BasePlugin
from kohakuterrarium.modules.plugin.manager import PluginManager
from kohakuterrarium.testing.terrarium import TestTerrariumBuilder


class _NamedPlugin(BasePlugin):
    """Minimal real ``BasePlugin`` — just a name — for registering into
    a real ``PluginManager`` so the adapter's ``toggle_plugin`` dispatch
    hits the genuine enable/disable surface, not an invented setter."""

    def __init__(self, name: str) -> None:
        super().__init__()
        self.name = name


class _FakeNode:
    def __init__(self, client_id=None):
        self.client_id = client_id
        self.registered = {}
        self.unregistered = []

    def register_app_extension(self, ns, handler):
        self.registered[ns] = handler

    def unregister_app_extension(self, ns):
        self.unregistered.append(ns)
        self.registered.pop(ns, None)


def _msg(type_, body=None, sender="ctrl") -> AppMessage:
    return AppMessage(
        namespace=TerrariumRuntimeAdapter.NAMESPACE,
        type=type_,
        body=body or {},
        sender_node=sender,
        request_id=None,
        in_reply_to=None,
    )


async def _make_adapter():
    engine = await (
        TestTerrariumBuilder()
        .with_creature("alice", responses=["hi"])
        .with_creature("bob")
        .with_channel("chat")
        .with_connection("alice", "bob", channel="chat")
        .build()
    )
    return TerrariumRuntimeAdapter(engine, _FakeNode())


# ── connect / disconnect ────────────────────────────────────────
# ── per-creature mutation ops ───────────────────────────────────


class TestMutationOps:
    async def test_switch_model_uses_agent_setter_when_present(self):
        adapter = await _make_adapter()
        try:
            creature = adapter._engine.get_creature("alice")
            switched = []
            creature.agent = SimpleNamespace(
                is_running=False,
                switch_model=lambda m: switched.append(m),
                config=SimpleNamespace(model="old"),
            )
            out = await adapter._dispatch(
                _msg("switch_model", {"creature_id": "alice", "model": "new"})
            )
            assert out == {"model": "new"}
            # Setter path is taken — config is NOT mutated directly.
            assert switched == ["new"]
            assert creature.agent.config.model == "old"
        finally:
            await adapter._engine.shutdown()

    async def test_switch_model_falls_back_to_config_assignment(self):
        adapter = await _make_adapter()
        try:
            creature = adapter._engine.get_creature("alice")
            creature.agent = SimpleNamespace(
                is_running=False,
                switch_model=None,
                config=SimpleNamespace(model="old"),
            )
            out = await adapter._dispatch(
                _msg("switch_model", {"creature_id": "alice", "model": "new"})
            )
            assert out == {"model": "new"}
            # No callable setter → the op writes config.model directly.
            assert creature.agent.config.model == "new"
        finally:
            await adapter._engine.shutdown()

    async def test_list_plugins_returns_plugin_list(self):
        adapter = await _make_adapter()
        try:
            out = await adapter._dispatch(
                _msg("list_plugins", {"creature_id": "alice"})
            )
            assert isinstance(out["plugins"], list)
        finally:
            await adapter._engine.shutdown()

    async def test_toggle_plugin_disables_a_real_plugin(self):
        """Regression guard for B-e2e-2: the ``toggle_plugin`` dispatch
        drives the creature's real ``PluginManager`` — ``enabled=False``
        actually disables ``budget``, observable via ``is_enabled``. The
        old dispatch reached for a non-existent ``set_plugin_enabled``
        and fabricated the response."""
        adapter = await _make_adapter()
        try:
            creature = adapter._engine.get_creature("alice")
            pm = PluginManager()
            pm.register(_NamedPlugin("budget"))
            creature.agent = SimpleNamespace(is_running=False, plugins=pm)
            out = await adapter._dispatch(
                _msg(
                    "toggle_plugin",
                    {
                        "creature_id": "alice",
                        "plugin_name": "budget",
                        "enabled": False,
                    },
                )
            )
            assert out == {"plugin": "budget", "enabled": False}
            # The real manager actually flipped — not a fabricated reply.
            assert pm.is_enabled("budget") is False
        finally:
            await adapter._engine.shutdown()

    async def test_toggle_plugin_defaults_to_enabled_true(self):
        """Regression guard for B-e2e-2: with ``enabled`` omitted the
        dispatch enables the (real, currently-disabled) plugin."""
        adapter = await _make_adapter()
        try:
            creature = adapter._engine.get_creature("alice")
            pm = PluginManager()
            pm.register(_NamedPlugin("sandbox"))
            pm.disable("sandbox")
            creature.agent = SimpleNamespace(is_running=False, plugins=pm)
            out = await adapter._dispatch(
                _msg(
                    "toggle_plugin",
                    {"creature_id": "alice", "plugin_name": "sandbox"},
                )
            )
            assert out == {"plugin": "sandbox", "enabled": True}
            assert pm.is_enabled("sandbox") is True
        finally:
            await adapter._engine.shutdown()

    async def test_toggle_plugin_unknown_is_not_found(self):
        """Regression guard for B-e2e-2: toggling a plugin name the
        creature doesn't have surfaces as a ``not_found`` error, never a
        fabricated success — ``agent_toggle_plugin`` raises ``KeyError``,
        which ``_dispatch`` maps to ``{"error": {"kind": "not_found"}}``.
        """
        adapter = await _make_adapter()
        try:
            creature = adapter._engine.get_creature("alice")
            pm = PluginManager()
            pm.register(_NamedPlugin("budget"))
            creature.agent = SimpleNamespace(is_running=False, plugins=pm)
            out = await adapter._dispatch(
                _msg(
                    "toggle_plugin",
                    {"creature_id": "alice", "plugin_name": "ghost"},
                )
            )
            assert out["error"]["kind"] == "not_found"
        finally:
            await adapter._engine.shutdown()


# ── per-creature wiring ─────────────────────────────────────────


class TestWiringOps:
    async def test_wire_output_then_list_then_unwire(self):
        adapter = await _make_adapter()
        try:
            wired = await adapter._dispatch(
                _msg("wire_output", {"creature_id": "alice", "target": "bob"})
            )
            edge_id = wired["edge_id"]
            assert edge_id
            listed = await adapter._dispatch(
                _msg("list_output_wiring", {"creature_id": "alice"})
            )
            assert any(e["id"] == edge_id for e in listed["edges"])
            unwired = await adapter._dispatch(
                _msg(
                    "unwire_output",
                    {"creature_id": "alice", "edge_id": edge_id},
                )
            )
            assert unwired == {"unwired": True}
        finally:
            await adapter._engine.shutdown()

    async def test_unwire_output_sink_reports_outcome(self):
        adapter = await _make_adapter()
        try:
            creature = adapter._engine.get_creature("alice")
            # Attach a real secondary sink, then unwire it by its id.
            sink = SimpleNamespace()
            removed = []
            creature.agent = SimpleNamespace(
                is_running=False,
                output_router=SimpleNamespace(
                    _secondary_outputs=[sink],
                    remove_secondary=removed.append,
                ),
            )
            out = await adapter._dispatch(
                _msg(
                    "unwire_output_sink",
                    {"creature_id": "alice", "sink_id": f"sink_{id(sink):x}"},
                )
            )
            assert out == {"unwired": True}
            assert removed == [sink]
            # An unknown sink id reports a clean miss, not an error.
            miss = await adapter._dispatch(
                _msg(
                    "unwire_output_sink",
                    {"creature_id": "alice", "sink_id": "sink_deadbeef"},
                )
            )
            assert miss == {"unwired": False}
        finally:
            await adapter._engine.shutdown()

    async def test_list_output_wiring_swallows_engine_error(self):
        adapter = await _make_adapter()
        try:
            creature = adapter._engine.get_creature("alice")
            # The op pre-checks _require_hosted (creature exists) then
            # calls engine.list_output_wiring; if that raises, the op
            # degrades to an empty edge list rather than erroring out.
            original = adapter._engine.list_output_wiring

            def _boom(cid):
                raise RuntimeError("wiring index unavailable")

            adapter._engine.list_output_wiring = _boom
            try:
                out = await adapter._dispatch(
                    _msg("list_output_wiring", {"creature_id": "alice"})
                )
            finally:
                adapter._engine.list_output_wiring = original
            assert out == {"edges": []}
            assert creature is not None
        finally:
            await adapter._engine.shutdown()

    async def test_wire_creature_unknown_creature_is_not_found(self):
        adapter = await _make_adapter()
        try:
            lg = await adapter._dispatch(_msg("list_graphs"))
            gid = lg["graphs"][0]["graph_id"]
            # wire_creature has no _require_hosted pre-check — a missing
            # creature surfaces as a plain ``not_found`` (KeyError), NOT
            # as creature_not_hosted.
            out = await adapter._dispatch(
                _msg(
                    "wire_creature",
                    {
                        "graph_id": gid,
                        "creature_id": "ghost",
                        "channel": "chat",
                        "direction": "send",
                    },
                )
            )
            assert out["error"]["kind"] == "not_found"
        finally:
            await adapter._engine.shutdown()

    async def test_wire_creature_toggles_existing_creature(self):
        adapter = await _make_adapter()
        try:
            lg = await adapter._dispatch(_msg("list_graphs"))
            gid = lg["graphs"][0]["graph_id"]
            out = await adapter._dispatch(
                _msg(
                    "wire_creature",
                    {
                        "graph_id": gid,
                        "creature_id": "alice",
                        "channel": "chat",
                        "direction": "listen",
                        "enabled": True,
                    },
                )
            )
            # Success returns an empty body.
            assert out == {}
        finally:
            await adapter._engine.shutdown()


# ── attach policies / runtime graph ─────────────────────────────


class TestAttachAndSnapshot:
    async def test_attach_policies_lists_policy_names(self):
        adapter = await _make_adapter()
        try:
            out = await adapter._dispatch(
                _msg("attach_policies", {"creature_id": "alice"})
            )
            assert isinstance(out["policies"], list)
        finally:
            await adapter._engine.shutdown()

    async def test_session_attach_policies_lists_policy_names(self):
        adapter = await _make_adapter()
        try:
            out = await adapter._dispatch(
                _msg("session_attach_policies", {"session_id": "_"})
            )
            assert isinstance(out["policies"], list)
        finally:
            await adapter._engine.shutdown()

    async def test_runtime_graph_snapshot_stamps_node_id(self):
        adapter = await _make_adapter()
        try:
            out = await adapter._dispatch(_msg("runtime_graph_snapshot"))
            snap = out["snapshot"]
            # Every graph in the snapshot is stamped with this node's id
            # so the controller can route follow-up ops back here.
            for g in snap.get("graphs", []):
                assert g["node_id"] == "_host"
        finally:
            await adapter._engine.shutdown()


# ── module catalog + slash commands ─────────────────────────────


class TestModuleCatalogOps:
    async def test_list_modules_returns_module_list(self):
        adapter = await _make_adapter()
        try:
            out = await adapter._dispatch(
                _msg("list_modules", {"creature_id": "alice"})
            )
            assert isinstance(out["modules"], list)
        finally:
            await adapter._engine.shutdown()

    async def test_get_module_options_forwards_module_coords(self):
        adapter = await _make_adapter()
        try:
            creature = adapter._engine.get_creature("alice")
            seen = {}
            creature.agent = SimpleNamespace(is_running=False)
            import kohakuterrarium.laboratory.adapters.terrarium_runtime as mod

            orig = mod.agent_get_module_options
            mod.agent_get_module_options = lambda ag, mt, mn: seen.update(
                {"type": mt, "name": mn}
            ) or {"options": {}}
            try:
                out = await adapter._dispatch(
                    _msg(
                        "get_module_options",
                        {
                            "creature_id": "alice",
                            "module_type": "trigger",
                            "module_name": "timer",
                        },
                    )
                )
            finally:
                mod.agent_get_module_options = orig
            assert out == {"options": {}}
            assert seen == {"type": "trigger", "name": "timer"}
        finally:
            await adapter._engine.shutdown()

    async def test_set_module_options_forwards_values(self):
        adapter = await _make_adapter()
        try:
            creature = adapter._engine.get_creature("alice")
            seen = {}
            creature.agent = SimpleNamespace(is_running=False)
            import kohakuterrarium.laboratory.adapters.terrarium_runtime as mod

            orig = mod.agent_set_module_options
            mod.agent_set_module_options = lambda ag, mt, mn, v: seen.update(
                {"type": mt, "name": mn, "values": v}
            ) or {"ok": True}
            try:
                out = await adapter._dispatch(
                    _msg(
                        "set_module_options",
                        {
                            "creature_id": "alice",
                            "module_type": "trigger",
                            "module_name": "timer",
                            "values": {"interval": 10},
                        },
                    )
                )
            finally:
                mod.agent_set_module_options = orig
            assert out == {"ok": True}
            assert seen == {
                "type": "trigger",
                "name": "timer",
                "values": {"interval": 10},
            }
        finally:
            await adapter._engine.shutdown()

    async def test_toggle_module_awaits_async_helper(self):
        adapter = await _make_adapter()
        try:
            creature = adapter._engine.get_creature("alice")
            creature.agent = SimpleNamespace(is_running=False)
            import kohakuterrarium.laboratory.adapters.terrarium_runtime as mod

            async def _toggle(ag, mt, mn):
                return {"enabled": False, "module": mn}

            orig = mod.agent_toggle_module
            mod.agent_toggle_module = _toggle
            try:
                out = await adapter._dispatch(
                    _msg(
                        "toggle_module",
                        {
                            "creature_id": "alice",
                            "module_type": "trigger",
                            "module_name": "timer",
                        },
                    )
                )
            finally:
                mod.agent_toggle_module = orig
            assert out == {"enabled": False, "module": "timer"}
        finally:
            await adapter._engine.shutdown()

    async def test_execute_command_normalizes_args_and_awaits(self):
        adapter = await _make_adapter()
        try:
            creature = adapter._engine.get_creature("alice")
            creature.agent = SimpleNamespace(is_running=False)
            import kohakuterrarium.laboratory.adapters.terrarium_runtime as mod

            captured = {}

            async def _exec(ag, command, args):
                captured["command"] = command
                captured["args"] = args
                return {"ran": command}

            orig = mod.agent_execute_command
            mod.agent_execute_command = _exec
            try:
                out = await adapter._dispatch(
                    _msg(
                        "execute_command",
                        {
                            "creature_id": "alice",
                            "command": "status",
                            "args": "verbose",
                        },
                    )
                )
            finally:
                mod.agent_execute_command = orig
            assert out == {"ran": "status"}
            # The string arg survives _normalize_command_args unchanged.
            assert captured == {"command": "status", "args": "verbose"}
        finally:
            await adapter._engine.shutdown()


# ── identity pre-warm ───────────────────────────────────────────


class _RecordingIdentityCache:
    """Records which providers/profiles get pre-warmed."""

    def __init__(self, profile=None):
        self._profile = profile
        self.providers: list[str] = []

    async def get_profile(self, name):
        return self._profile

    async def prefetch_for_provider(self, provider):
        self.providers.append(provider)


class TestPrewarmIdentity:
    async def test_prewarm_resolves_provider_from_profile(self):
        engine = await TestTerrariumBuilder().build()
        cache = _RecordingIdentityCache(profile={"provider": "openai"})
        adapter = TerrariumRuntimeAdapter(engine, _FakeNode(), identity_cache=cache)
        try:
            config = SimpleNamespace(llm_profile="my-profile", provider="", model="")
            await adapter._prewarm_identity(config)
            # The profile's provider is pre-warmed.
            assert "openai" in cache.providers
        finally:
            await engine.shutdown()

    async def test_prewarm_falls_back_to_model_prefix(self):
        engine = await TestTerrariumBuilder().build()
        cache = _RecordingIdentityCache()
        adapter = TerrariumRuntimeAdapter(engine, _FakeNode(), identity_cache=cache)
        try:
            # No explicit provider; provider derived from "openai/gpt-4o".
            config = SimpleNamespace(llm_profile="", provider="", model="openai/gpt-4o")
            await adapter._prewarm_identity(config)
            assert cache.providers == ["openai"]
        finally:
            await engine.shutdown()

    async def test_prewarm_noop_without_cache(self):
        adapter = await _make_adapter()
        try:
            # No identity cache wired → _prewarm_identity returns silently.
            config = SimpleNamespace(llm_profile="x", provider="y", model="z/m")
            await adapter._prewarm_identity(config)
        finally:
            await adapter._engine.shutdown()
