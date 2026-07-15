"""Pack / unpack helpers for terrarium DTOs sent over Laboratory APP.

Every method on :class:`TerrariumService` that crosses the wire
(``RemoteTerrariumService`` → APP request → adapter → engine) needs
its arguments and return values translated between Python dataclass
form and msgpack-compatible primitive form.

The Laboratory APP layer encodes msgpack-compatible primitives only
(:mod:`kohakuterrarium.laboratory._internal.app`).  ``kohakuvault.DataPacker("msgpack")``
rejects raw ``bytes`` — payloads that need binary data (file bundles,
``read``/``write``, etc.) base64-encode it into a string field on the
wire and decode at the receiver.  This module converts Python
dataclasses ↔ msgpack-compatible bags.

Rules followed throughout:

- ``tuple`` → ``list`` on the wire; re-tupled on receive where the
  Python API exposes ``tuple`` (e.g. :class:`CreatureInfo.listen_channels`).
- ``set`` → sorted ``list`` on the wire (msgpack has no set type).
- ``Path`` → ``str``.
- ``Enum`` → its ``.value`` (string).
- ``None`` stays ``None``.

Pack functions take a dataclass and return a primitive dict; unpack
take a primitive dict and return the dataclass.  Unpack functions are
strict — missing keys raise ``KeyError`` and unknown keys are ignored
(forward-compatible with field additions).

Path-form ``add_creature`` accepts only absolute worker-side paths; callers must
first deploy local files and then pass the resulting remote path.
"""

from dataclasses import is_dataclass
from pathlib import Path
from typing import Any

from kohakuterrarium.core.config_serde import (
    pack_agent_config,
    stringify_paths as _stringify_paths,
    unpack_agent_config,
)
from kohakuterrarium.core.config_types import AgentConfig
from kohakuterrarium.terrarium.drive.errors import (
    CrossNodeDriveNotSupportedError,
    DriveBackpressureError,
    DriveConflictError,
    DriveDeliveryError,
    DriveError,
    DriveIdempotencyConflictError,
    DriveNotFoundError,
    DrivePermissionError,
    DrivePersistenceRequiredError,
    DriveReconfigurationRequiredError,
    DriveRegistrationDisabledError,
    DriveRegistrationIncompatibleError,
    DriveRegistrationNotFoundError,
    DriveSettingsConflictError,
    DriveTransitionError,
    DriveValidationError,
)
from kohakuterrarium.terrarium.events import (
    ConnectionResult,
    DisconnectionResult,
    EngineEvent,
    EventFilter,
    EventKind,
)
from kohakuterrarium.terrarium.service import CreatureInfo
from kohakuterrarium.terrarium.topology import (
    ChannelInfo,
    GraphTopology,
    TopologyDelta,
)


class RemoteAddCreatureError(ValueError):
    """Raised when ``add_creature`` is called with an unsupported config form.

    In-memory :class:`AgentConfig` values can cross the wire directly.
    String and ``Path`` configs must first use the ``studio.deploy`` pipeline
    so the worker receives a path on its own filesystem.
    """


# ---------------------------------------------------------------------------
# CreatureInfo
# ---------------------------------------------------------------------------


def pack_creature_info(c: CreatureInfo) -> dict[str, Any]:
    return {
        "creature_id": c.creature_id,
        "name": c.name,
        "graph_id": c.graph_id,
        "is_running": c.is_running,
        "is_privileged": c.is_privileged,
        "parent_creature_id": c.parent_creature_id,
        "listen_channels": list(c.listen_channels),
        "send_channels": list(c.send_channels),
        "model": c.model,
        "llm_name": c.llm_name,
    }


def unpack_creature_info(d: dict[str, Any]) -> CreatureInfo:
    return CreatureInfo(
        creature_id=d["creature_id"],
        name=d["name"],
        graph_id=d["graph_id"],
        is_running=d["is_running"],
        is_privileged=d["is_privileged"],
        parent_creature_id=d.get("parent_creature_id"),
        listen_channels=tuple(d.get("listen_channels", ())),
        send_channels=tuple(d.get("send_channels", ())),
        # Older workers may omit ``model``; default to an empty string so
        # those payloads still decode cleanly and the UI shows "no model".
        model=str(d.get("model", "") or ""),
        # Older workers may also omit ``llm_name``; use the same
        # forward-compatibility treatment as ``model``.
        llm_name=str(d.get("llm_name", "") or ""),
    )


# ---------------------------------------------------------------------------
# ChannelInfo
# ---------------------------------------------------------------------------


def pack_channel_info(c: ChannelInfo) -> dict[str, Any]:
    return {"name": c.name, "description": c.description}


def unpack_channel_info(d: dict[str, Any]) -> ChannelInfo:
    return ChannelInfo(name=d["name"], description=d.get("description", ""))


# ---------------------------------------------------------------------------
# GraphTopology
# ---------------------------------------------------------------------------


def pack_graph_topology(g: GraphTopology) -> dict[str, Any]:
    return {
        "graph_id": g.graph_id,
        "creature_ids": sorted(g.creature_ids),
        "channels": {
            name: pack_channel_info(info) for name, info in g.channels.items()
        },
        "listen_edges": {cid: sorted(chans) for cid, chans in g.listen_edges.items()},
        "send_edges": {cid: sorted(chans) for cid, chans in g.send_edges.items()},
    }


def unpack_graph_topology(d: dict[str, Any]) -> GraphTopology:
    return GraphTopology(
        graph_id=d["graph_id"],
        creature_ids=set(d.get("creature_ids", [])),
        channels={
            name: unpack_channel_info(c) for name, c in d.get("channels", {}).items()
        },
        listen_edges={
            cid: set(chans) for cid, chans in d.get("listen_edges", {}).items()
        },
        send_edges={cid: set(chans) for cid, chans in d.get("send_edges", {}).items()},
    )


# ---------------------------------------------------------------------------
# TopologyDelta
# ---------------------------------------------------------------------------


def pack_topology_delta(t: TopologyDelta) -> dict[str, Any]:
    return {
        "kind": t.kind,
        "old_graph_ids": list(t.old_graph_ids),
        "new_graph_ids": list(t.new_graph_ids),
        "affected_creatures": sorted(t.affected_creatures),
    }


def unpack_topology_delta(d: dict[str, Any]) -> TopologyDelta:
    return TopologyDelta(
        kind=d["kind"],
        old_graph_ids=list(d.get("old_graph_ids", [])),
        new_graph_ids=list(d.get("new_graph_ids", [])),
        affected_creatures=set(d.get("affected_creatures", [])),
    )


# ---------------------------------------------------------------------------
# ConnectionResult / DisconnectionResult
# ---------------------------------------------------------------------------


def pack_connection_result(r: ConnectionResult) -> dict[str, Any]:
    return {
        "channel": r.channel,
        "trigger_id": r.trigger_id,
        "delta_kind": r.delta_kind,
        "graph_id": r.graph_id,
    }


def unpack_connection_result(d: dict[str, Any]) -> ConnectionResult:
    return ConnectionResult(
        channel=d["channel"],
        trigger_id=d.get("trigger_id", ""),
        delta_kind=d.get("delta_kind", "nothing"),
        graph_id=d.get("graph_id", ""),
    )


def pack_disconnection_result(r: DisconnectionResult) -> dict[str, Any]:
    return {"channels": list(r.channels), "delta_kind": r.delta_kind}


def unpack_disconnection_result(d: dict[str, Any]) -> DisconnectionResult:
    return DisconnectionResult(
        channels=list(d.get("channels", [])),
        delta_kind=d.get("delta_kind", "nothing"),
    )


# ---------------------------------------------------------------------------
# EngineEvent / EventFilter
# ---------------------------------------------------------------------------


def pack_engine_event(e: EngineEvent) -> dict[str, Any]:
    return {
        "kind": e.kind.value,
        "creature_id": e.creature_id,
        "graph_id": e.graph_id,
        "channel": e.channel,
        "payload": e.payload,
        "ts": e.ts,
    }


def unpack_engine_event(d: dict[str, Any]) -> EngineEvent:
    return EngineEvent(
        kind=EventKind(d["kind"]),
        creature_id=d.get("creature_id"),
        graph_id=d.get("graph_id"),
        channel=d.get("channel"),
        payload=dict(d.get("payload", {})),
        ts=d.get("ts", 0.0),
    )


def pack_event_filter(f: EventFilter | None) -> dict[str, Any] | None:
    if f is None:
        return None
    return {
        "kinds": sorted(k.value for k in f.kinds) if f.kinds is not None else None,
        "creature_ids": sorted(f.creature_ids) if f.creature_ids is not None else None,
        "graph_ids": sorted(f.graph_ids) if f.graph_ids is not None else None,
        "channels": sorted(f.channels) if f.channels is not None else None,
    }


def unpack_event_filter(d: dict[str, Any] | None) -> EventFilter | None:
    if d is None:
        return None
    kinds = d.get("kinds")
    creature_ids = d.get("creature_ids")
    graph_ids = d.get("graph_ids")
    channels = d.get("channels")
    return EventFilter(
        kinds={EventKind(v) for v in kinds} if kinds is not None else None,
        creature_ids=set(creature_ids) if creature_ids is not None else None,
        graph_ids=set(graph_ids) if graph_ids is not None else None,
        channels=set(channels) if channels is not None else None,
    )


# ---------------------------------------------------------------------------
# AgentConfig pack/unpack lives in :mod:`kohakuterrarium.core.config_serde`
# so the resume path can import it without dragging in this module's
# engine-coupled dependencies.  Re-exported above for back-compat.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# CreatureBuildInput dispatcher (the ``config`` arg to add_creature)
# ---------------------------------------------------------------------------


def pack_creature_build_input(config: Any) -> dict[str, Any]:
    """Pack the ``config`` argument of remote ``add_creature``.

    Accepts :class:`AgentConfig` (sent as a dataclass dict) or
    ``str``/:class:`Path` — the path must exist on the worker node.
    Deploy bytes first via
    :func:`kohakuterrarium.studio.deploy.deploy_creature_to_node`.

    :class:`CreatureConfig` is rejected because its ``base_dir`` is
    tied to the controller's filesystem.
    """
    if isinstance(config, AgentConfig):
        return {"kind": "agent_config", "value": pack_agent_config(config)}
    if isinstance(config, (str, Path)):
        path_str = str(config)
        # The path is resolved on the worker's filesystem; sending a
        # relative path silently resolves against the worker's CWD,
        # which is unknowable from the controller and almost certainly
        # not what the caller meant.  Cross-OS-safe absoluteness check:
        # leading slash (POSIX or pre-normalised Windows), backslash
        # (Windows), or a drive prefix like ``X:``.
        if not (
            path_str.startswith(("/", "\\"))
            or (len(path_str) >= 2 and path_str[1] == ":")
        ):
            raise RemoteAddCreatureError(
                f"path-form add_creature requires an absolute remote path; "
                f"got relative {path_str!r}.  Use studio.deploy first and pass "
                f"the returned target_path."
            )
        return {"kind": "path", "value": path_str}
    if is_dataclass(config) and type(config).__name__ == "CreatureConfig":
        raise RemoteAddCreatureError(
            "CreatureConfig contains a base_dir Path tied to the controller's "
            "filesystem; not supported in remote mode"
        )
    raise RemoteAddCreatureError(
        f"unsupported config type for remote add_creature: {type(config).__name__}"
    )


def unpack_creature_build_input(d: dict[str, Any]) -> Any:
    kind = d.get("kind")
    if kind == "agent_config":
        return unpack_agent_config(d["value"])
    if kind == "path":
        return d["value"]
    raise RemoteAddCreatureError(f"unknown packed creature build input kind: {kind!r}")


# ---------------------------------------------------------------------------
# Message content (for chat / inject_input)
# ---------------------------------------------------------------------------


def pack_content(message: str | list[dict[str, Any]]) -> str | list[dict[str, Any]]:
    """Validate a chat/inject_input message body for wire transit.

    Strings pass through; list-of-dict (multimodal) is validated to
    contain only msgpack-friendly primitives.
    """
    if isinstance(message, str):
        return message
    if isinstance(message, list):
        out: list[dict[str, Any]] = []
        for part in message:
            if not isinstance(part, dict):
                raise TypeError(f"content part must be dict, got {type(part).__name__}")
            out.append(_stringify_paths(part))
        return out
    raise TypeError(f"unsupported content type: {type(message).__name__}")


def unpack_content(value: str | list[dict[str, Any]]) -> str | list[dict[str, Any]]:
    # Symmetric — wire form matches Python form.  Provided for callsite
    # symmetry with the other pack/unpack pairs.
    return value


# ---------------------------------------------------------------------------
# Drive typed-error mapping across the Lab wire
# ---------------------------------------------------------------------------
#
# A worker's Drive op can fail with a *specific* typed error
# (:class:`DriveConflictError`, :class:`DrivePermissionError`, …). The generic
# adapter envelope only distinguishes ``not_found`` / ``invalid`` / ``engine``,
# which would collapse every Drive failure to ``KeyError`` / ``ValueError`` /
# ``RemoteEngineError`` and lose the subtype. These helpers pack a Drive error
# into a kind-tagged body on the worker and reconstruct the same subtype on the
# controller so ``except DriveConflictError`` keeps working across a node hop.

# Most-specific first: the first ``isinstance`` match wins, so a
# ``DriveIdempotencyConflictError`` is not shadowed by its ``DriveConflictError``
# sibling / the ``DriveError`` base.
_DRIVE_ERROR_ORDER: tuple[tuple[type[DriveError], str], ...] = (
    (DriveIdempotencyConflictError, "drive_idempotency_conflict"),
    (DriveSettingsConflictError, "drive_settings_conflict"),
    (DriveConflictError, "drive_conflict"),
    (DriveTransitionError, "drive_transition"),
    (DriveRegistrationNotFoundError, "drive_registration_not_found"),
    (DriveRegistrationDisabledError, "drive_registration_disabled"),
    (DriveRegistrationIncompatibleError, "drive_registration_incompatible"),
    (DriveReconfigurationRequiredError, "drive_reconfiguration_required"),
    (DrivePersistenceRequiredError, "drive_persistence_required"),
    (DriveBackpressureError, "drive_backpressure"),
    (DriveDeliveryError, "drive_delivery"),
    (DriveNotFoundError, "drive_not_found"),
    (DriveValidationError, "drive_validation"),
    (DrivePermissionError, "drive_permission"),
    (CrossNodeDriveNotSupportedError, "cross_node_drive_not_supported"),
    (DriveError, "drive"),
)

_DRIVE_ERROR_BY_KIND: dict[str, type[DriveError]] = {
    kind: cls for cls, kind in _DRIVE_ERROR_ORDER
}


def is_drive_error_kind(kind: object) -> bool:
    """Whether ``kind`` names a Drive typed error carried over the wire."""
    return isinstance(kind, str) and kind in _DRIVE_ERROR_BY_KIND


def pack_drive_error(exc: DriveError) -> dict[str, Any]:
    """Serialize a Drive error to a kind-tagged envelope inner body.

    The worker adapter wraps the result as ``{"error": <this>}``; the extra
    typed attributes (revisions, from/to status, idempotency key) travel under
    ``details`` so the controller can reconstruct the exact subtype.
    """
    kind = "drive"
    for cls, tag in _DRIVE_ERROR_ORDER:
        if isinstance(exc, cls):
            kind = tag
            break
    details: dict[str, Any] = {}
    for attr in (
        "expected_revision",
        "actual_revision",
        "from_status",
        "to_status",
        "idempotency_key",
    ):
        value = getattr(exc, attr, None)
        if value is not None:
            details[attr] = value
    return {"kind": kind, "message": str(exc), "details": details}


def drive_error_from_body(body: object) -> DriveError | None:
    """Reconstruct a Drive typed error from an ``{"error": {...}}`` envelope.

    Returns ``None`` when ``body`` is not a Drive error envelope, so callers can
    fall through to the generic ``not_found`` / ``invalid`` mapping.
    """
    if not isinstance(body, dict):
        return None
    err = body.get("error")
    if not isinstance(err, dict):
        return None
    kind = err.get("kind")
    cls = _DRIVE_ERROR_BY_KIND.get(kind) if isinstance(kind, str) else None
    if cls is None:
        return None
    message = str(err.get("message", ""))
    details = err.get("details") if isinstance(err.get("details"), dict) else {}
    if cls in (DriveConflictError, DriveSettingsConflictError):
        return cls(
            message,
            expected_revision=details.get("expected_revision"),
            actual_revision=details.get("actual_revision"),
        )
    if cls is DriveIdempotencyConflictError:
        return cls(message, idempotency_key=details.get("idempotency_key"))
    if cls is DriveTransitionError:
        return cls(
            message,
            from_status=details.get("from_status"),
            to_status=details.get("to_status"),
        )
    return cls(message)


__all__ = [
    "RemoteAddCreatureError",
    "drive_error_from_body",
    "is_drive_error_kind",
    "pack_drive_error",
    "pack_agent_config",
    "pack_channel_info",
    "pack_connection_result",
    "pack_content",
    "pack_creature_build_input",
    "pack_creature_info",
    "pack_disconnection_result",
    "pack_engine_event",
    "pack_event_filter",
    "pack_graph_topology",
    "pack_topology_delta",
    "unpack_agent_config",
    "unpack_channel_info",
    "unpack_connection_result",
    "unpack_content",
    "unpack_creature_build_input",
    "unpack_creature_info",
    "unpack_disconnection_result",
    "unpack_engine_event",
    "unpack_event_filter",
    "unpack_graph_topology",
    "unpack_topology_delta",
]
