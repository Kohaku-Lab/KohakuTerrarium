"""Disposable ingress probe, not a KT tool runtime or a supported CLI surface.

Only a fixed test file is exposed. Full KT execution belongs to the next phase.
"""

import argparse
import asyncio
import hashlib
import json
import os
import re
import secrets
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.responses import PlainTextResponse


class SecretPath:
    """Authenticate every HTTP request before handing it to the MCP SDK."""

    def __init__(self, app, secret: str):
        self.app = app
        self.path = ("/mcp/" + secret).encode("ascii")

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":
            await send({"type": "websocket.close", "code": 1008})
            return
        if not secrets.compare_digest(scope["path"].encode("utf-8"), self.path):
            await PlainTextResponse("Not Found", status_code=404)(scope, receive, send)
            return
        # The SDK never receives the credential-bearing URL (including in errors).
        clean = dict(scope, path="/mcp", raw_path=b"/mcp", query_string=b"")
        await self.app(clean, receive, send)


def normalized_origin(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "Public origin must be an HTTPS origin without a path or credentials"
        )
    return "https://" + parsed.netloc.lower()


def load_connection(state_file: Path, workspace: Path, public_origin: str) -> dict:
    """Persist a probe credential; reject rebinding instead of repairing state."""
    identity = os.path.normcase(str(workspace.resolve()))
    state_file.parent.mkdir(parents=True, exist_ok=True)
    if not state_file.exists():
        record = {
            "workspace": identity,
            "public_origin": public_origin,
            "secret": secrets.token_urlsafe(32),
        }
        try:
            # Exclusive creation avoids replacing a concurrent owner's secret.
            with state_file.open("x", encoding="utf-8") as stream:
                json.dump(record, stream, indent=2)
        except FileExistsError:
            pass
    record = json.loads(state_file.read_text(encoding="utf-8"))
    if not isinstance(record, dict):
        raise ValueError("Invalid connection record; restore it explicitly")
    if (
        record.get("workspace") != identity
        or record.get("public_origin") != public_origin
    ):
        raise ValueError(
            "Connection record belongs to a different workspace or public origin"
        )
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", record.get("secret", "")):
        raise ValueError("Invalid connection secret; restore it explicitly")
    return record


def create_app(workspace: Path, secret: str, port: int, public_origin: str, mode: str):
    boot_id = secrets.token_hex(12)
    allowed_hosts = [f"127.0.0.1:{port}", f"localhost:{port}"]
    allowed_origins = [f"http://127.0.0.1:{port}", f"http://localhost:{port}"]
    if public_origin:
        allowed_hosts.append(urlsplit(public_origin).netloc)
        allowed_origins.append(public_origin)
    mcp = FastMCP(
        "KT MCP ingress probe",
        instructions=(
            "This connection tests MCP ingress only. Use probe_read and probe_write "
            "on the single disposable probe.txt file. probe_status identifies each "
            "server boot. It does not expose KT tools, shell execution or jobs."
        ),
        json_response=mode == "json",
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            allowed_hosts=allowed_hosts, allowed_origins=allowed_origins
        ),
    )
    note = workspace / "probe.txt"

    def check_note():
        if note.is_symlink() or note.resolve().parent != workspace:
            raise ValueError(
                "The probe file must be a regular file in the test directory"
            )

    check_note()
    try:
        with note.open("x", encoding="utf-8") as stream:
            stream.write("KT MCP ingress probe ready.\n")
    except FileExistsError:
        pass

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
    async def probe_status() -> dict[str, str]:
        """Return the server boot ID; it changes after a service restart."""
        return {"boot_id": boot_id, "response_mode": mode, "file": "probe.txt"}

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
    async def probe_read() -> dict[str, str]:
        """Read the UTF-8 content of the single disposable probe.txt test file."""
        check_note()
        if note.stat().st_size > 32768:
            raise ValueError("Probe file exceeds the experiment size limit")
        content = await asyncio.to_thread(note.read_text, encoding="utf-8")
        return {"content": content, "boot_id": boot_id}

    @mcp.tool(
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=True,
            openWorldHint=False,
        )
    )
    async def probe_write(
        content: Annotated[str, Field(max_length=8192)],
    ) -> dict[str, str | int]:
        """Replace probe.txt with supplied UTF-8 text (at most 8192 characters)."""
        check_note()
        await asyncio.to_thread(note.write_text, content, encoding="utf-8")
        return {"written_characters": len(content), "boot_id": boot_id}

    return SecretPath(mcp.streamable_http_app(), secret)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workspace",
        type=Path,
        required=True,
        help="Existing disposable test directory",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        help="Private connection record outside the repository",
    )
    parser.add_argument(
        "--public-origin", default="", help="Stable HTTPS origin, without a path"
    )
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--response-mode", choices=("json", "sse"), default="json")
    parser.add_argument(
        "--show-url",
        action="store_true",
        help="Print the credential-bearing URL and exit",
    )
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    if not workspace.is_dir():
        parser.error("Create the disposable test directory before starting")
    if not 1 <= args.port <= 65535:
        parser.error("Port must be between 1 and 65535")
    try:
        public_origin = (
            normalized_origin(args.public_origin) if args.public_origin else ""
        )
        key = hashlib.sha256(os.path.normcase(str(workspace)).encode()).hexdigest()[:24]
        state_file = (
            args.state_file
            or Path.home() / ".kohakuterrarium" / "mcp-probe" / f"{key}.json"
        )
        if state_file.resolve().parent == workspace:
            raise ValueError("Store the connection record outside the test directory")
        record = load_connection(state_file, workspace, public_origin)
        if args.show_url:
            origin = public_origin or f"http://127.0.0.1:{args.port}"
            print(origin + "/mcp/" + record["secret"])
            return
        app = create_app(
            workspace, record["secret"], args.port, public_origin, args.response_mode
        )
    except (ValueError, OSError, TypeError):
        parser.error(
            "Invalid probe configuration, connection record or test file; no state was reset"
        )
    # No access logs: URL paths carry the credential. Only loopback is bound.
    uvicorn.run(
        app, host="127.0.0.1", port=args.port, access_log=False, log_level="warning"
    )


if __name__ == "__main__":
    main()
