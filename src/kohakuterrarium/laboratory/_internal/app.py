"""APP envelope helpers and the application extension dispatch model.

Build and parse namespaced application messages carried by ``APP`` envelopes.

Payloads are msgpack maps containing ``namespace``, ``type``, and ``body``.
Optional ``request_id`` and ``in_reply_to`` envelope fields correlate requests
and responses. Routing dispatches each message to the extension registered for
its namespace.
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from kohakuvault import DataPacker

from kohakuterrarium.laboratory._internal.envelope import (
    Envelope,
    EnvelopeKind,
)

_PACKER = DataPacker("msgpack")


class AppMessageError(ValueError):
    """Raised when an APP envelope payload cannot be parsed."""


class ExtensionNotFoundError(LookupError):
    """Raised when an APP envelope arrives for an unregistered namespace."""


@dataclass(frozen=True)
class AppMessage:
    """Represent a decoded application message for an extension handler."""

    namespace: str
    type: str
    body: Any
    sender_node: str
    request_id: str | None
    in_reply_to: str | None


ExtensionHandler = Callable[[AppMessage], Awaitable[Any]]
"""Handle an APP message and optionally return a serializable response.

``None`` suppresses a response even when the message has a request ID.
"""


def build_app_envelope(
    *,
    from_node: str,
    to_node: str,
    namespace: str,
    type: str,
    body: Any,
    stream_id: int = 0,
    seq: int = 0,
    request_id: str | None = None,
    in_reply_to: str | None = None,
) -> Envelope:
    """Build an ``APP`` envelope wrapping a structured message body."""
    payload_dict = {
        "namespace": namespace,
        "type": type,
        "body": body,
    }
    return Envelope(
        from_node=from_node,
        to_node=to_node,
        kind=EnvelopeKind.APP,
        stream_id=stream_id,
        seq=seq,
        payload=_PACKER.pack(payload_dict),
        request_id=request_id,
        in_reply_to=in_reply_to,
    )


def parse_app_envelope(env: Envelope) -> AppMessage:
    """Decode an ``APP`` envelope into an :class:`AppMessage`.

    Raises :class:`AppMessageError` on wrong kind or malformed payload.
    """
    if env.kind is not EnvelopeKind.APP:
        raise AppMessageError(f"expected APP envelope, got {env.kind.value}")
    try:
        body = _PACKER.unpack(env.payload, 0)
    except (ValueError, TypeError) as exc:
        raise AppMessageError(f"APP payload is not valid msgpack: {exc}") from exc
    if not isinstance(body, dict):
        raise AppMessageError(
            f"APP payload must be a msgpack map, got {type(body).__name__}"
        )
    for required_key in ("namespace", "type"):
        if required_key not in body:
            raise AppMessageError(f"APP payload missing required key: {required_key}")
        if not isinstance(body[required_key], str):
            raise AppMessageError(f"APP payload {required_key!r} must be a string")
    return AppMessage(
        namespace=body["namespace"],
        type=body["type"],
        body=body.get("body"),
        sender_node=env.from_node,
        request_id=env.request_id,
        in_reply_to=env.in_reply_to,
    )


def new_request_id() -> str:
    """Generate a fresh request correlation id."""
    return uuid.uuid4().hex


__all__ = [
    "AppMessage",
    "AppMessageError",
    "ExtensionHandler",
    "ExtensionNotFoundError",
    "build_app_envelope",
    "new_request_id",
    "parse_app_envelope",
]
