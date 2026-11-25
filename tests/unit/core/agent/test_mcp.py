"""Unit tests for :mod:`kohakuterrarium.core.agent.mcp`."""

import types
from unittest.mock import AsyncMock


from kohakuterrarium.core.agent import mcp as am
from kohakuterrarium.core.agent.mcp import (
    init_mcp,
    inject_mcp_tools_into_prompt,
)


class _FakeManager:
    def __init__(self, *, server_list=None, fail_on=None):
        self._servers = server_list or []
        self.connect = AsyncMock(side_effect=self._connect_impl)
        self.disconnect = AsyncMock()
        self._connect_log: list[str] = []
        self._fail_on = fail_on or set()

    async def _connect_impl(self, config):
        self._connect_log.append(config.name)
        if config.name in self._fail_on:
            raise RuntimeError("connect failed")
        return types.SimpleNamespace(tools=[{"name": "t1"}, {"name": "t2"}])

    def list_servers(self):
        return list(self._servers)


def _agent_with_mcp(*, mcp_configs=None):
    prompts: list[str] = []
    return types.SimpleNamespace(
        config=types.SimpleNamespace(mcp_servers=mcp_configs or []),
        _mcp_manager=None,
        prompts=prompts,
        set_system_prompt_section=lambda owner, content: prompts.append(content),
    )


# ── init_mcp ─────────────────────────────────────────────────────


class TestInitMCP:
    async def test_no_configs_still_constructs_manager(self):
        # E7: the manager is ALWAYS built — the ``mcp_connect`` runtime
        # tool must work on a configless agent (it used to get None).
        a = _agent_with_mcp()
        await init_mcp(a)
        assert a._mcp_manager is not None
        assert a._mcp_manager.list_servers() == []

    async def test_connects_all_servers(self, monkeypatch):
        fake = _FakeManager()
        monkeypatch.setattr(am, "MCPClientManager", lambda: fake)
        a = _agent_with_mcp(
            mcp_configs=[
                {"name": "s1", "transport": "stdio", "command": "echo"},
                {"name": "s2", "transport": "stdio", "command": "ls"},
            ]
        )
        await init_mcp(a)
        assert a._mcp_manager is fake
        assert fake._connect_log == ["s1", "s2"]

    async def test_headers_reach_the_server_config(self, monkeypatch):
        fake = _FakeManager()
        monkeypatch.setattr(am, "MCPClientManager", lambda: fake)
        auth = {"Authorization": "Bearer t0ken"}
        a = _agent_with_mcp(
            mcp_configs=[
                {
                    "name": "remote",
                    "transport": "streamable_http",
                    "url": "http://h/mcp",
                    "headers": auth,
                },
                {"name": "plain", "transport": "stdio", "command": "echo"},
            ]
        )
        await init_mcp(a)
        configs = {c.args[0].name: c.args[0] for c in fake.connect.await_args_list}
        assert configs["remote"].headers == auth
        assert configs["plain"].headers == {}

    async def test_registry_style_entry_has_env_vars_expanded(self, monkeypatch):
        fake = _FakeManager()
        monkeypatch.setattr(am, "MCPClientManager", lambda: fake)
        monkeypatch.setenv("KT_UT_SANDBOX_TOKEN", "s3cret")
        a = _agent_with_mcp(
            mcp_configs=[
                {
                    "name": "sandbox",
                    "transport": "streamable_http",
                    "url": "http://h/mcp",
                    "headers": {"Authorization": "Bearer ${KT_UT_SANDBOX_TOKEN}"},
                }
            ]
        )
        await init_mcp(a)
        sent = fake.connect.await_args_list[0].args[0]
        assert sent.headers == {"Authorization": "Bearer s3cret"}
        assert a.config.mcp_servers[0]["headers"] == {
            "Authorization": "Bearer ${KT_UT_SANDBOX_TOKEN}"
        }

    async def test_unset_env_var_skips_the_server_with_a_clear_warning(
        self, monkeypatch, caplog
    ):
        fake = _FakeManager()
        monkeypatch.setattr(am, "MCPClientManager", lambda: fake)
        monkeypatch.delenv("KT_UT_MISSING_TOKEN", raising=False)
        a = _agent_with_mcp(
            mcp_configs=[
                {
                    "name": "sandbox",
                    "transport": "streamable_http",
                    "url": "http://h/mcp",
                    "headers": {"Authorization": "Bearer ${KT_UT_MISSING_TOKEN}"},
                },
                {"name": "plain", "transport": "stdio", "command": "echo"},
            ]
        )
        with caplog.at_level("WARNING"):
            await init_mcp(a)
        assert fake._connect_log == ["plain"]
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert [getattr(r, "error", "") for r in warnings] == [
            "environment variable KT_UT_MISSING_TOKEN is not set "
            "(used in headers of server sandbox)"
        ]

    async def test_skips_non_dict_entries(self, monkeypatch):
        fake = _FakeManager()
        monkeypatch.setattr(am, "MCPClientManager", lambda: fake)
        a = _agent_with_mcp(
            mcp_configs=[
                "not a dict",
                {"name": "s1", "transport": "stdio", "command": "echo"},
            ]
        )
        await init_mcp(a)
        assert fake._connect_log == ["s1"]

    async def test_empty_name_skipped(self, monkeypatch):
        fake = _FakeManager()
        monkeypatch.setattr(am, "MCPClientManager", lambda: fake)
        a = _agent_with_mcp(mcp_configs=[{"transport": "stdio", "command": "echo"}])
        await init_mcp(a)
        # Not connected — no name.
        assert fake._connect_log == []

    async def test_failed_connect_disconnects(self, monkeypatch):
        fake = _FakeManager(fail_on={"bad"})
        monkeypatch.setattr(am, "MCPClientManager", lambda: fake)
        a = _agent_with_mcp(
            mcp_configs=[
                {"name": "bad", "transport": "stdio", "command": "x"},
                {"name": "good", "transport": "stdio", "command": "y"},
            ]
        )
        await init_mcp(a)
        # Bad attempted; disconnect called for cleanup; good still connected.
        fake.disconnect.assert_awaited_with("bad")
        assert "good" in fake._connect_log


# ── inject_mcp_tools_into_prompt ─────────────────────────────────


class TestInjectMcpToolsIntoPrompt:
    def test_no_manager_no_op(self):
        a = _agent_with_mcp()
        inject_mcp_tools_into_prompt(a)
        assert a.prompts == []

    def test_no_servers_no_op(self):
        a = _agent_with_mcp()
        a._mcp_manager = _FakeManager(server_list=[])
        inject_mcp_tools_into_prompt(a)
        assert a.prompts == []

    def test_disconnected_server_skipped(self):
        a = _agent_with_mcp()
        a._mcp_manager = _FakeManager(
            server_list=[
                {
                    "name": "s",
                    "status": "disconnected",
                    "tools": [{"name": "t"}],
                }
            ]
        )
        inject_mcp_tools_into_prompt(a)
        # All servers disconnected → nothing added beyond the header.
        assert a.prompts == []

    def test_connected_server_renders_tools(self):
        a = _agent_with_mcp()
        a._mcp_manager = _FakeManager(
            server_list=[
                {
                    "name": "fs",
                    "status": "connected",
                    "tools": [
                        {
                            "name": "read_file",
                            "description": "Read a file",
                            "input_schema": {
                                "properties": {
                                    "path": {
                                        "type": "string",
                                        "description": "file path",
                                    },
                                    "binary": {"type": "boolean"},
                                },
                                "required": ["path"],
                            },
                        }
                    ],
                }
            ]
        )
        inject_mcp_tools_into_prompt(a)
        assert len(a.prompts) == 1
        prompt = a.prompts[0]
        assert "Available MCP Tools" in prompt
        assert "### Server: fs" in prompt
        assert "**read_file**" in prompt
        assert "Read a file" in prompt
        # Required arg labelled.
        assert "`path`: string (required)" in prompt
        # Non-required arg present.
        assert "`binary`: boolean" in prompt

    def test_tool_without_description_or_schema(self):
        a = _agent_with_mcp()
        a._mcp_manager = _FakeManager(
            server_list=[
                {
                    "name": "minimal",
                    "status": "connected",
                    "tools": [{"name": "x"}],  # no desc, no schema
                }
            ]
        )
        inject_mcp_tools_into_prompt(a)
        prompt = a.prompts[-1]
        # Tool listed without description.
        assert "**x**" in prompt
        # No "—" separator because no description.
        assert "**x** —" not in prompt

    def test_server_instructions_follow_the_server_heading(self):
        a = _agent_with_mcp()
        a._mcp_manager = _FakeManager(
            server_list=[
                {
                    "name": "sandbox",
                    "status": "connected",
                    "instructions": "  Refer to files as sandbox:<name>/<path>.\n",
                    "tools": [{"name": "run"}],
                },
                {
                    "name": "fs",
                    "status": "connected",
                    "instructions": "",
                    "tools": [{"name": "read"}],
                },
            ]
        )
        inject_mcp_tools_into_prompt(a)
        prompt = a.prompts[-1]
        assert (
            "### Server: sandbox\n"
            "Refer to files as sandbox:<name>/<path>.\n"
            "- **run**" in prompt
        )
        assert "### Server: fs\n- **read**" in prompt

    def test_no_instructions_add_nothing(self):
        with_key = _agent_with_mcp()
        with_key._mcp_manager = _FakeManager(
            server_list=[
                {
                    "name": "fs",
                    "status": "connected",
                    "instructions": "   ",
                    "tools": [{"name": "read"}],
                }
            ]
        )
        without_key = _agent_with_mcp()
        without_key._mcp_manager = _FakeManager(
            server_list=[
                {"name": "fs", "status": "connected", "tools": [{"name": "read"}]}
            ]
        )
        inject_mcp_tools_into_prompt(with_key)
        inject_mcp_tools_into_prompt(without_key)
        assert with_key.prompts == without_key.prompts
        assert "### Server: fs\n- **read**" in with_key.prompts[-1]

    def test_long_instructions_are_capped_with_a_marker(self):
        long_text = "a" * (am.MAX_SERVER_INSTRUCTIONS_CHARS + 500)
        a = _agent_with_mcp()
        a._mcp_manager = _FakeManager(
            server_list=[
                {
                    "name": "big",
                    "status": "connected",
                    "instructions": long_text,
                    "tools": [],
                }
            ]
        )
        inject_mcp_tools_into_prompt(a)
        prompt = a.prompts[-1]
        assert (
            "a" * am.MAX_SERVER_INSTRUCTIONS_CHARS
            + "\n[... 500 more characters of server instructions truncated]"
            in prompt
        )
        assert "a" * (am.MAX_SERVER_INSTRUCTIONS_CHARS + 1) not in prompt


# ── disconnect failure path during connect cleanup (lines 60-61) ──


class TestFailedConnectDisconnectFailure:
    async def test_disconnect_failure_silently_swallowed(self, monkeypatch):
        from unittest.mock import AsyncMock

        fake = _FakeManager(fail_on={"bad"})
        # Make disconnect also raise.
        fake.disconnect = AsyncMock(side_effect=RuntimeError("disconnect failed"))
        monkeypatch.setattr(am, "MCPClientManager", lambda: fake)
        a = _agent_with_mcp(
            mcp_configs=[
                {"name": "bad", "transport": "stdio", "command": "x"},
            ]
        )
        # Should not raise — disconnect failure is swallowed.
        await init_mcp(a)
