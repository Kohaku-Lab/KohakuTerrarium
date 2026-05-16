"""Unit tests for :mod:`kohakuterrarium.terrarium.resume`.

The two real branches load actual Agents from a saved store, which we
short-circuit by patching :func:`resume_agent` and
:func:`detect_session_type`. Engine + Creature integration stays real.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from kohakuterrarium.builtins.inputs.none import NoneInput
from kohakuterrarium.terrarium import resume as resume_mod
from kohakuterrarium.testing.terrarium import TestTerrariumBuilder, _FakeAgent
from kohakuterrarium.terrarium.creature_host import Creature

# ── _resolve_store_path ───────────────────────────────────────


class TestResolveStorePath:
    def test_session_store_object_uses_path(self):
        ss = SimpleNamespace(path="/some/p.kohakutr")
        out = resume_mod._resolve_store_path(ss)
        assert isinstance(out, Path)

    def test_session_store_object_fallback_to_str(self):
        class _Bare:
            def __str__(self):
                return "/fallback.kohakutr"

        out = resume_mod._resolve_store_path(_Bare())
        # No ``path`` attr → falls back to str(store).
        assert isinstance(out, Path)

    def test_string_path(self):
        out = resume_mod._resolve_store_path("/some/file.kohakutr")
        assert out == Path("/some/file.kohakutr")

    def test_path_object(self):
        out = resume_mod._resolve_store_path(Path("/some/file.kohakutr"))
        assert out == Path("/some/file.kohakutr")


# ── resume_into_engine dispatch ───────────────────────────────


class TestResumeIntoEngine:
    async def test_unknown_session_type_raises(self, monkeypatch, tmp_path):
        monkeypatch.setattr(resume_mod, "detect_session_type", lambda p: "bogus")
        t = await TestTerrariumBuilder().build()
        try:
            with pytest.raises(ValueError, match="Unknown saved-session"):
                await resume_mod.resume_into_engine(t, tmp_path / "x.kohakutr")
        finally:
            await t.shutdown()

    async def test_agent_path_dispatches(self, monkeypatch, tmp_path):
        monkeypatch.setattr(resume_mod, "detect_session_type", lambda p: "agent")

        fake_agent = _FakeAgent(name="alice")
        fake_agent.config = SimpleNamespace(name="alice")
        fake_store = SimpleNamespace()
        captured: dict = {}

        def _resume_agent(
            path,
            pwd_override=None,
            io_mode=None,
            llm_override=None,
            *,
            input_module=None,
            output_module=None,
        ):
            captured["input_module"] = input_module
            return fake_agent, fake_store

        monkeypatch.setattr(resume_mod, "resume_agent", _resume_agent)

        t = await TestTerrariumBuilder().build()
        try:
            # Stub attach_session so it doesn't need a real store.
            t.attach_session = AsyncMock()
            gid = await resume_mod.resume_into_engine(t, tmp_path / "saved.kohakutr")
            assert gid
            t.attach_session.assert_awaited()
            # Engine-hosted resume MUST suppress the config's own IO loop
            # — the creature is driven by the engine / attach WebSocket,
            # never a stdin reader. Without this a worker-side resume
            # boots ``input: cli`` with no TTY and wedges the worker.
            assert isinstance(captured["input_module"], NoneInput)
        finally:
            await t.shutdown()

    async def test_terrarium_path_dispatches(self, monkeypatch, tmp_path):
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

        fake_config = TerrariumConfig(name="t", creatures=[], channels=[])
        monkeypatch.setattr(resume_mod, "load_terrarium_config", lambda p: fake_config)

        injects = []

        def _inject(agent, store, name):
            injects.append(name)

        monkeypatch.setattr(resume_mod, "inject_saved_state", _inject)

        t = await TestTerrariumBuilder().build()
        try:
            t.attach_session = AsyncMock()
            gid = await resume_mod.resume_into_engine(t, tmp_path / "saved.kohakutr")
            assert gid
            t.attach_session.assert_awaited()
        finally:
            await t.shutdown()

    async def test_terrarium_resume_missing_config_path(self, monkeypatch, tmp_path):
        monkeypatch.setattr(resume_mod, "detect_session_type", lambda p: "terrarium")

        fake_store = SimpleNamespace(
            load_meta=lambda: {"config_path": ""},
            update_status=lambda s: None,
        )
        monkeypatch.setattr(
            resume_mod, "_open_store_with_migration", lambda p: fake_store
        )

        t = await TestTerrariumBuilder().build()
        try:
            with pytest.raises(ValueError, match="no config_path"):
                await resume_mod.resume_into_engine(t, tmp_path / "saved.kohakutr")
        finally:
            await t.shutdown()

    async def test_terrarium_resume_with_saved_agents_alignment(
        self, monkeypatch, tmp_path
    ):
        # Saved agents list is ["bob"] but the rebuild produces "alice".
        # Positional consumption should rename the rebuilt creature to "bob".
        monkeypatch.setattr(resume_mod, "detect_session_type", lambda p: "terrarium")
        fake_store = SimpleNamespace(
            load_meta=lambda: {
                "config_path": "/tmp/recipe.yaml",
                "pwd": ".",
                "agents": ["bob"],
            },
            update_status=lambda s: None,
        )
        monkeypatch.setattr(
            resume_mod, "_open_store_with_migration", lambda p: fake_store
        )

        from kohakuterrarium.terrarium.config import (
            CreatureConfig,
            TerrariumConfig,
        )

        fake_config = TerrariumConfig(
            name="t",
            creatures=[
                CreatureConfig(
                    name="alice",
                    config_data={"name": "alice"},
                    base_dir=Path("."),
                )
            ],
            channels=[],
        )
        monkeypatch.setattr(resume_mod, "load_terrarium_config", lambda p: fake_config)

        # Stub apply_recipe to build a fake creature directly.

        async def _fake_apply_recipe(config, pwd=None, **_):
            t = engine_holder["t"]
            agent = _FakeAgent(name="alice")
            agent.config = SimpleNamespace(name="alice")
            agent.attach_session_store = lambda s: None
            c = Creature(
                creature_id="alice",
                name="alice",
                agent=agent,
                config=agent.config,
            )
            await t.add_creature(c, start=False)
            return t._topology.graphs[c.graph_id]

        monkeypatch.setattr(resume_mod, "inject_saved_state", lambda *a, **kw: None)

        engine_holder = {}
        t = await TestTerrariumBuilder().build()
        engine_holder["t"] = t
        t.apply_recipe = _fake_apply_recipe
        t.attach_session = AsyncMock()
        try:
            await resume_mod.resume_into_engine(t, tmp_path / "saved.kohakutr")
            # The creature got renamed positionally to "bob".
            c = t.get_creature("alice")
            assert c.name == "bob"
        finally:
            await t.shutdown()
