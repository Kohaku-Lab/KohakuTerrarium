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
from kohakuterrarium.testing.llm import ScriptedLLM, ScriptEntry


class TestMCPServer:
    # Several real interpreter launches and intentional offline readiness waits
    # exceed the normal 60-second per-test budget on Windows.
    @pytest.mark.timeout(200)
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
            if command == "setup":
                extra = ("--non-interactive", *extra)
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
                    *([] if command == "url" else ["--json"]),
                    *extra,
                ],
                capture_output=True,
                text=True,
                env=env,
                timeout=45,
            )
            output = completed.stdout.strip().splitlines()[-1]
            return completed.returncode, (
                output if command == "url" else json.loads(output)
            )

        def port():
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                return sock.getsockname()[1]

        async def call(workspace, name, args):
            store = ConnectionStore(workspace, state_dir)
            record = store.load_active(store.runtime()["run_id"])
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
                "setup",
                first,
                "--origin",
                "https://kt-mcp-test.invalid",
                "--mode",
                "external",
                "--port",
                str(port()),
            )
            setup_code, configured = cli(*first_args)
            assert setup_code == 0 and not configured["running"]
            (code, a), (_, racing) = await asyncio.gather(
                asyncio.to_thread(cli, "start"), asyncio.to_thread(cli, "start")
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
            assert (
                cli(
                    "setup",
                    second,
                    "--origin",
                    "https://kt-mcp-second.invalid",
                    "--mode",
                    "external",
                    "--port",
                    str(port()),
                )[0]
                == 0
            )
            code, b = cli("start", second)
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
            code, refused = cli("rotate")
            assert code == 1 and "stop" in refused["error"]
            assert ConnectionStore(first, state_dir).load() == original
            assert cli("stop")[1]["state"] == "stopped"
            other_record = ConnectionStore(second, state_dir).load()
            code, rotation = cli("rotate")
            assert code == 0 and rotation["rotated"] and not rotation["running"]
            rotated = ConnectionStore(first, state_dir).load()
            assert rotated.secret != original.secret
            assert cli("url")[1] == rotated.url
            assert ConnectionStore(second, state_dir).load() == other_record
            assert cli("status", second)[1]["instance_id"] == b["instance_id"]
            assert not (await call(second, "read", {"path": "note.txt"})).isError
            _, restarted = cli("start")
            assert restarted["instance_id"] != a["instance_id"]
            assert ConnectionStore(first, state_dir).load().url == rotated.url
            async with httpx.AsyncClient(trust_env=False) as http:
                rejected = await http.get(
                    f"http://127.0.0.1:{rotated.port}/mcp/{original.secret}"
                )
                assert rejected.status_code == 404
            authenticated = await call(first, "python", {"code": "print('new-key-ok')"})
            assert not authenticated.isError
            assert "new-key-ok" in authenticated.structuredContent["output"]
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
                assert (
                    cli("setup", first, "--port", str(occupied.getsockname()[1]))[0]
                    == 0
                )
                code, failed = cli("start")
                assert code == 1 and not failed["public_ready"]
                assert "unavailable" in failed["error"]
                assert occupied.getsockname()[1] > 0
            # A real child that cannot act as ngrok exits. Only ingress retries;
            # the Python tool job survives those restarts in the same instance.
            # Failure even before the child launches must also stay isolated.
            tunnel_log = ConnectionStore(first, state_dir).directory / "tunnel.log"
            tunnel_log.mkdir()
            # Python stands in for an external tunnel process. Its real child
            # records ingress arguments then exits, exercising guardian retries.
            (first / "http").write_text(
                "import json, sys, time\nfrom pathlib import Path\n"
                "with Path('ingress-observed.jsonl').open('a') as f:\n"
                "    f.write(json.dumps(sys.argv[1:]) + '\\n')\n"
                "time.sleep(.2)\nraise SystemExit(1)\n"
            )
            assert (
                cli(
                    "setup",
                    first,
                    "--port",
                    str(port()),
                    "--mode",
                    "ngrok",
                    "--ngrok-bin",
                    sys.executable,
                )[0]
                == 0
            )
            _, tunnel_failed = cli("start")
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
            running_record = ConnectionStore(first, state_dir).load()
            saved_code, saved = cli(
                "setup",
                first,
                "--mode",
                "external",
                "--origin",
                "https://kt-mcp-next.invalid",
                "--port",
                str(port()),
            )
            assert saved_code == 0 and saved["restart_required"]
            _, pending = cli("start")
            assert pending["instance_id"] == tunnel_failed["instance_id"]
            assert pending["public_origin"] == running_record.public_origin
            assert (
                pending["configured"]["public_origin"] == "https://kt-mcp-next.invalid"
            )
            assert cli("url")[1] == running_record.url
            assert (
                cli("url", first, "--configured")[1]
                == ConnectionStore(first, state_dir).load().url
            )
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
            observed = first / "ingress-observed.jsonl"
            count = len(observed.read_text().splitlines()) if observed.exists() else 0
            for _ in range(100):
                if observed.exists() and len(observed.read_text().splitlines()) > count:
                    break
                await asyncio.sleep(0.2)
            lines = observed.read_text().splitlines()
            assert len(lines) > count
            for line in lines:
                arguments = json.loads(line)
                assert f"http://127.0.0.1:{running_record.port}" in arguments
                assert running_record.public_origin in arguments
                assert "https://kt-mcp-next.invalid" not in arguments
            assert cli("stop")[1]["state"] == "stopped"
            _, applied = cli("start")
            assert applied["local_ready"] and not applied["public_ready"]
            assert applied["instance_id"] != tunnel_failed["instance_id"]
            assert applied["public_origin"] == "https://kt-mcp-next.invalid"
            assert not applied["restart_required"] and applied["tunnel"] == "external"
        finally:
            for workspace in (first, second):
                if ConnectionStore(workspace, state_dir).record_path.exists():
                    cli("stop", workspace)

    async def test_authenticated_tools_and_job_lifecycle(self, tmp_path, capsys):
        secret = "a" * 43
        creature_path = tmp_path / "creature.json"
        creature_path.write_text(
            json.dumps(
                {
                    "name": "worker",
                    "input": {"type": "none"},
                    "output": {"type": "stdout"},
                    "tools": [],
                    "compact": {"enabled": False},
                }
            ),
            encoding="utf-8",
        )
        subagent_path = tmp_path / "subagent.json"
        subagent_path.write_text(
            json.dumps(
                {
                    "name": "writer",
                    "tools": ["write"],
                    "can_modify": True,
                }
            ),
            encoding="utf-8",
        )

        def provider(target):
            if target == "writer":
                return ScriptedLLM(
                    [
                        "[/write]\n@@path=delegated.txt\n@@content=via MCP\n[write/]",
                        "written via delegated tool",
                    ]
                )
            return ScriptedLLM(
                [
                    ScriptEntry(
                        "slow creature answer",
                        match="slow",
                        delay_per_chunk=0.1,
                        chunk_size=1,
                    ),
                    ScriptEntry("creature reply", match="hello"),
                ]
            )

        app = create_app(
            MCPToolsConfig.model_validate(
                {
                    "workspace": tmp_path,
                    "delegation": {
                        "worker": {"kind": "creature", "config": str(creature_path)},
                        "writer": {"kind": "subagent", "config": str(subagent_path)},
                    },
                }
            ),
            secret=secret,
            port=8765,
            llm_factory=provider,
        )
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
                        assert len(tools) == 19
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

                        _, targets = await call("delegation_targets", {})
                        assert {t["name"] for t in targets["targets"]} == {
                            "worker",
                            "writer",
                        }
                        assert (
                            await call(
                                "delegate", {"target": "foreign", "prompt": "hello"}
                            )
                        )[0].isError
                        assert (
                            await client.call_tool(
                                "delegate",
                                {
                                    "target": "worker",
                                    "prompt": "hello",
                                    "model": "arbitrary",
                                },
                            )
                        ).isError
                        _, delegated = await call(
                            "delegate", {"target": "writer", "prompt": "write"}
                        )
                        _, written = await call(
                            "job_wait", {"job_id": delegated["job_id"]}
                        )
                        assert written["state"] == "done", written
                        assert (tmp_path / "delegated.txt").read_text() == "via MCP"
                        _, messages = await call(
                            "delegation_history",
                            {
                                "session_id": delegated["session_id"],
                                "view": "conversation",
                            },
                        )
                        assert "delegated.txt" in json.dumps(messages)
                        _, creature = await call(
                            "delegate", {"target": "worker", "prompt": "slow"}
                        )
                        sid = creature["session_id"]
                        busy, _ = await call(
                            "delegate",
                            {"target": "worker", "session_id": sid, "prompt": "hello"},
                        )
                        assert busy.isError
                        assert not (
                            await call(
                                "job_wait", {"job_id": creature["job_id"], "timeout": 0}
                            )
                        )[0].isError
                        _, cancelled = await call(
                            "job_cancel", {"job_id": creature["job_id"]}
                        )
                        assert cancelled["cancelled"]
                        queried, state = await call(
                            "job_status", {"job_id": creature["job_id"]}
                        )
                        assert not queried.isError and state["state"] == "cancelled"
                        _, resumed = await call(
                            "delegate",
                            {"target": "worker", "session_id": sid, "prompt": "hello"},
                        )
                        assert (await call("job_wait", {"job_id": resumed["job_id"]}))[
                            1
                        ]["state"] == "done"
                        assert capsys.readouterr().out == ""
                        _, listing = await call("delegation_sessions", {})
                        assert sid in {s["session_id"] for s in listing["sessions"]}
                        assert not (
                            await call("delegation_close", {"session_id": sid})
                        )[0].isError

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
