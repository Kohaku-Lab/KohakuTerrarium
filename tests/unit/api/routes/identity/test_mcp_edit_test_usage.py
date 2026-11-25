"""Identity MCP — PATCH / /test / /usage routes."""

import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.routes.identity import mcp as mcp_mod
from kohakuterrarium.errors import MCPConnectionError, MCPTimeoutError
from kohakuterrarium.mcp.client import MCPClientManager

if sys.version_info < (3, 11):  # pragma: no cover - CI runs 3.12+
    from exceptiongroup import ExceptionGroup


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
    app = FastAPI()
    app.state.lab_mode = "standalone"
    app.include_router(mcp_mod.router, prefix="/api/settings")
    c = TestClient(app)
    # Seed one server.
    c.post(
        "/api/settings/mcp",
        json={
            "name": "fs",
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem"],
        },
    )
    return c


class TestPatch:
    def test_patch_updates_only_set_fields(self, client):
        resp = client.patch(
            "/api/settings/mcp/fs",
            json={"args": ["-y", "newargs"]},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["server"]["args"] == ["-y", "newargs"]
        # Unchanged fields preserved.
        assert body["server"]["command"] == "npx"

    def test_patch_headers_roundtrip_and_clear(self, client):
        auth = {"Authorization": "Bearer t0ken"}
        resp = client.patch(
            "/api/settings/mcp/fs",
            json={"transport": "http", "url": "http://h/mcp", "headers": auth},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["server"]["headers"] == auth
        stored = {
            s["name"]: s for s in client.get("/api/settings/mcp").json()["servers"]
        }
        assert stored["fs"]["headers"] == auth

        client.patch("/api/settings/mcp/fs", json={"url": "http://h/other"})
        stored = {
            s["name"]: s for s in client.get("/api/settings/mcp").json()["servers"]
        }
        assert stored["fs"]["headers"] == auth, "an unrelated patch keeps the headers"

        client.patch("/api/settings/mcp/fs", json={"headers": {}})
        stored = {
            s["name"]: s for s in client.get("/api/settings/mcp").json()["servers"]
        }
        assert stored["fs"]["headers"] == {}

    def test_patch_name_immutable(self, client):
        resp = client.patch(
            "/api/settings/mcp/fs",
            json={"command": "node"},
        )
        # Body has no name key — server keeps "fs".
        assert resp.status_code == 200
        assert resp.json()["server"]["name"] == "fs"

    def test_patch_unknown_404(self, client):
        resp = client.patch(
            "/api/settings/mcp/nosuch",
            json={"command": "x"},
        )
        assert resp.status_code == 404


class TestTest:
    def test_test_unknown_404(self, client):
        resp = client.post("/api/settings/mcp/nosuch/test")
        assert resp.status_code == 404

    def test_test_failure_returns_ok_false(self, client, monkeypatch):
        # Force the inline probe to raise — we want a clean ok=False,
        # not a 500.
        async def _boom(server):
            raise RuntimeError("simulated")

        monkeypatch.setattr(mcp_mod, "_probe_server", _boom)
        resp = client.post("/api/settings/mcp/fs/test")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is False
        assert "simulated" in body["error"]
        assert body["elapsed_ms"] is not None

    def test_typed_mcp_error_is_shown_as_is(self, client, monkeypatch):
        async def _refused(self, config):
            raise MCPConnectionError(
                "MCP server fs: HTTP 401 Unauthorized from http://h/mcp; "
                "check the headers/token"
            )

        monkeypatch.setattr(MCPClientManager, "connect", _refused)
        body = client.post("/api/settings/mcp/fs/test").json()
        assert body["ok"] is False
        assert body["error"] == (
            "MCP server fs: HTTP 401 Unauthorized from http://h/mcp; "
            "check the headers/token"
        )

    def test_exception_group_is_unwrapped_to_its_cause(self, client, monkeypatch):
        async def _grouped(server):
            raise ExceptionGroup(
                "unhandled errors in a TaskGroup", [OSError("socket gone")]
            )

        monkeypatch.setattr(mcp_mod, "_probe_server", _grouped)
        body = client.post("/api/settings/mcp/fs/test").json()
        assert body["ok"] is False
        assert body["error"] == "OSError: socket gone"

    def test_probe_applies_the_default_connect_timeout(self, client, monkeypatch):
        seen = []

        async def _capture(self, config):
            seen.append(config.connect_timeout)
            raise MCPTimeoutError("MCP server fs: timed out after 20s")

        monkeypatch.setattr(MCPClientManager, "connect", _capture)
        body = client.post("/api/settings/mcp/fs/test").json()
        assert seen == [20.0]
        assert body["error"] == "MCP server fs: timed out after 20s"

        client.patch("/api/settings/mcp/fs", json={"connect_timeout": 3})
        client.post("/api/settings/mcp/fs/test")
        assert seen[-1] == 3

    def test_test_success_path(self, client, monkeypatch):
        async def _fake(server):
            return {"tool_count": 7}

        monkeypatch.setattr(mcp_mod, "_probe_server", _fake)
        resp = client.post("/api/settings/mcp/fs/test")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["tool_count"] == 7


class TestUsage:
    def test_usage_empty_returns_empty_list(self, client, monkeypatch):
        monkeypatch.setattr(mcp_mod, "find_creatures_using_server", lambda name: [])
        resp = client.get("/api/settings/mcp/fs/usage")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_usage_reports_refs(self, client, monkeypatch):
        monkeypatch.setattr(
            mcp_mod,
            "find_creatures_using_server",
            lambda name: [
                {"name": "alpha", "kind": "creature", "path": "/p/a.yaml"},
                {"name": "beta", "kind": "terrarium", "path": "/p/b.yaml"},
            ],
        )
        resp = client.get("/api/settings/mcp/fs/usage")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 2
        assert body[0]["name"] == "alpha"
        assert body[1]["kind"] == "terrarium"
