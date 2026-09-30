"""Complete authenticated tool workflows through the real MCP HTTP SDK."""

import asyncio
import hashlib
import json
import os
import socket
import signal
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from kohakuterrarium.api.mcp_tools import create_app
from kohakuterrarium.mcp_server.config import GlobalToolsConfig
from kohakuterrarium.mcp_server.endpoint import EndpointStore
from kohakuterrarium.mcp_server.service import is_running, status
from kohakuterrarium.mcp_server.workspaces import WorkspaceRegistry
from kohakuterrarium.testing.llm import ScriptedLLM, ScriptEntry


class TestMCPServer:
    @pytest.mark.timeout(300)
    async def test_cli_lifecycle_isolation_restart_and_ingress_failure(self, tmp_path):
        home, other_home = tmp_path / "home", tmp_path / "other-home"
        workspace = tmp_path / "project"
        workspace.mkdir()
        store, other = EndpointStore(home), EndpointStore(other_home)
        env = {
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src"),
        }

        def cli(command, *extra, root=home):
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "kohakuterrarium",
                    "mcp-serve",
                    *command.split(),
                    "--home-dir",
                    str(root),
                    *([] if command == "url" else ["--json"]),
                    *(["--wait", "8"] if command == "start" else []),
                    *map(str, extra),
                ],
                capture_output=True,
                text=True,
                env={**env, "KT_CONFIG_DIR": str(root)},
                cwd=tmp_path,
                timeout=45,
            )
            assert completed.stdout.strip(), completed.stderr
            text = completed.stdout.strip().splitlines()[-1]
            return completed.returncode, text if command == "url" else json.loads(text)

        def port():
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                return sock.getsockname()[1]

        async def call(name, args=None, owner=store):
            record = owner.load_active(owner.runtime()["run_id"])
            async with httpx.AsyncClient(trust_env=False) as http:
                async with streamable_http_client(
                    f"http://127.0.0.1:{record.port}/mcp/{record.secret}",
                    http_client=http,
                ) as (read, write, _):
                    async with ClientSession(read, write) as client:
                        await client.initialize()
                        return await client.call_tool(name, args or {})

        try:
            assert (
                cli(
                    "setup",
                    "--non-interactive",
                    "--mode",
                    "external",
                    "--origin",
                    "https://kt-mcp-test.invalid",
                    "--port",
                    port(),
                )[0]
                == 0
            )
            identity = os.path.normcase(str(workspace.resolve()))
            store.registry.path.write_text('{"version": 1}')
            code, malformed = cli("workspace list")
            assert code == 1 and "Invalid workspace registry" in malformed["error"]
            store.registry.path.unlink()
            legacy_root = tmp_path / "legacy"
            legacy_dir = (
                legacy_root / hashlib.sha256(identity.encode()).hexdigest()[:32]
            )
            legacy_dir.mkdir(parents=True)
            legacy_tools = tmp_path / "legacy-tools.json"
            legacy_tools.write_text(
                json.dumps(
                    {
                        "workspace": str(workspace),
                        "tools": [{"name": "read"}],
                    }
                )
            )
            legacy_record = legacy_dir / "connection.json"
            legacy_record.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "workspace": identity,
                        "secret": "a" * 43,
                        "public_origin": "https://kt-mcp-test.invalid",
                        "port": port(),
                        "tunnel": "external",
                        "tools_config": str(legacy_tools),
                    }
                )
            )
            legacy_bytes = legacy_record.read_bytes()
            code, migrated = cli(
                "migrate",
                "--workspace",
                workspace,
                "--name",
                "legacy",
                "--legacy-state-dir",
                legacy_root,
                root=other_home,
            )
            assert code == 0 and migrated["migrated"] and not migrated["running"]
            assert other.load().secret != "a" * 43
            assert legacy_record.read_bytes() == legacy_bytes
            (code, started), (_, racing) = await asyncio.gather(
                asyncio.to_thread(cli, "start"), asyncio.to_thread(cli, "start")
            )
            assert code == 1 and started["local_ready"] and not started["public_ready"]
            assert started["management"]["state"] == "ready"
            assert started["run_id"] == racing["run_id"]
            assert (await call("workspaces")).structuredContent["workspaces"] == []
            original = store.load()
            registered = cli("workspace add", "first", "./project")
            assert registered[0] == 0, registered[1]
            assert Path(registered[1]["path"]).samefile(workspace)
            assert cli("workspace add", "second", workspace)[0] == 0
            assert cli("url")[1] == original.url
            listed = (await call("workspaces")).structuredContent["workspaces"]
            assert all(item["state"] == "unloaded" for item in listed)
            (workspace / "note.txt").write_text("original")
            assert not (
                await call("read", {"workspace_id": "first", "path": "note.txt"})
            ).isError
            blocked = await call(
                "write",
                {"workspace_id": "second", "path": "note.txt", "content": "bad"},
            )
            assert (
                blocked.isError and (workspace / "note.txt").read_text() == "original"
            )
            assert (
                await call("python", {"code": "print('missing workspace')"})
            ).isError
            bg = await call(
                "python",
                {
                    "workspace_id": "first",
                    "code": "import time; time.sleep(60)",
                    "run_in_background": True,
                },
            )
            old_job = bg.structuredContent["job_id"]
            saved_control = store.control_path.read_bytes()
            store.control_path.write_text("{broken", encoding="utf-8")
            for _ in range(100):
                broken_status = store.runtime()
                if broken_status.get("management", {}).get("state") == "degraded":
                    break
                await asyncio.sleep(0.05)
            assert broken_status["management"]["state"] == "degraded"
            assert broken_status["state"] == "degraded" and broken_status["local_ready"]
            assert broken_status["instance_id"] == started["instance_id"]
            snapshot_code, snapshot_list = cli("workspace list")
            assert snapshot_code == 0 and snapshot_list["source"] == "registry_snapshot"
            assert {w["workspace_id"] for w in snapshot_list["workspaces"]} == {
                "first",
                "second",
            }
            assert all(w["state"] == "unknown" for w in snapshot_list["workspaces"])
            assert store.control_path.read_text() == "{broken"
            assert not (
                await call("read", {"workspace_id": "first", "path": "note.txt"})
            ).isError
            assert (
                await call("job_status", {"workspace_id": "first", "job_id": old_job})
            ).structuredContent["state"] == "running"
            store.control_path.write_bytes(saved_control)
            for _ in range(100):
                if store.runtime().get("management", {}).get("state") == "ready":
                    break
                await asyncio.sleep(0.05)
            assert store.runtime()["management"]["state"] == "ready"
            assert (
                await call("job_status", {"workspace_id": "second", "job_id": old_job})
            ).isError
            assert cli("workspace remove", "first")[0] == 1
            assert cli("workspace remove", "first", "--force")[0] == 0
            assert cli("workspace add", "first", workspace)[0] == 0
            assert (
                await call("job_status", {"workspace_id": "first", "job_id": old_job})
            ).isError
            done = await call(
                "python", {"workspace_id": "first", "code": "print('before crash')"}
            )
            assert "before crash" in done.structuredContent["output"]
            old_job = done.structuredContent["job_id"]
            _, peer = cli("start", root=other_home)
            assert peer["local_ready"] and peer["instance_id"] != started["instance_id"]
            peer_workspaces = (await call("workspaces", owner=other)).structuredContent[
                "workspaces"
            ]
            assert [entry["workspace_id"] for entry in peer_workspaces] == ["legacy"]
            migrated_read = await call(
                "read", {"workspace_id": "legacy", "path": "note.txt"}, owner=other
            )
            assert (
                not migrated_read.isError
                and "original" in migrated_read.structuredContent["output"]
            )
            assert (
                await call(
                    "python",
                    {"workspace_id": "legacy", "code": "print('disabled')"},
                    owner=other,
                )
            ).isError
            assert legacy_record.read_bytes() == legacy_bytes
            assert cli("rotate")[0] == 1
            os.kill(
                store.runtime()["pid"],
                signal.SIGTERM if os.name == "nt" else signal.SIGKILL,
            )
            for _ in range(100):
                if not is_running(store):
                    break
                await asyncio.sleep(0.05)
            assert not is_running(store)
            store.control_path.write_text(
                json.dumps(
                    {
                        "run_id": started["run_id"],
                        "request_id": "stale-command",
                        "operation": "add",
                        "name": "stale",
                        "path": str(workspace),
                    }
                )
            )
            store.response_path.write_text("{broken")
            _, restarted = cli("start")
            restart_run_id = restarted.get("run_id")
            assert restart_run_id and restart_run_id != started["run_id"], restarted
            deadline = asyncio.get_running_loop().time() + 8
            while not (restarted.get("instance_id") and restarted.get("local_ready")):
                assert restarted.get("running") and restarted.get("state") not in {
                    "failed",
                    "stopped",
                }, restarted
                assert asyncio.get_running_loop().time() < deadline, restarted
                await asyncio.sleep(0.05)
                restarted = status(store)
                assert restarted.get("run_id") == restart_run_id, restarted
            assert (
                restarted["instance_id"] != started["instance_id"]
                and restarted["local_ready"]
            ), restarted
            assert cli("workspace add", "after-restart", workspace)[0] == 0
            assert "stale" not in store.registry.read()
            stale = await call(
                "job_status", {"workspace_id": "first", "job_id": old_job}
            )
            assert (
                stale.isError and "restart" in stale.structuredContent["recovery_hint"]
            )
            assert (
                cli("status", root=other_home)[1]["instance_id"] == peer["instance_id"]
            )
            settings = home / "tools.json"
            settings.write_text(json.dumps({"tools": [{"name": "read"}]}))
            assert cli("setup", "--non-interactive", "--config", settings)[0] == 0
            settings.write_text("tools: [\n")
            code, invalid_pending = cli("status")
            assert code == 0 and invalid_pending["local_ready"]
            assert invalid_pending["instance_id"] == restarted["instance_id"]
            assert invalid_pending["configured"]["tools_revision"] == "invalid"
            assert invalid_pending["active"]["tools_revision"] != "invalid"
            settings.write_text(json.dumps({"tools": [{"name": "read"}]}))
            assert cli("workspace add", "third", workspace)[0] == 0
            before_restart = await call(
                "python", {"workspace_id": "third", "code": "print('snapshot')"}
            )
            assert (
                not before_restart.isError
                and "snapshot" in before_restart.structuredContent["output"]
            )
            assert cli("stop")[1]["state"] == "stopped"
            assert cli("rotate")[0] == 0
            rotated = store.load()
            assert rotated.secret != original.secret
            _, applied = cli("start")
            assert applied["local_ready"]
            async with httpx.AsyncClient(trust_env=False) as http:
                rejected = await http.get(
                    f"http://127.0.0.1:{rotated.port}/mcp/{original.secret}"
                )
                assert rejected.status_code == 404
            assert (
                await call(
                    "python", {"workspace_id": "first", "code": "print('disabled')"}
                )
            ).isError
            assert not (
                await call("read", {"workspace_id": "first", "path": "note.txt"})
            ).isError
            assert cli("stop")[1]["state"] == "stopped"
            with socket.socket() as occupied:
                occupied.bind(("127.0.0.1", 0))
                occupied.listen()
                assert (
                    cli(
                        "setup",
                        "--non-interactive",
                        "--port",
                        occupied.getsockname()[1],
                    )[0]
                    == 0
                )
                code, collision = cli("start")
                assert code == 1 and "unavailable" in collision["error"]
            tunnel_log = store.directory / "tunnel.log"
            tunnel_log.mkdir()
            (home / "http").write_text(
                "import json,sys,time\nfrom pathlib import Path\n"
                "with Path('ingress-observed.jsonl').open('a') as f:\n"
                "    f.write(json.dumps(sys.argv[1:]) + '\\n')\n"
                "time.sleep(.2)\nraise SystemExit(1)\n"
            )
            assert (
                cli(
                    "setup",
                    "--non-interactive",
                    "--clear-config",
                    "--port",
                    port(),
                    "--mode",
                    "ngrok",
                    "--ngrok-bin",
                    sys.executable,
                )[0]
                == 0
            )
            _, ingress_failed = cli("start")
            assert ingress_failed["local_ready"] and "launch" in ingress_failed["error"]
            bg = await call(
                "python",
                {
                    "workspace_id": "first",
                    "code": "import time; time.sleep(3); print('survived')",
                    "run_in_background": True,
                },
            )
            tunnel_log.rmdir()
            active = store.load()
            assert (
                cli(
                    "setup",
                    "--non-interactive",
                    "--mode",
                    "external",
                    "--origin",
                    "https://next.invalid",
                )[0]
                == 0
            )
            _, pending = cli("start")
            assert pending["instance_id"] == ingress_failed["instance_id"]
            assert (
                pending["public_origin"] == active.public_origin
                and pending["restart_required"]
            )
            done = await call(
                "job_wait",
                {
                    "workspace_id": "first",
                    "job_id": bg.structuredContent["job_id"],
                    "timeout": 10,
                },
            )
            assert (
                done.structuredContent["state"] == "done"
                and "survived" in done.structuredContent["output"]
            )
            observed = home / "ingress-observed.jsonl"
            for _ in range(100):
                if observed.exists() and observed.read_text().strip():
                    break
                await asyncio.sleep(0.2)
            for line in observed.read_text().splitlines():
                args = json.loads(line)
                assert (
                    active.public_origin in args and "https://next.invalid" not in args
                )
            assert cli("stop")[1]["state"] == "stopped"
            _, reapplied = cli("start")
            assert reapplied["public_origin"] == "https://next.invalid"
            assert reapplied["local_ready"] and not reapplied["restart_required"]
        finally:
            for root, owner in ((home, store), (other_home, other)):
                if owner.record_path.exists():
                    cli("stop", root=root)

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

        startup_plugin = tmp_path / "startup.py"
        startup_plugin.write_text(
            "import asyncio\n"
            "from kohakuterrarium.modules.plugin.base import BasePlugin\n"
            "class Startup(BasePlugin):\n"
            "    name = 'startup'\n"
            "    async def on_load(self, context):\n"
            "        self.root = context.working_dir\n"
            "        (self.root / 'startup-entered').touch()\n"
            "        try:\n"
            "            while not (self.root / 'startup-release').exists():\n"
            "                await asyncio.sleep(.01)\n"
            "        except asyncio.CancelledError:\n"
            "            (self.root / 'startup-cancelled').touch()\n"
            "            raise\n"
            "    async def on_unload(self):\n"
            "        (self.root / 'startup-unloaded').touch()\n"
        )
        blocked_path = tmp_path / "blocked.json"
        blocked_path.write_text(
            json.dumps(
                {
                    "name": "blocked",
                    "tools": [],
                    "plugins": [
                        {
                            "name": "startup",
                            "type": "custom",
                            "module": str(startup_plugin),
                            "class": "Startup",
                        }
                    ],
                }
            )
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

        registry = WorkspaceRegistry(tmp_path / "workspaces.json")
        registry.add("main", tmp_path)
        registry.add("other", tmp_path)
        app = create_app(
            GlobalToolsConfig.model_validate(
                {
                    "delegation": {
                        "worker": {"kind": "creature", "config": str(creature_path)},
                        "writer": {"kind": "subagent", "config": str(subagent_path)},
                        "blocked": {"kind": "subagent", "config": str(blocked_path)},
                    },
                }
            ),
            secret=secret,
            port=8765,
            llm_factory=provider,
            registry=registry,
            base_dir=tmp_path,
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
                        assert len(tools) == 20
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
                            result = await client.call_tool(
                                name, {"workspace_id": "main", **args}
                            )
                            return result, json.loads(result.content[0].text)

                        _, targets = await call("delegation_targets", {})
                        assert {t["name"] for t in targets["targets"]} == {
                            "worker",
                            "writer",
                            "blocked",
                        }
                        _, starting = await call(
                            "delegate", {"target": "blocked", "prompt": "task"}
                        )
                        try:
                            for _ in range(300):
                                if (tmp_path / "startup-entered").exists():
                                    break
                                await asyncio.sleep(0.01)
                            assert (tmp_path / "startup-entered").exists()
                            _, cancellation = await asyncio.wait_for(
                                call("job_cancel", {"job_id": starting["job_id"]}), 5
                            )
                            assert cancellation["cancelled"]
                            assert (tmp_path / "startup-cancelled").exists()
                            assert (tmp_path / "startup-unloaded").exists()
                            assert (
                                await call("job_status", {"job_id": starting["job_id"]})
                            )[1]["state"] == "cancelled"
                            assert not (
                                await call(
                                    "delegation_close",
                                    {"session_id": starting["session_id"]},
                                )
                            )[0].isError
                        finally:
                            (tmp_path / "startup-release").touch()
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
                                    "workspace_id": "main",
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
                        for operation, field in (
                            ("job_cancel", "cancelled"),
                            ("job_promote", "promoted"),
                        ):
                            result, unchanged = await call(
                                operation, {"job_id": delegated["job_id"]}
                            )
                            assert not result.isError and unchanged[field] is False
                            assert (await call(operation, {"job_id": "nonexistent"}))[
                                0
                            ].isError
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
                        foreign = await client.call_tool(
                            "delegate",
                            {
                                "workspace_id": "other",
                                "target": "worker",
                                "session_id": sid,
                                "prompt": "hello",
                            },
                        )
                        assert (
                            foreign.isError
                            and "Unknown session" in foreign.structuredContent["error"]
                        )
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
                        for query in ("job_wait", "job_status"):
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
                                            "workspace_id": "main",
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
            app.state.workspace_pool.config,
            secret=secret,
            port=8765,
            registry=registry,
            base_dir=tmp_path,
            llm_factory=provider,
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
                            "job_status", {"workspace_id": "main", "job_id": job_id}
                        )
                        assert (
                            missing.isError
                            and missing.structuredContent["error"] == "Unknown job"
                        )
                        stale_session = await client.call_tool(
                            "delegate",
                            {
                                "workspace_id": "main",
                                "target": "worker",
                                "session_id": sid,
                                "prompt": "hello",
                            },
                        )
                        assert (
                            stale_session.isError
                            and "Unknown session"
                            in stale_session.structuredContent["error"]
                        )
                        blocked = await client.call_tool(
                            "write",
                            {
                                "workspace_id": "main",
                                "path": "note.txt",
                                "content": "unread",
                            },
                        )
                        assert (
                            blocked.isError
                            and (tmp_path / "note.txt").read_text() == "after"
                        )
                        readback = await client.call_tool(
                            "read", {"workspace_id": "main", "path": "note.txt"}
                        )
                        assert (
                            not readback.isError
                            and "after" in readback.structuredContent["output"]
                        )
