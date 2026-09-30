"""``pwd_guard`` agent option: how paths outside the working directory are treated."""

import pytest

from kohakuterrarium import Agent
from kohakuterrarium.core.config import load_agent_config
from kohakuterrarium.errors import ConfigError
from kohakuterrarium.testing.llm import ScriptedLLM


def _write(tmp_path, extra=""):
    (tmp_path / "config.yaml").write_text(
        "name: guard\nsystem_prompt: offline\ninput: {type: none}\n"
        "output: {type: stdout}\n" + extra
    )
    return tmp_path


class TestPwdGuardConfig:
    def test_defaults_to_warn(self, tmp_path):
        assert load_agent_config(_write(tmp_path)).pwd_guard == "warn"

    @pytest.mark.parametrize("mode", ["warn", "block", "off"])
    def test_top_level_value_is_read(self, tmp_path, mode):
        assert (
            load_agent_config(_write(tmp_path, f"pwd_guard: {mode}\n")).pwd_guard
            == mode
        )

    def test_controller_section_value_is_read(self, tmp_path):
        cfg = load_agent_config(_write(tmp_path, "controller:\n  pwd_guard: block\n"))
        assert cfg.pwd_guard == "block"

    def test_unknown_value_is_a_config_error_naming_the_choices(self, tmp_path):
        with pytest.raises(ConfigError, match="pwd_guard.*warn.*block.*off"):
            load_agent_config(_write(tmp_path, "pwd_guard: strict\n"))


class TestPwdGuardOnARunningAgent:
    async def _agent(self, tmp_path, mode):
        cfg = _write(tmp_path, f"pwd_guard: {mode}\n")
        return await Agent.build(
            str(cfg), llm=ScriptedLLM(["ok"]), io="headless", pwd=tmp_path
        )

    async def test_block_refuses_outside_paths_every_time(self, tmp_path):
        agent = await self._agent(tmp_path, "block")
        outside = str(tmp_path.parent / "elsewhere.txt")
        guard = agent.executor._path_guard
        assert guard.mode == "block"
        assert "Access denied" in guard.check(outside)
        assert "Access denied" in guard.check(outside)

    async def test_off_allows_outside_paths(self, tmp_path):
        agent = await self._agent(tmp_path, "off")
        assert agent.executor._path_guard.check(str(tmp_path.parent / "x.txt")) is None

    async def test_mode_survives_a_working_directory_switch(self, tmp_path):
        agent = await self._agent(tmp_path, "block")
        moved = tmp_path / "sub"
        moved.mkdir()
        agent.workspace.set(moved)
        guard = agent.executor._path_guard
        assert guard.mode == "block"
        assert "Access denied" in guard.check(str(tmp_path / "top.txt"))
