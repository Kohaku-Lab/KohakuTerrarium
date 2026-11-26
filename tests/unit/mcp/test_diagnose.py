"""Unit tests for ``mcp/diagnose.py``: turning MCP connect failures into clear errors.

The handshake observer is driven through a real ``httpx`` client on an
``httpx.MockTransport`` (the only I/O seam); the explainer is fed the
exception shapes the ``mcp`` SDK really raises (anyio exception groups
wrapping ``httpx`` errors, ``McpError``, ``FileNotFoundError``).
"""

import asyncio
import ssl
import sys

import httpx
import pytest

from kohakuterrarium.errors import (
    MCPCommandNotFoundError,
    MCPConnectionError,
    MCPError,
    MCPTimeoutError,
)
from kohakuterrarium.mcp.client import MCPServerConfig
from kohakuterrarium.mcp.diagnose import (
    HandshakeObserver,
    ObservingClient,
    display_url,
    explain_failure,
    explain_timeout,
    leaf_exceptions,
)

if sys.version_info < (3, 11):  # pragma: no cover - CI runs 3.12+
    from exceptiongroup import BaseExceptionGroup, ExceptionGroup

TOKEN = "s3cret-t0ken"
URL = "http://127.0.0.1:9/mcp?key=s3cret-t0ken"


def _remote(transport="streamable_http", url=URL):
    return MCPServerConfig(
        name="sandbox",
        transport=transport,
        url=url,
        headers={"Authorization": f"Bearer {TOKEN}"},
    )


def _status_error(status, method="POST", url=URL):
    request = httpx.Request(method, url)
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError(f"{status}", request=request, response=response)


def _observed(status, *, transport="streamable_http", method="POST"):
    observer = HandshakeObserver(transport)
    request = httpx.Request(method, URL)
    observer.record_response(request, httpx.Response(status, request=request))
    return observer


class TestLeafExceptions:
    def test_plain_exception_is_its_own_leaf(self):
        err = RuntimeError("x")
        assert leaf_exceptions(err) == [err]

    def test_nested_groups_flatten_to_their_leaves(self):
        a, b = ValueError("a"), KeyError("b")
        group = ExceptionGroup("outer", [ExceptionGroup("inner", [a]), b])
        assert leaf_exceptions(group) == [a, b]

    def test_cancellations_are_dropped_when_a_real_cause_exists(self):
        real = httpx.ConnectError("refused")
        group = BaseExceptionGroup("g", [asyncio.CancelledError(), real])
        assert leaf_exceptions(group) == [real]

    def test_only_cancellations_are_kept(self):
        cancel = asyncio.CancelledError()
        group = BaseExceptionGroup("g", [cancel])
        assert leaf_exceptions(group) == [cancel]


class TestDisplayUrl:
    def test_query_and_credentials_are_removed(self):
        assert (
            display_url("https://user:pw@example.com:8443/mcp?token=abc#frag")
            == "https://example.com:8443/mcp"
        )

    def test_plain_url_is_unchanged(self):
        assert display_url("http://127.0.0.1:8000/mcp") == "http://127.0.0.1:8000/mcp"

    def test_empty_url_stays_empty(self):
        assert display_url("") == ""


class TestExplainHttpStatus:
    @pytest.mark.parametrize(
        ("status", "phrase", "hint"),
        [
            (401, "Unauthorized", "check the headers/token"),
            (403, "Forbidden", "check the headers/token"),
            (404, "Not Found", "check the URL path"),
            (500, "Internal Server Error", "server error"),
            (503, "Service Unavailable", "server error"),
        ],
    )
    def test_status_from_the_observer_names_server_url_and_hint(
        self, status, phrase, hint
    ):
        err = explain_failure(
            _remote(), RuntimeError("opaque"), _observed(status), timeout=20
        )
        assert isinstance(err, MCPConnectionError)
        assert err.status == status
        assert str(err).startswith(
            f"MCP server sandbox: HTTP {status} {phrase} from " "http://127.0.0.1:9/mcp"
        )
        assert hint in str(err)
        assert TOKEN not in str(err)

    def test_status_inside_an_exception_group_is_found_without_observer(self):
        group = ExceptionGroup("unhandled errors in a TaskGroup", [_status_error(401)])
        err = explain_failure(_remote(), group, None, timeout=20)
        assert str(err) == (
            "MCP server sandbox: HTTP 401 Unauthorized from http://127.0.0.1:9/mcp; "
            "check the headers/token"
        )

    def test_bad_request_on_sse_suggests_streamable_http(self):
        err = explain_failure(
            _remote("sse"),
            RuntimeError("x"),
            _observed(400, transport="sse", method="GET"),
            timeout=20,
        )
        assert "HTTP 400 Bad Request" in str(err)
        assert "try the streamable_http transport" in str(err)

    def test_legacy_http_alias_counts_as_sse(self):
        err = explain_failure(
            _remote("http"),
            RuntimeError("x"),
            _observed(405, transport="http", method="GET"),
            timeout=20,
        )
        assert "try the streamable_http transport" in str(err)

    def test_bad_request_on_streamable_http_has_no_transport_hint(self):
        err = explain_failure(_remote(), RuntimeError("x"), _observed(400), timeout=20)
        assert "streamable_http transport" not in str(err)
        assert "HTTP 400 Bad Request" in str(err)


class TestExplainTransportErrors:
    def test_connect_error_says_cannot_connect(self):
        group = ExceptionGroup(
            "g", [httpx.ConnectError("All connection attempts failed")]
        )
        err = explain_failure(_remote(), group, None, timeout=20)
        assert isinstance(err, MCPConnectionError)
        assert str(err) == (
            "MCP server sandbox: cannot connect to http://127.0.0.1:9/mcp: "
            "All connection attempts failed"
        )

    def test_observed_connect_error_wins_over_the_opaque_exception(self):
        observer = HandshakeObserver("streamable_http")
        observer.record_error(
            httpx.Request("POST", URL), httpx.ConnectError("Connection refused")
        )
        err = explain_failure(_remote(), RuntimeError("opaque"), observer, timeout=20)
        assert "cannot connect to http://127.0.0.1:9/mcp: Connection refused" in str(
            err
        )

    def test_tls_failure_is_named(self):
        cause = ssl.SSLCertVerificationError("certificate verify failed")
        connect = httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED]")
        connect.__cause__ = cause
        err = explain_failure(_remote(url="https://h/mcp"), connect, None, timeout=20)
        assert str(err).startswith(
            "MCP server sandbox: TLS handshake with https://h/mcp failed:"
        )

    def test_http_timeout_is_named(self):
        err = explain_failure(
            _remote(), httpx.ReadTimeout("read timed out"), None, timeout=20
        )
        assert isinstance(err, MCPTimeoutError)
        assert "timed out talking to http://127.0.0.1:9/mcp" in str(err)

    def test_session_terminated_without_status_points_at_the_path(self):
        class McpError(Exception):
            pass

        err = explain_failure(
            _remote(), McpError("Session terminated"), None, timeout=20
        )
        assert "check the URL path" in str(err)

    def test_unknown_cause_keeps_its_type_and_message(self):
        err = explain_failure(
            _remote(), RuntimeError("transport exploded"), None, timeout=20
        )
        assert isinstance(err, MCPConnectionError)
        assert str(err) == "MCP server sandbox: RuntimeError: transport exploded"
        assert isinstance(err.__cause__, RuntimeError)

    def test_typed_mcp_errors_pass_through(self):
        original = MCPConnectionError("MCP server sandbox: already clear")
        assert explain_failure(_remote(), original, None, timeout=20) is original


class TestExplainStdio:
    def test_missing_command(self):
        cfg = MCPServerConfig(name="fs", command="no-such-binary-xyz")
        err = explain_failure(cfg, FileNotFoundError(2, "nope"), None, timeout=20)
        assert isinstance(err, MCPCommandNotFoundError)
        assert isinstance(err, FileNotFoundError)
        assert str(err) == "MCP server fs: command not found: no-such-binary-xyz"

    def test_process_exit_before_handshake(self):
        class McpError(Exception):
            pass

        cfg = MCPServerConfig(name="fs", command="python", args=["--key", TOKEN])
        err = explain_failure(cfg, McpError("Connection closed"), None, timeout=20)
        assert TOKEN not in str(err)
        assert str(err) == (
            "MCP server fs: the server process exited before the handshake finished "
            "(Connection closed); run python by hand to see its error"
        )


class TestExplainTimeout:
    def test_timeout_reports_last_status(self):
        err = explain_timeout(_remote(), _observed(200), 20)
        assert isinstance(err, MCPTimeoutError)
        assert isinstance(err, TimeoutError)
        assert str(err) == (
            "MCP server sandbox: timed out after 20s; last HTTP response was 200 OK "
            "for POST http://127.0.0.1:9/mcp"
        )

    def test_timeout_without_any_response(self):
        err = explain_timeout(_remote(), HandshakeObserver("streamable_http"), 2.5)
        assert str(err) == (
            "MCP server sandbox: timed out after 2.5s; no HTTP response from "
            "http://127.0.0.1:9/mcp"
        )

    def test_stdio_timeout(self):
        err = explain_timeout(MCPServerConfig(name="fs", command="c"), None, 5)
        assert str(err) == "MCP server fs: timed out after 5s"


class TestHandshakeObserver:
    async def _send(self, observer, handler, method="POST"):
        client = ObservingClient(observer, transport=httpx.MockTransport(handler))
        async with client:
            return await client.request(method, URL)

    async def test_error_status_on_post_fails_streamable_handshake(self):
        observer = HandshakeObserver("streamable_http")
        await self._send(observer, lambda r: httpx.Response(401))
        assert observer.failed.is_set()
        assert observer.failure.status == 401
        assert observer.failure.method == "POST"

    async def test_get_and_delete_errors_do_not_fail_streamable(self):
        observer = HandshakeObserver("streamable_http")
        await self._send(observer, lambda r: httpx.Response(405), method="GET")
        await self._send(observer, lambda r: httpx.Response(404), method="DELETE")
        assert not observer.failed.is_set()
        assert observer.last.status == 404

    async def test_any_error_status_fails_sse(self):
        observer = HandshakeObserver("sse")
        await self._send(observer, lambda r: httpx.Response(400), method="GET")
        assert observer.failed.is_set()

    async def test_redirect_and_success_do_not_fail(self):
        observer = HandshakeObserver("streamable_http")
        await self._send(observer, lambda r: httpx.Response(307))
        await self._send(observer, lambda r: httpx.Response(202))
        assert not observer.failed.is_set()
        assert observer.last.status == 202

    async def test_transport_error_is_recorded_and_reraised(self):
        observer = HandshakeObserver("streamable_http")

        def _refuse(request):
            raise httpx.ConnectError("Connection refused", request=request)

        with pytest.raises(httpx.ConnectError):
            await self._send(observer, _refuse)
        assert observer.failed.is_set()
        assert isinstance(observer.failure.error, httpx.ConnectError)

    async def test_disarmed_observer_records_but_never_fails(self):
        observer = HandshakeObserver("streamable_http")
        observer.disarm()
        await self._send(observer, lambda r: httpx.Response(500))
        assert not observer.failed.is_set()
        assert observer.last.status == 500

    async def test_first_failure_is_kept(self):
        observer = HandshakeObserver("sse")
        await self._send(observer, lambda r: httpx.Response(401))
        await self._send(observer, lambda r: httpx.Response(500))
        assert observer.failure.status == 401

    def test_client_factory_matches_the_sdk_signature(self):
        observer = HandshakeObserver("sse")
        client = observer.client_factory(
            headers={"Authorization": "Bearer x"},
            timeout=httpx.Timeout(5, read=7),
            auth=None,
        )
        assert isinstance(client, ObservingClient)
        assert client.headers["Authorization"] == "Bearer x"
        assert client.timeout.read == 7
        default = observer.client_factory()
        assert default.timeout.read == 300


def test_mcp_errors_share_one_base():
    assert issubclass(MCPTimeoutError, MCPError)
    assert issubclass(MCPCommandNotFoundError, MCPError)
