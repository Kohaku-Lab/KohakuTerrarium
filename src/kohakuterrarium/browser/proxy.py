"""Loopback forward proxy that applies the browser network policy to every connection.

Handles ``CONNECT`` tunnels and absolute-form plain-HTTP requests. Each
connection is checked by host, resolved once, checked by every resolved
address, and dialled to an address that passed. Plain HTTP carries one
request per connection; both sides are told ``Connection: close``. While
connections are open their hosts and pinned addresses are judged again every
``REJUDGE_INTERVAL_S``, and those the policy now refuses are cut.
"""

import asyncio
import re
import socket
import time
from collections import deque
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from urllib.parse import SplitResult, urlsplit

from kohakuterrarium.browser.policy import (
    WEB_SCHEMES,
    Decision,
    NetworkPolicy,
    canonical_ip,
    normalize_host,
    parse_ip,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

Streams = tuple[asyncio.StreamReader, asyncio.StreamWriter]
Resolver = Callable[[str, int], Awaitable[list[str]]]
Dialer = Callable[[str, int], Awaitable[Streams]]

BLOCKED_HEADER = "X-KT-Browser-Blocked"
MAX_HEAD_BYTES = 64 * 1024
CONNECT_TIMEOUT_S = 10.0
HEAD_TIMEOUT_S = 30.0
IDLE_TIMEOUT_S = 300.0
LINGER_TIMEOUT_S = 2.0
LINGER_BYTES = 1024 * 1024
REJUDGE_INTERVAL_S = 2.0
LOG_SIZE = 256
CHUNK = 64 * 1024

_TOKEN = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
_VERSION = re.compile(r"HTTP/1\.[01]")
_CONTROL = re.compile(r"[\x00-\x08\x0a-\x1f\x7f]")
_DIGITS = re.compile(r"[0-9]+")
_ABSOLUTE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")
_CHUNK_SIZE = re.compile(rb"[0-9A-Fa-f]{1,16}")
_REQUEST_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-connection",
        "proxy-authorization",
        "proxy-authenticate",
        "te",
        "trailer",
        "upgrade",
        "host",
    }
)
_RESPONSE_HOP = frozenset({"connection", "keep-alive", "proxy-connection"})
_STATUS_TEXT = {
    400: "Bad Request",
    403: "Forbidden",
    431: "Request Header Fields Too Large",
    502: "Bad Gateway",
}
_CLIENT_ERRORS = frozenset({"malformed", "credentials", "control_char"})


@dataclass(frozen=True)
class EgressRecord:
    """One proxy decision: target, request kind, outcome and the address used."""

    host: str
    port: int
    method: str
    blocked: bool
    code: str
    address: str | None = None


class _Reject(Exception):
    """A request that ends with an error response."""

    def __init__(self, status: int, code: str, reason: str):
        super().__init__(reason)
        self.status, self.code, self.reason = status, code, reason


@dataclass
class _Request:
    method: str
    target: str
    version: str
    headers: list[tuple[str, str]]


def _parse_head(head: bytes) -> _Request:
    lines = head.decode("latin-1")[:-4].split("\r\n")
    parts = lines[0].split(" ")
    if (
        len(parts) != 3
        or not _TOKEN.fullmatch(parts[0])
        or not parts[1]
        or not _VERSION.fullmatch(parts[2])
    ):
        raise _Reject(400, "malformed", "malformed request line")
    headers: list[tuple[str, str]] = []
    for line in lines[1:]:
        name, sep, value = line.partition(":")
        if not sep or not _TOKEN.fullmatch(name) or _CONTROL.search(value):
            raise _Reject(400, "malformed", "malformed header line")
        headers.append((name, value.strip(" \t")))
    return _Request(parts[0], parts[1], parts[2], headers)


def _header_values(headers: list[tuple[str, str]], name: str) -> list[str]:
    return [value for key, value in headers if key.lower() == name]


def _connection_tokens(headers: list[tuple[str, str]]) -> set[str]:
    tokens = ",".join(_header_values(headers, "connection")).split(",")
    return {token.strip().lower() for token in tokens if token.strip()}


def _authority(target: str) -> tuple[str, int]:
    if target.startswith("["):
        end = target.find("]")
        host, rest = target[1:end], target[end + 1 :]
        if end < 0 or not rest.startswith(":"):
            raise _Reject(400, "malformed", "malformed CONNECT target")
        port_text = rest[1:]
    else:
        host, sep, port_text = target.rpartition(":")
        if not sep or ":" in host:
            raise _Reject(400, "malformed", "malformed CONNECT target")
    if not _DIGITS.fullmatch(port_text) or not 0 < int(port_text) < 65536:
        raise _Reject(400, "malformed", "malformed CONNECT port")
    return host, int(port_text)


def _body_framing(headers: list[tuple[str, str]]) -> int | None:
    """Content length of the request body, or ``None`` for chunked."""
    encodings = _header_values(headers, "transfer-encoding")
    lengths = set(_header_values(headers, "content-length"))
    if encodings:
        if lengths or ",".join(encodings).strip().lower() != "chunked":
            raise _Reject(400, "malformed", "unsupported request body framing")
        return None
    if len(lengths) > 1 or (lengths and not _DIGITS.fullmatch(next(iter(lengths)))):
        raise _Reject(400, "malformed", "invalid Content-Length")
    return int(lengths.pop()) if lengths else 0


def _request_head(request: _Request, origin: str, host_header: str) -> bytes:
    drop = _REQUEST_HOP | _connection_tokens(request.headers)
    lines = [f"{request.method} {origin} {request.version}", f"Host: {host_header}"]
    lines += [f"{k}: {v}" for k, v in request.headers if k.lower() not in drop]
    lines.append("Connection: close")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")


def _response_head(head: bytes) -> bytes:
    lines = head.decode("latin-1")[:-4].split("\r\n")
    pairs = [line.partition(":") for line in lines[1:]]
    headers = [(name.strip(), value.strip()) for name, _, value in pairs]
    drop = _RESPONSE_HOP | {BLOCKED_HEADER.lower()} | _connection_tokens(headers)
    kept = [lines[0]]
    kept += [
        line for line, (name, _) in zip(lines[1:], headers) if name.lower() not in drop
    ]
    kept.append("Connection: close")
    return ("\r\n".join(kept) + "\r\n\r\n").encode("latin-1")


async def _default_resolver(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(
        host, port, type=socket.SOCK_STREAM
    )
    addresses: list[str] = []
    for family, _, _, _, sockaddr in infos:
        if family in (socket.AF_INET, socket.AF_INET6) and sockaddr[0] not in addresses:
            addresses.append(sockaddr[0])
    return addresses


async def _default_dialer(host: str, port: int) -> Streams:
    return await asyncio.open_connection(host, port)


async def _race(
    operations: list[Coroutine[object, object, None]], return_when: str
) -> None:
    """Run ``operations`` as tasks until ``return_when``, then cancel and await the rest."""
    tasks = [asyncio.create_task(operation) for operation in operations]
    try:
        await asyncio.wait(tasks, return_when=return_when)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _discard_until_eof(reader: asyncio.StreamReader) -> None:
    while await reader.read(CHUNK):
        pass


async def _close_writer(writer: asyncio.StreamWriter) -> None:
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass


class FilteringProxy:
    """Asyncio forward proxy on 127.0.0.1 that enforces a :class:`NetworkPolicy`.

    ``resolver(host, port)`` returns candidate IP strings and
    ``dialer(ip, port)`` opens streams to one of them; both default to the
    running loop's networking. ``policy`` may be replaced while running and
    applies from the next connection on; open connections are judged again
    every ``rejudge_interval`` seconds (default ``REJUDGE_INTERVAL_S``).
    """

    def __init__(
        self,
        policy: NetworkPolicy,
        *,
        resolver: Resolver | None = None,
        dialer: Dialer | None = None,
        connect_timeout: float = CONNECT_TIMEOUT_S,
        head_timeout: float = HEAD_TIMEOUT_S,
        idle_timeout: float = IDLE_TIMEOUT_S,
        max_head_bytes: int = MAX_HEAD_BYTES,
        log_size: int = LOG_SIZE,
        rejudge_interval: float | None = None,
    ):
        self._policy = policy
        self._resolver = resolver or _default_resolver
        self._dialer = dialer or _default_dialer
        self._connect_timeout = connect_timeout
        self._head_timeout = head_timeout
        self._idle_timeout = idle_timeout
        self._max_head_bytes = max_head_bytes
        self._log: deque[EgressRecord] = deque(maxlen=log_size)
        self._count = 0
        self._blocked: deque[EgressRecord] = deque(maxlen=log_size)
        self._server: asyncio.AbstractServer | None = None
        self._port: int | None = None
        self._closed = False
        self._tasks: set[asyncio.Task] = set()
        self._targets: dict[asyncio.Task, tuple[str, int, str, str | None]] = {}
        self._rejudge_interval = rejudge_interval
        self._watch_task: asyncio.Task | None = None

    @property
    def port(self) -> int:
        """The listening port; raises ``RuntimeError`` before :meth:`start`."""
        if self._port is None:
            raise RuntimeError("the proxy is not started")
        return self._port

    @property
    def url(self) -> str:
        """The proxy URL to hand to a browser or HTTP client."""
        return f"http://127.0.0.1:{self.port}"

    async def start(self) -> None:
        """Listen on an ephemeral loopback port."""
        if self._server is not None:
            raise RuntimeError("the proxy was already started")
        self._server = await asyncio.start_server(
            self._handle, "127.0.0.1", 0, limit=self._max_head_bytes
        )
        self._port = self._server.sockets[0].getsockname()[1]

    async def close(self) -> None:
        """Stop listening, cancel every connection and wait for them to finish."""
        if self._server is None or self._closed:
            return
        self._closed = True
        self._server.close()
        watch, self._watch_task = self._watch_task, None
        if watch is not None:
            watch.cancel()
            await asyncio.gather(watch, return_exceptions=True)
        await asyncio.sleep(0)
        while self._tasks:
            await self._cancel(list(self._tasks))
        await self._server.wait_closed()

    @property
    def policy(self) -> NetworkPolicy:
        """The policy new connections are judged by."""
        return self._policy

    async def set_policy(self, policy: NetworkPolicy, *, drop: bool = True) -> None:
        """Judge connections by ``policy``; with ``drop``, cut every open one now.

        Without ``drop``, open connections ``policy`` refuses are cut by the
        next re-judge.
        """
        self._policy = policy
        if drop:
            await self.drop_connections()

    async def drop_connections(self) -> None:
        """Cancel and await every open connection; the listener keeps accepting."""
        await self._cancel(list(self._tasks))

    def open_connections(self) -> list[tuple[str, int]]:
        """``(host, port)`` of every connection the policy allowed that is still open."""
        return [(host, port) for host, port, _, _ in self._targets.values()]

    async def rejudge(self) -> int:
        """Cut the open connections the policy now refuses; how many were cut.

        Each is judged by its host and, once connected, by the address it was
        pinned to.
        """
        policy = self._policy
        refused: list[asyncio.Task] = []
        for task, (host, port, method, address) in list(self._targets.items()):
            decision = policy.check_host(host, port)
            if decision and address is not None:
                decision = policy.check_address(address, port)
            if not decision:
                self._record(host, port, method, decision, address)
                refused.append(task)
        if refused:
            await self._cancel(refused)
        return len(refused)

    def _register(self, host: str, port: int, method: str) -> None:
        """Remember the current connection's target and keep the re-judge running."""
        task = asyncio.current_task()
        if task is None or self._closed:
            return
        self._targets[task] = (host, port, method, None)
        if self._watch_task is None:
            self._watch_task = asyncio.create_task(self._watch())

    def _pin(self, address: str) -> None:
        """Remember the address the current connection was dialled to."""
        task = asyncio.current_task()
        target = self._targets.get(task)
        if target is not None:
            self._targets[task] = (*target[:3], address)

    async def _watch(self) -> None:
        """Re-judge open connections every interval; ends once none is open."""
        interval = self._rejudge_interval or REJUDGE_INTERVAL_S
        while True:
            await asyncio.sleep(interval)
            try:
                await self.rejudge()
            except Exception as exc:
                logger.warning("Re-judging proxy connections failed", error=str(exc))
            if not self._targets or self._closed:
                self._watch_task = None
                return

    async def _cancel(self, tasks: list[asyncio.Task]) -> None:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.difference_update(tasks)
        for task in tasks:
            self._targets.pop(task, None)

    def recent(self) -> list[EgressRecord]:
        """The most recent decisions, oldest first."""
        return list(self._log)

    def mark(self) -> int:
        """A position in the decision log, for :meth:`since`."""
        return self._count

    def since(self, mark: int) -> list[EgressRecord]:
        """Decisions recorded after ``mark`` that the bounded log still holds, oldest first."""
        newer = self._count - mark
        return list(self._log)[-newer:] if newer > 0 else []

    def drain_blocked(self) -> list[EgressRecord]:
        """Return and forget the blocked decisions recorded since the last drain."""
        blocked = list(self._blocked)
        self._blocked.clear()
        return blocked

    def forget_blocked(self, records: list[EgressRecord]) -> None:
        """Leave these exact records (not equal ones) out of the next drain."""
        gone = {id(record) for record in records}
        kept = [record for record in self._blocked if id(record) not in gone]
        self._blocked.clear()
        self._blocked.extend(kept)

    def _record(
        self, host: str, port: int, method: str, decision: Decision, address: str | None
    ) -> None:
        blocked = not decision.allowed and decision.code not in (
            "upstream",
            "resolve_failed",
        )
        record = EgressRecord(host, port, method, blocked, decision.code, address)
        self._count += 1
        self._log.append(record)
        if blocked:
            self._blocked.append(record)

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        task = asyncio.current_task()
        self._tasks.add(task)
        try:
            if not self._closed:
                await self._serve(reader, writer)
        except (OSError, EOFError, asyncio.LimitOverrunError, asyncio.TimeoutError):
            pass
        except Exception as exc:
            logger.warning(
                "Browser proxy connection failed", error=str(exc), exc_info=True
            )
        finally:
            self._targets.pop(task, None)
            try:
                await _close_writer(writer)
            finally:
                self._tasks.discard(task)

    async def _serve(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            head = await asyncio.wait_for(
                reader.readuntil(b"\r\n\r\n"), self._head_timeout
            )
        except asyncio.LimitOverrunError:
            await self._respond(
                reader, writer, _Reject(431, "head_too_large", "request head too large")
            )
            return
        except (asyncio.IncompleteReadError, asyncio.TimeoutError):
            return
        try:
            await self._dispatch(_parse_head(head), reader, writer)
        except _Reject as reject:
            self._targets.pop(asyncio.current_task(), None)
            await self._respond(reader, writer, reject)

    async def _dispatch(
        self,
        request: _Request,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        policy = self._policy
        if request.method == "CONNECT":
            host, port = _authority(request.target)
            decision = policy.check_host(host, port)
        else:
            if not _ABSOLUTE.match(request.target):
                raise _Reject(400, "malformed", "the proxy needs an absolute URL")
            decision = policy.check_url(request.target)
            try:
                parts = urlsplit(request.target)
                host = parts.hostname or ""
                port = parts.port
                if port is None:
                    port = WEB_SCHEMES.get(parts.scheme.lower(), 80)
            except ValueError:
                raise _Reject(400, "malformed", "the URL cannot be parsed") from None
            if decision and parts.scheme.lower() != "http":
                decision = Decision(
                    False, "scheme", "only http:// is forwarded; https uses CONNECT"
                )
            length = _body_framing(request.headers)
        try:
            name = normalize_host(host)
        except ValueError:
            name = _CONTROL.sub("", host)
        if not decision:
            self._record(name, port, request.method, decision, None)
            status = 400 if decision.code in _CLIENT_ERRORS else 403
            raise _Reject(status, decision.code, decision.reason)
        self._register(name, port, request.method)
        upstream, address = await self._open(policy, request.method, name, port)
        self._pin(address)
        try:
            if request.method == "CONNECT":
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await writer.drain()
                await self._tunnel(reader, writer, *upstream)
            else:
                await self._forward(request, parts, length, reader, writer, *upstream)
        finally:
            await _close_writer(upstream[1])

    async def _open(
        self, policy: NetworkPolicy, method: str, host: str, port: int
    ) -> tuple[Streams, str]:
        literal = parse_ip(host)
        if literal is not None:
            answers = [canonical_ip(literal)]
        else:
            try:
                answers = await asyncio.wait_for(
                    self._resolver(host, port), self._connect_timeout
                )
            except (OSError, UnicodeError, asyncio.TimeoutError):
                answers = []
            if not answers:
                failure = Decision(False, "resolve_failed", f"cannot resolve {host}")
                self._record(host, port, method, failure, None)
                raise _Reject(502, failure.code, failure.reason)
        passed: list[str] = []
        denied: tuple[str, Decision] | None = None
        for answer in answers:
            verdict = policy.check_address(answer, port)
            ip = parse_ip(answer)
            if verdict and ip is not None:
                passed.append(canonical_ip(ip))
            elif denied is None:
                denied = (answer, verdict)
        if not passed:
            address, verdict = denied
            self._record(host, port, method, verdict, address)
            raise _Reject(403, verdict.code, verdict.reason)
        for address in passed:
            try:
                streams = await asyncio.wait_for(
                    self._dialer(address, port), self._connect_timeout
                )
            except (OSError, asyncio.TimeoutError):
                continue
            self._record(host, port, method, Decision(True, "ok", "allowed"), address)
            return streams, address
        failure = Decision(False, "upstream", f"cannot connect to {host}:{port}")
        self._record(host, port, method, failure, passed[-1])
        raise _Reject(502, failure.code, failure.reason)

    async def _respond(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        reject: _Reject,
    ) -> None:
        prefix = (
            "Blocked by the browser network policy: " if reject.status == 403 else ""
        )
        body = f"{prefix}{_CONTROL.sub('', reject.reason)}\n".encode()
        head = (
            f"HTTP/1.1 {reject.status} {_STATUS_TEXT[reject.status]}\r\n"
            "Content-Type: text/plain; charset=utf-8\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n"
            f"{BLOCKED_HEADER}: {reject.code}\r\n\r\n"
        )
        writer.write(head.encode("ascii") + body)
        await writer.drain()
        if writer.can_write_eof():
            writer.write_eof()
        await self._linger(reader)

    async def _linger(self, reader: asyncio.StreamReader) -> None:
        """Read and discard client input until EOF, a time limit or a byte limit."""
        deadline = time.monotonic() + LINGER_TIMEOUT_S
        budget = LINGER_BYTES
        try:
            while budget > 0:
                remaining = deadline - time.monotonic()
                data = await asyncio.wait_for(reader.read(CHUNK), remaining)
                if not data:
                    return
                budget -= len(data)
        except (OSError, asyncio.TimeoutError):
            return

    async def _read(self, operation: Awaitable[bytes]) -> bytes:
        return await asyncio.wait_for(operation, self._idle_timeout)

    async def _forward(
        self,
        request: _Request,
        parts: SplitResult,
        length: int | None,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        up_reader: asyncio.StreamReader,
        up_writer: asyncio.StreamWriter,
    ) -> None:
        origin = parts.path or "/"
        if parts.query:
            origin += f"?{parts.query}"
        up_writer.write(_request_head(request, origin, parts.netloc))
        if length is None:
            await self._copy_chunked(reader, up_writer)
        else:
            await self._copy_exact(reader, up_writer, length)
        await up_writer.drain()
        try:
            head = await self._read(up_reader.readuntil(b"\r\n\r\n"))
        except asyncio.IncompleteReadError as exc:
            writer.write(exc.partial)
            await writer.drain()
            return
        except asyncio.LimitOverrunError:
            raise _Reject(502, "upstream", "the upstream response head is too large")
        writer.write(_response_head(head))
        await _race(
            [
                self._pump(up_reader, writer, [time.monotonic()], half_close=False),
                _discard_until_eof(reader),
            ],
            asyncio.FIRST_COMPLETED,
        )

    async def _copy_exact(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, size: int
    ) -> None:
        while size > 0:
            data = await self._read(reader.read(min(CHUNK, size)))
            if not data:
                raise asyncio.IncompleteReadError(b"", size)
            writer.write(data)
            await writer.drain()
            size -= len(data)

    async def _copy_chunked(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        while True:
            line = await self._read(reader.readuntil(b"\r\n"))
            size_text = line[:-2].split(b";", 1)[0].strip()
            if not _CHUNK_SIZE.fullmatch(size_text):
                raise EOFError("malformed chunked request body")
            writer.write(line)
            size = int(size_text, 16)
            if size == 0:
                break
            await self._copy_exact(reader, writer, size)
            if await self._read(reader.readexactly(2)) != b"\r\n":
                raise EOFError("malformed chunked request body")
            writer.write(b"\r\n")
        while True:
            line = await self._read(reader.readuntil(b"\r\n"))
            writer.write(line)
            if line == b"\r\n":
                return

    async def _pump(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        activity: list[float],
        half_close: bool,
    ) -> None:
        """Copy until EOF; give up once neither direction has moved for the idle timeout."""
        while True:
            remaining = activity[0] + self._idle_timeout - time.monotonic()
            if remaining <= 0:
                raise asyncio.TimeoutError("relay idle")
            try:
                data = await asyncio.wait_for(reader.read(CHUNK), remaining)
            except asyncio.TimeoutError:
                continue
            if not data:
                break
            activity[0] = time.monotonic()
            writer.write(data)
            await writer.drain()
        if half_close and writer.can_write_eof():
            writer.write_eof()

    async def _tunnel(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        up_reader: asyncio.StreamReader,
        up_writer: asyncio.StreamWriter,
    ) -> None:
        activity = [time.monotonic()]
        await _race(
            [
                self._pump(reader, up_writer, activity, True),
                self._pump(up_reader, writer, activity, True),
            ],
            asyncio.FIRST_EXCEPTION,
        )
