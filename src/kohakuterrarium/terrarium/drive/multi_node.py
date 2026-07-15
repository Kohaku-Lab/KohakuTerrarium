"""Resolve and fence canonical Drive homes in multi-node mode.

A drive's canonical home is the worker owning its graph repository. Routing
tracks drive, delivery, and proposal homes, verifies uniqueness across connected
workers, and quarantines IDs claimed by multiple homes. Fan-out reads union worker
results but treat duplicate writers as an integrity failure. Because delivery
fencing is not yet integrated end to end, a move to a different home is refused
rather than risking late dispatch from the previous writer. Helpers accept the
service explicitly and use deterministic, injected fencing tokens.
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
from kohakuterrarium.terrarium.drive.fencing import (
    FencingRegistry,
    monotonic_token_counter,
)
from kohakuterrarium.terrarium.drive.models import SYSTEM_ACTOR, ActorRef
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.terrarium.drive.wire_service import DriveView

logger = get_logger(__name__)

# Probes locate canonical state only; the routed operation reauthorizes using the
# caller's actor, so probe identity grants no mutation authority.
ROUTE_ACTOR: ActorRef = SYSTEM_ACTOR


class DriveHomeUnavailableError(DriveError):
    """Indicate that canonical state is offline rather than absent."""

    def __init__(self, drive_id: str, *, home_node: str | None = None) -> None:
        super().__init__(
            f"Drive {drive_id!r} home node {home_node!r} is offline; "
            "canonical state is unavailable until it reconnects"
        )
        self.drive_id = drive_id
        self.home_node = home_node


class DriveRouteIntegrityError(DriveError):
    """Indicate that multiple workers claim the same canonical object."""


class DriveHomeMovedError(DriveError):
    """Reject movement to a new worker while stale-home delivery is unfenced.

    Fencing tokens exist but are not yet validated throughout delivery admission,
    so a late dispatch from the old writer cannot be rejected mechanically. Home
    resolution and routed writes therefore refuse every cross-worker movement
    until end-to-end fencing is available.
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
    """Indicate that incomplete probes cannot establish a unique home."""

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
    """Unique object home verified for one membership generation."""

    node_id: str
    generation: Any


class DriveRouteCache:
    """Cache active object homes while retaining the last known drive home.

    Active routes are removed when nodes disconnect. Sticky drive history
    distinguishes temporarily unavailable canonical state from an ID that was
    never observed. Fan-out reads and routed operations both warm the cache.
    """

    def __init__(self) -> None:
        self._active_drive: dict[str, str] = {}
        self._last_drive: dict[str, str] = {}
        self._active_delivery: dict[str, str] = {}
        self._active_proposal: dict[str, str] = {}
        # An active route is trusted only in the membership generation that
        # verified its uniqueness.
        self._generation: dict[tuple[str, str], Any] = {}
        # Duplicate claims remain quarantined until a complete probe finds one home.
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
        """Quarantine a duplicate claim and revoke its active route."""
        self._quarantined.add((kind, object_id))
        self.invalidate(kind, object_id)

    def clear_quarantine(self, kind: str, object_id: str) -> None:
        self._quarantined.discard((kind, object_id))

    def get_drive_home(self, drive_id: str) -> str | None:
        return self.active_home("drive", drive_id)

    def last_drive_home(self, drive_id: str) -> str | None:
        return self._last_drive.get(drive_id)

    def put_drive_home(self, drive_id: str, node_id: str) -> None:
        # Manual warming has no generation proof and lasts only while the node
        # remains connected; normal resolution records a concrete generation.
        self.bind_home("drive", drive_id, node_id, generation=None)

    def invalidate_drive(self, drive_id: str) -> None:
        self.invalidate("drive", drive_id)

    def get_delivery_home(self, delivery_id: str) -> str | None:
        return self.active_home("delivery", delivery_id)

    def put_delivery_home(self, delivery_id: str, node_id: str) -> None:
        self.bind_home("delivery", delivery_id, node_id, generation=None)

    def invalidate_delivery(self, delivery_id: str) -> None:
        self.invalidate("delivery", delivery_id)

    def get_proposal_home(self, proposal_id: str) -> str | None:
        return self.active_home("proposal", proposal_id)

    def put_proposal_home(self, proposal_id: str, node_id: str) -> None:
        self.bind_home("proposal", proposal_id, node_id, generation=None)

    def purge_node(self, node_id: str) -> None:
        """Drop routes to a departed worker while retaining drive history."""
        for did in [d for d, n in self._active_drive.items() if n == node_id]:
            self.invalidate("drive", did)
        for xid in [d for d, n in self._active_delivery.items() if n == node_id]:
            self.invalidate("delivery", xid)
        for pid in [p for p, n in self._active_proposal.items() if n == node_id]:
            self.invalidate("proposal", pid)


def _topology_generation(service: Any) -> int:
    """Return a generation that changes on worker join, leave, or replacement.

    Lightweight service fakes without a native membership epoch receive an
    identity-based fallback generation.
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
    """Convert a complete claimant set into one authoritative home verdict.

    One claimant binds, multiple claimants quarantine and raise, and no claimant
    clears the active route. A movement fence runs before caching so a refused
    home cannot poison trusted routing state.
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
    """Resolve a unique object home across all connected workers.

    Resolution never accepts the first claimant. Quarantined IDs require a full
    probe, cached routes require the current generation, and any probe failure
    makes uniqueness indeterminate. ``use_cache=False`` always reprobes.
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
            # Partial evidence cannot authorize mutation; quarantine remains
            # until a later complete probe proves a unique home.
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
    """Resolve within a stable membership generation, retrying one raced change."""
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
    """Resolve the unique connected worker hosting a drive.

    No claimant means unavailable when the sticky home is offline, otherwise not
    found. Multiple claimants are an integrity error. Movement to a different
    known home is refused before updating the cache.
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
    """Resolve the unique worker hosting a delivery for administrative replay.

    Replay has no graph ID, so all connected workers are probed. Duplicate claims
    quarantine the delivery; no claimant raises :class:`DriveDeliveryError`.
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
    """Verify the unique worker holding a pending proposal."""
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
    """Route a mutation to one verified home without moving mid-write.

    Mutation routing always reprobes because ownership can move without a
    membership change. If the selected worker reports not found, one re-resolution
    distinguishes genuine disappearance from a refused cross-worker move. The
    write is never retried on a second worker.
    """
    # Ownership can change without membership churn, so mutation authority
    # requires fresh complete evidence.
    node_id = await resolve_drive_home(service, drive_id, actor=actor, use_cache=False)
    try:
        return await fn(service.service_for(node_id))
    except DriveNotFoundError:
        service._drive_routes.invalidate_drive(drive_id)
        # Re-resolution either refuses movement without caching it or confirms
        # genuine disappearance at the same home; the write never reaches a
        # second worker.
        await resolve_drive_home(service, drive_id, actor=actor)
        raise


async def fanout_list_views(
    service: Any, call: Callable[[Any], Any]
) -> "tuple[DriveView, ...]":
    """Union list results by drive ID and warm only uniquely verified routes.

    Worker failures are logged and skipped for result availability, but incomplete
    evidence quarantines affected routes. Duplicate homes raise an integrity error,
    and refused home movement leaves the cache unwarmed.
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
            # Refused movement must leave trusted routing state empty.
            continue
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
