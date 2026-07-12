"""Canonical Drive routing / home / fencing helpers for multi-node mode.

A Drive is graph-scoped and its **home** is the worker that owns the graph's
repository (design §10.1). ``MultiNodeTerrariumService`` therefore needs, on top
of the creature/graph routing it already has:

- a ``drive_id -> home_node`` route cache that re-resolves a stale *active*
  entry on a ``not_hosted``/``not_found`` response, but refuses an unfenced move
  to a different worker (design §10.3);
- a delivery-id route cache for admin replay (which carries no graph id);
- fan-out list that unions worker results and dedupes by Drive id, treating a
  Drive claimed by two different homes as a hard integrity error (a
  double-writer would corrupt canonical state);
- a fencing/lease registry so an old home's late dispatch is rejected after a
  graph moved (design §10.3).

These helpers take the service as their first argument (mirroring
:mod:`terrarium.multi_node_routing`) so they can mutate the service's route
cache without the service class importing this module's internals. Everything
is deterministic: token issuance uses an injected counter, never wall-clock or
random, so unit tests pin exact fencing behavior.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from kohakuterrarium.terrarium.drive.errors import (
    DriveDeliveryError,
    DriveError,
    DriveNotFoundError,
    DriveValidationError,
)
from kohakuterrarium.terrarium.drive.fencing import FencingRegistry, monotonic_token_counter
from kohakuterrarium.terrarium.drive.models import SYSTEM_ACTOR, ActorRef
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.terrarium.drive.wire_service import DriveView

logger = get_logger(__name__)

# Routing probes read a Drive purely to locate its home; the real op re-fetches
# with the caller's actor, so a fixed system actor here never leaks authority.
ROUTE_ACTOR: ActorRef = SYSTEM_ACTOR


class DriveHomeUnavailableError(DriveError):
    """The Drive's home node is disconnected; its canonical state is offline.

    Distinct from :class:`DriveNotFoundError` (the Drive does not exist anywhere)
    so the host can report *unavailable/recovery* rather than *gone* when a home
    worker drops (design §10.2). The host never promotes a replica to writer.
    """

    def __init__(self, drive_id: str, *, home_node: str | None = None) -> None:
        super().__init__(
            f"Drive {drive_id!r} home node {home_node!r} is offline; "
            "canonical state is unavailable until it reconnects"
        )
        self.drive_id = drive_id
        self.home_node = home_node


class DriveRouteIntegrityError(DriveError):
    """Two connected homes both claim one Drive id — a double-writer hazard.

    A Drive must have exactly one canonical writer (design §10.1); if a fan-out
    sees the same ``drive_id`` on two nodes something is badly wrong (a botched
    move, a duplicated repository) and the host must fail loudly rather than pick
    one and race two dispatchers.
    """


class DriveHomeMovedError(DriveError):
    """A Drive's home moved to a different worker (old home connected or gone).

    Stale-home fencing (design §10.3) is defined (:class:`FencingRegistry`) but
    NOT integrated into delivery claim/admission in v1, so a late dispatch from
    the old home cannot be mechanically rejected — whether the old home departed
    or is merely no longer the writer. Rather than silently route to the new home
    and risk a double-dispatch, the host refuses every (unfenced) home movement:
    home resolution refuses a departed-old-home replacement, and
    :func:`route_drive_write` refuses a mid-write re-resolve to a different
    connected worker. Remove these guards only once fencing tokens are
    issued/validated on the worker delivery path.
    """

    def __init__(self, drive_id: str, *, old_home: str, new_home: str) -> None:
        super().__init__(
            f"Drive {drive_id!r} home moved from {old_home!r} to {new_home!r}; "
            "stale-home fencing is not integrated in v1, so the unfenced move is "
            "refused (design §10.3)"
        )
        self.drive_id = drive_id
        self.old_home = old_home
        self.new_home = new_home


class DriveRouteIndeterminateError(DriveError):
    """A home probe failed, so single-writer uniqueness cannot be established.

    A partial scan must never be trusted (design §10.1): if any connected node
    could not answer whether it hosts the id, the resolver withholds a home
    rather than routing on incomplete evidence (a silently-skipped node could be
    the second writer).
    """

    def __init__(self, kind: str, object_id: str, node_id: str) -> None:
        super().__init__(
            f"{kind} {object_id!r} home probe failed on {node_id!r}; uniqueness "
            "is indeterminate and routing is withheld (design §10.1)"
        )
        self.kind = kind
        self.object_id = object_id
        self.node_id = node_id


@dataclass(frozen=True, slots=True)
class VerifiedHome:
    """A home node whose single-writer uniqueness was verified at a topology gen.

    ``generation`` is the topology token the verification was bound to; a cached
    entry is only trusted on a later resolve while that token still matches.
    """

    node_id: str
    generation: Any


# ---------------------------------------------------------------------------
# Route cache
# ---------------------------------------------------------------------------


class DriveRouteCache:
    """``drive_id`` / ``delivery_id`` → home ``node_id`` with sticky last-home.

    ``_active`` maps ids to a *currently connected* home; it is purged when a
    node drops. ``_last`` is sticky: it survives a purge so the service can tell
    "home went offline" (report unavailable) from "Drive never existed" (report
    not-found). Both are warmed by fan-out reads and the routed op path.
    """

    def __init__(self) -> None:
        self._active_drive: dict[str, str] = {}
        self._last_drive: dict[str, str] = {}
        self._active_delivery: dict[str, str] = {}
        self._active_proposal: dict[str, str] = {}
        # (kind, object_id) -> topology generation the active entry was bound at.
        self._generation: dict[tuple[str, str], Any] = {}
        # (kind, object_id) pairs a duplicate-home probe found; never fast-pathed.
        self._quarantined: set[tuple[str, str]] = set()

    def _active_map(self, kind: str) -> dict[str, str]:
        match kind:
            case "drive":
                return self._active_drive
            case "delivery":
                return self._active_delivery
            case "proposal":
                return self._active_proposal
        raise KeyError(kind)

    # -- generic kind-keyed surface (used by resolve_unique_home) --------
    def active_home(self, kind: str, object_id: str) -> str | None:
        return self._active_map(kind).get(object_id)

    def home_generation(self, kind: str, object_id: str) -> Any:
        return self._generation.get((kind, object_id))

    def bind_home(
        self, kind: str, object_id: str, node_id: str, *, generation: Any
    ) -> None:
        self._active_map(kind)[object_id] = node_id
        if kind == "drive":
            self._last_drive[object_id] = node_id
        self._generation[(kind, object_id)] = generation

    def invalidate(self, kind: str, object_id: str) -> None:
        self._active_map(kind).pop(object_id, None)
        self._generation.pop((kind, object_id), None)

    def is_quarantined(self, kind: str, object_id: str) -> bool:
        return (kind, object_id) in self._quarantined

    def quarantine(self, kind: str, object_id: str) -> None:
        """Mark an id duplicate-claimed and drop any trusted active route to it."""
        self._quarantined.add((kind, object_id))
        self.invalidate(kind, object_id)

    def clear_quarantine(self, kind: str, object_id: str) -> None:
        self._quarantined.discard((kind, object_id))

    # -- drives ----------------------------------------------------------
    def get_drive_home(self, drive_id: str) -> str | None:
        return self.active_home("drive", drive_id)

    def last_drive_home(self, drive_id: str) -> str | None:
        return self._last_drive.get(drive_id)

    def put_drive_home(self, drive_id: str, node_id: str) -> None:
        # Legacy/manual warm carries no generation (None = topology-wildcard):
        # trusted while its node stays connected. Resolver/fan-out warms bind a
        # real generation via bind_home.
        self.bind_home("drive", drive_id, node_id, generation=None)

    def invalidate_drive(self, drive_id: str) -> None:
        self.invalidate("drive", drive_id)

    # -- deliveries ------------------------------------------------------
    def get_delivery_home(self, delivery_id: str) -> str | None:
        return self.active_home("delivery", delivery_id)

    def put_delivery_home(self, delivery_id: str, node_id: str) -> None:
        self.bind_home("delivery", delivery_id, node_id, generation=None)

    def invalidate_delivery(self, delivery_id: str) -> None:
        self.invalidate("delivery", delivery_id)

    # -- proposals -------------------------------------------------------
    def get_proposal_home(self, proposal_id: str) -> str | None:
        return self.active_home("proposal", proposal_id)

    def put_proposal_home(self, proposal_id: str, node_id: str) -> None:
        self.bind_home("proposal", proposal_id, node_id, generation=None)

    # -- membership ------------------------------------------------------
    def purge_node(self, node_id: str) -> None:
        """Drop *active* routes to a departed worker; keep the sticky last-home."""
        for did in [d for d, n in self._active_drive.items() if n == node_id]:
            self.invalidate("drive", did)
        for xid in [d for d, n in self._active_delivery.items() if n == node_id]:
            self.invalidate("delivery", xid)
        for pid in [p for p, n in self._active_proposal.items() if n == node_id]:
            self.invalidate("proposal", pid)


# ---------------------------------------------------------------------------
# Resolution + routing (operate on a MultiNodeTerrariumService)
# ---------------------------------------------------------------------------


def _topology_generation(service: Any) -> int:
    """Return the service's monotonic worker-membership epoch.

    The epoch changes on every real join/leave, including a reconnect under the
    same node id.  Tests and lightweight protocol fakes which predate the epoch
    get a private identity-based counter; production services always own
    ``_membership_epoch`` directly.
    """
    epoch = getattr(service, "_membership_epoch", None)
    if epoch is not None:
        return int(epoch)
    signature = tuple(
        (node_id, id(remote)) for node_id, remote in service._remotes.items()
    )
    previous = getattr(service, "_drive_membership_signature", None)
    if previous != signature:
        service._drive_membership_signature = signature
        service._drive_membership_epoch = (
            getattr(service, "_drive_membership_epoch", 0) + 1
        )
    return int(getattr(service, "_drive_membership_epoch", 0))


def _verdict(
    cache: DriveRouteCache,
    kind: str,
    object_id: str,
    claimants: list[str],
    *,
    generation: Any,
    fence: Callable[[str], None] | None,
) -> VerifiedHome | None:
    """The single uniqueness verdict for a probed claimant set.

    Exactly one claimant binds (and returns) a :class:`VerifiedHome`; more than
    one quarantines the id (dropping any trusted route) and raises
    :class:`DriveRouteIntegrityError`; none clears the active route and returns
    ``None``. ``fence`` may veto a unique home (drive home-movement, R1-16)
    BEFORE it is cached, so a refused move never poisons the cache.
    """
    if len(claimants) > 1:
        cache.quarantine(kind, object_id)
        raise DriveRouteIntegrityError(
            f"{kind} {object_id!r} is claimed by multiple homes "
            f"{sorted(claimants)!r}; refusing to route (single-writer invariant, "
            "design §10.1)"
        )
    if not claimants:
        cache.invalidate(kind, object_id)
        return None
    home_node = claimants[0]
    if fence is not None:
        try:
            fence(home_node)
        except DriveHomeMovedError:
            cache.invalidate(kind, object_id)
            raise
    cache.clear_quarantine(kind, object_id)
    cache.bind_home(kind, object_id, home_node, generation=generation)
    return VerifiedHome(home_node, generation)

async def resolve_unique_home(
    cache: DriveRouteCache,
    kind: str,
    object_id: str,
    connected_nodes: list[str],
    probe: Callable[[str], Awaitable[bool]],
    *,
    generation: Any,
    fence: Callable[[str], None] | None = None,
    use_cache: bool = True,
) -> VerifiedHome | None:
    """The one authoritative home resolver for drives / deliveries / proposals.

    Probes ALL connected nodes (never first-wins) and delegates the verdict to
    :func:`_verdict`. A quarantined id is never fast-pathed; an otherwise-usable
    cache entry is trusted only while bound to the current ``generation`` (a
    ``None`` generation is a legacy topology-wildcard). A probe that raises is
    INDETERMINATE (:class:`DriveRouteIndeterminateError`) — a partial scan is
    never trusted. ``use_cache=False`` forces a full re-evaluation (fan-out).
    """
    if use_cache and not cache.is_quarantined(kind, object_id):
        home = cache.active_home(kind, object_id)
        if home is not None and home in set(connected_nodes):
            rec = cache.home_generation(kind, object_id)
            if rec == generation:
                return VerifiedHome(home, generation)
    claimants: list[str] = []
    for node_id in connected_nodes:
        try:
            hosted = await probe(node_id)
        except Exception as exc:
            # Indeterminate evidence revokes mutation authority but deliberately
            # preserves quarantine: only a later complete unique probe may clear it.
            cache.quarantine(kind, object_id)
            raise DriveRouteIndeterminateError(kind, object_id, node_id) from exc
        if hosted:
            claimants.append(node_id)
    return _verdict(
        cache, kind, object_id, claimants, generation=generation, fence=fence
    )


async def _resolve_service_home(
    service: Any,
    cache: DriveRouteCache,
    kind: str,
    object_id: str,
    probe: Callable[[str], Awaitable[bool]],
    *,
    fence: Callable[[str], None] | None = None,
    use_cache: bool = True,
) -> VerifiedHome | None:
    """Resolve against one stable membership epoch, retrying one raced scan."""
    for attempt in range(2):
        generation = _topology_generation(service)
        nodes = list(service._remotes.keys())
        try:
            home = await resolve_unique_home(
                cache,
                kind,
                object_id,
                nodes,
                probe,
                generation=generation,
                fence=fence,
                use_cache=use_cache,
            )
        except (
            DriveRouteIndeterminateError,
            DriveRouteIntegrityError,
            DriveHomeMovedError,
        ):
            if _topology_generation(service) == generation:
                raise
            home = None
        if _topology_generation(service) == generation:
            return home
        cache.quarantine(kind, object_id)
        use_cache = False
        if attempt:
            raise DriveRouteIndeterminateError(kind, object_id, "membership_changed")
    raise AssertionError("unreachable")


async def resolve_drive_home(
    service: Any,
    drive_id: str,
    *,
    actor: ActorRef = ROUTE_ACTOR,
    use_cache: bool = True,
) -> str:
    """Resolve the connected worker that hosts ``drive_id``.

    Routes through :func:`resolve_unique_home`: a trusted, generation-current
    cache entry returns immediately; otherwise every connected worker is probed
    and exactly one must claim it (two is a :class:`DriveRouteIntegrityError`;
    none, with a sticky last-home now offline, is a
    :class:`DriveHomeUnavailableError`; none otherwise is a
    :class:`DriveNotFoundError`). The R1-16 fence refuses an unfenced move to a
    worker other than the Drive's known prior home BEFORE the cache is written.
    """
    cache: DriveRouteCache = service._drive_routes

    async def probe(node_id: str) -> bool:
        try:
            view = await service._remotes[node_id].get_drive(drive_id, actor=actor)
        except DriveNotFoundError:
            return False
        return view is not None

    def fence(home_node: str) -> None:
        last = cache.last_drive_home(drive_id)
        if last is not None and last != home_node:
            raise DriveHomeMovedError(drive_id, old_home=last, new_home=home_node)

    home = await _resolve_service_home(
        service,
        cache,
        "drive",
        drive_id,
        probe,
        fence=fence,
        use_cache=use_cache,
    )
    if home is not None:
        return home.node_id
    last = cache.last_drive_home(drive_id)
    if last is not None and last not in service._remotes:
        raise DriveHomeUnavailableError(drive_id, home_node=last)
    raise DriveNotFoundError(f"no Drive {drive_id!r} on any connected worker")


async def resolve_delivery_home(service: Any, delivery_id: str) -> str:
    """Resolve the connected worker hosting delivery ``delivery_id`` (admin replay).

    Admin replay carries no graph id, so the delivery is located by probing every
    connected worker through the SAME uniqueness resolver as drives: two workers
    claiming one delivery id is a :class:`DriveRouteIntegrityError` (quarantined),
    never a first-wins accept. No claimant is a :class:`DriveDeliveryError`.
    """
    cache: DriveRouteCache = service._drive_routes

    async def probe(node_id: str) -> bool:
        return bool(await service._remotes[node_id].locate_drive_delivery(delivery_id))

    home = await _resolve_service_home(
        service,
        cache,
        "delivery",
        delivery_id,
        probe,
        use_cache=False,
    )
    if home is None:
        raise DriveDeliveryError(f"no delivery {delivery_id!r} on any connected worker")
    return home.node_id


async def resolve_proposal_home(service: Any, proposal_id: str) -> str:
    """Freshly verify the single worker holding a pending proposal."""
    cache: DriveRouteCache = service._drive_routes

    async def probe(node_id: str) -> bool:
        return bool(await service._remotes[node_id].locate_drive_proposal(proposal_id))

    home = await _resolve_service_home(
        service,
        cache,
        "proposal",
        proposal_id,
        probe,
        use_cache=False,
    )
    if home is None:
        raise DriveValidationError(f"no pending proposal {proposal_id!r}")
    return home.node_id


async def route_drive_write(
    service: Any,
    drive_id: str,
    fn: Callable[[Any], Any],
    *,
    actor: ActorRef = ROUTE_ACTOR,
) -> Any:
    """Route a per-Drive mutation to the Drive's home; never move it mid-write.

    A stale *active* cache can point at a node that no longer hosts the Drive, so
    on ``drive_not_found`` we invalidate and re-resolve once. Re-resolution refuses
    (raises :class:`DriveHomeMovedError`) when the Drive now lives on a DIFFERENT
    worker — an unfenced A→B home movement a late old-home dispatch could not be
    fenced against (R1-16) — and does so WITHOUT rewriting the home cache, so a
    refused move leaves the trusted/sticky home untouched and every later write
    stays refused too. A same-home re-resolve that still cannot find the Drive
    re-raises: it is genuinely gone. This helper therefore never executes a write
    on a worker other than the one first resolved.
    """
    # Claim ownership may change without a membership event. Mutations therefore
    # require fresh complete evidence rather than a cache fast path.
    node_id = await resolve_drive_home(service, drive_id, actor=actor, use_cache=False)
    try:
        return await fn(service.service_for(node_id))
    except DriveNotFoundError:
        service._drive_routes.invalidate_drive(drive_id)
        # A moved Drive raises DriveHomeMovedError here (and leaves the cache
        # unpoisoned); a same-home re-resolve returns and we re-raise the genuine
        # not-found. Either way the write never runs on a second worker.
        await resolve_drive_home(service, drive_id, actor=actor)
        raise


async def fanout_list_views(
    service: Any, call: Callable[[Any], Any]
) -> "tuple[DriveView, ...]":
    """Fan out a Drive *list* read; union + dedupe by Drive id, warm the cache.

    A Drive id reported by two different connected homes is a hard
    :class:`DriveRouteIntegrityError` (double writer). Per-worker failures are
    logged and skipped so one unreachable worker cannot blank the whole list.

    Every listed Drive's cache warm goes through the SAME uniqueness resolver as
    a routed op (``use_cache=False`` forces a fresh verdict on the listing), so a
    duplicate-home Drive quarantines the id and drops any trusted route to it
    (R3b) rather than warming one arbitrary home — even one already cached. The
    R1-16 fence still leaves a moved (changed-home) Drive un-warmed; a first
    observation or unchanged home warms normally.
    """
    cache: DriveRouteCache = service._drive_routes
    by_id: dict[str, Any] = {}
    homes_of: dict[str, set[str]] = {}
    failed_nodes: set[str] = set()
    generation = _topology_generation(service)
    for node_id, svc in list(service._remotes.items()):
        try:
            views = await call(svc)
        except Exception:
            logger.exception("drive list fan-out failed on %s", node_id)
            failed_nodes.add(node_id)
            continue
        for view in views:
            drive_id = view.record.drive_id
            homes_of.setdefault(drive_id, set()).add(node_id)
            by_id[drive_id] = view
    connected = list(service._remotes.keys())
    complete = not failed_nodes and _topology_generation(service) == generation
    integrity: DriveRouteIntegrityError | None = None
    for drive_id, listed in homes_of.items():
        if not complete:
            cache.quarantine("drive", drive_id)
            continue

        async def probe(node_id: str, _listed: set[str] = listed) -> bool:
            return node_id in _listed

        def fence(home_node: str, _drive_id: str = drive_id) -> None:
            last = cache.last_drive_home(_drive_id)
            if last is not None and last != home_node:
                raise DriveHomeMovedError(_drive_id, old_home=last, new_home=home_node)

        try:
            await resolve_unique_home(
                cache,
                "drive",
                drive_id,
                connected,
                probe,
                generation=generation,
                fence=fence,
                use_cache=False,
            )
        except DriveHomeMovedError:
            continue  # R1-16: leave a moved Drive fenced, not repopulated.
        except DriveRouteIntegrityError as exc:
            integrity = integrity or exc
    if integrity is not None:
        raise integrity
    return tuple(by_id.values())


__all__ = [
    "DriveHomeMovedError",
    "DriveHomeUnavailableError",
    "DriveRouteCache",
    "DriveRouteIndeterminateError",
    "DriveRouteIntegrityError",
    "FencingRegistry",
    "ROUTE_ACTOR",
    "VerifiedHome",
    "fanout_list_views",
    "monotonic_token_counter",
    "resolve_delivery_home",
    "resolve_drive_home",
    "resolve_proposal_home",
    "resolve_unique_home",
    "route_drive_write",
]
