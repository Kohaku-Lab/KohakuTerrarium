"""Authenticated Streamable HTTP adapter for KT tools and local delegation."""

import copy
import json
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, ImageContent, TextContent, Tool, ToolAnnotations
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.routing import Route

from kohakuterrarium.api.auth.mcp_secret import MCPSecretPath
from kohakuterrarium.api.mcp_delegation import call_delegation, delegation_tools
from kohakuterrarium.core.backgroundify import PromotionResult
from kohakuterrarium.llm.message import ImagePart
from kohakuterrarium.llm.artifact_resolve import resolve_artifact_url
from kohakuterrarium.mcp_server.config import GlobalToolsConfig, MCPToolsConfig
from kohakuterrarium.mcp_server.runtime import (
    JobOperationResult,
    ToolCatalog,
    ToolRuntime,
)
from kohakuterrarium.mcp_server.workspaces import WorkspacePool, WorkspaceRegistry

_JOB_DESCRIPTIONS = {
    "job_status": "Read a retained job, or list this instance's jobs when job_id is omitted.",
    "job_wait": "Wait up to timeout seconds for a job; timeout/disconnect does not cancel execution.",
    "job_cancel": (
        "Cancel a running job in this instance. A Creature delegation uses KT stop: "
        "its triggers and all its managed tools/subagents stop, including work from earlier turns. "
        "Continue explicitly with delegate and its session_id. Completed jobs are unchanged."
    ),
    "job_promote": "Release a foreground call into the background using its existing job ID; never reruns it.",
}


def _tool_list(runtime: ToolRuntime) -> list[Tool]:
    tools = []
    for schema in runtime.schemas():
        params = copy.deepcopy(schema.parameters)
        params["additionalProperties"] = False
        bg = params.get("properties", {}).get("run_in_background")
        if bg is not None:
            bg["description"] = (
                "Return a job_id immediately. Retrieve results with job_status or job_wait; no automatic reply."
            )
        implementation = runtime.registry.get_tool(schema.name)
        tools.append(
            Tool(
                name=schema.name,
                description=implementation.get_full_documentation(),
                inputSchema=params,
                annotations=ToolAnnotations(
                    readOnlyHint=schema.name in {"read", "glob", "grep", "tree"},
                    openWorldHint=True,
                ),
            )
        )
    for name, description in _JOB_DESCRIPTIONS.items():
        params = {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "additionalProperties": False,
        }
        if name != "job_status":
            params["required"] = ["job_id"]
        if name == "job_wait":
            params["properties"]["timeout"] = {
                "type": "number",
                "minimum": 0,
                "maximum": 60,
                "default": 10,
            }
        tools.append(
            Tool(
                name=name,
                description=description,
                inputSchema=params,
                annotations=ToolAnnotations(
                    readOnlyHint=name in {"job_status", "job_wait"}, openWorldHint=False
                ),
            )
        )
    if runtime.config.delegation:
        tools.extend(delegation_tools())
    return tools


def _reply(data: dict, *, result=None, is_error: bool | None = None) -> CallToolResult:
    content = [
        TextContent(type="text", text=json.dumps(data, ensure_ascii=False, default=str))
    ]
    if result is not None and isinstance(result.output, list):
        for part in result.output:
            if isinstance(part, ImagePart):
                url = resolve_artifact_url(part.url)
                header, separator, encoded = url.partition(";base64,")
                if separator:
                    content.append(
                        ImageContent(type="image", mimeType=header[5:], data=encoded)
                    )
    if is_error is None:
        is_error = bool(data.get("error")) or data.get("exit_code") not in (None, 0)
    return CallToolResult(content=content, structuredContent=data, isError=is_error)


def _job_reply(result: JobOperationResult) -> CallToolResult:
    """A retained job's failure is data, not a failure to query that job."""
    return _reply(result.data, is_error=result.outcome != "ok")


class _HTTPTransport:
    def __init__(self, manager):
        self.manager = manager

    async def __call__(self, scope, receive, send):
        await self.manager.handle_request(scope, receive, send)


def create_app(
    config: MCPToolsConfig | GlobalToolsConfig,
    *,
    secret: str,
    port: int = 8765,
    public_origin: str = "",
    json_response: bool = True,
    llm_factory=None,
    registry: WorkspaceRegistry | None = None,
    base_dir: Path | None = None,
) -> Starlette:
    """Serve registered workspaces or one explicitly bound workspace.

    Registered mode takes GlobalToolsConfig and a registry. Fixed mode takes
    MCPToolsConfig without a registry or base_dir.

    The hosting server MUST disable access logs, since those run outside ASGI.
    Bind to loopback and publish via the configured HTTPS tunnel.
    """
    if registry is None:
        if not isinstance(config, MCPToolsConfig):
            raise ValueError(
                "Without a registry, config must bind a workspace using MCPToolsConfig"
            )
        if base_dir is not None:
            raise ValueError("base_dir is only supported with a registry")
    elif isinstance(config, MCPToolsConfig):
        raise ValueError("With a registry, use GlobalToolsConfig without a workspace")
    if not 1 <= port <= 65535:
        raise ValueError("Invalid listen port")
    hosts = [f"127.0.0.1:{port}", f"localhost:{port}"]
    origins = [f"http://127.0.0.1:{port}", f"http://localhost:{port}"]
    if public_origin:
        parsed = urlsplit(public_origin)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "Public origin must be an HTTPS origin without path or credentials"
            )
        if parsed.port in (None, 443):
            host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
            hosts.extend([host, f"{host}:443"])
            origins.extend([f"https://{host}", f"https://{host}:443"])
        else:
            hosts.append(parsed.netloc.lower())
            origins.append(public_origin)
    # Validate before constructing the runtime or loading configured modules.
    MCPSecretPath(None, secret=secret)
    pool = (
        WorkspacePool(config, registry, llm_factory=llm_factory)
        if registry is not None
        else None
    )
    owner = pool if pool is not None else ToolRuntime(config, llm_factory=llm_factory)
    catalog = ToolCatalog(config, base_dir or Path.cwd()) if pool is not None else owner
    server = Server(
        config.name,
        instructions=(
            f"KT instance_id: {owner.instance_id}\n"
            + (
                "Registered workspace mode. No default workspace is selected. "
                "Call workspaces to discover names. Every workspace tool requires workspace_id. "
                "Unknown or expired job/session IDs must never be retried in another workspace. "
                if pool is not None
                else (
                    f"Fixed workspace mode. Default working directory: {config.workspace}. "
                    "Do not pass workspace_id to tools. No workspaces discovery tool is available. "
                )
            )
            + (
                "KT tools and locally registered delegation targets. Creatures may run autonomous triggers. "
                if config.delegation
                else "KT tools only: no local LLM or autonomous turns. "
            )
            + "Workspace is the default directory, "
            "not a sandbox. Read existing files before modifying them; stale reads require rereading. "
            "Jobs and file-read state belong to each workspace runtime, shared across its authenticated clients. "
            "Use run_in_background for long bash/python work, or job_promote for an already running job. "
            "Use job_status/job_wait to retrieve results; background completion cannot send an automatic reply. "
            "Job history is bounded and lost on restart. Never resubmit a write merely because its HTTP reply was lost."
        ),
    )

    @server.list_tools()
    async def list_tools():
        tools = [tool.model_copy(deep=True) for tool in _tool_list(catalog)]
        if pool is not None:
            for tool in tools:
                params = tool.inputSchema
                params.setdefault("properties", {})["workspace_id"] = {
                    "type": "string",
                    "description": "Registered workspace name returned by workspaces.",
                }
                params.setdefault("required", []).append("workspace_id")
            tools.append(
                Tool(
                    name="workspaces",
                    description="Discover registered default directories without loading their runtimes. No registration or configuration mutations are exposed remotely.",
                    inputSchema={
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    },
                    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
                )
            )
        return tools

    async def dispatch(runtime, name, arguments):
        args = arguments or {}
        if config.delegation and (name == "delegate" or name.startswith("delegation_")):
            try:
                return _reply(await call_delegation(runtime.delegation, name, args))
            except (ValueError, RuntimeError) as exc:
                return _reply({"error": str(exc)})
        if name in _JOB_DESCRIPTIONS:
            return _job_reply(
                await runtime.job_operation(
                    name.removeprefix("job_"),
                    args.get("job_id"),
                    timeout=args.get("timeout", 10),
                )
            )
        result = await runtime.call(name, args)
        if isinstance(result, PromotionResult):
            return _reply(
                {
                    "job_id": result.job_id,
                    "message": "Execution continues. Use job_status or job_wait for results; no automatic reply.",
                }
            )
        return _reply(
            {
                "job_id": result.job_id,
                "output": result.get_text_output(),
                "error": result.error,
                "exit_code": result.exit_code,
                "metadata": result.metadata,
            },
            result=result,
        )

    @server.call_tool()
    async def call_tool(name, arguments):
        args = dict(arguments or {})
        if pool is None:
            return await dispatch(owner, name, args)
        if name == "workspaces":
            return _reply({"instance_id": pool.instance_id, "workspaces": pool.list()})
        workspace_id = args.pop("workspace_id", None)
        if not isinstance(workspace_id, str) or not workspace_id:
            return _reply({"error": "workspace_id is required; call workspaces first"})
        try:
            async with pool.use(workspace_id) as runtime:
                result = await dispatch(runtime, name, args)
                if result.structuredContent is not None:
                    result.structuredContent.update(
                        workspace_id=workspace_id,
                        instance_id=runtime.instance_id,
                        server_instance_id=pool.instance_id,
                    )
                    if result.isError:
                        result.structuredContent["recovery_hint"] = (
                            "Handles belong to their original workspace runtime and expire on restart/removal. Never replay side effects automatically."
                        )
                    result.content[0] = TextContent(
                        type="text",
                        text=json.dumps(
                            result.structuredContent, ensure_ascii=False, default=str
                        ),
                    )
                return result
        except (ValueError, RuntimeError, OSError) as exc:
            return _reply(
                {
                    "workspace_id": workspace_id,
                    "server_instance_id": pool.instance_id,
                    "error": str(exc),
                }
            )

    manager = StreamableHTTPSessionManager(
        server,
        json_response=json_response,
        stateless=True,
        security_settings=TransportSecuritySettings(
            allowed_hosts=hosts, allowed_origins=origins
        ),
    )

    @asynccontextmanager
    async def lifespan(app):
        async with owner, manager.run():
            yield

    app = Starlette(
        routes=[
            Route("/mcp", _HTTPTransport(manager), methods=["GET", "POST", "DELETE"])
        ],
        middleware=[Middleware(MCPSecretPath, secret=secret)],
        lifespan=lifespan,
    )
    app.state.mcp_instance_id = owner.instance_id
    app.state.workspace_pool = pool
    return app
