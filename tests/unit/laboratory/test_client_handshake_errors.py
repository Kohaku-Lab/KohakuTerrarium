"""Error-branch coverage for ClientConnector handshake + loop teardown."""

from unittest.mock import MagicMock

import pytest

from kohakuterrarium.laboratory.config import ClientConfig
from kohakuterrarium.laboratory._internal.client import (
    ClientConnector,
    ClientError,
    ProtocolMismatchError,
)
from kohakuterrarium.laboratory._internal.envelope import (
    Envelope,
    EnvelopeKind,
)
from kohakuterrarium.laboratory._internal.transport_base import (
    ConnectionClosed,
)


class _FakeConnection:
    def __init__(self, sent=None, to_recv=None, raise_send=None, raise_recv=None):
        self.sent = sent if sent is not None else []
        self.to_recv = list(to_recv or [])
        self.raise_send = raise_send
        self.raise_recv = raise_recv
        self.closed = False

    async def send_frame(self, data):
        if self.raise_send:
            raise self.raise_send
        self.sent.append(data)

    async def recv_frame(self):
        if self.raise_recv:
            raise self.raise_recv
        if not self.to_recv:
            raise ConnectionClosed("no more frames")
        return self.to_recv.pop(0)

    async def close(self):
        self.closed = True


class _FakeTransport:
    def __init__(self, conn):
        self._conn = conn

    async def connect(self, addr):
        return self._conn


def _client(transport):
    cfg = ClientConfig(
        client_name="w1",
        host_url="x:1",
        token="t",
        reconnect_initial_delay_seconds=0.05,
    )
    c = ClientConnector(cfg, transport)
    return c


# ── _connect_once handshake error branches ─────────────────


class TestConnectOnceHandshakeErrors:
    async def test_hello_send_fails(self):
        conn = _FakeConnection(raise_send=ConnectionClosed("send broken"))
        c = _client(_FakeTransport(conn))
        with pytest.raises(ConnectionClosed):
            await c._connect_once()

    async def test_welcome_recv_fails(self):
        conn = _FakeConnection(raise_recv=ConnectionClosed("recv broken"))
        c = _client(_FakeTransport(conn))
        with pytest.raises(ConnectionClosed):
            await c._connect_once()

    async def test_malformed_handshake_response(self):
        # Send back garbage that fails Envelope.decode.
        conn = _FakeConnection(to_recv=[b"not-an-envelope"])
        c = _client(_FakeTransport(conn))
        with pytest.raises(ClientError, match="malformed"):
            await c._connect_once()

    async def test_unexpected_envelope_kind(self):
        # Build a valid envelope of an unexpected kind (HEARTBEAT).
        env = Envelope(
            from_node="h",
            to_node="w1",
            kind=EnvelopeKind.HEARTBEAT,
            stream_id=0,
            seq=0,
        )
        conn = _FakeConnection(to_recv=[env.encode()])
        c = _client(_FakeTransport(conn))
        with pytest.raises(ClientError, match="unexpected envelope kind"):
            await c._connect_once()


# ── reject parsing branches ─────────────────────────────────


class TestRejectParsing:
    async def test_malformed_reject_raises(self):
        # A CONTROL envelope but the reject body is malformed.
        env = Envelope(
            from_node="h",
            to_node="w1",
            kind=EnvelopeKind.CONTROL,
            stream_id=0,
            seq=0,
            payload=b"\x00\x00",  # nonsense control body
        )
        conn = _FakeConnection(to_recv=[env.encode()])
        c = _client(_FakeTransport(conn))
        with pytest.raises(ClientError, match="malformed control"):
            await c._connect_once()

    async def test_protocol_mismatch_reject_raises_typed_error(self):
        # A reject with reason ``protocol_mismatch`` must surface as the
        # dedicated ProtocolMismatchError, carrying the host's detail.
        from kohakuterrarium.laboratory._internal.protocol import (
            RejectPayload,
            build_reject,
        )

        env = build_reject(
            RejectPayload(reason="protocol_mismatch", detail="host needs 2.0"),
            to_node="w1",
        )
        conn = _FakeConnection(to_recv=[env.encode()])
        c = _client(_FakeTransport(conn))
        with pytest.raises(ProtocolMismatchError, match="host needs 2.0"):
            await c._connect_once()

    async def test_unknown_reject_reason_raises_generic_client_error(self):
        # An unrecognised reject reason falls through to a generic
        # ClientError that names both the reason and the detail.
        from kohakuterrarium.laboratory._internal.protocol import (
            RejectPayload,
            build_reject,
        )

        env = build_reject(
            RejectPayload(reason="quota_exceeded", detail="too many nodes"),
            to_node="w1",
        )
        conn = _FakeConnection(to_recv=[env.encode()])
        c = _client(_FakeTransport(conn))
        with pytest.raises(ClientError, match="quota_exceeded"):
            await c._connect_once()


# ── invalid welcome payload ─────────────────────────────────


class TestInvalidWelcome:
    async def test_malformed_welcome_payload_raises_client_error(self):
        # A WELCOME-kind envelope whose payload can't be parsed must
        # surface as a ClientError("invalid welcome"), not leak the raw
        # ProtocolError.
        env = Envelope(
            from_node="h",
            to_node="w1",
            kind=EnvelopeKind.WELCOME,
            stream_id=0,
            seq=0,
            payload=b"\x80",  # empty-map / missing required welcome keys
        )
        conn = _FakeConnection(to_recv=[env.encode()])
        c = _client(_FakeTransport(conn))
        with pytest.raises(ClientError, match="invalid welcome"):
            await c._connect_once()


# ── _tear_down_connection idempotency ──────────────────────


class TestTearDownConnection:
    async def test_no_connection_returns(self):
        # No tasks, no connection — should not raise.
        cfg = ClientConfig(client_name="w", host_url="x", token="t")
        c = ClientConnector(cfg, MagicMock())
        await c._tear_down_connection()

    async def test_close_swallows_exception(self):
        cfg = ClientConfig(client_name="w", host_url="x", token="t")
        c = ClientConnector(cfg, MagicMock())

        class _BadConn:
            async def close(self):
                raise RuntimeError("close failed")

        c._connection = _BadConn()
        await c._tear_down_connection()
        # No re-raise.
        assert c._connection is None
