"""L2 envelope — framing for the Laboratory wire protocol.

Every byte stream between nodes is broken into envelopes. Each envelope
carries routing metadata plus an opaque binary payload (and an optional
signature).

Wire format is a length-prefixed **msgpack header** followed by **raw
binary payload** and **raw binary signature**. No base64, no string
escaping for binary data:

::

    +------------------ envelope on the wire ------------------+
    | 4 bytes  big-endian uint32        header_len             |
    +----------------------------------------------------------+
    | header_len bytes                  msgpack-encoded header |
    |   { from, to, kind, stream_id, seq, flags,               |
    |     payload_len, sig_len }                               |
    +----------------------------------------------------------+
    | header.payload_len bytes          raw payload            |
    +----------------------------------------------------------+
    | header.sig_len bytes              raw signature          |
    +----------------------------------------------------------+

The msgpack header (via ``kohakuvault.DataPacker('msgpack')``) handles
metadata efficiently; payload and signature ride on the wire raw, with
their lengths referenced from the header. This avoids the base64
expansion that a flat-msgpack design would require (kohakuvault's
msgpack rejects raw bytes values).
"""

import struct
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from kohakuvault import DataPacker

_PACKER = DataPacker("msgpack")
"""Process-wide msgpack codec. DataPacker construction is cheap but
caching avoids repeated allocation on the hot path."""

_HEADER_LEN_STRUCT = struct.Struct(">I")
"""4-byte big-endian uint32 prefix carrying header length."""

_HEADER_PREFIX_LEN = _HEADER_LEN_STRUCT.size


class EnvelopeKind(str, Enum):
    """The L4 verb or control-plane purpose an envelope serves.

    L4 user verbs:
        SEND — point-to-point delivery (Channel.send)
        BROADCAST — pub-sub fan-out (Topic.publish)
        APP — structured application-level message with
            namespace + type + body (request/response capable)
        LOG — Replicate verb (reserved; not shipped in 1.5)

    Control plane:
        ACK — acknowledgement for ack-required SEND
        HELLO — client-to-host handshake (carries auth token)
        WELCOME — host-to-client handshake response
        HEARTBEAT — periodic liveness check
        CONTROL — framework-internal control messages
            (subscribe, register_creature, reject, etc.)
    """

    SEND = "send"
    BROADCAST = "broadcast"
    APP = "app"
    LOG = "log"
    ACK = "ack"
    HELLO = "hello"
    WELCOME = "welcome"
    HEARTBEAT = "heartbeat"
    CONTROL = "control"


class EnvelopeDecodeError(ValueError):
    """Raised when envelope bytes cannot be decoded.

    Wraps the underlying parsing error (msgpack, struct, truncation,
    etc.) with a message that identifies which field failed validation.
    """


_BROADCAST_TARGET = "*"
"""Sentinel ``to_node`` value indicating fan-out to all subscribers."""


@dataclass(frozen=True)
class Envelope:
    """L2 envelope — the unit of framing on the wire.

    Attributes:
        from_node: NodeId of the sending node.
        to_node: NodeId of the receiving node, or ``"*"`` for broadcast.
        kind: Discriminator for what the envelope carries (see
            :class:`EnvelopeKind`).
        stream_id: Logical sub-stream within the connection. Each L4
            call uses a distinct ``stream_id`` so streams multiplex
            without head-of-line blocking.
        seq: Per-``stream_id`` monotonically increasing sequence number.
            Used by L3 for ordering and dedupe.
        payload: Opaque bytes. The L4 verb interprets contents.
        flags: Per-call options recognized by L3 (e.g.
            ``ack_required``, ``retransmit``).
        sig: Optional cryptographic signature over the envelope. Reserved
            for trust-boundary deployments; not used in 1.5.0.
        request_id: Optional correlation id for request/response RPC.
            When set, the receiver may respond with an envelope whose
            ``in_reply_to`` matches this value. Applies to any kind, but
            primarily meaningful for ``APP`` envelopes.
        in_reply_to: Set on response envelopes; matches the original
            request's ``request_id``. Senders match outstanding requests
            by this field.
    """

    from_node: str
    to_node: str
    kind: EnvelopeKind
    stream_id: int
    seq: int
    payload: bytes = b""
    flags: dict[str, Any] = field(default_factory=dict)
    sig: bytes | None = None
    request_id: str | None = None
    in_reply_to: str | None = None

    def is_broadcast(self) -> bool:
        """Return whether this envelope targets every listener."""
        return self.to_node == _BROADCAST_TARGET

    def encode(self) -> bytes:
        """Serialize this envelope to wire bytes.

        Wire layout: ``[4-byte header_len][msgpack header][payload][sig]``.
        """
        sig_len = len(self.sig) if self.sig is not None else 0
        header: dict[str, Any] = {
            "from": self.from_node,
            "to": self.to_node,
            "kind": self.kind.value,
            "stream_id": self.stream_id,
            "seq": self.seq,
            "flags": self.flags,
            "payload_len": len(self.payload),
            "sig_len": sig_len,
        }
        # Optional correlation fields are omitted from the header when
        # unset to keep the on-the-wire footprint minimal for the common
        # SEND/BROADCAST case where neither applies.
        if self.request_id is not None:
            header["request_id"] = self.request_id
        if self.in_reply_to is not None:
            header["in_reply_to"] = self.in_reply_to
        header_bytes = _PACKER.pack(header)
        parts = [
            _HEADER_LEN_STRUCT.pack(len(header_bytes)),
            header_bytes,
            self.payload,
        ]
        if sig_len > 0:
            parts.append(self.sig)  # type: ignore[arg-type]
        return b"".join(parts)

    @classmethod
    def decode(cls, raw: bytes) -> "Envelope":
        """Deserialize envelope bytes.

        Unknown header fields are tolerated. Missing required fields,
        malformed msgpack header, truncation, or unknown ``kind`` raise
        :class:`EnvelopeDecodeError`.
        """
        if len(raw) < _HEADER_PREFIX_LEN:
            raise EnvelopeDecodeError(
                f"envelope too short: {len(raw)} bytes, "
                f"need at least {_HEADER_PREFIX_LEN}"
            )
        (header_len,) = _HEADER_LEN_STRUCT.unpack(raw[:_HEADER_PREFIX_LEN])
        header_end = _HEADER_PREFIX_LEN + header_len
        if len(raw) < header_end:
            raise EnvelopeDecodeError(
                f"envelope header truncated: header_len={header_len}, "
                f"available={len(raw) - _HEADER_PREFIX_LEN}"
            )
        try:
            header = _PACKER.unpack(raw[_HEADER_PREFIX_LEN:header_end], 0)
        except (ValueError, TypeError) as exc:
            raise EnvelopeDecodeError(
                f"envelope header is not valid msgpack: {exc}"
            ) from exc
        if not isinstance(header, dict):
            raise EnvelopeDecodeError(
                f"envelope header must be a msgpack map, "
                f"got {type(header).__name__}"
            )

        required = ("from", "to", "kind", "stream_id", "seq")
        missing = [k for k in required if k not in header]
        if missing:
            raise EnvelopeDecodeError(f"missing required fields: {missing}")

        try:
            kind = EnvelopeKind(header["kind"])
        except ValueError as exc:
            raise EnvelopeDecodeError(f"unknown kind: {header['kind']!r}") from exc

        try:
            stream_id = int(header["stream_id"])
            seq = int(header["seq"])
        except (TypeError, ValueError) as exc:
            raise EnvelopeDecodeError(
                f"stream_id and seq must be integers: {exc}"
            ) from exc

        payload_len = int(header.get("payload_len", 0))
        sig_len = int(header.get("sig_len", 0))
        if payload_len < 0 or sig_len < 0:
            raise EnvelopeDecodeError(
                f"payload_len and sig_len must be non-negative; "
                f"got payload_len={payload_len}, sig_len={sig_len}"
            )

        expected_total = header_end + payload_len + sig_len
        if len(raw) < expected_total:
            raise EnvelopeDecodeError(
                f"envelope body truncated: expected {expected_total} "
                f"bytes total, got {len(raw)}"
            )

        payload_end = header_end + payload_len
        payload = bytes(raw[header_end:payload_end])
        sig: bytes | None
        if sig_len > 0:
            sig = bytes(raw[payload_end : payload_end + sig_len])
        else:
            sig = None

        flags = header.get("flags", {}) or {}
        if not isinstance(flags, dict):
            raise EnvelopeDecodeError(
                f"flags must be a msgpack map, got {type(flags).__name__}"
            )

        request_id = _optional_str(header, "request_id")
        in_reply_to = _optional_str(header, "in_reply_to")

        return cls(
            from_node=str(header["from"]),
            to_node=str(header["to"]),
            kind=kind,
            stream_id=stream_id,
            seq=seq,
            payload=payload,
            flags=flags,
            sig=sig,
            request_id=request_id,
            in_reply_to=in_reply_to,
        )


def _optional_str(header: dict, key: str) -> str | None:
    """Read an optional string-or-null header field; raise on wrong type."""
    value = header.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise EnvelopeDecodeError(
            f"{key} must be a string or null, got {type(value).__name__}"
        )
    return value


__all__ = [
    "Envelope",
    "EnvelopeDecodeError",
    "EnvelopeKind",
]
