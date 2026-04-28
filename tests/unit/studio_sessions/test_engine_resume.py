"""Coverage tests for ``terrarium/resume.py`` — engine-level resume.

The module dispatches between the agent-resume and terrarium-resume
paths based on the saved ``meta.config_type``.  We exercise both
branches plus the unknown-type error and the ``SessionStore`` /
``str`` / ``Path`` overload of the entry point.

The heavy lifting (``resume_agent`` from ``session.resume``,
``apply_recipe`` for terrarium) is mocked so we don't need real
configs on disk.
"""

from pathlib import Path

import pytest

import kohakuterrarium.terrarium.resume as eng_resume
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.terrarium.config import (
    ChannelConfig,
    CreatureConfig,
    TerrariumConfig,
)
from kohakuterrarium.terrarium.engine import Terrarium

from tests.unit.studio_sessions._fakes import _FakeAgent, make_creature

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _seed_agent_session(path: Path, agent_name: str = "alice") -> SessionStore:
    """Write a minimal agent session_store to ``path`` and close."""
    store = SessionStore(path)
    store.init_meta(
        session_id=path.stem,
        config_type="agent",
        config_path="examples/agent-apps/swe_agent",
        pwd=str(path.parent),
        agents=[agent_name],
    )
    store.close(update_status=False)
    return SessionStore(path)


def _seed_terrarium_session(
    path: Path, *, config_path: str = "terrariums/team"
) -> SessionStore:
    store = SessionStore(path)
    store.init_meta(
        session_id=path.stem,
        config_type="terrarium",
        config_path=config_path,
        pwd=str(path.parent),
        agents=["alice", "bob"],
        terrarium_name="team",
    )
    store.close(update_status=False)
    return SessionStore(path)


# ---------------------------------------------------------------------------
# _resolve_store_path
# ---------------------------------------------------------------------------


class TestResolveStorePath:
    def test_path_from_str(self):
        out = eng_resume._resolve_store_path("foo.kohakutr")
        assert out == Path("foo.kohakutr")

    def test_path_from_pathlib(self, tmp_path):
        out = eng_resume._resolve_store_path(tmp_path)
        assert out == tmp_path

    def test_path_from_session_store(self, tmp_path):
        kt = tmp_path / "x.kohakutr"
        store = _seed_agent_session(kt)
        try:
            out = eng_resume._resolve_store_path(store)
            assert Path(str(out)).resolve() == kt.resolve()
        finally:
            store.close(update_status=False)


# ---------------------------------------------------------------------------
# resume_into_engine — agent path
# ---------------------------------------------------------------------------


class TestResumeAgentPath:
    @pytest.mark.asyncio
    async def test_agent_resume_dispatches_to_agent_helper(self, tmp_path, monkeypatch):
        kt = tmp_path / "alice.kohakutr"
        store = _seed_agent_session(kt)
        store.close(update_status=False)

        # Stub session.resume.resume_agent: returns a fake (agent, store)
        fake_agent = _FakeAgent(name="alice")

        def _fake_resume_agent(
            path, *, pwd_override=None, io_mode=None, llm_override=None
        ):
            assert llm_override is None or isinstance(llm_override, str)
            return fake_agent, SessionStore(path)

        monkeypatch.setattr(eng_resume, "resume_agent", _fake_resume_agent)

        engine = Terrarium()
        try:
            graph_id = await eng_resume.resume_into_engine(
                engine, kt, llm_override="m1"
            )
            assert graph_id
            # The creature_id is name + random suffix
            names = [c.name for c in engine.list_creatures()]
            assert "alice" in names
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_agent_resume_via_session_store_arg(self, tmp_path, monkeypatch):
        kt = tmp_path / "alice.kohakutr"
        store = _seed_agent_session(kt)
        store.close(update_status=False)

        store_obj = SessionStore(kt)

        fake_agent = _FakeAgent(name="alice")
        monkeypatch.setattr(
            eng_resume,
            "resume_agent",
            lambda p, *, pwd_override=None, io_mode=None, llm_override=None: (
                fake_agent,
                store_obj,
            ),
        )

        engine = Terrarium()
        try:
            gid = await eng_resume.resume_into_engine(engine, store_obj)
            assert gid
            store_obj.close(update_status=False)
        finally:
            await engine.shutdown()


# ---------------------------------------------------------------------------
# resume_into_engine — terrarium path
# ---------------------------------------------------------------------------


def _fake_recipe_builder(cr_cfg, *, creature_id=None, pwd=None, llm_override=None):
    return make_creature(name=cr_cfg.name)


class TestResumeTerrariumPath:
    @pytest.mark.asyncio
    async def test_terrarium_resume(self, tmp_path, monkeypatch):
        kt = tmp_path / "team.kohakutr"
        store = _seed_terrarium_session(kt)
        store.close(update_status=False)

        cfg = TerrariumConfig(
            name="team",
            creatures=[
                CreatureConfig(
                    name="alice",
                    config_data={"name": "alice"},
                    base_dir=tmp_path,
                    listen_channels=["tasks"],
                    send_channels=["results"],
                ),
                CreatureConfig(
                    name="bob",
                    config_data={"name": "bob"},
                    base_dir=tmp_path,
                    listen_channels=["results"],
                    send_channels=["tasks"],
                ),
            ],
            channels=[
                ChannelConfig(name="tasks"),
                ChannelConfig(name="results"),
            ],
        )

        monkeypatch.setattr(eng_resume, "load_terrarium_config", lambda p: cfg)

        # Stub inject_saved_state — we just want to confirm it was called
        injected: list[str] = []

        def _inject(agent, store, agent_name):
            injected.append(agent_name)

        monkeypatch.setattr(eng_resume, "inject_saved_state", _inject)

        from kohakuterrarium.terrarium import recipe as _recipe

        original_apply = _recipe.apply_recipe

        async def _apply(
            engine, recipe, *, graph=None, pwd=None, creature_builder=None
        ):
            return await original_apply(
                engine,
                recipe,
                graph=graph,
                pwd=pwd,
                creature_builder=_fake_recipe_builder,
            )

        monkeypatch.setattr(_recipe, "apply_recipe", _apply)

        engine = Terrarium()
        try:
            gid = await eng_resume.resume_into_engine(engine, kt, pwd=str(tmp_path))
            assert gid
            assert set(injected) >= {"alice", "bob"}
            names = {c.name for c in engine.list_creatures()}
            assert {"alice", "bob"} <= names
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_terrarium_resume_missing_config_path_raises(
        self, tmp_path, monkeypatch
    ):
        kt = tmp_path / "team.kohakutr"
        store = _seed_terrarium_session(kt, config_path="")
        store.close(update_status=False)

        engine = Terrarium()
        try:
            with pytest.raises(ValueError, match="no config_path"):
                await eng_resume.resume_into_engine(engine, kt)
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_terrarium_resume_skips_missing_creatures(
        self, tmp_path, monkeypatch
    ):
        """When ``apply_recipe`` registers a creature_id that ``get_creature``
        can't resolve (race / cleanup), the resume loop's ``KeyError``
        continue branch swallows it.
        """
        kt = tmp_path / "team.kohakutr"
        store = _seed_terrarium_session(kt)
        store.close(update_status=False)

        cfg = TerrariumConfig(
            name="team",
            creatures=[
                CreatureConfig(
                    name="alice",
                    config_data={"name": "alice"},
                    base_dir=tmp_path,
                ),
            ],
            channels=[],
        )
        monkeypatch.setattr(eng_resume, "load_terrarium_config", lambda p: cfg)
        monkeypatch.setattr(eng_resume, "inject_saved_state", lambda a, s, n: None)

        from kohakuterrarium.terrarium import recipe as _recipe

        original_apply = _recipe.apply_recipe

        async def _apply(
            engine, recipe, *, graph=None, pwd=None, creature_builder=None
        ):
            graph = await original_apply(
                engine,
                recipe,
                graph=graph,
                pwd=pwd,
                creature_builder=_fake_recipe_builder,
            )
            # Make a single creature lookup raise to exercise the
            # ``except KeyError: continue`` branch in resume.py.
            real_get = engine.get_creature
            calls = {"first": True}

            def _flaky(cid):
                if calls["first"]:
                    calls["first"] = False
                    raise KeyError("simulated gap")
                return real_get(cid)

            monkeypatch.setattr(engine, "get_creature", _flaky)
            return graph

        monkeypatch.setattr(_recipe, "apply_recipe", _apply)

        engine = Terrarium()
        try:
            gid = await eng_resume.resume_into_engine(engine, kt, pwd=str(tmp_path))
            assert gid
        finally:
            await engine.shutdown()


# ---------------------------------------------------------------------------
# resume_into_engine — error path
# ---------------------------------------------------------------------------


class TestResumeUnknownType:
    @pytest.mark.asyncio
    async def test_unknown_type_raises(self, tmp_path, monkeypatch):
        kt = tmp_path / "x.kohakutr"

        # Bypass the real detect_session_type: make it return "weird".
        monkeypatch.setattr(eng_resume, "detect_session_type", lambda p: "weird")

        engine = Terrarium()
        try:
            with pytest.raises(ValueError, match="Unknown saved-session type"):
                await eng_resume.resume_into_engine(engine, kt)
        finally:
            await engine.shutdown()
