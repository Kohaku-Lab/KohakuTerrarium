"""One tool process with independent ingress supervision and private file control."""

import argparse
import asyncio
import json
import logging
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import uvicorn
from mcp.types import LATEST_PROTOCOL_VERSION

from kohakuterrarium.api.mcp_tools import create_app
from kohakuterrarium.mcp_server.config import MCPToolsConfig, load_config
from kohakuterrarium.mcp_server.connection import (
    ConnectionStore,
    workspace_identity,
    write_json,
)
from kohakuterrarium.utils.file_lock import FileLockBusy

logger = logging.getLogger(__name__)


async def _publish(store, snapshot):
    """Diagnostics cannot own the lifetime of the tool execution process."""
    try:
        await asyncio.to_thread(write_json, store.runtime_path, snapshot)
    except OSError:
        logger.warning(
            "MCP runtime status could not be published; retrying on the next update"
        )


async def probe_public(record, instance_id: str) -> tuple[bool, str | None]:
    """Verify the configured HTTPS route reaches this exact tool instance."""
    try:
        async with httpx.AsyncClient(timeout=5, follow_redirects=False) as client:
            response = await client.post(
                record.url,
                headers={"accept": "application/json, text/event-stream"},
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": LATEST_PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "kt-ingress-check", "version": "1"},
                    },
                },
            )
            if response.status_code != 200:
                return False, f"Public endpoint returned HTTP {response.status_code}"
            instructions = response.json().get("result", {}).get("instructions", "")
            if f"KT instance_id: {instance_id}" not in instructions.splitlines():
                return False, "Public endpoint did not identify this instance"
            return True, None
    except (httpx.HTTPError, ValueError, AttributeError):
        return False, "Public endpoint unavailable or not an MCP JSON response"


def _stop_requested(store: ConnectionStore, run_id: str) -> bool:
    try:
        return (
            json.loads(store.stop_path.read_text(encoding="utf-8")).get("run_id")
            == run_id
        )
    except (ValueError, OSError, AttributeError):
        return False


def _start_tunnel(store: ConnectionStore, run_id: str):
    fd = os.open(
        store.directory / "tunnel.log", os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600
    )
    with os.fdopen(fd, "ab") as log:
        return subprocess.Popen(
            [
                sys.executable,
                "-m",
                "kohakuterrarium.mcp_server.tunnel",
                "--workspace",
                store.workspace,
                "--state-dir",
                str(store.directory.parent),
                "--run-id",
                run_id,
            ],
            stdin=subprocess.PIPE,
            stdout=log,
            stderr=log,
            close_fds=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )


async def _monitor(store, record, server, serving, snapshot):
    tunnel = None
    probing = None
    next_probe = next_start = 0.0
    retry_delay = 1.0
    snapshot.update(
        tunnel_state="external" if record.tunnel == "external" else "starting"
    )
    try:
        while not serving.done() and not _stop_requested(store, snapshot["run_id"]):
            now = time.monotonic()
            snapshot["local_ready"] = bool(server.started)
            if record.tunnel == "ngrok":
                if tunnel is not None and tunnel.poll() is not None:
                    snapshot.update(
                        tunnel_state="reconnecting",
                        public_ready=False,
                        error=f"Owned tunnel exited with code {tunnel.returncode}",
                        tunnel_pid=None,
                    )
                    tunnel.stdin.close()
                    tunnel = None
                    next_start = now + retry_delay
                    retry_delay = min(retry_delay * 2, 30)
                if tunnel is None and now >= next_start and server.started:
                    try:
                        tunnel = _start_tunnel(store, snapshot["run_id"])
                    except OSError:
                        snapshot.update(
                            tunnel_pid=None,
                            tunnel_state="reconnecting",
                            public_ready=False,
                            error="Owned tunnel launch failed; inspect local log access and process resources",
                        )
                        next_start = now + retry_delay
                        retry_delay = min(retry_delay * 2, 30)
                    else:
                        snapshot.update(
                            tunnel_pid=tunnel.pid, tunnel_state="connecting"
                        )
            ingress_alive = record.tunnel == "external" or tunnel is not None
            if (
                probing is None
                and server.started
                and ingress_alive
                and now >= next_probe
            ):
                probing = asyncio.create_task(
                    probe_public(record, snapshot["instance_id"])
                )
            if probing is not None and probing.done():
                ok, error = probing.result()
                probing = None
                snapshot.update(
                    public_ready=ok and ingress_alive,
                    error=error,
                    public_checked_at=time.time(),
                )
                if ok and record.tunnel == "ngrok" and ingress_alive:
                    snapshot["tunnel_state"] = "online"
                    retry_delay = 1
                next_probe = now + (10 if ok else 2)
                snapshot["state"] = "ready" if snapshot["public_ready"] else "offline"
            if not ingress_alive and server.started:
                snapshot["state"] = "offline"
            snapshot["updated_at"] = time.time()
            await _publish(store, snapshot)
            await asyncio.sleep(0.25)
    finally:
        if probing is not None:
            probing.cancel()
            await asyncio.gather(probing, return_exceptions=True)
        if tunnel is not None:
            # Closing the ownership pipe asks the guardian to reap ngrok. A
            # supervisor crash closes the same pipe in the OS automatically.
            tunnel.stdin.close()
            await asyncio.to_thread(tunnel.wait, 10)


async def serve(store: ConnectionStore, run_id: str) -> None:
    record = store.load_active(run_id)
    snapshot = {
        "run_id": run_id,
        "pid": os.getpid(),
        "ownership_acquired": True,
        "state": "starting",
        "local_ready": False,
        "public_ready": False,
        "updated_at": time.time(),
        "tunnel_pid": None,
    }
    serving = server = None
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if os.name == "nt":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", record.port))
        except OSError as exc:
            raise RuntimeError(
                f"Local port {record.port} is unavailable; no alternate port was selected"
            ) from exc
        sock.listen()
        config = (
            load_config(Path(record.tools_config))
            if record.tools_config
            else MCPToolsConfig(workspace=Path(record.workspace))
        )
        if workspace_identity(config.workspace) != record.workspace:
            raise ValueError("Tool configuration belongs to a different workspace")
        app = create_app(
            config,
            secret=record.secret,
            port=record.port,
            public_origin=record.public_origin,
        )
        snapshot["instance_id"] = app.state.mcp_instance_id
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                access_log=False,
                log_level="warning",
                log_config=None,
                timeout_graceful_shutdown=5,
            )
        )
        serving = asyncio.create_task(server.serve(sockets=[sock]))
        await _monitor(store, record, server, serving, snapshot)
        if serving.done():
            await serving
            if not _stop_requested(store, run_id):
                snapshot["error"] = "Local server exited"
    except Exception as exc:
        snapshot.update(
            state="failed", error=str(exc).replace(record.secret, "<redacted>")
        )
    finally:
        if server is not None:
            server.should_exit = True
        if serving is not None:
            await asyncio.gather(serving, return_exceptions=True)
        sock.close()
        snapshot.update(
            local_ready=False,
            public_ready=False,
            tunnel_pid=None,
            updated_at=time.time(),
        )
        if snapshot["state"] != "failed":
            snapshot["state"] = "stopped"
        await _publish(store, snapshot)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    store = ConnectionStore(args.workspace, args.state_dir)
    try:
        with store.instance_lock:
            write_json(
                store.runtime_path,
                {
                    "run_id": args.run_id,
                    "pid": os.getpid(),
                    "ownership_acquired": True,
                    "state": "starting",
                    "updated_at": time.time(),
                    "local_ready": False,
                    "public_ready": False,
                },
            )
            asyncio.run(serve(store, args.run_id))
    except FileLockBusy:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
