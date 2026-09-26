"""Authenticated Streamable HTTP adapter for a KT tool-only runtime."""

import copy
import json
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, ImageContent, TextContent, Tool, ToolAnnotations
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.routing import Route

from kohakuterrarium.api.auth.mcp_secret import MCPSecretPath
from kohakuterrarium.core.backgroundify import PromotionResult
from kohakuterrarium.llm.message import ImagePart
from kohakuterrarium.llm.artifact_resolve import resolve_artifact_url
from kohakuterrarium.mcp_server.config import MCPToolsConfig
from kohakuterrarium.mcp_server.runtime import ToolRuntime

_JOB_DESCRIPTIONS = {
    "job_status": "Read a retained job, or list this instance's jobs when job_id is omitted.",
    "job_wait": "Wait up to timeout seconds for a job; timeout/disconnect does not cancel execution.",
    "job_cancel": "Cancel a running job in this instance. Completed jobs are unchanged.",
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


def _job_reply(data: dict) -> CallToolResult:
    """A retained job's failure is data, not a failure to query that job."""
    return _reply(data, is_error=bool(data.get("error")) and "state" not in data)


class _HTTPTransport:
    def __init__(self, manager):
        self.manager = manager

    async def __call__(self, scope, receive, send):
        await self.manager.handle_request(scope, receive, send)


def create_app(
    config: MCPToolsConfig,
    *,
    secret: str,
    port: int = 8765,
    public_origin: str = "",
    json_response: bool = True,
) -> Starlette:
    """Create one process-lifetime runtime; serve only through its secret path.

    The hosting server MUST disable access logs, since those run outside ASGI.
    Bind to loopback and publish via the configured HTTPS tunnel.
    """
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
        hosts.append(parsed.netloc)
        origins.append(public_origin)
    # Validate before constructing the runtime or loading configured modules.
    MCPSecretPath(None, secret=secret)
    runtime = ToolRuntime(config)
    server = Server(
        config.name,
        instructions=(
            f"KT instance_id: {runtime.instance_id}\n"
            "KT tools only: no local LLM or autonomous turns. Workspace is the default directory, "
            "not a sandbox. Read existing files before modifying them; stale reads require rereading. "
            "Jobs and file-read state belong to this instance, shared across its authenticated clients. "
            "Use run_in_background for long bash/python work, or job_promote for an already running job. "
            "Use job_status/job_wait to retrieve results; background completion cannot send an automatic reply. "
            "Job history is bounded and lost on restart. Never resubmit a write merely because its HTTP reply was lost."
        ),
    )

    @server.list_tools()
    async def list_tools():
        return _tool_list(runtime)

    @server.call_tool()
    async def call_tool(name, arguments):
        args = arguments or {}
        if name == "job_status":
            return _job_reply(
                runtime.job(args["job_id"])
                if "job_id" in args
                else {"instance_id": runtime.instance_id, "jobs": runtime.jobs()}
            )
        if name == "job_wait":
            return _job_reply(
                await runtime.wait(args["job_id"], args.get("timeout", 10))
            )
        if name in {"job_cancel", "job_promote"}:
            job_id = args["job_id"]
            data = runtime.job(job_id)
            if "error" in data and data["error"] == "Unknown job":
                return _reply(data)
            key = "cancelled" if name == "job_cancel" else "promoted"
            changed = (
                await runtime.cancel(job_id)
                if name == "job_cancel"
                else runtime.promote(job_id)
            )
            return _reply({"job_id": job_id, key: changed})
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
        async with runtime, manager.run():
            yield

    app = Starlette(
        routes=[
            Route("/mcp", _HTTPTransport(manager), methods=["GET", "POST", "DELETE"])
        ],
        middleware=[Middleware(MCPSecretPath, secret=secret)],
        lifespan=lifespan,
    )
    app.state.mcp_instance_id = runtime.instance_id
    return app
