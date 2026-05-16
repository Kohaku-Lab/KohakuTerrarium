"""Unit tests for :mod:`kohakuterrarium.bootstrap.triggers`."""

import textwrap


from kohakuterrarium.bootstrap import triggers as trig_mod
from kohakuterrarium.bootstrap.triggers import create_trigger, init_triggers
from kohakuterrarium.core.config_types import AgentConfig, TriggerConfig
from kohakuterrarium.core.loader import ModuleLoader
from kohakuterrarium.core.trigger_manager import TriggerManager
from kohakuterrarium.modules.trigger import (
    ChannelTrigger,
    ContextUpdateTrigger,
    TimerTrigger,
)

# ── create_trigger: builtin types ───────────────────────────────


class TestCreateTriggerBuiltin:
    def test_timer(self):
        cfg = TriggerConfig(type="timer", prompt="tick", options={"interval": 30.0})
        t = create_trigger(cfg, session=None, loader=None)
        # ``timer`` resolves to a TimerTrigger carrying the configured
        # interval / prompt and the default ``immediate=False``.
        assert isinstance(t, TimerTrigger)
        assert t.interval == 30.0
        assert t.prompt == "tick"
        assert t.immediate is False

    def test_timer_with_immediate(self):
        cfg = TriggerConfig(type="timer", options={"interval": 1.0, "immediate": True})
        t = create_trigger(cfg, session=None, loader=None)
        assert isinstance(t, TimerTrigger)
        assert t.interval == 1.0
        assert t.immediate is True

    def test_context(self):
        cfg = TriggerConfig(type="context", prompt="ctx", options={"debounce_ms": 200})
        t = create_trigger(cfg, session=None, loader=None)
        # ``context`` resolves to a ContextUpdateTrigger with the configured
        # debounce window and prompt.
        assert isinstance(t, ContextUpdateTrigger)
        assert t.debounce_ms == 200
        assert t.prompt == "ctx"

    def test_channel(self):
        cfg = TriggerConfig(
            type="channel",
            options={"channel": "chan1", "filter_sender": "alice"},
        )
        t = create_trigger(cfg, session=None, loader=None)
        # ``channel`` resolves to a ChannelTrigger bound to the named channel
        # with the sender filter applied.
        assert isinstance(t, ChannelTrigger)
        assert t.channel_name == "chan1"
        assert t.filter_sender == "alice"


# ── create_trigger: custom ──────────────────────────────────────


class TestCreateTriggerCustom:
    def test_missing_module(self):
        cfg = TriggerConfig(type="custom", module=None, class_name="X")
        assert create_trigger(cfg, session=None, loader=None) is None

    def test_no_loader(self):
        cfg = TriggerConfig(type="custom", module="m.py", class_name="X")
        assert create_trigger(cfg, session=None, loader=None) is None

    def test_load_failure(self, tmp_path):
        loader = ModuleLoader(agent_path=tmp_path)
        cfg = TriggerConfig(
            type="custom",
            module="nope/missing.py",
            class_name="Trig",
        )
        assert create_trigger(cfg, session=None, loader=loader) is None

    def test_load_success(self, tmp_path):
        custom = tmp_path / "custom"
        custom.mkdir()
        (custom / "trig.py").write_text(textwrap.dedent("""
                import asyncio

                from kohakuterrarium.modules.trigger.base import BaseTrigger


                class MyTrigger(BaseTrigger):
                    async def wait_for_trigger(self):
                        await asyncio.sleep(60)
                        return None
                """))
        loader = ModuleLoader(agent_path=tmp_path)
        cfg = TriggerConfig(
            type="custom",
            module="custom/trig.py",
            class_name="MyTrigger",
        )
        t = create_trigger(cfg, session=None, loader=loader)
        # The loaded instance is the custom class, not a builtin fallback.
        assert t is not None
        assert type(t).__name__ == "MyTrigger"


# ── create_trigger: bare-name fallback to package lookup ────────


class TestCreateTriggerPackageFallback:
    def test_unknown_returns_none(self, monkeypatch):
        monkeypatch.setattr(trig_mod, "resolve_package_trigger", lambda name: None)
        cfg = TriggerConfig(type="not_a_real_trigger_type")
        assert create_trigger(cfg, session=None, loader=None) is None

    def test_package_resolved_but_no_loader(self, monkeypatch):
        monkeypatch.setattr(
            trig_mod,
            "resolve_package_trigger",
            lambda name: ("some.module", "TriggerClass"),
        )
        cfg = TriggerConfig(type="some_packaged_trigger")
        assert create_trigger(cfg, session=None, loader=None) is None

    def test_package_load_failure(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            trig_mod,
            "resolve_package_trigger",
            lambda name: ("nonexistent_package_xyz", "Trig"),
        )
        loader = ModuleLoader(agent_path=tmp_path)
        cfg = TriggerConfig(type="some_packaged_trigger")
        assert create_trigger(cfg, session=None, loader=loader) is None


# ── init_triggers ───────────────────────────────────────────────


class TestInitTriggers:
    def test_registers_triggers_without_starting(self):
        async def proc(evt):
            pass

        mgr = TriggerManager(proc)
        cfg = AgentConfig(
            name="a",
            triggers=[
                TriggerConfig(type="timer", options={"interval": 60}, name="my_timer")
            ],
        )
        init_triggers(cfg, mgr, session=None, loader=None)
        assert "my_timer" in mgr._triggers

    def test_skips_failed_triggers(self):
        async def proc(evt):
            pass

        mgr = TriggerManager(proc)
        cfg = AgentConfig(
            name="a",
            triggers=[
                TriggerConfig(type="invalid_type"),
                TriggerConfig(type="timer", options={"interval": 60}),
            ],
        )
        init_triggers(cfg, mgr, session=None, loader=None)
        # Only one survived.
        assert len(mgr._triggers) == 1

    def test_auto_id_when_no_name(self):
        async def proc(evt):
            pass

        mgr = TriggerManager(proc)
        cfg = AgentConfig(
            name="a",
            triggers=[TriggerConfig(type="timer", options={"interval": 60})],
        )
        init_triggers(cfg, mgr, session=None, loader=None)
        # The auto-id is built as ``timer_builtin`` (no class_name).
        assert "timer_builtin" in mgr._triggers
