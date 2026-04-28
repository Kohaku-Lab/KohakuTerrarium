"""Regression: every public ``studio.sessions.creature_*`` helper must
accept ``session_id`` as the second positional argument.

This guards the class of bug where a helper's body still references an
outer ``session_id`` after a refactor migrated callers to pass it in
explicitly (the bug fixed in commit ``021de7bf``).  A pure import-then-
call test catches the runtime ``NameError`` that pydantic / argparse
type-checks would happily skip.

Each helper is exercised against a real :class:`Terrarium` engine
holding a fake creature, so the call shape *and* internal lookups
(``find_creature(engine, session_id, creature_id)``) actually run.
"""

import pytest

import kohakuterrarium.studio.sessions.creature_chat as chat_mod
import kohakuterrarium.studio.sessions.creature_command as command_mod
import kohakuterrarium.studio.sessions.creature_ctl as ctl_mod
import kohakuterrarium.studio.sessions.creature_model as model_mod
import kohakuterrarium.studio.sessions.creature_plugins as plugins_mod
import kohakuterrarium.studio.sessions.creature_state as state_mod
from kohakuterrarium.terrarium.engine import Terrarium

from tests.unit.studio_sessions._fakes import install_fake_creature


# A single fake creature is enough — we only care that every helper
# is *callable* with ``(engine, session_id, creature_id, ...)``.
@pytest.fixture
async def engine_and_ids():
    engine = Terrarium()
    creature = await install_fake_creature(engine, "alice")
    yield engine, creature.graph_id, "alice"
    await engine.shutdown()


class TestCreatureChatCallShapes:
    """Every helper in ``creature_chat`` must accept session_id."""

    @pytest.mark.asyncio
    async def test_regenerate(self, engine_and_ids):
        engine, sid, cid = engine_and_ids
        # Used to raise NameError: name 'session_id' is not defined.
        await chat_mod.regenerate(engine, sid, cid)

    @pytest.mark.asyncio
    async def test_edit_message_str(self, engine_and_ids):
        engine, sid, cid = engine_and_ids
        ok = await chat_mod.edit_message(engine, sid, cid, 0, "edited")
        assert ok is True

    @pytest.mark.asyncio
    async def test_edit_message_multimodal(self, engine_and_ids):
        engine, sid, cid = engine_and_ids
        # The frontend's buildMessageParts emits a list of content-part
        # dicts even for text-only edits.  The helper must accept it.
        ok = await chat_mod.edit_message(
            engine,
            sid,
            cid,
            0,
            [{"type": "text", "text": "edited"}],
            turn_index=2,
            user_position=1,
        )
        assert ok is True

    @pytest.mark.asyncio
    async def test_rewind(self, engine_and_ids):
        engine, sid, cid = engine_and_ids
        await chat_mod.rewind(engine, sid, cid, 0)

    @pytest.mark.asyncio
    async def test_history(self, engine_and_ids):
        engine, sid, cid = engine_and_ids
        out = chat_mod.history(engine, sid, cid)
        assert out["session_id"] == sid
        assert out["creature_id"] == cid

    @pytest.mark.asyncio
    async def test_branches(self, engine_and_ids):
        engine, sid, cid = engine_and_ids
        out = chat_mod.branches(engine, sid, cid)
        assert out["creature_id"] == cid


class TestCreatureCtlCallShapes:
    @pytest.mark.asyncio
    async def test_interrupt(self, engine_and_ids):
        engine, sid, cid = engine_and_ids
        ctl_mod.interrupt(engine, sid, cid)

    @pytest.mark.asyncio
    async def test_list_jobs(self, engine_and_ids):
        engine, sid, cid = engine_and_ids
        assert ctl_mod.list_jobs(engine, sid, cid) == []

    @pytest.mark.asyncio
    async def test_cancel_job(self, engine_and_ids):
        engine, sid, cid = engine_and_ids
        # No matching job — but the call shape must succeed.
        assert await ctl_mod.cancel_job(engine, sid, cid, "missing") is False

    @pytest.mark.asyncio
    async def test_promote_job(self, engine_and_ids):
        engine, sid, cid = engine_and_ids
        assert ctl_mod.promote_job(engine, sid, cid, "promote-me") is True


class TestCreatureModelCallShapes:
    @pytest.mark.asyncio
    async def test_switch_model(self, engine_and_ids):
        engine, sid, cid = engine_and_ids
        out = model_mod.switch_model(engine, sid, cid, "test/profile")
        assert out == "test/profile"

    @pytest.mark.asyncio
    async def test_set_native_tool_options(self, engine_and_ids):
        engine, sid, cid = engine_and_ids
        out = model_mod.set_native_tool_options(engine, sid, cid, "tool", {"k": 1})
        assert out == {"k": 1}


class TestCreaturePluginsCallShapes:
    @pytest.mark.asyncio
    async def test_list_plugins(self, engine_and_ids):
        engine, sid, cid = engine_and_ids
        out = plugins_mod.list_plugins(engine, sid, cid)
        assert isinstance(out, list)

    @pytest.mark.asyncio
    async def test_toggle_plugin(self, engine_and_ids):
        engine, sid, cid = engine_and_ids
        out = await plugins_mod.toggle_plugin(engine, sid, cid, "plug_b")
        assert out == {"name": "plug_b", "enabled": True}


class TestCreatureCommandCallShapes:
    @pytest.mark.asyncio
    async def test_execute_command_unknown_raises(self, engine_and_ids):
        engine, sid, cid = engine_and_ids
        with pytest.raises(ValueError):
            await command_mod.execute_command(engine, sid, cid, "no-such-cmd")


class TestCreatureStateCallShapes:
    @pytest.mark.asyncio
    async def test_get_scratchpad(self, engine_and_ids):
        engine, sid, cid = engine_and_ids
        assert state_mod.get_scratchpad(engine, sid, cid) == {}

    @pytest.mark.asyncio
    async def test_patch_scratchpad(self, engine_and_ids):
        engine, sid, cid = engine_and_ids
        out = state_mod.patch_scratchpad(engine, sid, cid, {"k": "v"})
        assert out == {"k": "v"}

    @pytest.mark.asyncio
    async def test_list_triggers(self, engine_and_ids):
        engine, sid, cid = engine_and_ids
        assert state_mod.list_triggers(engine, sid, cid) == []

    @pytest.mark.asyncio
    async def test_get_env(self, engine_and_ids):
        engine, sid, cid = engine_and_ids
        out = state_mod.get_env(engine, sid, cid)
        assert "pwd" in out and "env" in out

    @pytest.mark.asyncio
    async def test_get_system_prompt(self, engine_and_ids):
        engine, sid, cid = engine_and_ids
        out = state_mod.get_system_prompt(engine, sid, cid)
        assert "text" in out

    @pytest.mark.asyncio
    async def test_get_working_dir(self, engine_and_ids):
        engine, sid, cid = engine_and_ids
        assert state_mod.get_working_dir(engine, sid, cid) == "."

    @pytest.mark.asyncio
    async def test_set_working_dir(self, engine_and_ids, tmp_path):
        engine, sid, cid = engine_and_ids
        out = state_mod.set_working_dir(engine, sid, cid, str(tmp_path))
        assert out == str(tmp_path)

    @pytest.mark.asyncio
    async def test_native_tool_inventory(self, engine_and_ids):
        engine, sid, cid = engine_and_ids
        assert state_mod.native_tool_inventory(engine, sid, cid) == []

    @pytest.mark.asyncio
    async def test_get_native_tool_options(self, engine_and_ids):
        engine, sid, cid = engine_and_ids
        assert state_mod.get_native_tool_options(engine, sid, cid) == {}

    @pytest.mark.asyncio
    async def test_set_native_tool_options(self, engine_and_ids):
        engine, sid, cid = engine_and_ids
        out = state_mod.set_native_tool_options(engine, sid, cid, "tool", {"k": 1})
        assert out == {"k": 1}
