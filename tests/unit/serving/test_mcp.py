"""Ingress identity probes transfer initialization metadata, never job contents."""

import json

import httpx

from kohakuterrarium.mcp_server.connection import Connection
from kohakuterrarium.serving.mcp import probe_public


async def test_public_probe_is_bounded_and_matches_instance(monkeypatch):
    record = Connection(
        workspace="work", public_origin="https://example.com", secret="a" * 43
    )
    seen = []

    def endpoint(request):
        data = json.loads(request.content)
        seen.append(data["method"])
        assert data["method"] == "initialize"
        return httpx.Response(
            200,
            json={"result": {"instructions": "KT instance_id: expected\nTools only."}},
        )

    client_class = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: client_class(
            transport=httpx.MockTransport(endpoint), **kwargs
        ),
    )
    assert await probe_public(record, "expected") == (True, None)
    ok, error = await probe_public(record, "different")
    assert not ok and "this instance" in error
    assert seen == ["initialize", "initialize"]
