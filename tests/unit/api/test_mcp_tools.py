"""MCP response conversion and strict transport configuration."""

import json

import httpx
import pytest
from PIL import Image

from kohakuterrarium.api.mcp_tools import _job_reply, _reply, create_app
from kohakuterrarium.core.job import JobResult
from kohakuterrarium.llm.message import ImagePart
from kohakuterrarium.mcp_server.config import MCPToolsConfig


def test_image_and_error_delivery():
    result = JobResult(
        "image", output=[ImagePart(url="data:image/png;base64,aGVsbG8=")]
    )
    reply = _reply({"job_id": "image", "output": "image"}, result=result)
    assert reply.content[1].data == "aGVsbG8="
    assert reply.content[1].mimeType == "image/png"
    assert json.loads(reply.content[0].text)["job_id"] == "image"
    assert _reply({"exit_code": 1}).isError
    assert _reply({"error": "denied"}).isError


def test_local_image_delivery(tmp_path):
    path = tmp_path / "image.png"
    Image.new("RGB", (1, 1)).save(path)
    result = JobResult("image", output=[ImagePart(url=path.as_uri())])
    reply = _reply({"job_id": "image"}, result=result)
    assert len(reply.content) == 2
    assert reply.content[1].mimeType == "image/png"


@pytest.mark.parametrize("state, exit_code", [("cancelled", None), ("error", 1)])
def test_retained_job_failure_is_successful_query(state, exit_code):
    data = {
        "job_id": "retained",
        "state": state,
        "error": "job failed",
        "exit_code": exit_code,
    }
    reply = _job_reply(data)
    assert not reply.isError
    assert reply.structuredContent == data
    assert json.loads(reply.content[0].text) == data
    assert _job_reply({"job_id": "missing", "error": "Unknown job"}).isError
    assert _reply(data).isError


@pytest.mark.parametrize(
    "origin",
    [
        "http://example.com",
        "https://example.com/path",
        "https://user:pass@example.com",
        "https://example.com?secret=x",
    ],
)
def test_rejects_invalid_origin(tmp_path, origin):
    with pytest.raises(ValueError, match="origin"):
        create_app(
            MCPToolsConfig(workspace=tmp_path), secret="a" * 43, public_origin=origin
        )


@pytest.mark.parametrize("origin", ["https://example.com", "https://example.com:443"])
async def test_default_https_port_equivalence_and_host_boundary(tmp_path, origin):
    app = create_app(
        MCPToolsConfig(workspace=tmp_path), secret="a" * 43, public_origin=origin
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app)) as client:
            for host, request_origin, expected in [
                ("example.com", "https://example.com", 200),
                ("example.com:443", "https://example.com:443", 200),
                ("example.com:8443", "https://example.com", 421),
                ("other.example", "https://example.com", 421),
                ("example.com", "https://other.example", 403),
            ]:
                response = await client.post(
                    origin + "/mcp/" + "a" * 43,
                    headers={
                        "host": host,
                        "origin": request_origin,
                        "accept": "application/json, text/event-stream",
                    },
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2025-03-26",
                            "capabilities": {},
                            "clientInfo": {"name": "test", "version": "1"},
                        },
                    },
                )
                assert response.status_code == expected, response.text
