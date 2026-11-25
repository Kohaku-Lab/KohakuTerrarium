"""Unit tests for ``kohakuterrarium.testing.mcp_http``: the bearer-protected
streamable-HTTP MCP server the MCP integration and e2e tiers connect to."""

import httpx
import pytest

from kohakuterrarium.testing.mcp_http import bearer_mcp_server, closed_loopback_port

_INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "unit", "version": "0"},
    },
}
_ACCEPT = {"Accept": "application/json, text/event-stream"}


def test_server_rejects_missing_and_wrong_tokens_and_accepts_the_right_one():
    with bearer_mcp_server("tok") as base:
        with httpx.Client() as client:
            assert client.post(f"{base}/mcp", json=_INIT).status_code == 401
            wrong = {**_ACCEPT, "Authorization": "Bearer nope"}
            assert (
                client.post(f"{base}/mcp", json=_INIT, headers=wrong).status_code == 401
            )
            right = {**_ACCEPT, "Authorization": "Bearer tok"}
            resp = client.post(f"{base}/mcp", json=_INIT, headers=right)
            assert resp.status_code == 200
            assert "kt-bearer-test" in resp.text


def test_server_stops_when_the_context_exits():
    with bearer_mcp_server("tok") as base:
        pass
    with pytest.raises(httpx.ConnectError):
        httpx.post(f"{base}/mcp", json=_INIT, timeout=2)


def test_closed_loopback_port_refuses_connections():
    port = closed_loopback_port()
    with pytest.raises(httpx.ConnectError):
        httpx.get(f"http://127.0.0.1:{port}/", timeout=2)
