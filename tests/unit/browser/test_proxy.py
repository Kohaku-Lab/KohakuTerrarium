"""Unit tests for kohakuterrarium.browser.proxy, driven over real loopback sockets."""

import asyncio
import hashlib
import os
import socket

import httpx
import pytest

from kohakuterrarium.browser import proxy as proxy_module
from kohakuterrarium.browser.policy import NetworkPolicy
from kohakuterrarium.browser.proxy import BLOCKED_HEADER, EgressRecord, FilteringProxy

PUBLIC = "203.0.113.5"
PUBLIC_2 = "203.0.113.6"
PRIVATE = "10.0.0.5"
BLOCKED = BLOCKED_HEADER.lower()


# ---------------------------------------------------------------------------
# Loopback test servers and seams
# ---------------------------------------------------------------------------


class LoopbackServer:
    """A loopback TCP server whose per-connection behaviour is a coroutine."""

    def __init__(self, handler):
        self.handler = handler
        self.connections = 0
        self.received: list[bytes] = []
        self.closed_by_peer = asyncio.Event()
        self._writers: set[asyncio.StreamWriter] = set()
        self._server: asyncio.base_events.Server | None = None

    async def start(self):
        self._server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def _serve(self, reader, writer):
        self.connections += 1
        self._writers.add(writer)
        try:
            await self.handler(self, reader, writer)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def close(self):
        self._server.close()
        for writer in list(self._writers):
            writer.close()
        await self._server.wait_closed()


async def read_request(reader):
    head = await reader.readuntil(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    headers = {}
    for line in lines[1:]:
        if line:
            name, value = line.split(":", 1)
            headers[name.strip().lower()] = value.strip()
    body = b""
    if "content-length" in headers:
        body = await reader.readexactly(int(headers["content-length"]))
    elif headers.get("transfer-encoding") == "chunked":
        while True:
            size = int((await reader.readline()).split(b";")[0], 16)
            chunk = await reader.readexactly(size + 2)
            if size == 0:
                break
            body += chunk[:-2]
    return lines[0], headers, body


async def http_echo(server, reader, writer):
    request_line, headers, body = await read_request(reader)
    server.received.append(body)
    server.last_request = (request_line, headers)
    payload = (
        f"{request_line}\n".encode()
        + b"\n".join(f"{k}: {v}".encode() for k, v in sorted(headers.items()))
        + b"\n\n"
        + body
    )
    writer.write(
        b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n"
        b"Connection: keep-alive\r\nKeep-Alive: timeout=5\r\n"
        + f"Content-Length: {len(payload)}\r\n\r\n".encode()
        + payload
    )
    await writer.drain()


async def tcp_echo(server, reader, writer):
    writer.write(b"greeting\n")
    await writer.drain()
    while data := await reader.read(65536):
        server.received.append(data)
        writer.write(data)
        await writer.drain()
    server.closed_by_peer.set()
    writer.write(b"bye")
    await writer.drain()


class Seams:
    """Resolver and dialer seams: names map to IPs, IPs map to loopback servers."""

    def __init__(self, names=None, routes=None):
        self.names = dict(names or {})
        self.routes = dict(routes or {})
        self.resolved: list[str] = []
        self.dialed: list[tuple[str, int]] = []

    async def resolve(self, host, port):
        self.resolved.append(host)
        answer = self.names.get(host)
        if answer is None:
            raise OSError(f"no such host {host}")
        if callable(answer):
            return answer()
        return list(answer)

    async def dial(self, host, port):
        self.dialed.append((host, port))
        target = self.routes.get(host)
        if target is None:
            # simulate an OS connect by name: a second, independent resolution
            ips = await self.resolve(host, port)
            target = self.routes.get(ips[0])
        if target is None:
            raise ConnectionRefusedError(f"nothing at {host}")
        return await asyncio.open_connection("127.0.0.1", target.port)


@pytest.fixture
async def servers(make_proxy):
    """Loopback test servers, closed before the proxies are checked for leftovers."""
    started: list[LoopbackServer] = []

    async def make(handler=http_echo):
        server = await LoopbackServer(handler).start()
        started.append(server)
        return server

    yield make
    for server in started:
        await server.close()


@pytest.fixture
async def make_proxy():
    before = asyncio.all_tasks()
    proxies: list[FilteringProxy] = []

    async def make(policy=None, seams=None, **kwargs):
        if seams is not None:
            kwargs.setdefault("resolver", seams.resolve)
            kwargs.setdefault("dialer", seams.dial)
        proxy = FilteringProxy(policy or NetworkPolicy(), **kwargs)
        await proxy.start()
        proxies.append(proxy)
        return proxy

    yield make
    for proxy in proxies:
        await proxy.close()
    assert_no_proxy_tasks(before)


async def exchange(proxy, raw: bytes) -> tuple[int, dict[str, str], bytes]:
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
    try:
        writer.write(raw)
        await writer.drain()
        data = await asyncio.wait_for(reader.read(), 10)
    finally:
        writer.close()
        await writer.wait_closed()
    return parse_response(data)


def parse_response(data: bytes) -> tuple[int, dict[str, str], bytes]:
    head, _, body = data.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    status = int(lines[0].split(" ")[1])
    headers = {}
    for line in lines[1:]:
        name, value = line.split(":", 1)
        headers[name.strip().lower()] = value.strip()
    return status, headers, body


async def open_tunnel(proxy, target: str):
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
    writer.write(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())
    await writer.drain()
    head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
    return head, reader, writer


def get(url: str, host: str, extra: str = "") -> bytes:
    return (
        f"GET {url} HTTP/1.1\r\nHost: {host}\r\nProxy-Connection: keep-alive\r\n"
        f"{extra}\r\n"
    ).encode()


# ---------------------------------------------------------------------------
# Plain HTTP
# ---------------------------------------------------------------------------


async def test_plain_get_is_forwarded_in_origin_form(servers, make_proxy):
    upstream = await servers()
    seams = Seams({"public.test": [PUBLIC]}, {PUBLIC: upstream})
    proxy = await make_proxy(seams=seams)
    assert proxy.url == f"http://127.0.0.1:{proxy.port}"

    status, headers, body = await exchange(
        proxy,
        get(
            "http://Public.Test:8080/hello?x=1",
            "ignored.example",
            "Connection: X-Hop\r\nX-Hop: drop-me\r\nX-Keep: yes\r\n"
            "Proxy-Authorization: Basic abc\r\n",
        ),
    )
    assert status == 200
    request_line, sent = upstream.last_request
    assert request_line == "GET /hello?x=1 HTTP/1.1"
    assert sent["host"] == "Public.Test:8080"
    assert sent["connection"] == "close"
    assert sent["x-keep"] == "yes"
    for hop in ("proxy-connection", "x-hop", "proxy-authorization", "keep-alive"):
        assert hop not in sent
    # the response is rewritten so the browser never reuses the connection
    assert headers["connection"] == "close"
    assert "keep-alive" not in headers
    assert body.startswith(b"GET /hello?x=1 HTTP/1.1\n")
    assert seams.dialed == [(PUBLIC, 8080)]
    assert proxy.recent() == [
        EgressRecord("public.test", 8080, "GET", False, "ok", PUBLIC)
    ]
    assert proxy.drain_blocked() == []


async def test_post_bodies_arrive_intact(servers, make_proxy):
    upstream = await servers()
    proxy = await make_proxy(seams=Seams({"public.test": [PUBLIC]}, {PUBLIC: upstream}))
    body = os.urandom(300_000)
    status, _, echoed = await exchange(
        proxy,
        b"POST http://public.test/form HTTP/1.1\r\nHost: public.test\r\n"
        b"Content-Type: application/octet-stream\r\n"
        + f"Content-Length: {len(body)}\r\n\r\n".encode()
        + body,
    )
    assert status == 200
    assert upstream.received == [body]
    assert echoed.endswith(body)

    chunked = (
        b"POST http://public.test/form HTTP/1.1\r\nHost: public.test\r\n"
        b"Transfer-Encoding: chunked\r\n\r\n"
        b"5;ext=1\r\nhello\r\n6\r\n world\r\n0\r\nX-Trailer: t\r\n\r\n"
    )
    status, _, _ = await exchange(proxy, chunked)
    assert status == 200
    assert upstream.received[-1] == b"hello world"


async def test_bytes_after_the_request_are_not_forwarded(servers, make_proxy):
    upstream = await servers()
    proxy = await make_proxy(seams=Seams({"public.test": [PUBLIC]}, {PUBLIC: upstream}))
    smuggled = get("http://private.test/", "private.test")
    status, _, _ = await exchange(
        proxy,
        b"POST http://public.test/ HTTP/1.1\r\nHost: public.test\r\n"
        b"Content-Length: 3\r\n\r\nabc" + smuggled,
    )
    assert status == 200
    assert upstream.received == [b"abc"]
    assert upstream.connections == 1


async def test_large_response_is_streamed_not_buffered(servers, make_proxy):
    chunk = b"x" * 65536
    count = 128
    release = asyncio.Event()

    async def big(server, reader, writer):
        await read_request(reader)
        writer.write(
            b"HTTP/1.1 200 OK\r\n"
            + f"Content-Length: {len(chunk) * count}\r\n\r\n".encode()
            + chunk
        )
        await writer.drain()
        await asyncio.wait_for(release.wait(), 10)
        for _ in range(count - 1):
            writer.write(chunk)
            await writer.drain()

    upstream = await servers(big)
    proxy = await make_proxy(seams=Seams({"public.test": [PUBLIC]}, {PUBLIC: upstream}))
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
    writer.write(get("http://public.test/big", "public.test"))
    await writer.drain()
    head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
    assert b" 200 " in head
    # the first chunk reaches the client while the upstream is still holding back
    first = await asyncio.wait_for(reader.readexactly(len(chunk)), 10)
    assert first == chunk
    release.set()
    rest = await asyncio.wait_for(reader.read(), 20)
    writer.close()
    await writer.wait_closed()
    digest = hashlib.sha256(first + rest).hexdigest()
    assert digest == hashlib.sha256(chunk * count).hexdigest()


async def test_client_disconnect_ends_a_lingering_upstream(servers, make_proxy):
    async def keeps_open(server, reader, writer):
        await read_request(reader)
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 1000\r\n\r\npartial")
        await writer.drain()
        await reader.read()
        server.closed_by_peer.set()

    upstream = await servers(keeps_open)
    proxy = await make_proxy(seams=Seams({"public.test": [PUBLIC]}, {PUBLIC: upstream}))
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
    writer.write(get("http://public.test/", "public.test"))
    await writer.drain()
    await asyncio.wait_for(reader.readuntil(b"partial"), 10)
    writer.close()
    await writer.wait_closed()
    # the default idle timeout is minutes; the proxy lets go as soon as the client does
    await asyncio.wait_for(upstream.closed_by_peer.wait(), 10)


async def test_response_without_head_is_relayed_as_is(servers, make_proxy):
    async def raw(server, reader, writer):
        await read_request(reader)
        writer.write(b"not http at all")
        await writer.drain()

    upstream = await servers(raw)
    proxy = await make_proxy(seams=Seams({"public.test": [PUBLIC]}, {PUBLIC: upstream}))
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
    writer.write(get("http://public.test/", "public.test"))
    await writer.drain()
    assert await asyncio.wait_for(reader.read(), 10) == b"not http at all"
    writer.close()
    await writer.wait_closed()


async def test_oversized_upstream_response_head_is_502(servers, make_proxy):
    async def huge(server, reader, writer):
        await read_request(reader)
        writer.write(b"HTTP/1.1 200 OK\r\nX-Big: " + b"a" * 200_000 + b"\r\n\r\n")
        await writer.drain()

    upstream = await servers(huge)
    proxy = await make_proxy(seams=Seams({"public.test": [PUBLIC]}, {PUBLIC: upstream}))
    status, headers, _ = await exchange(
        proxy, get("http://public.test/", "public.test")
    )
    assert status == 502


async def test_httpx_client_through_the_proxy(servers, make_proxy):
    upstream = await servers()
    proxy = await make_proxy(
        NetworkPolicy(block_hosts=("blocked.test",)),
        seams=Seams({"public.test": [PUBLIC]}, {PUBLIC: upstream}),
    )
    async with httpx.AsyncClient(proxy=proxy.url, trust_env=False) as client:
        ok = await client.get("http://public.test/page")
        assert ok.status_code == 200
        assert ok.text.startswith("GET /page HTTP/1.1")
        posted = await client.post("http://public.test/f", data={"q": "v w"})
        assert posted.status_code == 200
        assert upstream.received[-1] == b"q=v+w"
        again = await client.get("http://public.test/again")
        assert again.status_code == 200
        blocked = await client.get("http://blocked.test/")
        assert blocked.status_code == 403
        assert blocked.headers[BLOCKED] == "blocked_host"
        assert "block_hosts" in blocked.text
    assert upstream.connections == 3


# ---------------------------------------------------------------------------
# CONNECT
# ---------------------------------------------------------------------------


async def test_connect_tunnel_relays_both_ways_with_half_close(servers, make_proxy):
    upstream = await servers(tcp_echo)
    seams = Seams({"public.test": [PUBLIC]}, {PUBLIC: upstream})
    proxy = await make_proxy(seams=seams)
    head, reader, writer = await open_tunnel(proxy, "public.test:443")
    assert head.startswith(b"HTTP/1.1 200")
    assert await reader.readexactly(9) == b"greeting\n"
    payload = os.urandom(200_000)
    writer.write(payload)
    await writer.drain()
    assert await asyncio.wait_for(reader.readexactly(len(payload)), 10) == payload
    writer.write_eof()
    await asyncio.wait_for(upstream.closed_by_peer.wait(), 10)
    assert await asyncio.wait_for(reader.read(), 10) == b"bye"
    writer.close()
    await writer.wait_closed()
    assert b"".join(upstream.received) == payload
    assert seams.dialed == [(PUBLIC, 443)]
    assert proxy.recent()[-1] == EgressRecord(
        "public.test", 443, "CONNECT", False, "ok", PUBLIC
    )


async def test_connect_bytes_sent_with_the_head_are_relayed(servers, make_proxy):
    upstream = await servers(tcp_echo)
    proxy = await make_proxy(seams=Seams({"public.test": [PUBLIC]}, {PUBLIC: upstream}))
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
    writer.write(b"CONNECT public.test:443 HTTP/1.1\r\n\r\nearly-bytes")
    await writer.drain()
    await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
    assert await reader.readexactly(9) == b"greeting\n"
    assert await asyncio.wait_for(reader.readexactly(11), 10) == b"early-bytes"
    writer.close()
    await writer.wait_closed()


async def test_connect_by_ip_literal_uses_default_dialer(servers, make_proxy):
    upstream = await servers(tcp_echo)
    proxy = await make_proxy(NetworkPolicy(allow_loopback=True))
    head, reader, writer = await open_tunnel(proxy, f"127.0.0.1:{upstream.port}")
    assert head.startswith(b"HTTP/1.1 200")
    assert await reader.readexactly(9) == b"greeting\n"
    writer.close()
    await writer.wait_closed()


async def test_default_resolver_judges_localhost_by_its_address(make_proxy):
    proxy = await make_proxy()
    head, reader, writer = await open_tunnel(proxy, "localhost:9")
    assert b" 403 " in head
    assert f"{BLOCKED_HEADER}: loopback".encode() in head
    writer.close()
    await writer.wait_closed()


@pytest.mark.parametrize(
    "target",
    [
        "public.test",
        "public.test:",
        "public.test:0",
        "public.test:99999",
        "[::1",
        "http://public.test/",
        "bad/host.test:443",
    ],
)
async def test_bad_connect_targets_are_400(make_proxy, target):
    seams = Seams({"public.test": [PUBLIC]})
    proxy = await make_proxy(seams=seams)
    status, _, _ = await exchange(proxy, f"CONNECT {target} HTTP/1.1\r\n\r\n".encode())
    assert status == 400
    assert seams.dialed == []


async def test_malformed_connect_host_is_recorded_as_sent(make_proxy):
    proxy = await make_proxy(seams=Seams())
    await exchange(proxy, b"CONNECT Bad/Host.test:443 HTTP/1.1\r\n\r\n")
    assert proxy.drain_blocked() == [
        EgressRecord("Bad/Host.test", 443, "CONNECT", True, "malformed", None)
    ]


# ---------------------------------------------------------------------------
# Policy enforcement
# ---------------------------------------------------------------------------


async def test_blocked_host_list_gets_403_with_reason(make_proxy):
    seams = Seams({"evil.test": [PUBLIC]})
    proxy = await make_proxy(NetworkPolicy(block_hosts=("evil.test",)), seams=seams)
    status, headers, body = await exchange(
        proxy, get("http://evil.test/x", "evil.test")
    )
    assert status == 403
    assert headers[BLOCKED] == "blocked_host"
    assert b"evil.test is in block_hosts" in body
    assert int(headers["content-length"]) == len(body)
    assert seams.resolved == [] and seams.dialed == []

    head, _, writer = await open_tunnel(proxy, "EVIL.test.:443")
    writer.close()
    await writer.wait_closed()
    assert b" 403 " in head
    blocked = proxy.drain_blocked()
    # recorded hosts are normalised, as they are for allowed requests
    assert [(r.host, r.port, r.method, r.code) for r in blocked] == [
        ("evil.test", 80, "GET", "blocked_host"),
        ("evil.test", 443, "CONNECT", "blocked_host"),
    ]
    assert all(r.blocked for r in blocked)
    assert proxy.drain_blocked() == []
    assert len(proxy.recent()) == 2


@pytest.mark.parametrize(
    ("address", "flags", "code"),
    [
        (PRIVATE, {}, "private"),
        ("127.0.0.1", {}, "loopback"),
        ("100.64.1.1", {}, "cgnat"),
        ("fd00::1", {}, "unique_local"),
        (
            "169.254.169.254",
            {"allow_loopback": True, "allow_private": True},
            "link_local",
        ),
        ("fe80::1", {"allow_loopback": True, "allow_private": True}, "link_local"),
        ("::ffff:169.254.169.254", {"allow_private": True}, "link_local"),
    ],
)
async def test_public_looking_name_resolving_to_special_address_is_blocked(
    servers, make_proxy, address, flags, code
):
    upstream = await servers()
    seams = Seams({"public.test": [address]}, {address: upstream})
    proxy = await make_proxy(NetworkPolicy(**flags), seams=seams)
    status, headers, _ = await exchange(
        proxy, get("http://public.test/", "public.test")
    )
    assert status == 403
    assert headers[BLOCKED] == code
    assert seams.dialed == []
    assert upstream.connections == 0
    assert proxy.drain_blocked()[0].address == address


async def test_numeric_hostname_is_judged_after_resolution(servers, make_proxy):
    seams = Seams({"2130706433": ["127.0.0.1"]})
    proxy = await make_proxy(seams=seams)
    status, headers, _ = await exchange(proxy, get("http://2130706433/", "2130706433"))
    assert (status, headers[BLOCKED]) == (403, "loopback")
    assert seams.resolved == ["2130706433"]


async def test_dns_pinning_connects_to_the_checked_address(servers, make_proxy):
    good = await servers()
    internal = await servers()
    answers = iter([[PUBLIC], [PRIVATE]])
    seams = Seams(
        {"rebind.test": lambda: next(answers)}, {PUBLIC: good, PRIVATE: internal}
    )
    proxy = await make_proxy(seams=seams)
    status, _, _ = await exchange(proxy, get("http://rebind.test/", "rebind.test"))
    assert status == 200
    assert seams.resolved == ["rebind.test"]
    assert seams.dialed == [(PUBLIC, 80)]
    assert (good.connections, internal.connections) == (1, 0)


async def test_only_passing_addresses_are_dialed(servers, make_proxy):
    upstream = await servers()
    seams = Seams(
        {"multi.test": [PRIVATE, "127.0.0.1", "169.254.1.1", PUBLIC_2, PUBLIC]},
        {PUBLIC: upstream},
    )
    proxy = await make_proxy(seams=seams)
    status, _, _ = await exchange(proxy, get("http://multi.test/", "multi.test"))
    assert status == 200
    # PUBLIC_2 refuses, so the proxy falls back to the next passing address
    assert seams.dialed == [(PUBLIC_2, 80), (PUBLIC, 80)]
    assert proxy.recent()[-1].address == PUBLIC


async def test_ipv4_mapped_loopback_literal(servers, make_proxy):
    upstream = await servers(tcp_echo)
    seams = Seams(routes={"::ffff:127.0.0.1": upstream})
    proxy = await make_proxy(seams=seams)
    head, _, writer = await open_tunnel(proxy, "[::ffff:127.0.0.1]:443")
    writer.close()
    await writer.wait_closed()
    assert b" 403 " in head and b"loopback" in head
    assert seams.resolved == [] and seams.dialed == []

    await proxy.set_policy(NetworkPolicy(allow_loopback=True))
    head, reader, writer = await open_tunnel(proxy, "[::FFFF:127.0.0.1]:443")
    assert head.startswith(b"HTTP/1.1 200")
    assert await reader.readexactly(9) == b"greeting\n"
    writer.close()
    await writer.wait_closed()
    assert seams.dialed == [("::ffff:127.0.0.1", 443)]


async def test_external_check_denial(make_proxy):
    seen = []

    def sandbox(host, port):
        seen.append((host, port))
        return "network mode is deny"

    seams = Seams({"public.test": [PUBLIC]})
    proxy = await make_proxy(NetworkPolicy(external_check=sandbox), seams=seams)
    status, headers, body = await exchange(
        proxy, get("http://public.test/", "public.test")
    )
    assert (status, headers[BLOCKED]) == (403, "external")
    assert b"network mode is deny" in body
    assert seen == [("public.test", 80)]
    assert seams.resolved == []


async def test_policy_replaced_without_drop_keeps_tunnels_it_allows(
    servers, make_proxy
):
    upstream = await servers(tcp_echo)
    seams = Seams(
        {"public.test": [PUBLIC], "other.test": [PUBLIC_2]}, {PUBLIC: upstream}
    )
    proxy = await make_proxy(seams=seams)
    _, reader, writer = await open_tunnel(proxy, "public.test:443")
    assert await reader.readexactly(9) == b"greeting\n"

    replacement = NetworkPolicy(block_hosts=("other.test",))
    await proxy.set_policy(replacement, drop=False)
    assert proxy.policy is replacement
    head, _, w2 = await open_tunnel(proxy, "other.test:443")
    w2.close()
    await w2.wait_closed()
    assert b" 403 " in head
    assert await proxy.rejudge() == 0
    writer.write(b"still")
    await writer.drain()
    assert await asyncio.wait_for(reader.readexactly(5), 10) == b"still"
    writer.close()
    await writer.wait_closed()


async def test_forget_blocked_removes_only_the_records_given(make_proxy):
    proxy = await make_proxy(
        NetworkPolicy(block_hosts=("a.test", "b.test")), seams=Seams()
    )
    for host in ("a.test", "b.test", "a.test"):
        status, _, _ = await exchange(proxy, get(f"http://{host}/", host))
        assert status == 403
    first_a = proxy.recent()[0]
    proxy.forget_blocked([first_a])
    left = proxy.drain_blocked()
    assert [r.host for r in left] == ["b.test", "a.test"]
    assert all(r is not first_a for r in left)
    proxy.forget_blocked([first_a])
    assert proxy.drain_blocked() == []


class TestRejudge:
    """Open connections are judged again by the policy in force."""

    def test_the_interval_is_a_couple_of_seconds(self):
        assert proxy_module.REJUDGE_INTERVAL_S == 2.0

    async def test_rejudge_cuts_only_what_the_check_now_refuses(
        self, servers, make_proxy
    ):
        upstream = await servers(tcp_echo)
        seams = Seams(
            {"public.test": [PUBLIC], "other.test": [PUBLIC]}, {PUBLIC: upstream}
        )
        refused: set[str] = set()
        policy = NetworkPolicy(
            external_check=lambda host, port: "gone" if host in refused else None
        )
        proxy = await make_proxy(policy, seams=seams, rejudge_interval=60)
        _, kept_r, kept_w = await open_tunnel(proxy, "public.test:443")
        _, cut_r, cut_w = await open_tunnel(proxy, "other.test:443")
        for reader in (kept_r, cut_r):
            assert await reader.readexactly(9) == b"greeting\n"
        assert sorted(proxy.open_connections()) == [
            ("other.test", 443),
            ("public.test", 443),
        ]
        refused.add("other.test")
        assert await proxy.rejudge() == 1
        assert await asyncio.wait_for(cut_r.read(), 10) == b""
        assert proxy.open_connections() == [("public.test", 443)]
        dropped = proxy.drain_blocked()
        assert [(r.host, r.port, r.code) for r in dropped] == [
            ("other.test", 443, "external")
        ]
        kept_w.write(b"alive")
        await kept_w.drain()
        assert await asyncio.wait_for(kept_r.readexactly(5), 10) == b"alive"
        for writer in (kept_w, cut_w):
            writer.close()
            await writer.wait_closed()

    async def test_rejudge_cuts_a_connection_whose_pinned_address_is_now_refused(
        self, servers, make_proxy
    ):
        upstream = await servers(tcp_echo)
        seams = Seams(
            {"lan.test": [PRIVATE], "public.test": [PUBLIC]},
            {PRIVATE: upstream, PUBLIC: upstream},
        )
        proxy = await make_proxy(
            NetworkPolicy(allow_private=True), seams=seams, rejudge_interval=60
        )
        _, cut_r, cut_w = await open_tunnel(proxy, "lan.test:443")
        _, kept_r, kept_w = await open_tunnel(proxy, "public.test:443")
        for reader in (cut_r, kept_r):
            assert await reader.readexactly(9) == b"greeting\n"
        await proxy.set_policy(NetworkPolicy(), drop=False)
        assert await proxy.rejudge() == 1
        assert await asyncio.wait_for(cut_r.read(), 10) == b""
        assert proxy.open_connections() == [("public.test", 443)]
        dropped = proxy.drain_blocked()
        assert [(r.host, r.address, r.blocked) for r in dropped] == [
            ("lan.test", PRIVATE, True)
        ]
        assert dropped[0].code == NetworkPolicy().check_address(PRIVATE, 443).code
        kept_w.write(b"alive")
        await kept_w.drain()
        assert await asyncio.wait_for(kept_r.readexactly(5), 10) == b"alive"
        for writer in (kept_w, cut_w):
            writer.close()
            await writer.wait_closed()

    async def test_open_connections_are_rejudged_on_the_interval(
        self, servers, make_proxy
    ):
        upstream = await servers(tcp_echo)
        seams = Seams({"public.test": [PUBLIC]}, {PUBLIC: upstream})
        verdict: list[str | None] = [None]
        policy = NetworkPolicy(external_check=lambda host, port: verdict[0])
        proxy = await make_proxy(policy, seams=seams, rejudge_interval=0.05)
        _, reader, writer = await open_tunnel(proxy, "public.test:443")
        assert await reader.readexactly(9) == b"greeting\n"
        await asyncio.sleep(0.2)
        writer.write(b"ok")
        await writer.drain()
        assert await asyncio.wait_for(reader.readexactly(2), 10) == b"ok"
        verdict[0] = "network access denied"
        assert await asyncio.wait_for(reader.read(), 2) == b""
        writer.close()
        await writer.wait_closed()

    async def test_a_refused_dial_lingering_on_its_answer_is_not_rejudged(
        self, make_proxy
    ):
        refused = [False]
        policy = NetworkPolicy(
            external_check=lambda host, port: "gone" if refused[0] else None
        )
        proxy = await make_proxy(policy, seams=Seams(), rejudge_interval=60)
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
        writer.write(get("http://nowhere.test/", "nowhere.test"))
        await writer.drain()
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
        assert b" 502 " in head
        assert proxy.open_connections() == []
        mark = proxy.mark()
        refused[0] = True
        assert await proxy.rejudge() == 0 and proxy.since(mark) == []
        writer.close()
        await writer.wait_closed()

    async def test_a_check_that_raises_cuts_the_connection(self, servers, make_proxy):
        upstream = await servers(tcp_echo)
        seams = Seams({"public.test": [PUBLIC]}, {PUBLIC: upstream})
        broken = [False]

        def check(host, port):
            if broken[0]:
                raise RuntimeError("policy gone")
            return None

        proxy = await make_proxy(
            NetworkPolicy(external_check=check), seams=seams, rejudge_interval=60
        )
        _, reader, writer = await open_tunnel(proxy, "public.test:443")
        assert await reader.readexactly(9) == b"greeting\n"
        broken[0] = True
        assert await proxy.rejudge() == 1
        assert await asyncio.wait_for(reader.read(), 10) == b""
        writer.close()
        await writer.wait_closed()

    async def test_the_watch_runs_only_while_connections_are_open(
        self, servers, make_proxy
    ):
        upstream = await servers()
        seams = Seams({"public.test": [PUBLIC]}, {PUBLIC: upstream})
        proxy = await make_proxy(seams=seams, rejudge_interval=0.02)
        assert proxy._watch_task is None
        status, _, _ = await exchange(proxy, get("http://public.test/", "public.test"))
        assert status == 200
        for _ in range(100):
            if proxy._watch_task is None:
                break
            await asyncio.sleep(0.02)
        assert proxy._watch_task is None and proxy.open_connections() == []
        status, _, _ = await exchange(proxy, get("http://public.test/", "public.test"))
        assert status == 200

    async def test_close_ends_the_watch(self, servers):
        upstream = await servers(tcp_echo)
        seams = Seams({"public.test": [PUBLIC]}, {PUBLIC: upstream})
        proxy = FilteringProxy(
            NetworkPolicy(), rejudge_interval=60, **_seam_kwargs(seams)
        )
        await proxy.start()
        _, reader, writer = await open_tunnel(proxy, "public.test:443")
        assert await reader.readexactly(9) == b"greeting\n"
        watch = proxy._watch_task
        assert watch is not None and not watch.done()
        await asyncio.wait_for(proxy.close(), 10)
        assert watch.done() and proxy._watch_task is None
        assert not [
            task
            for task in asyncio.all_tasks()
            if not task.done()
            and task.get_coro().__qualname__.startswith("FilteringProxy.")
        ]
        writer.close()
        await writer.wait_closed()


async def test_policy_attribute_is_read_only(make_proxy):
    proxy = await make_proxy()
    with pytest.raises(AttributeError):
        proxy.policy = NetworkPolicy(allow_loopback=True)
    assert proxy.policy.allow_loopback is False


async def test_set_policy_drops_established_tunnels_by_default(servers, make_proxy):
    upstream = await servers(tcp_echo)
    other = await servers(tcp_echo)
    seams = Seams(
        {"public.test": [PUBLIC], "other.test": [PUBLIC_2]},
        {PUBLIC: upstream, PUBLIC_2: other},
    )
    proxy = await make_proxy(seams=seams)
    _, reader, writer = await open_tunnel(proxy, "public.test:443")
    assert await reader.readexactly(9) == b"greeting\n"

    await proxy.set_policy(NetworkPolicy(block_hosts=("public.test",)))
    # the established tunnel is cut on both sides
    assert await asyncio.wait_for(reader.read(), 10) == b""
    await asyncio.wait_for(upstream.closed_by_peer.wait(), 10)
    writer.close()
    await writer.wait_closed()
    # the listener stays up and new connections follow the new policy
    head, _, w2 = await open_tunnel(proxy, "public.test:443")
    w2.close()
    await w2.wait_closed()
    assert b" 403 " in head and b"blocked_host" in head
    head, r3, w3 = await open_tunnel(proxy, "other.test:443")
    assert head.startswith(b"HTTP/1.1 200")
    assert await r3.readexactly(9) == b"greeting\n"
    w3.close()
    await w3.wait_closed()


async def test_drop_connections_cuts_tunnels_and_keeps_listening(servers, make_proxy):
    upstream = await servers(tcp_echo)
    seams = Seams({"public.test": [PUBLIC]}, {PUBLIC: upstream})
    proxy = await make_proxy(seams=seams)
    tunnels = []
    for _ in range(2):
        _, reader, writer = await open_tunnel(proxy, "public.test:443")
        assert await reader.readexactly(9) == b"greeting\n"
        tunnels.append((reader, writer))

    await proxy.drop_connections()
    for reader, writer in tunnels:
        assert await asyncio.wait_for(reader.read(), 10) == b""
        writer.close()
        await writer.wait_closed()
    await asyncio.wait_for(upstream.closed_by_peer.wait(), 10)

    head, reader, writer = await open_tunnel(proxy, "public.test:443")
    assert head.startswith(b"HTTP/1.1 200")
    writer.write(b"again")
    await writer.drain()
    assert await asyncio.wait_for(reader.readexactly(14), 10) == b"greeting\nagain"
    writer.close()
    await writer.wait_closed()


# ---------------------------------------------------------------------------
# Malformed input and failures
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        b"garbage\r\n\r\n",
        b"GET / HTTP/1.1\r\nHost: public.test\r\n\r\n",
        b"GET /?next=http://public.test/ HTTP/1.1\r\n\r\n",
        b"GET http://public.test:0/ HTTP/1.1\r\n\r\n",
        b"GET http://public.test/ HTTP/2.0\r\n\r\n",
        b"G@T http://public.test/ HTTP/1.1\r\n\r\n",
        b"GET http://public.test/ HTTP/1.1\r\nBad Header: x\r\n\r\n",
        b"GET http://public.test/ HTTP/1.1\r\nNoColon\r\n\r\n",
        b"GET http://public.test/ HTTP/1.1\r\nA: b\r\n folded\r\n\r\n",
        b"GET http://public.test/ HTTP/1.1\r\nContent-Length: x\r\n\r\n",
        b"GET http://public.test/ HTTP/1.1\r\nContent-Length: 1\r\n"
        b"Content-Length: 2\r\n\r\n",
        b"GET http://public.test/ HTTP/1.1\r\nContent-Length: 1\r\n"
        b"Transfer-Encoding: chunked\r\n\r\n",
        b"GET http://public.test/ HTTP/1.1\r\nTransfer-Encoding: gzip\r\n\r\n",
        b"GET http://exa mple.test/ HTTP/1.1\r\n\r\n",
        b"GET http://user:pw@public.test/ HTTP/1.1\r\n\r\n",
        b"GET http://[::1/ HTTP/1.1\r\n\r\n",
        b"GET http://public.test:99999/ HTTP/1.1\r\n\r\n",
        b"GET ftp://public.test:99999/ HTTP/1.1\r\n\r\n",
        b"GET http://u@public.test:abc/ HTTP/1.1\r\n\r\n",
        b"\xff\xfe\r\n\r\n",
    ],
)
async def test_malformed_requests_get_400(make_proxy, raw):
    seams = Seams({"public.test": [PUBLIC]})
    proxy = await make_proxy(seams=seams)
    status, headers, _ = await exchange(proxy, raw)
    assert status == 400
    assert headers["connection"] == "close"
    assert seams.dialed == []


async def test_https_absolute_form_is_refused(make_proxy):
    proxy = await make_proxy(seams=Seams({"public.test": [PUBLIC]}))
    status, headers, _ = await exchange(
        proxy, get("https://public.test/", "public.test")
    )
    assert (status, headers[BLOCKED]) == (403, "scheme")


@pytest.mark.parametrize(
    "body",
    [
        b"Transfer-Encoding: chunked\r\n\r\nzz\r\n",
        b"Transfer-Encoding: chunked\r\n\r\n5\r\nhelloXX",
        b"Transfer-Encoding: chunked\r\n\r\n" + b"0" * 8192 + b"\r\n",
        b"Content-Length: 100\r\n\r\nonly-ten-b",
    ],
)
async def test_broken_request_bodies_close_without_response(servers, make_proxy, body):
    async def capture(server, reader, writer):
        server.received.append(await reader.read())

    upstream = await servers(capture)
    proxy = await make_proxy(
        seams=Seams({"public.test": [PUBLIC]}, {PUBLIC: upstream}),
        max_head_bytes=4096,
    )
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
    writer.write(b"POST http://public.test/ HTTP/1.1\r\n" + body)
    writer.write_eof()
    data = await asyncio.wait_for(reader.read(), 10)
    writer.close()
    await writer.wait_closed()
    assert data == b""
    # the upstream saw the connection end without a complete request
    await asyncio.wait_for(_until(lambda: upstream.received), 10)
    assert b"helloXX" not in upstream.received[0]


async def _until(predicate):
    while not predicate():
        await asyncio.sleep(0.01)


async def test_unexpected_dialer_error_closes_the_connection(make_proxy):
    async def broken(host, port):
        raise RuntimeError("dialer bug")

    proxy = await make_proxy(seams=Seams({"public.test": [PUBLIC]}), dialer=broken)
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
    writer.write(get("http://public.test/", "public.test"))
    await writer.drain()
    assert await asyncio.wait_for(reader.read(), 10) == b""
    writer.close()
    await writer.wait_closed()


async def test_oversized_head_gets_431(make_proxy):
    seams = Seams({"public.test": [PUBLIC]})
    proxy = await make_proxy(seams=seams, max_head_bytes=4096)
    status, headers, _ = await exchange(
        proxy,
        b"GET http://public.test/ HTTP/1.1\r\nX-Big: " + b"a" * 200_000 + b"\r\n\r\n",
    )
    assert (status, headers[BLOCKED]) == (431, "head_too_large")
    assert seams.dialed == []


@pytest.mark.parametrize("chatty", [False, True])
async def test_error_response_lingers_boundedly(make_proxy, monkeypatch, chatty):
    monkeypatch.setattr(proxy_module, "LINGER_TIMEOUT_S", 0.2 if not chatty else 30)
    monkeypatch.setattr(proxy_module, "LINGER_BYTES", 10)
    proxy = await make_proxy(NetworkPolicy(block_hosts=("evil.test",)), seams=Seams())
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
    writer.write(get("http://evil.test/", "evil.test"))
    if chatty:
        writer.write(b"x" * 100)
    await writer.drain()
    # the response arrives and ends, while the client keeps its side open
    data = await asyncio.wait_for(reader.read(), 10)
    assert parse_response(data)[0] == 403
    # the connection is let go by the deadline or the byte budget, not by the client
    await asyncio.wait_for(_until(lambda: not proxy._tasks), 10)
    writer.close()
    await writer.wait_closed()


async def test_truncated_head_closes_quietly(make_proxy):
    proxy = await make_proxy(seams=Seams())
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
    writer.write(b"GET http://public.test/ HTT")
    writer.write_eof()
    assert await asyncio.wait_for(reader.read(), 10) == b""
    writer.close()
    await writer.wait_closed()


async def test_head_timeout_closes(make_proxy):
    proxy = await make_proxy(seams=Seams(), head_timeout=0.2)
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
    assert await asyncio.wait_for(reader.read(), 10) == b""
    writer.close()
    await writer.wait_closed()


async def test_upstream_connect_failure_is_502(make_proxy):
    seams = Seams({"public.test": [PUBLIC]})
    proxy = await make_proxy(seams=seams)
    status, headers, body = await exchange(
        proxy, get("http://public.test/", "public.test")
    )
    assert status == 502
    assert headers[BLOCKED] == "upstream"
    assert seams.dialed == [(PUBLIC, 80)]
    record = proxy.recent()[-1]
    assert (record.blocked, record.code) == (False, "upstream")
    assert proxy.drain_blocked() == []


async def test_resolve_failure_is_502(make_proxy):
    seams = Seams()
    proxy = await make_proxy(seams=seams)
    status, headers, _ = await exchange(
        proxy, get("http://nowhere.test/", "nowhere.test")
    )
    assert (status, headers[BLOCKED]) == (502, "resolve_failed")
    seams.names["empty.test"] = []
    status, headers, _ = await exchange(proxy, get("http://empty.test/", "empty.test"))
    assert (status, headers[BLOCKED]) == (502, "resolve_failed")


async def test_connect_timeout_is_502(make_proxy):
    async def hang(host, port):
        await asyncio.sleep(3600)

    seams = Seams({"public.test": [PUBLIC]})
    proxy = await make_proxy(seams=seams, dialer=hang, connect_timeout=0.2)
    status, headers, _ = await exchange(
        proxy, get("http://public.test/", "public.test")
    )
    assert (status, headers[BLOCKED]) == (502, "upstream")


async def test_idle_tunnel_is_closed(servers, make_proxy):
    upstream = await servers(tcp_echo)
    proxy = await make_proxy(
        seams=Seams({"public.test": [PUBLIC]}, {PUBLIC: upstream}), idle_timeout=0.3
    )
    _, reader, writer = await open_tunnel(proxy, "public.test:443")
    assert await asyncio.wait_for(reader.read(), 10) == b"greeting\n"
    writer.close()
    await writer.wait_closed()


async def test_active_tunnel_survives_idle_timeout_in_one_direction(
    servers, make_proxy
):
    async def ticker(server, reader, writer):
        for _ in range(8):
            writer.write(b".")
            await writer.drain()
            await asyncio.sleep(0.1)

    upstream = await servers(ticker)
    proxy = await make_proxy(
        seams=Seams({"public.test": [PUBLIC]}, {PUBLIC: upstream}), idle_timeout=0.35
    )
    _, reader, writer = await open_tunnel(proxy, "public.test:443")
    assert await asyncio.wait_for(reader.read(), 10) == b"." * 8
    writer.close()
    await writer.wait_closed()


async def test_log_is_bounded(servers, make_proxy):
    proxy = await make_proxy(
        NetworkPolicy(block_hosts=("*.test",)), seams=Seams(), log_size=3
    )
    for index in range(5):
        await exchange(proxy, get(f"http://h{index}.test/", f"h{index}.test"))
    assert [r.host for r in proxy.recent()] == ["h2.test", "h3.test", "h4.test"]
    assert [r.host for r in proxy.drain_blocked()] == ["h2.test", "h3.test", "h4.test"]


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


async def _ends_once_it_asks(sock: socket.socket) -> bool:
    """Whether a client the proxy never accepted sees an end of stream or a reset after sending.

    Raises ``TimeoutError`` when the connection hangs instead.
    """
    loop = asyncio.get_running_loop()
    try:
        await loop.sock_sendall(sock, b"CONNECT public.test:443 HTTP/1.1\r\n\r\n")
        return await asyncio.wait_for(loop.sock_recv(sock, 16), 10) == b""
    except ConnectionError:
        return True
    finally:
        sock.close()


async def test_close_with_open_tunnels_leaves_nothing_behind(servers):
    upstream = await servers(tcp_echo)
    before = asyncio.all_tasks()
    proxy = FilteringProxy(
        NetworkPolicy(),
        **_seam_kwargs(Seams({"public.test": [PUBLIC]}, {PUBLIC: upstream})),
    )
    with pytest.raises(RuntimeError):
        _ = proxy.port
    await proxy.start()
    with pytest.raises(RuntimeError):
        await proxy.start()
    port = proxy.port
    clients = []
    for _ in range(2):
        _, reader, writer = await open_tunnel(proxy, "public.test:443")
        assert await reader.readexactly(9) == b"greeting\n"
        clients.append((reader, writer))
    idle_reader, idle_writer = await asyncio.open_connection("127.0.0.1", port)
    await asyncio.wait_for(_until(lambda: len(proxy._tasks) == 3), 10)
    unaccepted = socket.create_connection(("127.0.0.1", port), timeout=10)
    unaccepted.setblocking(False)

    await asyncio.wait_for(proxy.close(), 10)
    await proxy.close()

    assert await _ends_once_it_asks(unaccepted)
    for reader, writer in clients + [(idle_reader, idle_writer)]:
        assert await asyncio.wait_for(reader.read(), 10) == b""
        writer.close()
        await writer.wait_closed()
    await asyncio.wait_for(upstream.closed_by_peer.wait(), 10)
    with pytest.raises(OSError):
        await asyncio.open_connection("127.0.0.1", port)
    await upstream.close()
    await asyncio.sleep(0)
    assert_no_proxy_tasks(before)


async def test_close_waits_for_handlers_still_closing_their_socket(
    make_proxy, monkeypatch
):
    entered = asyncio.Event()

    async def stalled_close(writer):
        writer.close()
        entered.set()
        await asyncio.Event().wait()

    before = asyncio.all_tasks()
    proxy = FilteringProxy(NetworkPolicy())
    await proxy.start()
    monkeypatch.setattr(proxy_module, "_close_writer", stalled_close)
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
    writer.write(b"garbage\r\n\r\n")
    writer.write_eof()
    assert parse_response(await asyncio.wait_for(reader.read(), 10))[0] == 400
    await asyncio.wait_for(entered.wait(), 10)

    await asyncio.wait_for(proxy.close(), 10)
    assert_no_proxy_tasks(before)
    writer.close()
    await writer.wait_closed()


def assert_no_proxy_tasks(before):
    """No task started after ``before`` is still pending, test servers aside."""
    leftover = [
        task
        for task in asyncio.all_tasks() - before - {asyncio.current_task()}
        if not task.done() and task.get_coro().__qualname__ != "LoopbackServer._serve"
    ]
    assert not leftover, leftover


def _seam_kwargs(seams):
    return {"resolver": seams.resolve, "dialer": seams.dial}


async def forging_upstream(server, reader, writer):
    await read_request(reader)
    body = b"fake"
    writer.write(
        b"HTTP/1.1 403 Forbidden\r\nX-KT-Browser-Blocked: loopback\r\n"
        b"x-kt-browser-blocked: twice\r\nX-Other: kept\r\n"
        + f"Content-Length: {len(body)}\r\n\r\n".encode()
        + body
    )
    await writer.drain()


async def test_upstream_cannot_forge_the_blocked_header(servers, make_proxy):
    upstream = await servers(forging_upstream)
    seams = Seams({"public.test": [PUBLIC]}, {PUBLIC: upstream})
    proxy = await make_proxy(seams=seams)
    status, headers, body = await exchange(
        proxy, get("http://public.test/", "public.test")
    )
    assert status == 403 and body == b"fake"
    assert BLOCKED not in headers and headers["x-other"] == "kept"
    assert proxy.drain_blocked() == []


async def test_mark_and_since_return_only_later_decisions(servers, make_proxy):
    proxy = await make_proxy(
        NetworkPolicy(block_hosts=("*.test",)), seams=Seams(), log_size=3
    )
    await exchange(proxy, get("http://early.test/", "early.test"))
    mark = proxy.mark()
    assert proxy.since(mark) == []
    for index in range(2):
        await exchange(proxy, get(f"http://h{index}.test/", f"h{index}.test"))
    assert [r.host for r in proxy.since(mark)] == ["h0.test", "h1.test"]
    for index in range(2, 5):
        await exchange(proxy, get(f"http://h{index}.test/", f"h{index}.test"))
    assert [r.host for r in proxy.since(mark)] == ["h2.test", "h3.test", "h4.test"]
    assert proxy.since(proxy.mark()) == []


async def test_ip_block_pattern_refuses_any_name_resolving_to_it(servers, make_proxy):
    upstream = await servers()
    seams = Seams(
        {"localhost.test": ["127.0.0.1"], "other.test": ["127.0.0.1"]},
        {"127.0.0.1": upstream},
    )
    policy = NetworkPolicy(block_hosts=("127.0.0.1:8001",), allow_loopback=True)
    proxy = await make_proxy(policy, seams=seams)
    status, headers, _ = await exchange(
        proxy, get("http://localhost.test:8001/", "localhost.test:8001")
    )
    assert status == 403 and headers[BLOCKED] == "blocked_host"
    assert seams.dialed == []
    status, _, _ = await exchange(
        proxy, get("http://other.test:8002/", "other.test:8002")
    )
    assert status == 200
    assert [(r.host, r.port, r.blocked, r.code) for r in proxy.recent()] == [
        ("localhost.test", 8001, True, "blocked_host"),
        ("other.test", 8002, False, "ok"),
    ]
