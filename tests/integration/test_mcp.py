"""Integration suite for ``kohakuterrarium.mcp`` — the MCP client layer.

Each test method here drives a *complete* MCP workflow end-to-end the
way the framework's real consumer (``core/agent_mcp.py``) does it:

* ``MCPClientManager`` connects to a **real in-process stdio MCP
  server** — a tiny ``FastMCP`` script written to ``tmp_path`` and
  spawned as a subprocess over the real ``mcp`` SDK stdio transport.
  Faking the transport would hide the ``anyio`` task-binding that the
  cross-task lifecycle (B-mcp-1) is all about, so nothing is faked
  here except the LLM.
* The cross-task reality: ``core/agent_mcp.init_mcp`` calls
  ``manager.connect`` on the agent-build task, while the four meta-tools
  (``mcp_list`` / ``mcp_call`` / ``mcp_connect`` / ``mcp_disconnect``)
  run inside the ``Executor``'s per-tool ``asyncio`` task — a different
  task. The manager's ``_MCPConnection`` owner-task design exists
  precisely so connect-on-one-task / call+disconnect-on-another stays
  clean. The workflow methods exercise that split directly.
* The agent-level path builds a real :class:`Agent` whose
  ``AgentConfig.mcp_servers`` declares the stdio server; ``Agent.start``
  runs ``init_mcp`` and the scripted LLM emits ``[/mcp_call]`` so the
  real server's result rides back into the conversation — exactly the
  production flow. The ONLY seam is the LLM: both ``create_llm_provider``
  import sites are monkeypatched to a :class:`ScriptedLLM`.

No shape asserts: every assertion pins an exact tool list, an exact
tool-call result string, or an observable side effect.
"""

import asyncio
import sys
import tempfile
from pathlib import Path

import pytest

# Importing the builtins.tools package fires every ``@register_builtin``
# decorator — including the four MCP meta-tools in ``mcp/tools.py`` —
# the same way ``bootstrap/tools.py`` populates the catalog at runtime.
import kohakuterrarium.builtins.tools  # noqa: F401
from kohakuterrarium.bootstrap import agent_init as _agent_init_mod
from kohakuterrarium.bootstrap import llm as _bootstrap_llm_mod
from kohakuterrarium.builtins.tool_catalog import get_builtin_tool
from kohakuterrarium.core.agent import Agent
from kohakuterrarium.core.config_types import (
    AgentConfig,
    InputConfig,
    OutputConfig,
    ToolConfigItem,
)
from kohakuterrarium.core.session import Session
from kohakuterrarium.mcp.client import (
    MCPClientManager,
    MCPServerConfig,
    normalize_mcp_transport,
)
from kohakuterrarium.modules.tool.base import ToolContext
from kohakuterrarium.terrarium.creature_host import Creature
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.testing.llm import ScriptedLLM, ScriptEntry
from kohakuterrarium.testing.output import OutputRecorder

# ---------------------------------------------------------------------------
# Real in-process stdio MCP servers — tiny FastMCP scripts spawned as
# subprocesses by the real mcp SDK stdio transport. Two distinct servers
# so the multi-server / selective-disconnect workflow has real targets.
# ---------------------------------------------------------------------------

_GREETER_SERVER = '''\
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("kt-greeter")


@mcp.tool()
def greet(name: str) -> str:
    """Greet someone by name."""
    return f"Hello, {name}!"


@mcp.tool()
def shout(text: str) -> str:
    """Return text uppercased."""
    return text.upper()


@mcp.tool()
def explode(reason: str) -> str:
    """Always raises — exercises the MCP error-result path."""
    raise RuntimeError(f"boom: {reason}")


if __name__ == "__main__":
    mcp.run()
'''

_MATH_SERVER = '''\
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("kt-math")


@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


if __name__ == "__main__":
    mcp.run()
'''


@pytest.fixture
def greeter_script(tmp_path):
    path = tmp_path / "kt_greeter_server.py"
    path.write_text(_GREETER_SERVER, encoding="utf-8")
    return path


@pytest.fixture
def math_script(tmp_path):
    path = tmp_path / "kt_math_server.py"
    path.write_text(_MATH_SERVER, encoding="utf-8")
    return path


def _stdio_config(name: str, script, **kw) -> MCPServerConfig:
    """Build an ``MCPServerConfig`` for a real stdio server subprocess —
    the same dataclass ``core/agent_mcp.init_mcp`` constructs per
    ``mcp_servers`` entry."""
    return MCPServerConfig(
        name=name,
        transport="stdio",
        command=sys.executable,
        args=[str(script)],
        connect_timeout=kw.get("connect_timeout", 45),
    )


@pytest.fixture
def scripted_llm(monkeypatch):
    """Patch BOTH ``create_llm_provider`` import sites to a ScriptedLLM.

    ``bootstrap.agent_init`` imports the symbol directly and
    ``bootstrap.llm`` defines it — patching only one leaves a real
    provider on the other path. The closure lets a test set its script
    before it builds the agent.
    """

    holder: dict[str, list] = {"script": ["OK"]}

    def _fake_create(config, llm_override=None):
        return ScriptedLLM(holder["script"])

    monkeypatch.setattr(_bootstrap_llm_mod, "create_llm_provider", _fake_create)
    monkeypatch.setattr(_agent_init_mod, "create_llm_provider", _fake_create)
    return holder


# ---------------------------------------------------------------------------
# The integration suite.
# ---------------------------------------------------------------------------


class TestMcpIntegration:
    """Each method runs one complete ``mcp/`` workflow end-to-end."""

    @pytest.mark.timeout(60)
    async def test_manager_lifecycle_with_cross_task_disconnect(
        self, greeter_script, math_script
    ):
        """The full ``MCPClientManager`` lifecycle as ``core/agent_mcp.py``
        and the meta-tools drive it:

        connect a real stdio server (on THIS task — the agent-build task)
        -> ``list_servers`` returns the exact discovered tool list
        -> ``call_tool`` invokes a tool and returns its exact result
        -> connect a SECOND real server
        -> disconnect the first **from a different asyncio task** than the
           one that connected it (the executor's per-tool-task reality —
           the ``_MCPConnection`` owner task keeps the transport CM's
           enter+exit on one task so this does not raise ``CancelledError``)
        -> the second server is still callable after the first is gone
        -> ``shutdown`` tears down the rest.

        Mirrors: ``init_mcp`` connects on the build task; ``mcp_call`` /
        ``mcp_disconnect`` run in the ``Executor``'s tool task.
        """
        # --- transport-name normalization: the exact mapping
        # ``core/agent_mcp.py`` relies on before building a config.
        assert normalize_mcp_transport("stdio") == "stdio"
        assert normalize_mcp_transport("STDIO") == "stdio"
        assert normalize_mcp_transport("http") == "sse"
        assert normalize_mcp_transport("sse") == "sse"
        assert normalize_mcp_transport("streamable-http") == "streamable_http"
        assert normalize_mcp_transport("streamablehttp") == "streamable_http"
        assert normalize_mcp_transport("") == "stdio"  # empty defaults to stdio
        with pytest.raises(ValueError, match="Unknown transport"):
            normalize_mcp_transport("carrier-pigeon")

        manager = MCPClientManager()

        # --- input validation happens before any subprocess spawn ------
        # A non-positive connect_timeout is rejected up front.
        with pytest.raises(ValueError, match="connect_timeout must be greater"):
            await manager.connect(
                _stdio_config("bad", greeter_script, connect_timeout=0)
            )
        # A stdio transport with no command is rejected (server enters
        # the registry in "error" status, then the error is raised).
        with pytest.raises(ValueError, match="stdio transport requires 'command'"):
            await manager.connect(
                MCPServerConfig(name="nocommand", transport="stdio", command="")
            )
        assert manager.servers["nocommand"].status == "error"
        # A sse / streamable_http transport with no url is likewise rejected.
        with pytest.raises(ValueError, match="SSE transport requires 'url'"):
            await manager.connect(
                MCPServerConfig(name="nourl", transport="http", url="")
            )
        with pytest.raises(
            ValueError, match="streamable_http transport requires 'url'"
        ):
            await manager.connect(
                MCPServerConfig(name="nourl2", transport="streamable_http", url="")
            )

        # --- connect server #1 on this task (the "agent build" task) ----
        # Use connect_timeout=None to exercise the no-timeout connect path.
        greeter_cfg = MCPServerConfig(
            name="greeter",
            transport="stdio",
            command=sys.executable,
            args=[str(greeter_script)],
            connect_timeout=None,
        )
        greeter_info = await manager.connect(greeter_cfg)
        assert greeter_info.status == "connected"

        # The failed connects above each left an "error"-status entry in
        # the registry (the manager keeps them so the UI can show WHY a
        # connect failed) — but none is in a callable state.
        error_servers = {
            s["name"]: s["status"]
            for s in manager.list_servers()
            if s["status"] == "error"
        }
        assert error_servers == {
            "nocommand": "error",
            "nourl": "error",
            "nourl2": "error",
        }

        # list_servers reports the EXACT tools discovered on the one
        # server that actually connected.
        connected_servers = [
            s for s in manager.list_servers() if s["status"] == "connected"
        ]
        assert [s["name"] for s in connected_servers] == ["greeter"]
        greeter_tools = sorted(t["name"] for t in connected_servers[0]["tools"])
        assert greeter_tools == ["explode", "greet", "shout"]

        # get_server_tools returns the same discovered catalogue; an
        # unknown server name is a hard ValueError.
        assert sorted(t["name"] for t in manager.get_server_tools("greeter")) == [
            "explode",
            "greet",
            "shout",
        ]
        with pytest.raises(ValueError, match="MCP server not found: ghost"):
            manager.get_server_tools("ghost")

        # Re-connecting an already-connected server is a no-op that
        # returns the existing info (no second subprocess spawned).
        again = await manager.connect(_stdio_config("greeter", greeter_script))
        assert again is manager.servers["greeter"]

        # --- call a tool: exact result string round-trips back ----------
        greet_result = await manager.call_tool("greeter", "greet", {"name": "Kohaku"})
        assert greet_result == "Hello, Kohaku!"
        shout_result = await manager.call_tool("greeter", "shout", {"text": "hi"})
        assert shout_result == "HI"

        # A tool that RAISES server-side comes back as a result with
        # ``isError`` set — ``call_tool`` prefixes it with ``[MCP Error]``
        # and returns it as a string (the agent sees the failure, the
        # manager does not raise).
        err_result = await manager.call_tool("greeter", "explode", {"reason": "kaboom"})
        assert err_result.startswith("[MCP Error]")
        assert "boom: kaboom" in err_result

        # Unknown tool on a connected server is rejected before any I/O.
        with pytest.raises(ValueError, match="not found on server 'greeter'"):
            await manager.call_tool("greeter", "nonexistent", {})

        # --- connect server #2 ------------------------------------------
        math_info = await manager.connect(_stdio_config("math", math_script))
        assert math_info.status == "connected"
        assert sorted(
            s["name"] for s in manager.list_servers() if s["status"] == "connected"
        ) == ["greeter", "math"]

        # --- disconnect server #1 from a DIFFERENT task -----------------
        # This is the load-bearing cross-task case: connect() ran on this
        # task, disconnect() runs inside a child task — the structural
        # equivalent of the Executor invoking ``mcp_disconnect`` on its
        # own per-tool task. anyio cancel scopes are task-bound; the
        # owner-task design is what keeps this from raising.
        disconnect_box: dict[str, object] = {}

        async def _disconnect_child() -> None:
            disconnect_box["result"] = await manager.disconnect("greeter")

        await asyncio.create_task(_disconnect_child())
        assert disconnect_box["result"] is True
        assert "greeter" not in manager.servers
        assert "greeter" not in manager._sessions
        # Disconnecting an already-gone server is a clean no-op False.
        assert await manager.disconnect("greeter") is False

        # --- server #2 still fully functional after #1 is torn down -----
        add_result = await manager.call_tool("math", "add", {"a": 2, "b": 40})
        assert add_result == "42"
        # Calling the now-disconnected server raises, not crashes.
        with pytest.raises(ValueError, match="not connected: greeter"):
            await manager.call_tool("greeter", "greet", {"name": "x"})

        # --- shutdown tears down everything that's left -----------------
        await manager.shutdown()
        assert manager.servers == {}
        assert manager._sessions == {}
        assert manager._connections == {}

    @pytest.mark.timeout(60)
    async def test_runtime_connect_and_call_via_meta_tools(
        self, greeter_script, math_script
    ):
        """The four meta-tools driven through their real ``_execute``
        path with a real manager attached to a stand-in agent context —
        the way the ``Executor`` invokes them:

        ``mcp_list`` on an empty manager -> ``mcp_connect`` spawns the
        real stdio server -> ``mcp_list`` now renders the exact tool
        catalog -> ``mcp_call`` runs a tool and returns its exact output
        -> ``mcp_disconnect`` removes it -> ``mcp_list`` is empty again.

        Mirrors: ``mcp/tools.py`` meta-tools reading ``context.agent.
        _mcp_manager`` — the agent never sees MCP tools natively, it
        reaches them only through these four indirections.
        """
        manager = MCPClientManager()

        # The meta-tools read ``context.agent._mcp_manager`` — build the
        # real ToolContext the Executor's _build_tool_context produces,
        # carrying an agent stand-in that owns the manager.
        class _AgentStub:
            _mcp_manager = manager

        ctx = ToolContext(
            agent_name="mcp-test",
            session=Session(key="mcp-test"),
            working_dir=Path.cwd(),
            agent=_AgentStub(),
        )

        mcp_list = get_builtin_tool("mcp_list")
        mcp_connect = get_builtin_tool("mcp_connect")
        mcp_call = get_builtin_tool("mcp_call")
        mcp_disconnect = get_builtin_tool("mcp_disconnect")
        assert None not in (mcp_list, mcp_connect, mcp_call, mcp_disconnect)

        # --- no MCP manager on the agent -> every meta-tool returns a
        # clean tool error, never an exception. This is the path taken
        # when a creature config declared no ``mcp_servers``.
        class _BareAgent:
            pass

        bare_ctx = ToolContext(
            agent_name="bare",
            session=Session(key="bare"),
            working_dir=Path.cwd(),
            agent=_BareAgent(),
        )
        for tool in (mcp_list, mcp_connect, mcp_call, mcp_disconnect):
            res = await tool._execute({"name": "x", "server": "x"}, context=bare_ctx)
            assert res.output == ""
            assert "MCP is not available" in res.error

        # --- mcp_connect argument validation (before any spawn) ---------
        no_name = await mcp_connect._execute({}, context=ctx)
        assert no_name.error == "Missing 'name' argument. Give this server a name."
        no_target = await mcp_connect._execute({"name": "x"}, context=ctx)
        assert "Provide either 'command'" in no_target.error
        bad_timeout = await mcp_connect._execute(
            {"name": "x", "command": "echo", "connect_timeout": "soon"},
            context=ctx,
        )
        assert bad_timeout.error == "'connect_timeout' must be a number if provided."

        # --- mcp_list before any connection -----------------------------
        empty = await mcp_list._execute({}, context=ctx)
        assert empty.error is None
        assert empty.output == "No MCP servers connected. Use mcp_connect to add one."

        # mcp_list on an unknown server name -> ValueError surfaced as a
        # tool error (manager has nothing connected yet).
        # (after a connection exists this is a "not found" error; here
        # the empty-servers branch wins, so connect first below.)

        # --- mcp_connect spawns the real stdio server -------------------
        connected = await mcp_connect._execute(
            {
                "name": "greeter",
                "transport": "stdio",
                "command": sys.executable,
                "args": [str(greeter_script)],
                "connect_timeout": 45,
            },
            context=ctx,
        )
        assert connected.error is None
        # The summary names the real tools discovered from the server.
        assert connected.output == (
            "Connected to greeter (3 tools available): greet, shout, explode"
        )

        # --- mcp_list (detailed view) renders the exact tool catalog ----
        detail = await mcp_list._execute({"server": "greeter"}, context=ctx)
        assert detail.error is None
        detail_lines = detail.output.splitlines()
        assert detail_lines[0] == "MCP server: greeter"
        assert detail_lines[1] == "Tools (3):"
        assert "  greet" in detail_lines
        assert "  shout" in detail_lines
        assert "  explode" in detail_lines

        # --- mcp_call runs a tool: exact result is the tool output ------
        called = await mcp_call._execute(
            {"server": "greeter", "tool": "greet", "args": {"name": "Terrarium"}},
            context=ctx,
        )
        assert called.error is None
        assert called.output == "Hello, Terrarium!"

        # A tool that raises server-side: ``mcp_call`` returns the
        # ``[MCP Error]``-prefixed string as its output (not a tool
        # error) — the agent sees the failure text in the result.
        called_err = await mcp_call._execute(
            {"server": "greeter", "tool": "explode", "args": {"reason": "x"}},
            context=ctx,
        )
        assert called_err.error is None
        assert called_err.output.startswith("[MCP Error]")
        assert "boom: x" in called_err.output

        # mcp_call also accepts a JSON-string ``args`` (LLM-emitted form).
        called_str_args = await mcp_call._execute(
            {"server": "greeter", "tool": "shout", "args": '{"text": "mcp"}'},
            context=ctx,
        )
        assert called_str_args.error is None
        assert called_str_args.output == "MCP"

        # A bad tool name surfaces as a tool error, not an exception.
        bad = await mcp_call._execute(
            {"server": "greeter", "tool": "ghost", "args": {}}, context=ctx
        )
        assert bad.output == ""
        assert "not found on server 'greeter'" in bad.error

        # mcp_call argument validation: missing server / tool, bad JSON.
        miss_server = await mcp_call._execute({"tool": "greet"}, context=ctx)
        assert "Missing 'server'" in miss_server.error
        miss_tool = await mcp_call._execute({"server": "greeter"}, context=ctx)
        assert "Missing 'tool'" in miss_tool.error
        bad_json = await mcp_call._execute(
            {"server": "greeter", "tool": "greet", "args": "{not json"},
            context=ctx,
        )
        assert "Invalid JSON in 'args'" in bad_json.error
        # Calling a tool on a server name that isn't connected -> tool error.
        no_server = await mcp_call._execute(
            {"server": "ghostsrv", "tool": "greet", "args": {}}, context=ctx
        )
        assert "not connected: ghostsrv" in no_server.error

        # mcp_list detailed view on a server that is NOT connected -> the
        # get_server_tools ValueError surfaces as a tool error.
        bad_detail = await mcp_list._execute({"server": "ghostsrv"}, context=ctx)
        assert bad_detail.output == ""
        assert "MCP server not found: ghostsrv" in bad_detail.error

        # --- connect a SECOND server, then mcp_list's OVERVIEW render ---
        # (the multi-server, no-``server``-arg branch the agent sees when
        # it asks "what's connected" before picking one.)
        await mcp_connect._execute(
            {
                "name": "math",
                "transport": "stdio",
                "command": sys.executable,
                "args": [str(math_script)],
                "connect_timeout": 45,
            },
            context=ctx,
        )
        overview = await mcp_list._execute({}, context=ctx)
        assert overview.error is None
        ov_lines = overview.output.splitlines()
        assert ov_lines[0] == "Connected MCP servers:"
        # Both servers + their tool inventories appear in the overview.
        assert any("greeter (stdio, connected, 3 tools)" in ln for ln in ov_lines)
        assert any("math (stdio, connected, 1 tools)" in ln for ln in ov_lines)
        assert any("- greet" in ln for ln in ov_lines)
        assert any("- add" in ln for ln in ov_lines)
        # math is still callable alongside greeter.
        math_call = await mcp_call._execute(
            {"server": "math", "tool": "add", "args": {"a": 1, "b": 2}},
            context=ctx,
        )
        assert math_call.output == "3"
        await mcp_disconnect._execute({"server": "math"}, context=ctx)

        # mcp_disconnect with neither 'server' nor 'name' -> tool error.
        no_arg = await mcp_disconnect._execute({}, context=ctx)
        assert no_arg.error == "Missing 'server' or 'name' argument."
        # The ``name`` alias works as a synonym for ``server``.
        alias_disc = await mcp_disconnect._execute({"name": "ghostsrv"}, context=ctx)
        assert alias_disc.error == "Server not found: ghostsrv"

        # --- mcp_disconnect removes the server --------------------------
        gone = await mcp_disconnect._execute({"server": "greeter"}, context=ctx)
        assert gone.error is None
        assert gone.output == "Disconnected from greeter"

        after = await mcp_list._execute({}, context=ctx)
        assert after.output == "No MCP servers connected. Use mcp_connect to add one."

        # mcp_disconnect on a missing server is a clean tool error.
        missing = await mcp_disconnect._execute({"server": "greeter"}, context=ctx)
        assert missing.output == ""
        assert missing.error == "Server not found: greeter"

        await manager.shutdown()

    @pytest.mark.timeout(60)
    async def test_agent_turn_calls_config_declared_mcp_server(self, scripted_llm):
        """The production agent-level path: an ``AgentConfig`` declares an
        ``mcp_servers`` entry; ``Agent.start`` runs ``core/agent_mcp.
        init_mcp`` which connects the real stdio server on the agent-build
        task; the scripted LLM emits ``[/mcp_call]`` so the ``Executor``
        invokes the ``mcp_call`` meta-tool on its own per-tool task; the
        real MCP server's result rides back into the conversation and the
        follow-up turn sees it.

        This is the whole cross-task lifecycle that ``mcp/`` exists for —
        connect on one task, call on another — exercised through a real
        :class:`Agent` hosted in a real :class:`Terrarium` engine, the
        way every ``kt run`` / HTTP / WS chat path runs it.
        """
        # The greeter script lives next to the agent; write it directly
        # (this test does not take the tmp_path-script fixtures because
        # it needs the path before the config is built).
        workdir = Path(tempfile.mkdtemp(prefix="kt-mcp-agent-"))
        server_path = workdir / "kt_greeter_server.py"
        server_path.write_text(_GREETER_SERVER, encoding="utf-8")

        # The LLM script: round 1 emits a real mcp_call block (bracket
        # format — ``@@key=value`` per line, ``args`` as a JSON string);
        # round 2 fires once the tool result is fed back.
        scripted_llm["script"] = [
            ScriptEntry(
                "[/mcp_call]\n"
                "@@server=greeter\n"
                "@@tool=greet\n"
                '@@args={"name": "Kohaku"}\n'
                "[mcp_call/]",
                match="say hello",
            ),
            ScriptEntry("the server greeted us", match="Hello, Kohaku!"),
        ]

        cfg = AgentConfig(
            name="mcp-creature",
            llm_profile="openai/gpt-4-test",
            model="gpt-4",
            provider="openai",
            api_key_env="",
            system_prompt="You are a test agent with MCP access.",
            include_tools_in_prompt=True,
            include_hints_in_prompt=False,
            tool_format="bracket",
            agent_path=workdir,
            input=InputConfig(type="none"),
            output=OutputConfig(type="stdout"),
            tools=[
                ToolConfigItem(name="mcp_call", type="builtin"),
                ToolConfigItem(name="mcp_list", type="builtin"),
            ],
            mcp_servers=[
                {
                    "name": "greeter",
                    "transport": "stdio",
                    "command": sys.executable,
                    "args": [str(server_path)],
                    "connect_timeout": 45,
                }
            ],
        )

        agent = Agent(cfg)
        recorder = OutputRecorder()
        agent.output_router.default_output = recorder
        creature = Creature(
            creature_id="mcp-creature", name="mcp-creature", agent=agent
        )

        async with Terrarium() as engine:
            await engine.add_creature(creature)

            # Agent.start ran init_mcp: the real stdio server is connected
            # and the manager is attached to the live agent.
            assert agent._mcp_manager is not None
            servers = agent._mcp_manager.list_servers()
            assert [s["name"] for s in servers] == ["greeter"]
            assert servers[0]["status"] == "connected"
            assert sorted(t["name"] for t in servers[0]["tools"]) == [
                "explode",
                "greet",
                "shout",
            ]
            # init_mcp injected the discovered tools into the live system
            # prompt (the conversation's system message, via
            # ``Agent.update_system_prompt``).
            sys_prompt = agent.get_system_prompt()
            assert "## Available MCP Tools" in sys_prompt
            assert "**greet**" in sys_prompt

            # --- drive a turn: LLM emits [/mcp_call] -> Executor runs it
            chunks: list[str] = []
            async for chunk in creature.chat("say hello to Kohaku"):
                chunks.append(chunk)
            final = "".join(chunks)

            # The follow-up turn fired off the real MCP result.
            assert "the server greeted us" in final
            # The controller looped exactly twice: call, then wrap-up.
            assert agent.llm.call_count == 2
            # The real MCP server's exact output landed in the conversation
            # as the tool-result message that drove round 2.
            convo_text = " ".join(
                m.get_text_content()
                for m in agent.controller.conversation.get_messages()
            )
            assert "Hello, Kohaku!" in convo_text

        # Engine __aexit__ stopped the creature; the manager's servers
        # were connected for the whole run.
        assert creature.is_running is False
