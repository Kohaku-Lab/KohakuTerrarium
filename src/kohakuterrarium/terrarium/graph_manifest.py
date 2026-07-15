"""Versioned reconstruction manifests for live Terrarium graphs."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from kohakuterrarium.core.config import AgentConfig
from kohakuterrarium.core.config_serde import pack_agent_config, unpack_agent_config
from kohakuterrarium.errors import (
    GraphManifestError,
    GraphManifestPersistenceError,
    GraphManifestVersionError,
    SessionNotResumableError,
)

if TYPE_CHECKING:
    from kohakuterrarium.session.store import SessionStore
    from kohakuterrarium.terrarium.engine import Terrarium

MANIFEST_KEY = "live_graph_manifest"
MANIFEST_KIND = "kohakuterrarium.live_graph"
MANIFEST_VERSION = 1


@dataclass(frozen=True)
class ManifestCreature:
    """Declarative reconstruction data for one graph member."""

    creature_id: str
    name: str
    config_snapshot: dict[str, Any]
    source_ref: str | None
    pwd: str
    is_privileged: bool
    parent_creature_id: str | None

    def unpack_config(self) -> AgentConfig:
        """Recreate the resolved agent config stored in this record."""
        return unpack_agent_config(self.config_snapshot)


@dataclass(frozen=True)
class ManifestChannel:
    """A channel declaration, including channels with no edges."""

    name: str
    description: str = ""


@dataclass(frozen=True)
class GraphManifest:
    """Canonical complete reconstruction document for one live graph."""

    graph_id: str
    creatures: tuple[ManifestCreature, ...]
    channels: tuple[ManifestChannel, ...]
    listen: tuple[tuple[str, str], ...]
    send: tuple[tuple[str, str], ...]
    revision: int = 0
    kind: str = MANIFEST_KIND
    version: int = MANIFEST_VERSION


CreatureManifest = ManifestCreature
ChannelManifest = ManifestChannel


def _error(message: str, field: str) -> GraphManifestError:
    return GraphManifestError(
        f"Invalid live-graph manifest {field}: {message}", field=field
    )


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _error(f"expected a mapping, got {type(value).__name__}", field)
    return value


def _required(data: Mapping[str, Any], field: str) -> Any:
    if field not in data:
        raise _error("field is required", field)
    return data[field]


def _string(value: object, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        qualifier = "a string" if allow_empty else "a non-empty string"
        raise _error(f"expected {qualifier}", field)
    return value


def _optional_string(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _string(value, field)


def _parse_creature(value: object, index: int) -> ManifestCreature:
    prefix = f"creatures[{index}]"
    data = _mapping(value, prefix)
    snapshot = _mapping(_required(data, "config_snapshot"), f"{prefix}.config_snapshot")
    packed = dict(snapshot)
    try:
        unpack_agent_config(packed)
    except Exception as exc:
        raise _error(str(exc), f"{prefix}.config_snapshot") from exc

    privileged = _required(data, "is_privileged")
    if not isinstance(privileged, bool):
        raise _error("expected a boolean", f"{prefix}.is_privileged")

    return ManifestCreature(
        creature_id=_string(_required(data, "creature_id"), f"{prefix}.creature_id"),
        name=_string(_required(data, "name"), f"{prefix}.name"),
        config_snapshot=packed,
        source_ref=_optional_string(data.get("source_ref"), f"{prefix}.source_ref"),
        pwd=_string(_required(data, "pwd"), f"{prefix}.pwd"),
        is_privileged=privileged,
        parent_creature_id=_optional_string(
            data.get("parent_creature_id"), f"{prefix}.parent_creature_id"
        ),
    )


def _parse_channel(value: object, index: int) -> ManifestChannel:
    prefix = f"channels[{index}]"
    data = _mapping(value, prefix)
    return ManifestChannel(
        name=_string(_required(data, "name"), f"{prefix}.name"),
        description=_string(
            data.get("description", ""), f"{prefix}.description", allow_empty=True
        ),
    )


def _parse_edge(value: object, index: int, field: str) -> tuple[str, str]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise _error("expected [creature_id, channel_name]", f"{field}[{index}]")
    return (
        _string(value[0], f"{field}[{index}][0]"),
        _string(value[1], f"{field}[{index}][1]"),
    )


def _parse_sequence(data: Mapping[str, Any], field: str) -> list[Any]:
    value = _required(data, field)
    if not isinstance(value, (list, tuple)):
        raise _error("expected an array", field)
    return list(value)


def _ensure_unique(values: list[str], field: str) -> None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise _error(f"duplicate value {value!r}", field)
        seen.add(value)


def _validate_parents(creatures: tuple[ManifestCreature, ...]) -> None:
    parent_by_id = {c.creature_id: c.parent_creature_id for c in creatures}
    for creature_id, parent_id in parent_by_id.items():
        if parent_id is None:
            continue
        if parent_id == creature_id:
            raise _error("a creature cannot be its own parent", "parent_creature_id")
        if parent_id not in parent_by_id:
            raise _error(
                f"creature {creature_id!r} references missing parent {parent_id!r}",
                "parent_creature_id",
            )

    for start_id in parent_by_id:
        visited: set[str] = set()
        current: str | None = start_id
        while current is not None:
            if current in visited:
                raise _error(
                    f"parent cycle includes creature {current!r}",
                    "parent_creature_id",
                )
            visited.add(current)
            current = parent_by_id[current]


def _validate_edges(
    edges: tuple[tuple[str, str], ...],
    field: str,
    creature_ids: set[str],
    channel_names: set[str],
) -> None:
    _ensure_unique(
        [f"{creature_id}\0{channel}" for creature_id, channel in edges], field
    )
    for creature_id, channel in edges:
        if creature_id not in creature_ids:
            raise _error(f"unknown creature {creature_id!r}", field)
        if channel not in channel_names:
            raise _error(f"unknown channel {channel!r}", field)


def parse_manifest(raw: object) -> GraphManifest:
    """Validate raw metadata and return a canonically ordered manifest."""
    data = _mapping(raw, "root")
    kind = _required(data, "kind")
    if kind != MANIFEST_KIND:
        raise _error(f"unsupported kind {kind!r}", "kind")

    version = _required(data, "version")
    if type(version) is not int or version != MANIFEST_VERSION:
        raise GraphManifestVersionError(version)

    revision = _required(data, "revision")
    if type(revision) is not int or revision < 0:
        raise _error("expected a non-negative integer", "revision")

    creature_values = _parse_sequence(data, "creatures")
    if not creature_values:
        raise _error("at least one creature is required", "creatures")
    creatures = tuple(
        sorted(
            (
                _parse_creature(value, index)
                for index, value in enumerate(creature_values)
            ),
            key=lambda creature: creature.creature_id,
        )
    )
    channels = tuple(
        sorted(
            (
                _parse_channel(value, index)
                for index, value in enumerate(_parse_sequence(data, "channels"))
            ),
            key=lambda channel: channel.name,
        )
    )
    listen = tuple(
        sorted(
            _parse_edge(value, index, "listen")
            for index, value in enumerate(_parse_sequence(data, "listen"))
        )
    )
    send = tuple(
        sorted(
            _parse_edge(value, index, "send")
            for index, value in enumerate(_parse_sequence(data, "send"))
        )
    )

    creature_ids = [creature.creature_id for creature in creatures]
    _ensure_unique(creature_ids, "creatures.creature_id")
    _ensure_unique([creature.name for creature in creatures], "creatures.name")
    channel_names = [channel.name for channel in channels]
    _ensure_unique(channel_names, "channels.name")
    _validate_parents(creatures)
    _validate_edges(listen, "listen", set(creature_ids), set(channel_names))
    _validate_edges(send, "send", set(creature_ids), set(channel_names))

    return GraphManifest(
        kind=MANIFEST_KIND,
        version=MANIFEST_VERSION,
        revision=revision,
        graph_id=_string(_required(data, "graph_id"), "graph_id"),
        creatures=creatures,
        channels=channels,
        listen=listen,
        send=send,
    )


def manifest_to_dict(manifest: GraphManifest) -> dict[str, Any]:
    """Serialize a manifest to canonical primitive metadata."""
    canonical = parse_manifest(
        {
            "kind": manifest.kind,
            "version": manifest.version,
            "revision": manifest.revision,
            "graph_id": manifest.graph_id,
            "creatures": [
                {
                    "creature_id": creature.creature_id,
                    "name": creature.name,
                    "config_snapshot": creature.config_snapshot,
                    "source_ref": creature.source_ref,
                    "pwd": creature.pwd,
                    "is_privileged": creature.is_privileged,
                    "parent_creature_id": creature.parent_creature_id,
                }
                for creature in manifest.creatures
            ],
            "channels": [
                {"name": channel.name, "description": channel.description}
                for channel in manifest.channels
            ],
            "listen": [list(edge) for edge in manifest.listen],
            "send": [list(edge) for edge in manifest.send],
        }
    )
    return {
        "kind": canonical.kind,
        "version": canonical.version,
        "revision": canonical.revision,
        "graph_id": canonical.graph_id,
        "creatures": [
            {
                "creature_id": creature.creature_id,
                "name": creature.name,
                "config_snapshot": creature.config_snapshot,
                "source_ref": creature.source_ref,
                "pwd": creature.pwd,
                "is_privileged": creature.is_privileged,
                "parent_creature_id": creature.parent_creature_id,
            }
            for creature in canonical.creatures
        ],
        "channels": [
            {"name": channel.name, "description": channel.description}
            for channel in canonical.channels
        ],
        "listen": [list(edge) for edge in canonical.listen],
        "send": [list(edge) for edge in canonical.send],
    }


def _capture_config(creature: Any) -> dict[str, Any]:
    snapshot = getattr(creature, "config_snapshot", None)
    if not isinstance(snapshot, dict):
        raise SessionNotResumableError(
            f"Creature {creature.name!r} ({creature.creature_id}) has no "
            "declarative AgentConfig reconstruction provenance"
        )
    config = getattr(getattr(creature, "agent", None), "config", None)
    if isinstance(config, AgentConfig):
        snapshot = pack_agent_config(config)
        creature.config_snapshot = snapshot
    try:
        unpack_agent_config(snapshot)
    except Exception as exc:
        raise SessionNotResumableError(
            f"Creature {creature.name!r} ({creature.creature_id}) has invalid "
            f"AgentConfig reconstruction provenance: {exc}"
        ) from exc
    return dict(snapshot)


def capture_manifest(
    engine: "Terrarium", graph_id: str, *, revision: int = 0
) -> GraphManifest:
    """Capture a complete canonical manifest from one live engine graph."""
    graph = engine.get_graph(graph_id)
    creatures: list[dict[str, Any]] = []
    for creature_id in graph.creature_ids:
        creature = engine.get_creature(creature_id)
        build_pwd = getattr(creature, "build_pwd", "")
        if not isinstance(build_pwd, str) or not build_pwd:
            raise SessionNotResumableError(
                f"Creature {creature.name!r} ({creature_id}) has no effective "
                "build working directory provenance"
            )
        injected_runtime = getattr(creature, "injected_runtime", ())
        if injected_runtime:
            labels = ", ".join(sorted(injected_runtime))
            raise SessionNotResumableError(
                f"Creature {creature.name!r} ({creature_id}) depends on "
                f"non-declarative runtime injection: {labels}"
            )
        parent_id = creature.parent_creature_id
        if parent_id not in graph.creature_ids:
            parent_id = None
        creatures.append(
            {
                "creature_id": creature_id,
                "name": creature.name,
                "config_snapshot": _capture_config(creature),
                "source_ref": getattr(creature, "source_ref", None),
                "pwd": build_pwd,
                "is_privileged": creature.is_privileged,
                "parent_creature_id": parent_id,
            }
        )

    raw = {
        "kind": MANIFEST_KIND,
        "version": MANIFEST_VERSION,
        "revision": revision,
        "graph_id": graph_id,
        "creatures": creatures,
        "channels": [
            {"name": channel.name, "description": channel.description}
            for channel in graph.channels.values()
        ],
        "listen": [
            [creature_id, channel]
            for creature_id, channels in graph.listen_edges.items()
            for channel in channels
        ],
        "send": [
            [creature_id, channel]
            for creature_id, channels in graph.send_edges.items()
            for channel in channels
        ],
    }
    return parse_manifest(raw)


def _store_path(store: Any) -> str:
    return str(getattr(store, "_path", "<session>"))


def _raw_from_store(store: Any) -> tuple[bool, object]:
    try:
        meta = store.load_meta() if hasattr(store, "load_meta") else store.meta
        if MANIFEST_KEY not in meta:
            return False, None
        return True, meta[MANIFEST_KEY]
    except Exception as exc:
        raise GraphManifestPersistenceError(
            f"Failed to read live-graph manifest from {_store_path(store)}: {exc}"
        ) from exc


def unpack_creature_config(creature: ManifestCreature) -> AgentConfig:
    """Unpack one manifest creature's authoritative resolved config."""
    return unpack_agent_config(creature.config_snapshot)


def load_manifest(store: "SessionStore") -> GraphManifest | None:
    """Load and validate a store manifest, or return None when it is absent."""
    present, raw = _raw_from_store(store)
    if not present:
        return None
    return parse_manifest(raw)


def save_manifest(store: "SessionStore", manifest: GraphManifest) -> None:
    """Validate and atomically assign one complete manifest metadata value."""
    payload = manifest_to_dict(manifest)
    try:
        store.meta[MANIFEST_KEY] = payload
        flush = getattr(store, "flush", None)
        if flush is not None:
            flush()
    except Exception as exc:
        raise GraphManifestPersistenceError(
            f"Failed to save live-graph manifest to {_store_path(store)}: {exc}"
        ) from exc


def checkpoint_graph(engine: "Terrarium", graph_id: str) -> GraphManifest | None:
    """Capture and save the next manifest revision for a persisted graph."""
    store = engine._session_stores.get(graph_id)
    if store is None:
        return None
    raw = store.meta.get(MANIFEST_KEY)
    previous = load_manifest(store) if raw is not None else None
    revision = previous.revision + 1 if previous is not None else 1
    manifest = capture_manifest(engine, graph_id, revision=revision)
    save_manifest(store, manifest)
    return manifest


__all__ = [
    "ChannelManifest",
    "CreatureManifest",
    "GraphManifest",
    "MANIFEST_KEY",
    "MANIFEST_KIND",
    "MANIFEST_VERSION",
    "ManifestChannel",
    "ManifestCreature",
    "capture_manifest",
    "checkpoint_graph",
    "load_manifest",
    "manifest_to_dict",
    "parse_manifest",
    "save_manifest",
]
