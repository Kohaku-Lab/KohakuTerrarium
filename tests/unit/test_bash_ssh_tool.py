"""Tests for the kt-biome bash_ssh terminal backend."""

from unittest.mock import MagicMock

import pytest

from kohakuterrarium.builtins.tools.bash import ShellTool
from kt_biome.tools import bash_ssh as bash_ssh_mod
from kt_biome.tools.bash_ssh import SshBashTool


class TestAvailability:
    def test_missing_sdk_reports_unavailable(self, monkeypatch):
        """When paramiko is absent the tool must cleanly report unavailable."""
        monkeypatch.setattr(bash_ssh_mod, "_HAS_PARAMIKO", False)
        monkeypatch.setattr(bash_ssh_mod, "_paramiko", None)

        assert SshBashTool.is_available() is False

    async def test_missing_sdk_execute_returns_error(self, monkeypatch):
        monkeypatch.setattr(bash_ssh_mod, "_HAS_PARAMIKO", False)
        monkeypatch.setattr(bash_ssh_mod, "_paramiko", None)

        tool = SshBashTool(host="example.com", user="ubuntu")
        result = await tool.execute({"command": "echo hi"})
        assert not result.success
        assert "paramiko" in (result.error or "").lower()
        assert "pip install paramiko" in (result.error or "").lower()


class TestSchemaParity:
    def test_schema_matches_bash(self):
        """bash_ssh arg schema must be a compatible superset of bash's."""
        ssh_tool = SshBashTool(host="example.com", user="ubuntu")
        bash_tool = ShellTool()
        ssh_schema = ssh_tool.get_parameters_schema()
        bash_schema = bash_tool.get_parameters_schema()

        assert ssh_schema["type"] == bash_schema["type"] == "object"
        assert ssh_schema["required"] == bash_schema["required"] == ["command"]
        assert ssh_schema["properties"]["command"]["type"] == "string"
        assert ssh_schema["properties"]["type"]["type"] == "string"
        assert "timeout" in ssh_schema["properties"]
        assert ssh_schema["properties"]["timeout"]["type"] == "number"

    def test_tool_metadata(self):
        tool = SshBashTool(host="example.com", user="ubuntu", port=2222)
        assert tool.tool_name == "bash_ssh"
        assert "ssh" in tool.description.lower()
        assert "example.com" in tool.description
        assert tool.execution_mode.value == "direct"
        doc = tool.get_full_documentation()
        assert "bash_ssh" in doc
        assert "command" in doc


class TestMockedExecution:
    def _make_fake_paramiko(self):
        """Build a fake paramiko namespace with the attributes the tool uses."""
        fake_paramiko = MagicMock()

        # Host-key policy classes — instances are returned from the real
        # paramiko module, we only need the call to succeed.
        fake_paramiko.AutoAddPolicy.return_value = MagicMock()
        fake_paramiko.RejectPolicy.return_value = MagicMock()
        fake_paramiko.WarningPolicy.return_value = MagicMock()

        # SSHClient with exec_command returning stdin/stdout/stderr.
        stdout = MagicMock()
        stdout.read.return_value = b"remote-hello\n"
        stdout.channel.recv_exit_status.return_value = 0
        stderr = MagicMock()
        stderr.read.return_value = b""
        stdin = MagicMock()

        fake_transport = MagicMock()
        fake_transport.is_active.return_value = True

        fake_client_instance = MagicMock()
        fake_client_instance.exec_command.return_value = (stdin, stdout, stderr)
        fake_client_instance.get_transport.return_value = fake_transport

        fake_paramiko.SSHClient.return_value = fake_client_instance
        return fake_paramiko, fake_client_instance, stdout, stderr

    async def test_mocked_exec_returns_tool_result(self, monkeypatch):
        """A mocked paramiko session path produces a populated ToolResult."""
        monkeypatch.setattr(bash_ssh_mod, "_HAS_PARAMIKO", True)
        fake_paramiko, fake_client, stdout, _stderr = self._make_fake_paramiko()
        monkeypatch.setattr(bash_ssh_mod, "_paramiko", fake_paramiko)

        tool = SshBashTool(
            host="example.com", user="ubuntu", port=22, key_filename=None
        )
        result = await tool.execute({"command": "echo remote-hello"})

        assert result.success, f"unexpected error: {result.error}"
        assert "remote-hello" in result.get_text_output()
        assert result.exit_code == 0
        assert result.metadata["host"] == "example.com"
        assert result.metadata["shell_type"] == "bash"
        # connect() called on first use.
        assert fake_client.connect.called
        connect_kwargs = fake_client.connect.call_args.kwargs
        assert connect_kwargs["hostname"] == "example.com"
        assert connect_kwargs["username"] == "ubuntu"

        # exec_command should have received a bash -c "<quoted command>"
        exec_args = fake_client.exec_command.call_args
        cmd_line = exec_args.args[0] if exec_args.args else exec_args.kwargs["command"]
        assert cmd_line.startswith("bash -c ")
        assert "echo remote-hello" in cmd_line

        # Second call reuses the connection.
        await tool.execute({"command": "echo again"})
        assert fake_client.connect.call_count == 1
        assert fake_client.exec_command.call_count == 2

        # close() tears the session down.
        await tool.close()
        assert fake_client.close.called
        assert tool._client is None  # noqa: SLF001

    async def test_unknown_shell_type_rejected(self, monkeypatch):
        monkeypatch.setattr(bash_ssh_mod, "_HAS_PARAMIKO", True)
        monkeypatch.setattr(bash_ssh_mod, "_paramiko", MagicMock())

        tool = SshBashTool(host="example.com", user="ubuntu")
        result = await tool.execute({"command": "echo x", "type": "powershell"})
        assert not result.success
        assert "shell" in (result.error or "").lower()

    async def test_empty_command_rejected(self):
        tool = SshBashTool(host="example.com", user="ubuntu")
        result = await tool.execute({"command": ""})
        assert not result.success
        assert "command" in (result.error or "").lower()

    async def test_missing_host_rejected(self, monkeypatch):
        """A tool missing host/user config must surface a clear error."""
        monkeypatch.setattr(bash_ssh_mod, "_HAS_PARAMIKO", True)
        fake_paramiko, _client, _stdout, _stderr = self._make_fake_paramiko()
        monkeypatch.setattr(bash_ssh_mod, "_paramiko", fake_paramiko)

        tool = SshBashTool()  # no host, no user
        result = await tool.execute({"command": "echo hi"})
        assert not result.success
        assert "host" in (result.error or "").lower()


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
