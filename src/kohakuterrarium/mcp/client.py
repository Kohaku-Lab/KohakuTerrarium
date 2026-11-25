"""MCP client manager — manages connections to multiple MCP servers."""

import asyncio
from dataclasses import dataclass, field
from typing import Any

from kohakuterrarium.errors import MCPConfigError, MCPError
from kohakuterrarium.mcp.diagnose import (
    HandshakeObserver,
    display_url,
    explain_failure,
    explain_timeout,
    leaf_exceptions,
)
from kohakuterrarium.mcp.transports import normalize_mcp_transport
from kohakuterrarium.utils.env_interp import interpolate_env_vars, unset_env_vars
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

DEFAULT_MCP_CONNECT_TIMEOUT = 20.0


@dataclass
class MCPServerConfig:
    """Configuration for a single MCP server."""

    name: str
    transport: str = "stdio"  # stdio / sse / streamable_http
    command: str = ""  # For stdio: executable command
    args: list[str] = field(default_factory=list)  # For stdio: command arguments
    env: dict[str, str] = field(default_factory=dict)  # For stdio: env vars
    url: str = ""  # For sse / streamable_http: server URL
    headers: dict[str, str] = field(default_factory=dict)  # For sse / streamable_http
    connect_timeout: float | None = None  # Optional per-server connection timeout

    @classmethod
    def from_entry(
        cls, entry: dict[str, Any], *, expand_env: bool = True
    ) -> "MCPServerConfig":
        """Build a config from a registry or ``mcp_servers`` entry.

        Keys that are not connection fields are ignored and ``entry`` is not
        changed. With ``expand_env``, ``${VAR}`` / ``${VAR:default}`` in
        command, args, env, url and headers are expanded; an unset variable
        without a default raises :class:`MCPConfigError`.
        """
        name = str(entry.get("name") or "")
        values = {key: entry.get(key) for key in _EXPANDED_FIELDS}
        if expand_env:
            for key in _EXPANDED_FIELDS:
                _require_env_vars(name, key, values[key])
            values = interpolate_env_vars(values)
        return cls(
            name=name,
            transport=str(entry.get("transport") or "stdio"),
            command=str(values["command"] or ""),
            args=[str(a) for a in _shaped(name, "args", values["args"], list)],
            env={
                str(k): str(v)
                for k, v in _shaped(name, "env", values["env"], dict).items()
            },
            url=str(values["url"] or ""),
            headers={
                str(k): str(v)
                for k, v in _shaped(name, "headers", values["headers"], dict).items()
            },
            connect_timeout=_entry_timeout(name, entry.get("connect_timeout")),
        )


_EXPANDED_FIELDS = ("command", "args", "env", "url", "headers")


def _require_env_vars(server: str, key: str, value: Any) -> None:
    missing = unset_env_vars(value)
    if not missing:
        return
    if len(missing) == 1:
        subject = f"environment variable {missing[0]} is not set"
    else:
        subject = f"environment variables {', '.join(missing)} are not set"
    raise MCPConfigError(f"{subject} (used in {key} of server {server})")


def _shaped(server: str, key: str, value: Any, kind: type) -> Any:
    if value is None:
        return kind()
    if not isinstance(value, kind):
        noun = "a list" if kind is list else "a mapping"
        raise MCPConfigError(f"{key} of server {server} must be {noun}")
    return value


def _entry_timeout(server: str, value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as e:
        raise MCPConfigError(
            f"connect_timeout of server {server} must be a number, got {value!r}"
        ) from e


@dataclass
class MCPServerInfo:
    """Runtime info about a connected MCP server."""

    config: MCPServerConfig
    tools: list[dict[str, Any]] = field(default_factory=list)
    status: str = "disconnected"  # "connected", "disconnected", "error"
    error: str = ""
    instructions: str = ""  # Server instructions from the initialize result


def _observer_kind(transport: str) -> str:
    try:
        return normalize_mcp_transport(transport)
    except MCPError:
        return "stdio"


def _kinds(exc: BaseException | None) -> list[str]:
    """Exception type names of ``exc``'s root causes, safe to log."""
    return [type(e).__name__ for e in leaf_exceptions(exc)] if exc else []


async def _cancel_and_wait(task: asyncio.Task[Any]) -> None:
    """Cancel ``task`` and wait for it to settle without re-raising its outcome."""
    task.cancel()
    await asyncio.wait({task})
    if not task.cancelled():
        task.exception()


class _MCPConnection:
    """Owns one MCP server's transport + session lifecycle on a single
    dedicated asyncio task.

    The MCP stdio / sse / streamable-http transports and ``ClientSession``
    are built on ``anyio`` task groups, whose cancel scopes are
    *task-bound*: the context-manager ``__aenter__`` and ``__aexit__``
    MUST run on the same task. The manager's ``connect()`` runs on one
    task (agent build) and ``disconnect()`` on another (the executor's
    tool task) — entering the CM on one and exiting on the other raises
    ``CancelledError`` (BUG B-mcp-1). This owner task enters the CMs,
    signals readiness, waits for a close request, then exits the CMs —
    every CM operation stays on ``self._task``.
    """

    def __init__(self) -> None:
        self.session: Any = None
        self.transports: tuple[Any, Any] | None = None
        self.instructions: str = ""
        self.error: BaseException | None = None
        self._ready = asyncio.Event()
        self._close = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def open(self, name: str, ctx: Any, client_session_cls: Any) -> None:
        """Start the owner task and block until the connection is ready
        (or its open failed). Re-raises the open failure on the caller."""
        self._task = asyncio.create_task(self._run(name, ctx, client_session_cls))
        await self._ready.wait()
        if self.error is not None:
            # The owner task already unwound anything it entered.
            raise self.error

    async def _run(self, name: str, ctx: Any, client_session_cls: Any) -> None:
        session: Any = None
        entered_ctx = False
        entered_session = False
        try:
            entered = await ctx.__aenter__()
            entered_ctx = True
            try:
                read_stream = entered[0]
                write_stream = entered[1]
            except (IndexError, TypeError) as e:
                raise MCPConfigError(
                    f"MCP server {name}: transport did not yield read/write streams"
                ) from e
            session = client_session_cls(read_stream, write_stream)
            await session.__aenter__()
            entered_session = True
            init_result = await session.initialize()
            self.instructions = getattr(init_result, "instructions", None) or ""
            self.session = session
            self.transports = (read_stream, write_stream)
        except BaseException as e:  # noqa: BLE001 — surfaced via self.error
            self.error = e
            try:
                await self._unwind(
                    e,
                    session if entered_session else None,
                    ctx if entered_ctx else None,
                )
            finally:
                self._ready.set()
            return

        # Open succeeded — let the connect() caller proceed, then hold
        # the connection open until disconnect requests teardown.
        self._ready.set()
        await self._close.wait()
        try:
            await session.__aexit__(None, None, None)
        except Exception as e:
            logger.debug("Failed to close MCP session", error=str(e), exc_info=True)
        try:
            await ctx.__aexit__(None, None, None)
        except Exception as e:
            logger.debug(
                "Failed to exit MCP transport context", error=str(e), exc_info=True
            )

    async def _unwind(self, error: BaseException, session: Any, ctx: Any) -> None:
        """Exit the session and transport this task entered before ``error``."""
        if session is not None:
            try:
                await session.__aexit__(type(error), error, error.__traceback__)
            except BaseException as e:  # noqa: BLE001 — teardown of a failed open
                logger.debug("MCP session unwind after open failure", cause=_kinds(e))
        if ctx is not None:
            try:
                await ctx.__aexit__(type(error), error, error.__traceback__)
            except BaseException as e:  # noqa: BLE001 — teardown of a failed open
                logger.debug("MCP transport unwind after open failure", cause=_kinds(e))

    async def aclose(self) -> None:
        """Request teardown and wait for the owner task to finish it.

        Safe to call regardless of which task invokes it — the actual
        CM exit happens on ``self._task``. An open still in progress is
        cancelled. Idempotent.
        """
        self._close.set()
        task = self._task
        if task is None:
            return
        if not self._ready.is_set():
            task.cancel()
        await asyncio.wait({task})
        if not task.cancelled() and task.exception() is not None:
            logger.debug(
                "MCP connection owner task error", cause=_kinds(task.exception())
            )


class MCPClientManager:
    """Manages connections to multiple MCP servers.

    Each server gets its own ClientSession. Tools are discovered on connect
    and cached. The manager routes tool calls to the correct session.
    """

    def __init__(self) -> None:
        self._servers: dict[str, MCPServerInfo] = {}
        self._sessions: dict[str, Any] = {}  # name -> ClientSession
        self._transports: dict[str, Any] = {}  # name -> (read, write)
        self._stdio_contexts: dict[str, Any] = {}  # name -> transport context manager
        self._connections: dict[str, _MCPConnection] = {}  # name -> owner-task wrapper
        self._lock = asyncio.Lock()

    @property
    def servers(self) -> dict[str, MCPServerInfo]:
        return self._servers

    async def connect(self, config: MCPServerConfig) -> MCPServerInfo:
        """Connect to an MCP server and discover its tools.

        An HTTP error status or a refused connection during the handshake
        ends the attempt at once. Failures raise a typed
        :class:`~kohakuterrarium.errors.MCPError` naming the root cause
        (``MCPTimeoutError`` after ``connect_timeout``). Raises ImportError
        if the mcp package is not installed.
        """
        timeout = config.connect_timeout
        if timeout is not None and timeout <= 0:
            raise MCPConfigError("connect_timeout must be greater than 0")
        if config.name in self._sessions:
            logger.warning("MCP server already connected", server=config.name)
            return self._servers[config.name]

        observer = HandshakeObserver(_observer_kind(config.transport))
        connect_task = asyncio.create_task(self._connect_impl(config, observer))
        failed_task = asyncio.create_task(observer.failed.wait())
        try:
            done, _ = await asyncio.wait(
                {connect_task, failed_task},
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
        except asyncio.CancelledError:
            await _cancel_and_wait(connect_task)
            raise
        finally:
            failed_task.cancel()

        if connect_task in done:
            try:
                info = connect_task.result()
            except Exception as e:
                error = explain_failure(config, e, observer, timeout=timeout)
                cause: BaseException = e
            else:
                observer.disarm()
                return info
        else:
            await _cancel_and_wait(connect_task)
            if observer.failure is not None:
                cause = observer.failure.error or RuntimeError("handshake failed")
                error = explain_failure(config, cause, observer, timeout=timeout)
            else:
                cause = asyncio.TimeoutError()
                error = explain_timeout(config, observer, float(timeout or 0))
        observer.disarm()
        self._record_failure(config, error, cause)
        if error is cause:
            raise error
        raise error from cause

    def _record_failure(
        self, config: MCPServerConfig, error: BaseException, cause: BaseException
    ) -> None:
        info = self._servers.get(config.name)
        if info is None:
            info = MCPServerInfo(config=config)
            self._servers[config.name] = info
        info.status = "error"
        info.error = str(error)
        logger.warning(
            "MCP connect failed",
            server=config.name,
            transport=config.transport,
            url=display_url(config.url),
            status=getattr(error, "status", None),
            error=str(error),
        )
        logger.debug(
            "MCP connect failure causes", server=config.name, cause=_kinds(cause)
        )

    async def _connect_impl(
        self, config: MCPServerConfig, observer: HandshakeObserver
    ) -> MCPServerInfo:
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        name = config.name
        info = MCPServerInfo(config=config, status="connecting")
        self._servers[name] = info

        try:
            transport = normalize_mcp_transport(config.transport)
            if transport == "stdio":
                if not config.command:
                    raise MCPConfigError(
                        f"MCP server {name}: stdio transport requires 'command'"
                    )

                params = StdioServerParameters(
                    command=config.command,
                    args=config.args,
                    env=config.env if config.env else None,
                )
                ctx = stdio_client(params)
            elif transport == "sse":
                from mcp.client.sse import sse_client

                if not config.url:
                    raise MCPConfigError(
                        f"MCP server {name}: SSE transport requires 'url'"
                    )
                ctx = sse_client(
                    config.url,
                    headers=config.headers or None,
                    httpx_client_factory=observer.client_factory,
                )
            else:
                from mcp.client.streamable_http import streamablehttp_client

                if not config.url:
                    raise MCPConfigError(
                        f"MCP server {name}: streamable_http transport requires 'url'"
                    )
                ctx = streamablehttp_client(
                    config.url,
                    headers=config.headers or None,
                    httpx_client_factory=observer.client_factory,
                )

            session = await self._open_transport_session(name, ctx, ClientSession)
            info.instructions = self._connections[name].instructions

            tools_response = await session.list_tools()
            info.tools = [
                {
                    "name": t.name,
                    "description": t.description or "",
                    "input_schema": t.inputSchema if hasattr(t, "inputSchema") else {},
                }
                for t in tools_response.tools
            ]
            info.status = "connected"

            logger.info(
                "MCP server connected",
                server=name,
                transport=config.transport,
                url=display_url(config.url),
                tools=len(info.tools),
            )
            return info
        except BaseException:
            info.status = "error"
            await self._cleanup_connection(name, remove_server=False)
            raise

    async def _open_transport_session(
        self, name: str, ctx: Any, client_session_cls: Any
    ) -> Any:
        conn = _MCPConnection()
        # Register before opening so a cancelled / failed connect can
        # still find the owner task to tear it down.
        self._connections[name] = conn
        try:
            await conn.open(name, ctx, client_session_cls)
        except BaseException:
            self._connections.pop(name, None)
            await conn.aclose()
            raise

        self._stdio_contexts[name] = ctx
        self._sessions[name] = conn.session
        self._transports[name] = conn.transports
        return conn.session

    async def _cleanup_connection(self, name: str, *, remove_server: bool) -> None:
        conn = self._connections.pop(name, None)
        if conn is not None:
            # aclose() drives the CM __aexit__ on the owner task — not
            # on whatever task called disconnect() — so anyio's
            # task-bound cancel scopes stay on a single task.
            await conn.aclose()

        self._sessions.pop(name, None)
        self._stdio_contexts.pop(name, None)
        self._transports.pop(name, None)

        if remove_server:
            info = self._servers.pop(name, None)
            if info is not None:
                info.status = "disconnected"

    async def disconnect(self, name: str) -> bool:
        """Disconnect from an MCP server."""
        if name not in self._servers:
            return False

        await self._cleanup_connection(name, remove_server=True)
        logger.info("MCP server disconnected", server=name)
        return True

    async def call_tool_result(
        self, server_name: str, tool_name: str, args: dict[str, Any]
    ) -> Any:
        """Call a tool on a specific MCP server and return the raw ``CallToolResult``.

        Raises ValueError when the server is not connected or does not list
        the tool.
        """
        session = self._sessions.get(server_name)
        if not session:
            raise ValueError(f"MCP server not connected: {server_name}")

        info = self._servers.get(server_name)
        if info:
            tool_names = [t["name"] for t in info.tools]
            if tool_name not in tool_names:
                raise ValueError(
                    f"Tool '{tool_name}' not found on server '{server_name}'. "
                    f"Available: {', '.join(tool_names)}"
                )

        return await session.call_tool(tool_name, arguments=args)

    async def call_tool(
        self, server_name: str, tool_name: str, args: dict[str, Any]
    ) -> str:
        """Call a tool on a specific MCP server.

        Returns the tool result as a string.
        """
        result = await self.call_tool_result(server_name, tool_name, args)

        parts = []
        for content in result.content:
            if hasattr(content, "text"):
                parts.append(content.text)
            elif hasattr(content, "data"):
                parts.append(f"[binary data: {len(content.data)} bytes]")
            else:
                parts.append(str(content))

        output = "\n".join(parts) if parts else "(no output)"

        if result.isError:
            return f"[MCP Error] {output}"

        return output

    def list_servers(self) -> list[dict[str, Any]]:
        """List all servers with their tools."""
        result = []
        for name, info in self._servers.items():
            result.append(
                {
                    "name": name,
                    "transport": info.config.transport,
                    "status": info.status,
                    "error": info.error,
                    "tools": info.tools,
                }
            )
        return result

    def get_server_tools(self, server_name: str) -> list[dict[str, Any]]:
        """Get detailed tool info for a specific server."""
        info = self._servers.get(server_name)
        if not info:
            raise ValueError(f"MCP server not found: {server_name}")
        return info.tools

    async def shutdown(self) -> None:
        """Disconnect all servers."""
        names = list(self._servers.keys())
        for name in names:
            try:
                await self.disconnect(name)
            except Exception as e:
                logger.warning(
                    "Error disconnecting MCP server", server=name, error=str(e)
                )
