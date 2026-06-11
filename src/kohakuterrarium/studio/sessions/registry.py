"""Instance-scoped studio session registries.

Historically :mod:`studio.sessions.lifecycle` kept ``_meta`` and
``_session_stores`` as module-global dicts — two ``Studio()`` /
``LocalTerrariumService`` instances in one process (or two engines in
the L4 per-user pool) shared and cross-contaminated each other's
session bookkeeping.  This module re-homes that state per runtime.

The registry is anchored on the object that actually scopes a set of
sessions:

* a host engine when one exists (``host_engine_or_none`` resolves both
  a raw :class:`Terrarium` and a :class:`LocalTerrariumService` wrapping
  it to the SAME engine, so service wrappers created per-request — the
  L4 pool does this — keep seeing one registry per engine);
* the service itself in lab-host mode (``MultiNodeTerrariumService``
  runs no host agent engine; its remote-session ``_meta`` cache is
  controller-side state).

Accessors are lazy: the first touch attaches a fresh
:class:`StudioSessionRegistry` to the anchor as ``_studio_sessions``.
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


# Fallback for anchors that reject attribute assignment (slotted /
# frozen runtime fakes).  Weak so a dead engine's bookkeeping dies
# with it instead of leaking for the process lifetime.
_FALLBACK: "WeakKeyDictionary[Any, StudioSessionRegistry]" = WeakKeyDictionary()


def registry_for(runtime) -> StudioSessionRegistry:
    """Return (lazily creating) the session registry for ``runtime``.

    ``runtime`` may be a raw :class:`Terrarium`, a
    :class:`LocalTerrariumService`, or a multi-node service — the
    anchor resolution guarantees one registry per scope regardless of
    which form a caller holds.
    """
    engine = host_engine_or_none(runtime)
    # NB: Terrarium defines ``__len__`` so an empty engine is falsy —
    # test ``is None`` explicitly, never truthiness.
    anchor = runtime if engine is None else engine
    reg = getattr(anchor, "_studio_sessions", None)
    if isinstance(reg, StudioSessionRegistry):
        return reg
    try:
        reg = _FALLBACK.get(anchor)
    except TypeError:  # anchor not weakref-able — attribute attach only
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


# ---------------------------------------------------------------------------
# Public read / write accessors — what api/ and sibling studio modules
# use instead of reaching into the registry dicts directly.
# ---------------------------------------------------------------------------


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
