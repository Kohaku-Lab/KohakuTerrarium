from pathlib import Path

import pytest

from kohakuterrarium.core.config import AgentConfig
from kohakuterrarium.core.config_serde import pack_agent_config
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.terrarium import resume as resume_mod
from kohakuterrarium.terrarium.graph_manifest import MANIFEST_KEY
from kohakuterrarium.testing.terrarium import TestTerrariumBuilder, _FakeAgent


def _manifest():
    config = pack_agent_config(AgentConfig(name="alice"))
    return {
        "kind": "kohakuterrarium.live_graph",
        "version": 1,
        "revision": 2,
        "graph_id": "graph_saved",
        "creatures": [
            {
                "creature_id": "alice_id",
                "name": "alice",
                "config_snapshot": config,
                "source_ref": "@pack/alice",
                "pwd": ".",
                "is_privileged": False,
                "parent_creature_id": None,
            }
        ],
        "channels": [{"name": "tasks", "description": "Work"}],
        "listen": [["alice_id", "tasks"]],
        "send": [["alice_id", "tasks"]],
    }


class TestManifestResume:
    async def test_manifest_path_bypasses_legacy_and_restores_exact_topology(
        self, monkeypatch, tmp_path
    ):
        path = Path(tmp_path / "saved.kohakutr")
        store = SessionStore(path, writer_lock=True)
        store.init_meta("saved", "agent", "", ".", ["alice"])
        store.meta[MANIFEST_KEY] = _manifest()
        store.flush()
        store.close(update_status=False)

        monkeypatch.setattr(
            resume_mod,
            "detect_session_type",
            lambda _path: pytest.fail("legacy dispatch must not run"),
        )

        async def _add_creature(self, config, **kwargs):
            creature = self._creature_for_restore(config, kwargs)
            return await self.add_creature(creature, start=False, graph=kwargs["graph"])

        engine = await TestTerrariumBuilder().build()
        original_add = engine.add_creature

        def _creature_for_restore(config, kwargs):
            from kohakuterrarium.terrarium.creature_host import Creature

            agent = _FakeAgent(name=kwargs["name"])
            agent.config = config
            agent.attach_session_store = lambda _store: None
            return Creature(
                creature_id=kwargs["creature_id"],
                name=kwargs["name"],
                agent=agent,
                config=config,
                config_snapshot=pack_agent_config(config),
                source_ref="@pack/alice",
                build_pwd=".",
            )

        engine._creature_for_restore = _creature_for_restore

        async def _wrapped_add(config, **kwargs):
            if not isinstance(config, AgentConfig):
                return await original_add(config, **kwargs)
            creature = engine._creature_for_restore(config, kwargs)
            return await original_add(creature, start=False, graph=kwargs["graph"])

        engine.add_creature = _wrapped_add
        try:
            graph_id = await resume_mod.resume_into_engine(engine, path)
            assert graph_id == "graph_saved"
            graph = engine.get_graph(graph_id)
            assert graph.creature_ids == {"alice_id"}
            assert set(graph.channels) == {"tasks"}
            assert graph.listen_edges["alice_id"] == {"tasks"}
            assert graph.send_edges["alice_id"] == {"tasks"}
        finally:
            await engine.shutdown()
