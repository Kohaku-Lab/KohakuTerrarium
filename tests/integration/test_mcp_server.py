"""Complete authenticated tool workflows through the real MCP HTTP SDK."""

import asyncio
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from kohakuterrarium.api.mcp_tools import create_app
from kohakuterrarium.mcp_server.config import MCPToolsConfig
from kohakuterrarium.mcp_server.connection import ConnectionStore


class TestMCPServer:
    # Several real interpreter launches and intentional offline readiness waits
    # exceed the normal 60-second per-test budget on Windows.
    @pytest.mark.timeout(150)
    async def test_cli_lifecycle_isolation_restart_and_ingress_failure(self, tmp_path):
        first, second = tmp_path / "first", tmp_path / "second"
        first.mkdir()
        second.mkdir()
        state_dir = tmp_path / "private"
        env = {
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src"),
        }

        def cli(command, workspace=first, *extra):
            if command == "start":
                extra = ("--wait", "8", *extra)
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "kohakuterrarium",
                    "mcp-serve",
                    command,
                    "--workspace",
                    str(workspace),
                    "--state-dir",
                    str(state_dir),
                    "--json",
                    *extra,
                ],
                capture_output=True,
                text=True,
                env=env,
                timeout=45,
            )
            return completed.returncode, json.loads(
                completed.stdout.strip().splitlines()[-1]
            )

        def port():
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                return sock.getsockname()[1]

        async def call(workspace, name, args):
            record = ConnectionStore(workspace, state_dir).load()
            async with httpx.AsyncClient(trust_env=False) as http:
                async with streamable_http_client(
                    f"http://127.0.0.1:{record.port}/mcp/{record.secret}",
                    http_client=http,
                ) as (read, write, _):
                    async with ClientSession(read, write) as client:
                        await client.initialize()
                        return await client.call_tool(name, args)

        try:
            # HTTPS is deliberately unavailable: local service stays available,
            # startup reports failure to become publicly ready without lying.
            first_args = (
                "start",
                first,
                "--public-origin",
                "https://kt-mcp-test.invalid",
                "--tunnel",
                "external",
                "--port",
                str(port()),
            )
            (code, a), (_, racing) = await asyncio.gather(
                asyncio.to_thread(cli, *first_args), asyncio.to_thread(cli, *first_args)
            )
            assert racing["run_id"] == a["run_id"] and racing["pid"] == a["pid"]
            assert (
                code == 1
                and a["running"]
                and a["local_ready"]
                and not a["public_ready"]
            )
            original = ConnectionStore(first, state_dir).load()
            assert original.secret not in json.dumps(a)
            # Diagnostic publication may be temporarily blocked by a Windows
            # reader or scanner; this must not terminate tool execution.
            with ConnectionStore(first, state_dir).runtime_path.open("rb"):
                await asyncio.sleep(1.5)
                alive = await call(
                    first, "python", {"code": "print('telemetry lock survived')"}
                )
                assert (
                    not alive.isError
                    and "telemetry lock survived" in alive.structuredContent["output"]
                )
            _, repeated = cli("start")
            assert repeated["run_id"] == a["run_id"] and repeated["pid"] == a["pid"]
            code, b = cli(
                "start",
                second,
                "--public-origin",
                "https://kt-mcp-second.invalid",
                "--tunnel",
                "external",
                "--port",
                str(port()),
            )
            assert code == 1 and b["local_ready"] and b["pid"] != a["pid"]
            for workspace, content in ((first, "first"), (second, "second")):
                assert not (
                    await call(
                        workspace, "write", {"path": "note.txt", "content": content}
                    )
                ).isError
                assert (workspace / "note.txt").read_text() == content
            background = await call(
                first,
                "python",
                {"code": "import time; time.sleep(60)", "run_in_background": True},
            )
            old_job = background.structuredContent["job_id"]
            assert (await call(second, "job_status", {"job_id": old_job})).isError
            assert cli("stop")[1]["state"] == "stopped"
            assert cli("status", second)[1]["instance_id"] == b["instance_id"]
            assert not (await call(second, "read", {"path": "note.txt"})).isError
            _, restarted = cli("start")
            assert restarted["instance_id"] != a["instance_id"]
            assert ConnectionStore(first, state_dir).load().url == original.url
            assert (await call(first, "job_status", {"job_id": old_job})).isError
            assert (
                await call(first, "write", {"path": "note.txt", "content": "unread"})
            ).isError
            assert (first / "note.txt").read_text() == "first"
            cli("stop")
            # A collision is explicit and does not stop the other listener.
            with socket.socket() as occupied:
                occupied.bind(("127.0.0.1", 0))
                occupied.listen()
                code, failed = cli(
                    "start", first, "--port", str(occupied.getsockname()[1])
                )
                assert code == 1 and not failed["public_ready"]
                assert "unavailable" in failed["error"]
                assert occupied.getsockname()[1] > 0
            # A real child that cannot act as ngrok exits. Only ingress retries;
            # the Python tool job survives those restarts in the same instance.
            # Failure even before the child launches must also stay isolated.
            tunnel_log = ConnectionStore(first, state_dir).directory / "tunnel.log"
            tunnel_log.mkdir()
            _, tunnel_failed = cli(
                "start",
                first,
                "--port",
                str(port()),
                "--tunnel",
                "ngrok",
                "--ngrok-bin",
                sys.executable,
            )
            assert tunnel_failed["local_ready"] and not tunnel_failed["public_ready"]
            assert "launch" in tunnel_failed["error"]
            bg = await call(
                first,
                "python",
                {
                    "code": "import time; time.sleep(3); print('survived ingress failure')",
                    "run_in_background": True,
                },
            )
            tunnel_log.rmdir()
            done = await call(
                first,
                "job_wait",
                {"job_id": bg.structuredContent["job_id"], "timeout": 10},
            )
            assert (
                not done.isError
                and "survived ingress failure" in done.structuredContent["output"]
            )
            assert cli("status")[1]["instance_id"] == tunnel_failed["instance_id"]
        finally:
            for workspace in (first, second):
                if ConnectionStore(workspace, state_dir).record_path.exists():
                    cli("stop", workspace)

    async def test_authenticated_tools_and_job_lifecycle(self, tmp_path):
        secret = "a" * 43
        app = create_app(MCPToolsConfig(workspace=tmp_path), secret=secret, port=8765)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="http://127.0.0.1:8765"
            ) as http:
                for path in ("/mcp", "/mcp/wrong", "/", "/mcp/" + secret + "/"):
                    for method in ("GET", "POST", "DELETE"):
                        assert (await http.request(method, path)).status_code == 404
                assert (
                    await http.post(
                        "/mcp/" + secret, json={}, headers={"host": "evil.example"}
                    )
                ).status_code == 421
                assert (
                    await http.post(
                        "/mcp/" + secret,
                        json={},
                        headers={"origin": "https://evil.example"},
                    )
                ).status_code == 403
                async with streamable_http_client(
                    "http://127.0.0.1:8765/mcp/" + secret, http_client=http
                ) as (read, write, _):
                    async with ClientSession(read, write) as client:
                        await client.initialize()
                        tools = {t.name: t for t in (await client.list_tools()).tools}
                        assert len(tools) == 13
                        assert (
                            "run_in_background"
                            in tools["python"].inputSchema["properties"]
                        )
                        assert (
                            "run_in_background"
                            not in tools["read"].inputSchema["properties"]
                        )
                        assert (await client.call_tool("read", {})).isError

                        async def call(name, args):
                            result = await client.call_tool(name, args)
                            return result, json.loads(result.content[0].text)

                        (tmp_path / "note.txt").write_text("before", encoding="utf-8")
                        result, _ = await call(
                            "write", {"path": "note.txt", "content": "bad"}
                        )
                        assert result.isError
                        assert not (await call("read", {"path": "note.txt"}))[0].isError
                        assert not (
                            await call(
                                "edit",
                                {"path": "note.txt", "old": "before", "new": "after"},
                            )
                        )[0].isError
                        assert (tmp_path / "note.txt").read_text() == "after"
                        shell, shell_data = await call(
                            "bash", {"command": "echo MCP-SHELL"}
                        )
                        assert not shell.isError and "MCP-SHELL" in shell_data["output"]
                        _, bg = await call(
                            "python",
                            {
                                "code": "import time; time.sleep(.2); print('background done')",
                                "run_in_background": True,
                            },
                        )
                        assert bg["job_id"] and "job_wait" in bg["message"]
                        _, done = await call(
                            "job_wait", {"job_id": bg["job_id"], "timeout": 10}
                        )
                        assert (
                            done["state"] == "done"
                            and "background done" in done["output"]
                        )
                        _, bg = await call(
                            "python",
                            {
                                "code": "import time; time.sleep(30)",
                                "run_in_background": True,
                            },
                        )
                        assert (await call("job_cancel", {"job_id": bg["job_id"]}))[1][
                            "cancelled"
                        ]
                        for query in ("job_status", "job_wait"):
                            queried, cancelled = await call(
                                query, {"job_id": bg["job_id"]}
                            )
                            assert not queried.isError
                            assert cancelled["state"] == "cancelled"
                            assert (
                                cancelled["error"]
                                == "User manually interrupted this job."
                            )
                        failed, failure = await call(
                            "python",
                            {"code": "raise RuntimeError('expected job failure')"},
                        )
                        assert failed.isError and failure["exit_code"] != 0
                        for query in ("job_status", "job_wait"):
                            queried, snapshot = await call(
                                query, {"job_id": failure["job_id"]}
                            )
                            assert not queried.isError
                            assert snapshot["exit_code"] == failure["exit_code"]
                            assert "expected job failure" in snapshot["output"]
                            assert (await call(query, {"job_id": "nonexistent"}))[
                                0
                            ].isError
                        direct = asyncio.create_task(
                            call(
                                "python",
                                {
                                    "code": "import time; time.sleep(.5); print('promoted once')"
                                },
                            )
                        )
                        for _ in range(100):
                            _, snapshot = await call("job_status", {})
                            active = [
                                j for j in snapshot["jobs"] if j["state"] == "running"
                            ]
                            if active:
                                break
                            await asyncio.sleep(0.01)
                        assert active
                        job_id = active[0]["job_id"]
                        assert (await call("job_promote", {"job_id": job_id}))[1][
                            "promoted"
                        ]
                        assert (await direct)[1]["job_id"] == job_id
                        assert (await call("job_wait", {"job_id": job_id}))[1][
                            "output"
                        ].count("promoted once") == 1
                        assert (await call("job_status", {"job_id": "other-instance"}))[
                            0
                        ].isError
                        # Cancel the HTTP request itself, then retrieve the job
                        # through the independent initialized SDK connection.
                        disconnected = asyncio.create_task(
                            http.post(
                                "/mcp/" + secret,
                                headers={
                                    "accept": "application/json, text/event-stream"
                                },
                                json={
                                    "jsonrpc": "2.0",
                                    "id": 1000,
                                    "method": "tools/call",
                                    "params": {
                                        "name": "python",
                                        "arguments": {
                                            "code": "import time; from pathlib import Path; time.sleep(.5); Path('disconnected.txt').write_text('once')",
                                        },
                                    },
                                },
                            )
                        )
                        for _ in range(100):
                            _, snapshot = await call("job_status", {})
                            active = [
                                j for j in snapshot["jobs"] if j["state"] == "running"
                            ]
                            if active:
                                break
                            await asyncio.sleep(0.01)
                        assert active
                        job_id = active[0]["job_id"]
                        disconnected.cancel()
                        with pytest.raises(asyncio.CancelledError):
                            await disconnected
                        assert (await call("job_wait", {"job_id": job_id}))[1][
                            "state"
                        ] == "done"
                        assert (tmp_path / "disconnected.txt").read_text() == "once"

        # Recreate the process-lifetime state at the same authenticated URL.
        restarted = create_app(
            MCPToolsConfig(workspace=tmp_path), secret=secret, port=8765
        )
        async with restarted.router.lifespan_context(restarted):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(restarted)
            ) as http:
                async with streamable_http_client(
                    "http://127.0.0.1:8765/mcp/" + secret, http_client=http
                ) as (read, write, _):
                    async with ClientSession(read, write) as client:
                        await client.initialize()
                        missing = await client.call_tool(
                            "job_status", {"job_id": job_id}
                        )
                        assert (
                            missing.isError
                            and missing.structuredContent["error"] == "Unknown job"
                        )
                        blocked = await client.call_tool(
                            "write", {"path": "note.txt", "content": "unread"}
                        )
                        assert (
                            blocked.isError
                            and (tmp_path / "note.txt").read_text() == "after"
                        )
                        readback = await client.call_tool("read", {"path": "note.txt"})
                        assert (
                            not readback.isError
                            and "after" in readback.structuredContent["output"]
                        )
