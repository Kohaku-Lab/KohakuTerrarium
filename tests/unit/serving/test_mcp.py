"""Ingress identity probes transfer initialization metadata, never job contents."""

import json
import asyncio
import socket

import httpx

from kohakuterrarium.mcp_server.endpoint import Endpoint, EndpointStore
from kohakuterrarium.mcp_server.management import manage
from kohakuterrarium.mcp_server.records import write_json
from kohakuterrarium.serving.mcp import probe_public, serve


async def test_public_probe_does_not_contact_environment_proxy(monkeypatch):
    contacted = []

    async def proxy(reader, writer):
        contacted.append(await reader.readline())
        writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(proxy, "127.0.0.1", 0)
    with socket.socket() as unavailable:
        unavailable.bind(("127.0.0.1", 0))
        proxy_url = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
            monkeypatch.delenv(name.lower(), raising=False)
            monkeypatch.setenv(name, proxy_url)
        monkeypatch.delenv("no_proxy", raising=False)
        monkeypatch.setenv("NO_PROXY", "")
        record = Endpoint(
            home_dir="home",
            public_origin=f"https://127.0.0.1:{unavailable.getsockname()[1]}",
            secret="a" * 43,
        )
        ok, _ = await probe_public(record, "expected")
        assert not ok
        server.close()
        await server.wait_closed()
        assert contacted == []


async def test_public_probe_is_bounded_and_matches_instance(monkeypatch):
    record = Endpoint(
        home_dir="home", public_origin="https://example.com", secret="a" * 43
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


async def test_new_supervisor_fences_old_unconfirmed_commands(tmp_path):
    store = EndpointStore(tmp_path / "home")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    record = store.configure(
        public_origin="https://example.invalid", port=port, tunnel="external"
    )
    store.save_active("new", record)
    write_json(
        store.control_path,
        {
            "run_id": "old",
            "request_id": "pending",
            "operation": "add",
            "name": "stale",
            "path": str(tmp_path),
        },
    )
    store.response_path.write_text("{broken")
    with store.instance_lock:
        task = asyncio.create_task(serve(store, "new"))
        try:
            for _ in range(400):
                if store.runtime().get("management", {}).get("state") == "ready":
                    break
                await asyncio.sleep(0.01)
            assert store.runtime()["management"]["state"] == "ready"
            result = await asyncio.to_thread(
                manage, store, "add", name="new", path=tmp_path, wait=2
            )
            assert result["workspace_id"] == "new"
            assert set(store.registry.read()) == {"new"}
        finally:
            write_json(store.stop_path, {"run_id": "new"})
            await asyncio.wait_for(task, 10)
