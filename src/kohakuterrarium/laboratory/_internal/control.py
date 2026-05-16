"""CONTROL envelope payload helpers for the Laboratory layer.

Control envelopes carry framework-internal messages that don't fit the
L4 verb model — client capability changes, name binding updates, reject
notifications, etc. Each control payload is a msgpack-encoded dict with
a discriminator key (``"control"``).

Standard control types defined here:

- ``subscribe`` / ``unsubscribe`` — a client (un)registers as a
  listener on a channel.
- ``register_creature`` / ``unregister_creature`` — a client claims (or
  releases) a creature ref so other nodes can address it.

Reject control (``"control": "reject"``) is defined in
:mod:`kohakuterrarium.laboratory._internal.protocol`.
"""

from dataclasses import dataclass
from typing import Any

from kohakuvault import DataPacker

from kohakuterrarium.laboratory._internal.envelope import (
    Envelope,
    EnvelopeKind,
)

_PACKER = DataPacker("msgpack")


class ControlError(ValueError):
    """Raised when a CONTROL envelope's payload can't be parsed."""


@dataclass(frozen=True)
class SubscribePayload:
    """A client asks the host to register it as a listener for a channel."""

    channel: str


@dataclass(frozen=True)
class UnsubscribePayload:
    """A client withdraws a previous subscription."""

    channel: str


@dataclass(frozen=True)
class RegisterCreaturePayload:
    """A client claims responsibility for a creature ref."""

    ref: str


@dataclass(frozen=True)
class UnregisterCreaturePayload:
    """A client releases a creature ref it previously claimed."""

    ref: str


def build_subscribe(
    *, from_node: str, to_node: str, channel: str, stream_id: int = 0, seq: int = 0
) -> Envelope:
    """Build a CONTROL envelope carrying a subscribe directive."""
    return _build_control(
        from_node=from_node,
        to_node=to_node,
        stream_id=stream_id,
        seq=seq,
        body={"control": "subscribe", "channel": channel},
    )


def build_unsubscribe(
    *, from_node: str, to_node: str, channel: str, stream_id: int = 0, seq: int = 0
) -> Envelope:
    return _build_control(
        from_node=from_node,
        to_node=to_node,
        stream_id=stream_id,
        seq=seq,
        body={"control": "unsubscribe", "channel": channel},
    )


def build_register_creature(
    *, from_node: str, to_node: str, ref: str, stream_id: int = 0, seq: int = 0
) -> Envelope:
    return _build_control(
        from_node=from_node,
        to_node=to_node,
        stream_id=stream_id,
        seq=seq,
        body={"control": "register_creature", "ref": ref},
    )


def build_unregister_creature(
    *, from_node: str, to_node: str, ref: str, stream_id: int = 0, seq: int = 0
) -> Envelope:
    return _build_control(
        from_node=from_node,
        to_node=to_node,
        stream_id=stream_id,
        seq=seq,
        body={"control": "unregister_creature", "ref": ref},
    )


def parse_control(env: Envelope) -> tuple[str, dict[str, Any]]:
    """Decode the control body of an envelope.

    Returns ``(control_type, fields)``. ``control_type`` is the value of
    the ``"control"`` key (e.g. ``"subscribe"``); ``fields`` is the
    remaining payload keys.

    Raises :class:`ControlError` if the envelope is not CONTROL or the
    body is malformed.
    """
    if env.kind is not EnvelopeKind.CONTROL:
        raise ControlError(f"expected CONTROL envelope, got {env.kind.value}")
    try:
        body = _PACKER.unpack(env.payload, 0)
    except (ValueError, TypeError) as exc:
        raise ControlError(f"control payload is not valid msgpack: {exc}") from exc
    if not isinstance(body, dict):
        raise ControlError(f"control payload must be a map, got {type(body).__name__}")
    control_type = body.get("control")
    if not isinstance(control_type, str):
        raise ControlError("control payload missing 'control' string key")
    fields = {k: v for k, v in body.items() if k != "control"}
    return control_type, fields


def _build_control(
    *,
    from_node: str,
    to_node: str,
    stream_id: int,
    seq: int,
    body: dict[str, Any],
) -> Envelope:
    return Envelope(
        from_node=from_node,
        to_node=to_node,
        kind=EnvelopeKind.CONTROL,
        stream_id=stream_id,
        seq=seq,
        payload=_PACKER.pack(body),
    )


__all__ = [
    "ControlError",
    "RegisterCreaturePayload",
    "SubscribePayload",
    "UnregisterCreaturePayload",
    "UnsubscribePayload",
    "build_register_creature",
    "build_subscribe",
    "build_unregister_creature",
    "build_unsubscribe",
    "parse_control",
]
