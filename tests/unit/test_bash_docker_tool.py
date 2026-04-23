"""Tests for the kt-biome bash_docker terminal backend."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from kohakuterrarium.builtins.tools.bash import ShellTool
from kohakuterrarium.modules.tool.base import ToolContext
from kt_biome.tools import bash_docker as bash_docker_mod
from kt_biome.tools.bash_docker import DockerBashTool


def _make_context(working_dir: Path) -> ToolContext:
    return ToolContext(
        agent_name="test_agent",
        session=None,
        working_dir=working_dir,
    )


class TestAvailability:
    def test_missing_sdk_reports_unavailable(self, monkeypatch):
        """When docker-py is absent the tool must cleanly report unavailable."""
        monkeypatch.setattr(bash_docker_mod, "_HAS_DOCKER", False)
        monkeypatch.setattr(bash_docker_mod, "_docker", None)

        assert DockerBashTool.is_available() is False

    async def test_missing_sdk_execute_returns_error(self, monkeypatch, tmp_path):
        monkeypatch.setattr(bash_docker_mod, "_HAS_DOCKER", False)
        monkeypatch.setattr(bash_docker_mod, "_docker", None)

        tool = DockerBashTool()
        result = await tool.execute(
            {"command": "echo hi"}, context=_make_context(tmp_path)
        )
        assert not result.success
        assert "docker" in (result.error or "").lower()
        assert "pip install docker" in (result.error or "").lower()


class TestSchemaParity:
    def test_schema_matches_bash(self):
        """bash_docker arg schema must be a compatible superset of bash's."""
        docker_tool = DockerBashTool()
        bash_tool = ShellTool()
        docker_schema = docker_tool.get_parameters_schema()
        bash_schema = bash_tool.get_parameters_schema()

        assert docker_schema["type"] == bash_schema["type"] == "object"
        assert docker_schema["required"] == bash_schema["required"] == ["command"]
        # Both support a `command` string and an optional `type` shell selector.
        assert docker_schema["properties"]["command"]["type"] == "string"
        assert docker_schema["properties"]["type"]["type"] == "string"
        # bash_docker additionally exposes a timeout override.
        assert "timeout" in docker_schema["properties"]
        assert docker_schema["properties"]["timeout"]["type"] == "number"

    def test_tool_metadata(self):
        tool = DockerBashTool(image="python:3.13-slim")
        assert tool.tool_name == "bash_docker"
        assert "docker" in tool.description.lower()
        assert tool.execution_mode.value == "direct"
        doc = tool.get_full_documentation()
        assert "bash_docker" in doc
        assert "command" in doc


class TestMockedExecution:
    async def test_mocked_run_returns_tool_result(self, monkeypatch, tmp_path):
        """A mocked docker client path produces a populated ToolResult."""
        # Force the SDK-available branch without needing docker-py installed.
        monkeypatch.setattr(bash_docker_mod, "_HAS_DOCKER", True)
        fake_docker = MagicMock()
        monkeypatch.setattr(bash_docker_mod, "_docker", fake_docker)

        # Mock container + exec_run result.
        fake_exec = MagicMock()
        fake_exec.exit_code = 0
        fake_exec.output = b"hello from docker\n"
        fake_container = MagicMock()
        fake_container.id = "abc123def4567890"
        fake_container.status = "running"
        fake_container.exec_run.return_value = fake_exec
        fake_container.reload.return_value = None

        fake_client = MagicMock()
        fake_client.containers.run.return_value = fake_container
        fake_docker.from_env.return_value = fake_client

        tool = DockerBashTool(
            image="python:3.13-slim",
            volumes=["{cwd}:/workspace"],
            working_dir="/workspace",
        )
        context = _make_context(tmp_path)

        result = await tool.execute(
            {"command": "echo hello from docker"}, context=context
        )

        assert result.success, f"unexpected error: {result.error}"
        assert "hello from docker" in result.get_text_output()
        assert result.exit_code == 0
        assert result.metadata["image"] == "python:3.13-slim"
        assert result.metadata["shell_type"] == "bash"
        # First call spins up the container.
        assert fake_client.containers.run.called
        kwargs = fake_client.containers.run.call_args.kwargs
        assert kwargs["image"] == "python:3.13-slim"
        assert kwargs["working_dir"] == "/workspace"
        # Volume {cwd} placeholder resolved to the agent cwd.
        assert any(
            str(tmp_path) in vol and vol.endswith(":/workspace")
            for vol in kwargs["volumes"]
        )

        # Second call reuses the existing container.
        result2 = await tool.execute({"command": "echo again"}, context=context)
        assert result2.success
        assert fake_client.containers.run.call_count == 1
        assert fake_container.exec_run.call_count == 2

        # close() stops the container and drops the client handle.
        await tool.close()
        assert fake_container.stop.called
        assert tool._container is None  # noqa: SLF001 — exercising teardown
        assert tool._client is None  # noqa: SLF001

    async def test_unknown_shell_type_rejected(self, monkeypatch, tmp_path):
        monkeypatch.setattr(bash_docker_mod, "_HAS_DOCKER", True)
        monkeypatch.setattr(bash_docker_mod, "_docker", MagicMock())

        tool = DockerBashTool()
        result = await tool.execute(
            {"command": "echo x", "type": "powershell"},
            context=_make_context(tmp_path),
        )
        assert not result.success
        assert "shell" in (result.error or "").lower()

    async def test_empty_command_rejected(self, tmp_path):
        tool = DockerBashTool()
        result = await tool.execute({"command": ""}, context=_make_context(tmp_path))
        assert not result.success
        assert "command" in (result.error or "").lower()


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
