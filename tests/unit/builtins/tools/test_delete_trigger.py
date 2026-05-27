"""Unit tests for the ``delete_trigger`` builtin tool."""

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from kohakuterrarium.builtins.tools.delete_trigger import DeleteTriggerTool
from kohakuterrarium.modules.tool.base import ExecutionMode, ToolContext


class _StubAgent:
    """Minimal agent stand-in. Only ``remove_trigger`` is exercised."""

    def __init__(self, remove_result: bool = True, raises: Exception | None = None):
        self._remove_result = remove_result
        self._raises = raises
        self.calls: list[str] = []

    async def remove_trigger(self, trigger_id: str) -> bool:
        self.calls.append(trigger_id)
        if self._raises is not None:
            raise self._raises
        return self._remove_result


def _ctx(agent: Any | None) -> ToolContext:
    """Build a minimal ToolContext for the tool's needs_context branch."""
    return ToolContext(
        agent_name="test", session=None, working_dir=Path("/tmp"), agent=agent
    )


@pytest.fixture
def tool() -> DeleteTriggerTool:
    return DeleteTriggerTool()


class TestDeleteTrigger:
    def test_tool_metadata(self, tool):
        # Shape pin so the registry + parameter schema stay stable.
        assert tool.tool_name == "delete_trigger"
        assert tool.execution_mode is ExecutionMode.DIRECT
        schema = tool.get_parameters_schema()
        assert schema["required"] == ["trigger_id"]
        assert "trigger_id" in schema["properties"]

    async def test_removes_existing_trigger(self, tool):
        agent = _StubAgent(remove_result=True)
        result = await tool._execute({"trigger_id": "trigger_42"}, _ctx(agent))
        assert result.exit_code == 0
        assert "Removed trigger" in (result.output or "")
        assert "trigger_42" in (result.output or "")
        assert agent.calls == ["trigger_42"]

    async def test_unknown_trigger_returns_error_with_exit_code_1(self, tool):
        agent = _StubAgent(remove_result=False)
        result = await tool._execute({"trigger_id": "missing"}, _ctx(agent))
        # Behaviour assert (not shape): the LLM must see this as a
        # failure so it understands the trigger wasn't found.
        assert result.exit_code == 1
        assert result.error and "missing" in result.error
        assert "not found" in result.error.lower()

    async def test_empty_trigger_id_is_rejected_without_calling_agent(self, tool):
        # Defensive: empty / whitespace must not reach ``remove_trigger``.
        agent = _StubAgent()
        result = await tool._execute({"trigger_id": "   "}, _ctx(agent))
        assert result.exit_code == 1
        assert "trigger_id is required" in (result.error or "")
        assert agent.calls == []

    async def test_missing_context_surfaces_agent_required(self, tool):
        result = await tool._execute({"trigger_id": "x"}, None)
        assert result.exit_code == 1
        assert "Agent context required" in (result.error or "")

    async def test_agent_without_remove_trigger_surfaces_unsupported(self, tool):
        # Some integration paths attach a bare agent stub; bail out
        # cleanly rather than AttributeError.
        ctx = _ctx(object())
        result = await tool._execute({"trigger_id": "x"}, ctx)
        assert result.exit_code == 1
        assert "does not support" in (result.error or "")

    async def test_remove_trigger_exception_is_caught(self, tool):
        agent = _StubAgent(raises=RuntimeError("boom"))
        result = await tool._execute({"trigger_id": "x"}, _ctx(agent))
        assert result.exit_code == 1
        assert result.error and "boom" in result.error

    async def test_async_mock_compat(self, tool):
        # Sanity: an AsyncMock attached to ``remove_trigger`` works
        # exactly as a real coroutine — guards against future refactors
        # that accidentally drop the ``await``.
        agent = type("A", (), {})()
        agent.remove_trigger = AsyncMock(return_value=True)
        result = await tool._execute({"trigger_id": "x"}, _ctx(agent))
        assert result.exit_code == 0
        agent.remove_trigger.assert_awaited_once_with("x")
