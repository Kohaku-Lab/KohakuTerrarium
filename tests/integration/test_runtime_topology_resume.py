"""Runtime-topology persistence: does ``service.add_channel`` /
``service.connect`` *after* the initial recipe survive a resume?

Reproduces the user-asked question: "if I manually connect two
creatures at runtime, does the connection persist?"

Pre-fix expectation: NO. The recipe-described topology comes back,
but every runtime mutation is lost — they never reach the session
store.

After the fix lands: the runtime-added channel + wiring should
survive resume.
"""

from pathlib import Path

import pytest
import yaml

from kohakuterrarium.bootstrap import agent_init as _agent_init
from kohakuterrarium.bootstrap import llm as _bootstrap_llm
from kohakuterrarium.terrarium.config import load_terrarium_config
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.terrarium.service import LocalTerrariumService
from kohakuterrarium.testing.llm import ScriptedLLM

pytestmark = pytest.mark.timeout(30)


@pytest.fixture
def patched_llm(monkeypatch):
    def _fake_create(config, llm_override=None):
        return ScriptedLLM(["ack"])

    monkeypatch.setattr(_bootstrap_llm, "create_llm_provider", _fake_create)
    monkeypatch.setattr(_agent_init, "create_llm_provider", _fake_create)


def _write_creature_dir(root: Path, name: str) -> Path:
    cdir = root / f"creature_{name}"
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "agent.yaml").write_text(
        yaml.safe_dump(
            {
                "name": name,
                "system_prompt": f"You are {name}.",
                "llm_profile": "test/scripted",
                "model": "scripted-model",
                "provider": "test",
                "input": {"type": "none"},
                "output": {"type": "stdout"},
                "tool_format": "bracket",
                "include_tools_in_prompt": False,
                "include_hints_in_prompt": False,
            }
        ),
        encoding="utf-8",
    )
    return cdir


def _write_recipe(root: Path) -> Path:
    """Write a recipe with 2 creatures and NO channels in the recipe —
    the test adds the channel at runtime via ``service.add_channel``."""
    _write_creature_dir(root, "alice")
    _write_creature_dir(root, "bob")
    rdir = root / "duo"
    rdir.mkdir(parents=True, exist_ok=True)
    rpath = rdir / "terrarium.yaml"
    rpath.write_text(
        yaml.safe_dump(
            {
                "name": "duo",
                "creatures": [
                    {"name": "alice", "base_config": str(root / "creature_alice")},
                    {"name": "bob", "base_config": str(root / "creature_bob")},
                ],
                "channels": {},
            }
        ),
        encoding="utf-8",
    )
    return rpath


class TestRuntimeTopologyResume:
    """One workflow: apply recipe → runtime add_channel + connect →
    close → cold-reopen + adopt_session → verify channel + wiring."""

    async def test_runtime_added_channel_and_wiring_survive_resume(
        self, patched_llm, tmp_path
    ):
        recipe_path = _write_recipe(tmp_path)
        recipe = load_terrarium_config(str(recipe_path))
        sess_dir = tmp_path / "sessions"
        sess_dir.mkdir()

        # --- phase 1: apply recipe, runtime-mutate, save, shutdown -------
        from kohakuterrarium.session.store import SessionStore

        engine1 = Terrarium(pwd=str(tmp_path), session_dir=str(sess_dir))
        graph = await engine1.apply_recipe(recipe, pwd=str(tmp_path))
        gid = graph.graph_id

        store_path = sess_dir / f"{gid}.kohakutr"
        store = SessionStore(str(store_path))
        store.init_meta(
            session_id=gid,
            config_type="terrarium",
            config_path=str(recipe_path),
            pwd=str(tmp_path),
            agents=["alice", "bob"],
            terrarium_name="duo",
            terrarium_channels=[],
            terrarium_creatures=[
                {"name": "alice", "listen": [], "send": []},
                {"name": "bob", "listen": [], "send": []},
            ],
        )
        await engine1.attach_session(gid, store)

        service = LocalTerrariumService(engine1)

        # Runtime mutation — the thing the user is asking about.
        await service.add_channel(gid, "manual_chat", "user-added at runtime")
        await service.connect("alice", "bob", channel="manual_chat")

        # Pre-shutdown sanity: live engine has the wiring.
        info_alice = await service.get_creature_info("alice")
        info_bob = await service.get_creature_info("bob")
        assert "manual_chat" in info_alice.send_channels
        assert "manual_chat" in info_bob.listen_channels

        store.flush()
        await engine1.shutdown()
        store.close()

        # --- phase 2: cold engine, adopt the saved file -----------------
        engine2 = Terrarium(pwd=str(tmp_path), session_dir=str(sess_dir))
        # ``adopt_session`` may mint a fresh graph_id (apply_recipe
        # generates them); the saved-session identity check happens
        # via the saved meta's ``agents`` list rather than gid equality.
        sid = await engine2.adopt_session(store_path, pwd=str(tmp_path))

        service2 = LocalTerrariumService(engine2)
        chans = await service2.list_channels(sid)
        chan_names = {c.name for c in chans}
        info_alice2 = await service2.get_creature_info("alice")
        info_bob2 = await service2.get_creature_info("bob")

        await engine2.shutdown()

        # THE GAP: pre-fix, "manual_chat" disappears and alice / bob
        # come back without the wiring. These asserts pin the desired
        # post-fix behaviour.
        assert "manual_chat" in chan_names, (
            f"runtime-added channel did not survive resume; "
            f"channels after resume = {chan_names!r}"
        )
        assert "manual_chat" in info_alice2.send_channels, (
            f"alice wiring lost after resume; send_channels = "
            f"{info_alice2.send_channels!r}"
        )
        assert "manual_chat" in info_bob2.listen_channels, (
            f"bob wiring lost after resume; listen_channels = "
            f"{info_bob2.listen_channels!r}"
        )
