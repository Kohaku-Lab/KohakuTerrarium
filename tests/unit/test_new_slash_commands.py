"""Tests for the four new slash commands: /workspace /env /triggers
/system_prompt.

Each is a thin wrapper over an existing agent helper, so the tests
exercise the dispatch + error paths rather than the underlying state
management (which has its own tests).
"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from kohakuterrarium.builtins.user_commands import get_builtin_user_command
from kohakuterrarium.modules.user_command.base import UserCommandContext

# ── /workspace ───────────────────────────────────────────────────


class TestWorkspace:
    @pytest.fixture
    def cmd(self):
        return get_builtin_user_command("workspace")

    async def test_no_args_shows_current(self, cmd):
        agent = MagicMock()
        agent.workspace.get.return_value = "/home/user/proj"
        result = await cmd.execute("", UserCommandContext(agent=agent))
        assert result.success
        assert "/home/user/proj" in result.output

    async def test_set_path(self, cmd):
        agent = MagicMock()
        agent.workspace.set.return_value = "/new/path"
        result = await cmd.execute("/new/path", UserCommandContext(agent=agent))
        assert result.success
        assert "/new/path" in result.output
        agent.workspace.set.assert_called_with("/new/path")

    async def test_set_invalid_returns_error(self, cmd):
        agent = MagicMock()
        agent.workspace.set.side_effect = ValueError("Not a directory")
        result = await cmd.execute("/bad", UserCommandContext(agent=agent))
        assert not result.success
        assert "Not a directory" in result.error

    async def test_no_workspace_helper(self, cmd):
        agent = SimpleNamespace()  # no .workspace
        result = await cmd.execute("", UserCommandContext(agent=agent))
        assert not result.success
        assert "no workspace" in result.error.lower()


# ── /env ─────────────────────────────────────────────────────────


class TestEnv:
    @pytest.fixture
    def cmd(self):
        return get_builtin_user_command("env")

    async def test_lists_visible_vars(self, cmd, monkeypatch):
        monkeypatch.setenv("KT_TEST_FOO", "bar")
        agent = MagicMock()
        agent.workspace.get.return_value = "/tmp"
        result = await cmd.execute("", UserCommandContext(agent=agent))
        assert result.success
        assert "KT_TEST_FOO=bar" in result.output

    async def test_redacts_credential_keys(self, cmd, monkeypatch):
        monkeypatch.setenv("MY_API_KEY", "secret-xyz")
        monkeypatch.setenv("MY_TOKEN", "tok-xyz")
        monkeypatch.setenv("MY_SECRET", "shh")
        agent = MagicMock()
        agent.workspace.get.return_value = "/tmp"
        result = await cmd.execute("", UserCommandContext(agent=agent))
        assert result.success
        assert "secret-xyz" not in result.output
        assert "tok-xyz" not in result.output
        assert "MY_API_KEY" not in result.output

    async def test_filter_substring(self, cmd, monkeypatch):
        monkeypatch.setenv("KT_FILTER_MATCH", "yes")
        monkeypatch.setenv("OTHER_VAR", "no")
        agent = MagicMock()
        agent.workspace.get.return_value = "/tmp"
        result = await cmd.execute("filter_match", UserCommandContext(agent=agent))
        assert result.success
        assert "KT_FILTER_MATCH=yes" in result.output
        assert "OTHER_VAR" not in result.output


# ── /triggers ────────────────────────────────────────────────────


class TestTriggers:
    @pytest.fixture
    def cmd(self):
        return get_builtin_user_command("triggers")

    async def test_no_triggers(self, cmd):
        agent = MagicMock()
        agent.trigger_manager.list.return_value = []
        result = await cmd.execute("", UserCommandContext(agent=agent))
        assert result.success
        assert "No active triggers" in result.output

    async def test_lists_triggers(self, cmd):
        agent = MagicMock()
        agent.trigger_manager.list.return_value = [
            SimpleNamespace(
                trigger_id="timer-1",
                trigger_type="timer",
                running=True,
                created_at=datetime(2024, 1, 1, 12, 0, 0),
            ),
            SimpleNamespace(
                trigger_id="cond-x",
                trigger_type="condition",
                running=False,
                created_at=datetime(2024, 1, 1, 12, 5, 0),
            ),
        ]
        result = await cmd.execute("", UserCommandContext(agent=agent))
        assert result.success
        assert "timer-1" in result.output
        assert "cond-x" in result.output
        assert "running" in result.output
        assert "idle" in result.output

    async def test_no_manager(self, cmd):
        agent = SimpleNamespace()  # no trigger_manager
        result = await cmd.execute("", UserCommandContext(agent=agent))
        assert result.success
        assert "No trigger manager" in result.output


# ── /system_prompt ───────────────────────────────────────────────


class TestSystemPrompt:
    @pytest.fixture
    def cmd(self):
        return get_builtin_user_command("system_prompt")

    async def test_returns_prompt(self, cmd):
        agent = MagicMock()
        agent.get_system_prompt.return_value = "You are a helpful assistant."
        result = await cmd.execute("", UserCommandContext(agent=agent))
        assert result.success
        assert "helpful assistant" in result.output

    async def test_empty_prompt(self, cmd):
        agent = MagicMock()
        agent.get_system_prompt.return_value = ""
        result = await cmd.execute("", UserCommandContext(agent=agent))
        assert result.success
        assert "empty" in result.output.lower()

    async def test_no_helper(self, cmd):
        agent = SimpleNamespace()  # no get_system_prompt
        result = await cmd.execute("", UserCommandContext(agent=agent))
        assert not result.success
        assert "get_system_prompt" in result.error
