"""Explain MCP connect failures and stop a failing HTTP handshake early.

The ``mcp`` SDK's HTTP transports run their requests inside anyio task
groups, so an HTTP error status or a refused connection reaches the
caller as an opaque exception group, often only after the connect
timeout. :class:`HandshakeObserver` watches every request the transport
sends through :class:`ObservingClient` and flags the first fatal one;
:func:`explain_failure` / :func:`explain_timeout` turn what was seen into
one short typed error naming the server, its URL and a hint.
"""

import asyncio
import ssl
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from kohakuterrarium.errors import (
    MCPCommandNotFoundError,
    MCPConnectionError,
    MCPError,
    MCPTimeoutError,
)

SDK_DEFAULT_TIMEOUT = httpx.Timeout(30.0, read=300.0)

_SSE_KINDS = {"sse", "http"}
_AUTH_HINT = "check the headers/token"
_PATH_HINT = "check the URL path (for example /mcp)"
_SERVER_HINT = "server error: check the MCP server's logs"
_SSE_HINT = "the server may speak streamable HTTP: try the streamable_http transport"


def display_url(url: str) -> str:
    """Return ``url`` without credentials, query string or fragment."""
    if not url:
        return ""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url.split("?", 1)[0]
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    try:
        port = parts.port
    except ValueError:
        port = None
    netloc = f"{host}:{port}" if port is not None else host
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def leaf_exceptions(exc: BaseException) -> list[BaseException]:
    """Flatten nested exception groups into their leaf exceptions.

    Cancellations are dropped when a real cause sits beside them.
    """
    leaves: list[BaseException] = []
    stack = [exc]
    while stack:
        current = stack.pop(0)
        children = getattr(current, "exceptions", None)
        if isinstance(children, (tuple, list)) and all(
            isinstance(c, BaseException) for c in children
        ):
            stack[:0] = list(children)
        else:
            leaves.append(current)
    real = [e for e in leaves if not isinstance(e, asyncio.CancelledError)]
    return real or leaves


def _status_text(status: int) -> str:
    try:
        return f"{status} {HTTPStatus(status).phrase}"
    except ValueError:
        return str(status)


@dataclass
class HTTPOutcome:
    """One request the handshake sent: its status, or the error it raised."""

    method: str
    url: str
    status: int | None = None
    error: BaseException | None = None


class HandshakeObserver:
    """Record the HTTP outcomes of one MCP connect and flag the first fatal one.

    For ``streamable_http`` only POST errors are fatal (the GET event stream
    and the closing DELETE are optional); for SSE every request is.
    """

    def __init__(self, transport: str) -> None:
        self.transport = transport
        self.failed = asyncio.Event()
        self.failure: HTTPOutcome | None = None
        self.last: HTTPOutcome | None = None
        self._armed = True

    def disarm(self) -> None:
        """Stop treating errors as handshake failures (the connect finished)."""
        self._armed = False

    def _is_fatal_method(self, method: str) -> bool:
        return self.transport in _SSE_KINDS or method.upper() == "POST"

    def _flag(self, outcome: HTTPOutcome) -> None:
        if self._armed and self.failure is None:
            if self._is_fatal_method(outcome.method):
                self.failure = outcome
                self.failed.set()

    def record_response(self, request: httpx.Request, response: httpx.Response) -> None:
        """Record a response; an error status may fail the handshake."""
        outcome = HTTPOutcome(
            request.method, str(request.url), status=response.status_code
        )
        self.last = outcome
        if response.status_code >= 400:
            self._flag(outcome)

    def record_error(self, request: httpx.Request, error: BaseException) -> None:
        """Record a request that raised; transport errors fail the handshake."""
        outcome = HTTPOutcome(request.method, str(request.url), error=error)
        self.last = outcome
        if isinstance(error, httpx.TransportError):
            self._flag(outcome)

    def client_factory(
        self,
        headers: dict[str, str] | None = None,
        timeout: httpx.Timeout | None = None,
        auth: httpx.Auth | None = None,
    ) -> httpx.AsyncClient:
        """``httpx_client_factory`` for the SDK's ``sse_client`` / ``streamablehttp_client``."""
        kwargs: dict[str, Any] = {"timeout": timeout or SDK_DEFAULT_TIMEOUT}
        if headers is not None:
            kwargs["headers"] = headers
        if auth is not None:
            kwargs["auth"] = auth
        return ObservingClient(self, **kwargs)


class ObservingClient(httpx.AsyncClient):
    """``httpx.AsyncClient`` that reports every request's outcome to an observer."""

    def __init__(self, observer: HandshakeObserver, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._observer = observer

    async def send(self, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        try:
            response = await super().send(request, **kwargs)
        except Exception as e:
            self._observer.record_error(request, e)
            raise
        self._observer.record_response(request, response)
        return response


def _status_hint(status: int, transport: str) -> str:
    if status in (401, 403):
        return _AUTH_HINT
    if transport in _SSE_KINDS and status in (400, 405, 406):
        return _SSE_HINT
    if status == 404:
        return _PATH_HINT
    if status >= 500:
        return _SERVER_HINT
    return ""


def _status_error(
    config: Any, status: int, url: str, cause: BaseException | None
) -> MCPConnectionError:
    shown = display_url(url or config.url)
    message = f"MCP server {config.name}: HTTP {_status_text(status)} from {shown}"
    hint = _status_hint(status, config.transport)
    if hint:
        message = f"{message}; {hint}"
    err = MCPConnectionError(message, server=config.name, url=shown, status=status)
    err.__cause__ = cause
    return err


def _is_tls(exc: BaseException) -> bool:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        if isinstance(current, ssl.SSLError):
            return True
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    text = str(exc)
    return "SSL" in text or "CERTIFICATE" in text.upper()


def _transport_error(
    config: Any, exc: BaseException, url: str
) -> MCPConnectionError | None:
    shown = display_url(url or config.url)
    reason = str(exc) or type(exc).__name__
    message: str | None = None
    cls = MCPConnectionError
    match exc:
        case httpx.TimeoutException():
            message = f"timed out talking to {shown}: {reason}"
            cls = MCPTimeoutError
        case httpx.ConnectError() if _is_tls(exc):
            message = f"TLS handshake with {shown} failed: {reason}"
        case httpx.ConnectError() | ConnectionRefusedError():
            message = f"cannot connect to {shown}: {reason}"
        case httpx.NetworkError() | ConnectionError():
            message = f"connection to {shown} failed: {reason}"
        case httpx.RemoteProtocolError():
            message = f"{shown} broke the HTTP protocol: {reason}"
        case ssl.SSLError():
            message = f"TLS handshake with {shown} failed: {reason}"
    if message is None:
        return None
    err = cls(f"MCP server {config.name}: {message}", server=config.name, url=shown)
    err.__cause__ = exc
    return err


def _stdio_error(config: Any, exc: BaseException) -> MCPConnectionError | None:
    name = config.name
    if isinstance(exc, FileNotFoundError):
        err: MCPConnectionError = MCPCommandNotFoundError(
            f"MCP server {name}: command not found: {config.command}", server=name
        )
    elif isinstance(exc, PermissionError):
        err = MCPConnectionError(
            f"MCP server {name}: command is not executable: {config.command}",
            server=name,
        )
    elif type(exc).__name__ == "McpError" and "closed" in str(exc).lower():
        err = MCPConnectionError(
            f"MCP server {name}: the server process exited before the handshake "
            f"finished ({exc}); run {config.command} by hand to see its error",
            server=name,
        )
    else:
        return None
    err.__cause__ = exc
    return err


def explain_failure(
    config: Any,
    exc: BaseException,
    observer: HandshakeObserver | None,
    *,
    timeout: float | None,
) -> MCPError:
    """Turn a failed connect into one typed error naming its root cause.

    ``config`` is the :class:`MCPServerConfig`; ``observer`` holds what
    the HTTP handshake saw. Header values never reach the message.
    """
    if isinstance(exc, MCPError):
        return exc
    failure = observer.failure if observer is not None else None
    if failure is not None and failure.status is not None:
        return _status_error(config, failure.status, failure.url, exc)
    if failure is not None and failure.error is not None:
        translated = _transport_error(config, failure.error, failure.url)
        if translated is not None:
            return translated
    leaves = leaf_exceptions(exc)
    for leaf in leaves:
        if isinstance(leaf, httpx.HTTPStatusError):
            return _status_error(
                config, leaf.response.status_code, str(leaf.request.url), leaf
            )
    for leaf in leaves if config.url else []:
        url = str(leaf.request.url) if _has_request(leaf) else ""
        translated = _transport_error(config, leaf, url)
        if translated is not None:
            return translated
    for leaf in leaves:
        if config.transport == "stdio" or not config.url:
            translated = _stdio_error(config, leaf)
            if translated is not None:
                return translated
        if type(leaf).__name__ == "McpError" and "terminated" in str(leaf).lower():
            shown = display_url(config.url)
            err = MCPConnectionError(
                f"MCP server {config.name}: {shown} ended the session ({leaf}); "
                f"{_PATH_HINT}",
                server=config.name,
                url=shown,
            )
            err.__cause__ = leaf
            return err
    root = leaves[0]
    if isinstance(root, asyncio.TimeoutError) and timeout is not None:
        return explain_timeout(config, observer, timeout)
    detail = f"{type(root).__name__}: {root}" if str(root) else type(root).__name__
    err = MCPConnectionError(
        f"MCP server {config.name}: {detail}",
        server=config.name,
        url=display_url(config.url),
    )
    err.__cause__ = root
    return err


def _has_request(exc: BaseException) -> bool:
    try:
        return isinstance(exc, httpx.RequestError) and exc.request is not None
    except RuntimeError:
        return False


def explain_timeout(
    config: Any, observer: HandshakeObserver | None, timeout: float
) -> MCPTimeoutError:
    """Build the connect-timeout error, with the last HTTP outcome when known."""
    message = f"MCP server {config.name}: timed out after {timeout:g}s"
    status = None
    if observer is not None and config.url:
        last = observer.last
        if last is None:
            message += f"; no HTTP response from {display_url(config.url)}"
        elif last.status is not None:
            status = last.status
            message += (
                f"; last HTTP response was {_status_text(last.status)} "
                f"for {last.method} {display_url(last.url)}"
            )
        elif last.error is not None:
            message += (
                f"; last HTTP request {last.method} {display_url(last.url)} "
                f"failed: {type(last.error).__name__}: {last.error}"
            )
    return MCPTimeoutError(
        message, server=config.name, url=display_url(config.url), status=status
    )
