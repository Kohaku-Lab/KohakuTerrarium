"""Laboratory wire protocol version + Hello/Welcome/Reject handshake.

Protocol version is independent of the KohakuTerrarium framework
version (see `Locked decisions` in the 1.5.0 implementation plan):
two framework releases that speak the same protocol version interop.

The handshake sequence on every client connection is:

    client → host : Hello (carries auth token + client identity)
    host → client : Welcome (carries assigned client_id) on success
                    Reject (carries reason) on failure

After Welcome, both sides exchange Send / Broadcast / Heartbeat /
Control envelopes using NodeId addressing.
"""

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from kohakuvault import DataPacker

from kohakuterrarium.laboratory._internal.envelope import (
    Envelope,
    EnvelopeKind,
)

_PACKER = DataPacker("msgpack")
"""Process-wide msgpack codec for the inner payload of handshake envelopes."""


LAB_PROTOCOL_VERSION = "1.0"
"""Current wire protocol version this implementation speaks."""

SUPPORTED_PROTOCOL_VERSIONS: frozenset[str] = frozenset({"1.0"})
"""Set of protocol versions this implementation accepts from peers."""

HOST_NODE_ID = "_host"
"""Sentinel NodeId for the host. Both sides know this convention.

Used before a client has been assigned its own NodeId (during the
Hello handshake) and to address the host in cluster-level control
messages.
"""


class ProtocolError(ValueError):
    """Raised on protocol-level violations.

    Examples: wrong envelope kind for a parse helper, missing required
    field in a Hello/Welcome/Reject payload, malformed JSON inside the
    envelope payload.
    """


def protocol_compatible(
    remote_version: str,
    local_supported: Iterable[str] = SUPPORTED_PROTOCOL_VERSIONS,
) -> bool:
    """Return whether this implementation can speak ``remote_version``.

    Compatibility is currently strict equality membership: the remote
    version must appear in the local supported set. Future versions may
    relax this to range-based compatibility once we have more than one
    version to consider.
    """
    return remote_version in set(local_supported)


@dataclass(frozen=True)
class HelloPayload:
    """Client → host handshake payload.

    Attributes:
        protocol_version: Lab wire protocol version (independent of KT
            framework version).
        framework_version: KohakuTerrarium version, for diagnostics only.
        client_name: Human-readable client identifier from
            :class:`~kohakuterrarium.laboratory.config.ClientConfig`.
        token: Shared cluster token. Validated by host's
            :class:`TokenAuth` against the configured expected token.
        capabilities: Strings the client advertises (e.g. ``["gpu"]``).
    """

    protocol_version: str
    framework_version: str
    client_name: str
    token: str
    capabilities: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict for embedding in an envelope payload."""
        return {
            "protocol_version": self.protocol_version,
            "framework_version": self.framework_version,
            "client_name": self.client_name,
            "token": self.token,
            "capabilities": list(self.capabilities),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HelloPayload":
        """Parse from a dict; raises ProtocolError on missing/wrong-type fields."""
        required = (
            "protocol_version",
            "framework_version",
            "client_name",
            "token",
        )
        missing = [k for k in required if k not in data]
        if missing:
            raise ProtocolError(f"Hello missing required fields: {missing}")
        for key in required:
            if not isinstance(data[key], str):
                raise ProtocolError(
                    f"Hello.{key} must be a string, got " f"{type(data[key]).__name__}"
                )
        capabilities_raw = data.get("capabilities", [])
        if not isinstance(capabilities_raw, list):
            raise ProtocolError(
                f"Hello.capabilities must be a list, got "
                f"{type(capabilities_raw).__name__}"
            )
        capabilities = tuple(str(c) for c in capabilities_raw)
        return cls(
            protocol_version=data["protocol_version"],
            framework_version=data["framework_version"],
            client_name=data["client_name"],
            token=data["token"],
            capabilities=capabilities,
        )


@dataclass(frozen=True)
class WelcomePayload:
    """Host → client handshake-success payload.

    Sent in response to a valid Hello.

    Attributes:
        protocol_version: Lab wire protocol version (host's choice within
            the intersection of host and client supported sets).
        framework_version: Host's KT version, diagnostics only.
        host_node_id: Host's NodeId (typically :data:`HOST_NODE_ID`).
        assigned_client_id: NodeId the host has assigned to this client.
            May equal the client's requested name, or may be uniquified
            on name conflicts.
        supported_verbs: L4 verbs this host supports. 1.5.0 ships
            ``["send", "broadcast"]``; later releases add ``"replicate"``.
        cluster_info: Free-form extensible metadata (other nodes,
            capabilities, etc.).
    """

    protocol_version: str
    framework_version: str
    host_node_id: str
    assigned_client_id: str
    supported_verbs: tuple[str, ...]
    cluster_info: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "framework_version": self.framework_version,
            "host_node_id": self.host_node_id,
            "assigned_client_id": self.assigned_client_id,
            "supported_verbs": list(self.supported_verbs),
            "cluster_info": dict(self.cluster_info),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WelcomePayload":
        required = (
            "protocol_version",
            "framework_version",
            "host_node_id",
            "assigned_client_id",
        )
        missing = [k for k in required if k not in data]
        if missing:
            raise ProtocolError(f"Welcome missing required fields: {missing}")
        for key in required:
            if not isinstance(data[key], str):
                raise ProtocolError(
                    f"Welcome.{key} must be a string, got "
                    f"{type(data[key]).__name__}"
                )
        verbs_raw = data.get("supported_verbs", [])
        if not isinstance(verbs_raw, list):
            raise ProtocolError(
                f"Welcome.supported_verbs must be a list, got "
                f"{type(verbs_raw).__name__}"
            )
        info_raw = data.get("cluster_info", {})
        if not isinstance(info_raw, dict):
            raise ProtocolError(
                f"Welcome.cluster_info must be a dict, got "
                f"{type(info_raw).__name__}"
            )
        return cls(
            protocol_version=data["protocol_version"],
            framework_version=data["framework_version"],
            host_node_id=data["host_node_id"],
            assigned_client_id=data["assigned_client_id"],
            supported_verbs=tuple(str(v) for v in verbs_raw),
            cluster_info=dict(info_raw),
        )


@dataclass(frozen=True)
class RejectPayload:
    """Host → client handshake-failure payload.

    Sent in response to an invalid Hello (auth failure, protocol mismatch,
    name conflict, …). The connection should be closed after sending.

    Attributes:
        reason: Short, machine-readable category. Conventional values:
            ``"protocol_mismatch"``, ``"auth_failed"``, ``"name_conflict"``,
            ``"server_error"``.
        detail: Human-readable details for logging.
    """

    reason: str
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RejectPayload":
        if "reason" not in data:
            raise ProtocolError("Reject missing required field: reason")
        if not isinstance(data["reason"], str):
            raise ProtocolError(
                f"Reject.reason must be a string, got "
                f"{type(data['reason']).__name__}"
            )
        detail = data.get("detail", "")
        if not isinstance(detail, str):
            raise ProtocolError(
                f"Reject.detail must be a string, got " f"{type(detail).__name__}"
            )
        return cls(reason=data["reason"], detail=detail)


# ----------------------------------------------------------------------
# Envelope-level helpers
# ----------------------------------------------------------------------


def build_hello(
    payload: HelloPayload,
    *,
    stream_id: int = 0,
    seq: int = 0,
) -> Envelope:
    """Build a HELLO envelope wrapping a :class:`HelloPayload`.

    ``from_node`` is the client's pre-assignment identifier (its
    configured ``client_name``); ``to_node`` is :data:`HOST_NODE_ID`.
    """
    return Envelope(
        from_node=payload.client_name,
        to_node=HOST_NODE_ID,
        kind=EnvelopeKind.HELLO,
        stream_id=stream_id,
        seq=seq,
        payload=_PACKER.pack(payload.to_dict()),
    )


def parse_hello(env: Envelope) -> HelloPayload:
    """Extract a :class:`HelloPayload` from a HELLO envelope.

    Raises :class:`ProtocolError` if the envelope is not a HELLO or if
    its payload is not a valid Hello body.
    """
    if env.kind is not EnvelopeKind.HELLO:
        raise ProtocolError(f"expected HELLO envelope, got {env.kind.value}")
    return _parse_payload(env, HelloPayload)


def build_welcome(
    payload: WelcomePayload,
    *,
    to_node: str,
    stream_id: int = 0,
    seq: int = 0,
) -> Envelope:
    """Build a WELCOME envelope wrapping a :class:`WelcomePayload`."""
    return Envelope(
        from_node=payload.host_node_id,
        to_node=to_node,
        kind=EnvelopeKind.WELCOME,
        stream_id=stream_id,
        seq=seq,
        payload=_PACKER.pack(payload.to_dict()),
    )


def parse_welcome(env: Envelope) -> WelcomePayload:
    """Extract a :class:`WelcomePayload` from a WELCOME envelope."""
    if env.kind is not EnvelopeKind.WELCOME:
        raise ProtocolError(f"expected WELCOME envelope, got {env.kind.value}")
    return _parse_payload(env, WelcomePayload)


def build_reject(
    payload: RejectPayload,
    *,
    to_node: str,
    stream_id: int = 0,
    seq: int = 0,
) -> Envelope:
    """Build a CONTROL envelope carrying a Reject payload.

    Rejects ride on CONTROL (rather than WELCOME) so an observer can
    distinguish handshake success vs failure at envelope kind alone.
    """
    body = {"control": "reject", **payload.to_dict()}
    return Envelope(
        from_node=HOST_NODE_ID,
        to_node=to_node,
        kind=EnvelopeKind.CONTROL,
        stream_id=stream_id,
        seq=seq,
        payload=_PACKER.pack(body),
    )


def parse_reject(env: Envelope) -> RejectPayload:
    """Extract a :class:`RejectPayload` from a CONTROL envelope.

    The envelope must carry a body with ``control == "reject"`` to be
    treated as a reject; otherwise raises :class:`ProtocolError`.
    """
    if env.kind is not EnvelopeKind.CONTROL:
        raise ProtocolError(f"expected CONTROL envelope, got {env.kind.value}")
    body = _decode_payload(env)
    if body.get("control") != "reject":
        raise ProtocolError(
            f"CONTROL envelope is not a reject " f"(control={body.get('control')!r})"
        )
    payload = {k: v for k, v in body.items() if k != "control"}
    return RejectPayload.from_dict(payload)


def _decode_payload(env: Envelope) -> dict[str, Any]:
    """Decode envelope.payload as msgpack; raises ProtocolError on bad input."""
    try:
        body = _PACKER.unpack(env.payload, 0)
    except (ValueError, TypeError) as exc:
        raise ProtocolError(f"envelope payload is not valid msgpack: {exc}") from exc
    if not isinstance(body, dict):
        raise ProtocolError(
            f"envelope payload must be a msgpack map, " f"got {type(body).__name__}"
        )
    return body


def _parse_payload(env: Envelope, cls):
    """Decode envelope payload and route to the dataclass's from_dict."""
    return cls.from_dict(_decode_payload(env))


__all__ = [
    "HOST_NODE_ID",
    "HelloPayload",
    "LAB_PROTOCOL_VERSION",
    "ProtocolError",
    "RejectPayload",
    "SUPPORTED_PROTOCOL_VERSIONS",
    "WelcomePayload",
    "build_hello",
    "build_reject",
    "build_welcome",
    "parse_hello",
    "parse_reject",
    "parse_welcome",
    "protocol_compatible",
]
