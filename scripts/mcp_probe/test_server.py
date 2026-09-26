"""Real HTTP acceptance tests for the disposable MCP ingress experiment.

Run from the repository root: python -m unittest scripts.mcp_probe.test_server -v
No KT runtime is imported; this only tests the transport hypothesis.
"""

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

SERVER = Path(__file__).with_name("server.py")


class ProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_secret_transport_read_write_and_restart(self):
        self.assertTrue(SERVER.is_file(), "The MCP ingress probe is not implemented")
        for mode in ("json", "sse"):
            with (
                self.subTest(mode=mode),
                tempfile.TemporaryDirectory(
                    dir=os.environ.get("KT_PROBE_TEST_TMP")
                ) as folder,
            ):
                root = Path(folder)
                workspace = root / "workspace"
                workspace.mkdir()
                state = root / "connection.json"
                with socket.socket() as sock:
                    sock.bind(("127.0.0.1", 0))
                    port = sock.getsockname()[1]
                origin = f"http://127.0.0.1:{port}"
                command = [
                    sys.executable,
                    str(SERVER),
                    "--workspace",
                    str(workspace),
                    "--state-file",
                    str(state),
                    "--port",
                    str(port),
                    "--response-mode",
                    mode,
                    "--public-origin",
                    "https://probe.example",
                ]
                logs = root / "server.log"
                boot_ids = []
                first_record = None
                for restart in range(2):
                    with logs.open("ab") as log:
                        process = subprocess.Popen(
                            command,
                            stdout=log,
                            stderr=log,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                        )
                        try:
                            async with httpx.AsyncClient(
                                timeout=2, trust_env=False
                            ) as client:
                                for _ in range(100):
                                    if process.poll() is not None:
                                        self.fail(
                                            "Probe exited before listening: "
                                            + logs.read_text()
                                        )
                                    try:
                                        response = await client.get(origin + "/")
                                        if response.status_code == 404:
                                            break
                                    except httpx.TransportError:
                                        pass
                                    await asyncio.sleep(0.1)
                                else:
                                    self.fail("Probe did not listen within 10 seconds")
                                record = json.loads(state.read_text())
                                if first_record is None:
                                    first_record = record
                                self.assertEqual(record, first_record)
                                secret = record["secret"]
                                self.assertGreaterEqual(len(secret), 43)
                                endpoint = origin + "/mcp/" + secret
                                shown = await asyncio.to_thread(
                                    subprocess.run,
                                    command + ["--show-url"],
                                    capture_output=True,
                                    timeout=10,
                                    check=True,
                                )
                                self.assertEqual(
                                    shown.stdout.decode().strip(),
                                    "https://probe.example/mcp/" + secret,
                                )
                                request = {
                                    "jsonrpc": "2.0",
                                    "id": 1,
                                    "method": "tools/list",
                                }
                                for path in (
                                    "/mcp",
                                    "/mcp/",
                                    "/mcp/wrong",
                                    "/sse",
                                    "/docs",
                                    "/api",
                                    "/.well-known/oauth-authorization-server",
                                    "/mcp/" + secret + "/",
                                ):
                                    for method in ("GET", "POST", "DELETE", "OPTIONS"):
                                        response = await client.request(
                                            method, origin + path, json=request
                                        )
                                        self.assertEqual(
                                            response.status_code, 404, (path, method)
                                        )
                                        self.assertNotIn(secret, response.text)
                                blocked = await client.post(
                                    endpoint,
                                    json=request,
                                    headers={"Host": "evil.example"},
                                )
                                self.assertEqual(blocked.status_code, 421)
                                blocked = await client.post(
                                    endpoint,
                                    json=request,
                                    headers={"Origin": "https://evil.example"},
                                )
                                self.assertEqual(blocked.status_code, 403)
                                public = await client.post(
                                    endpoint,
                                    json=request,
                                    headers={
                                        "Host": "probe.example",
                                        "Origin": "https://probe.example",
                                        "Accept": "application/json, text/event-stream",
                                    },
                                )
                                self.assertEqual(public.status_code, 200)
                                expected_type = (
                                    "application/json"
                                    if mode == "json"
                                    else "text/event-stream"
                                )
                                self.assertTrue(
                                    public.headers["content-type"].startswith(
                                        expected_type
                                    )
                                )

                            async with streamable_http_client(endpoint) as (
                                read,
                                write,
                                _,
                            ):
                                async with ClientSession(read, write) as session:
                                    await session.initialize()
                                    listing = await session.list_tools()
                                    self.assertEqual(
                                        {tool.name for tool in listing.tools},
                                        {"probe_status", "probe_read", "probe_write"},
                                    )
                                    self.assertNotIn(secret, listing.model_dump_json())
                                    status = await session.call_tool("probe_status", {})
                                    self.assertFalse(status.isError)
                                    boot_ids.append(status.structuredContent["boot_id"])
                                    text = (
                                        "ChatGPT MCP probe: \u4f60\u597d\nsecond line"
                                    )
                                    if restart == 0:
                                        written = await session.call_tool(
                                            "probe_write", {"content": text}
                                        )
                                        self.assertFalse(written.isError)
                                        self.assertEqual(
                                            (workspace / "probe.txt").read_text(
                                                encoding="utf-8"
                                            ),
                                            text,
                                        )
                                    result = await session.call_tool("probe_read", {})
                                    self.assertFalse(result.isError)
                                    self.assertEqual(
                                        result.structuredContent["content"], text
                                    )
                                    self.assertNotIn(secret, result.model_dump_json())
                                    invalid = await session.call_tool(
                                        "probe_write", {"content": "x" * 8193}
                                    )
                                    self.assertTrue(invalid.isError)
                                    self.assertEqual(
                                        (workspace / "probe.txt").read_text(
                                            encoding="utf-8"
                                        ),
                                        text,
                                    )
                        finally:
                            process.terminate()
                            await asyncio.to_thread(process.wait, 10)
                self.assertNotEqual(*boot_ids)
                self.assertNotIn(secret, logs.read_text())
                # A saved URL must never silently become a different workspace.
                other = root / "other"
                other.mkdir()
                rebound = command.copy()
                rebound[rebound.index("--workspace") + 1] = str(other)
                refused = await asyncio.to_thread(
                    subprocess.run, rebound, capture_output=True, timeout=10
                )
                self.assertNotEqual(refused.returncode, 0)
                self.assertEqual(json.loads(state.read_text()), first_record)
                self.assertNotIn(secret, refused.stderr.decode())
                state.write_text("{broken json", encoding="utf-8")
                corrupt = await asyncio.to_thread(
                    subprocess.run, command, capture_output=True, timeout=10
                )
                self.assertNotEqual(corrupt.returncode, 0)
                self.assertEqual(state.read_text(), "{broken json")


if __name__ == "__main__":
    unittest.main()
