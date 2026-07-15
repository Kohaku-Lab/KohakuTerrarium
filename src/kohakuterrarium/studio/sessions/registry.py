"""Scope Studio session metadata and stores to each runtime.

Local services and raw Terrarium handles share the host engine as their registry
anchor. Multi-node lab hosts use the service itself because remote-session
metadata is controller-side state. Registries are created lazily on first use.
"""

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from weakref import WeakKeyDictionary

from kohakuterrarium.studio._runtime import host_engine_or_none

if TYPE_CHECKING:
    from kohakuterrarium.session.store import SessionStore


class StudioSessionRegistry:
    """Per-runtime session bookkeeping the engine doesn't store.

    ``meta`` holds studio-tier metadata captured at start time
    (config_path / pwd / created_at / remote on_node cache);
    ``stores`` maps ``session_id`` (graph_id) to the attached
    :class:`SessionStore`.
    """

    __slots__ = ("meta", "stores")

    def __init__(self) -> None:
        self.meta: dict[str, dict[str, Any]] = {}
        self.stores: dict[str, "SessionStore"] = {}


# Slotted or frozen anchors use weak fallback storage so dead runtimes release state.
_FALLBACK: "WeakKeyDictionary[Any, StudioSessionRegistry]" = WeakKeyDictionary()


def registry_for(runtime) -> StudioSessionRegistry:
    """Return (lazily creating) the session registry for ``runtime``.

    ``runtime`` may be a raw :class:`Terrarium`, a
    :class:`LocalTerrariumService`, or a multi-node service — the
    anchor resolution guarantees one registry per scope regardless of
    which form a caller holds.
    """
    engine = host_engine_or_none(runtime)
    # Empty Terrarium instances are falsy because they define ``__len__``.
    anchor = runtime if engine is None else engine
    reg = getattr(anchor, "_studio_sessions", None)
    if isinstance(reg, StudioSessionRegistry):
        return reg
    try:
        reg = _FALLBACK.get(anchor)
    except TypeError:  # Non-weak-referenceable anchors require attribute storage.
        reg = None
    if reg is not None:
        return reg
    reg = StudioSessionRegistry()
    try:
        anchor._studio_sessions = reg
    except (AttributeError, TypeError):
        _FALLBACK[anchor] = reg
    return reg


def meta_for(runtime) -> dict[str, dict[str, Any]]:
    """The per-runtime session-meta dict (``session_id`` → meta)."""
    return registry_for(runtime).meta


def stores_for(runtime) -> "dict[str, SessionStore]":
    """The per-runtime attached-store dict (``session_id`` → store)."""
    return registry_for(runtime).stores


def get_session_meta(runtime, session_id: str) -> dict[str, Any]:
    """Read-only copy of a session's studio metadata (``{}`` if unknown)."""
    return dict(meta_for(runtime).get(session_id, {}))


def register_session_meta(
    runtime, session_id: str, entry: dict[str, Any]
) -> dict[str, Any]:
    """Register (or replace) a session's studio metadata.

    Public write accessor for adapters (e.g. the HTTP resume route's
    remote-node registration) so nothing outside the studio tier
    touches the registry dicts.  ``created_at`` defaults to the
    studio-tier UTC ISO timestamp when the caller didn't set one.
    Returns the live entry.
    """
    entry = dict(entry)
    entry.setdefault("created_at", datetime.now(timezone.utc).isoformat())
    meta_for(runtime)[session_id] = entry
    return entry


def get_session_store(runtime, session_id: str) -> "SessionStore | None":
    """Return the SessionStore attached to ``session_id`` if any."""
    return stores_for(runtime).get(session_id)


def list_session_stores(runtime) -> "list[SessionStore]":
    """Return every live SessionStore the studio attached to ``runtime``."""
    return [s for s in stores_for(runtime).values() if s is not None]


__all__ = [
    "StudioSessionRegistry",
    "get_session_meta",
    "get_session_store",
    "list_session_stores",
    "meta_for",
    "register_session_meta",
    "registry_for",
    "stores_for",
]
