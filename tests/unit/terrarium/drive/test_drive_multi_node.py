"""Unit tests for the canonical Drive routing / fencing helpers (design §10).

Covers :mod:`kohakuterrarium.terrarium.drive.multi_node`: the route cache,
fencing tokens, home resolution (including the duplicate-home integrity error and
home-loss unavailability), stale-route repair-once, and fan-out list dedupe.
"""

import itertools
import types

import pytest

from kohakuterrarium.terrarium.drive.errors import (
    DriveConflictError,
    DriveDeliveryError,
    DriveNotFoundError,
)
from kohakuterrarium.terrarium.drive.multi_node import (
    DriveHomeMovedError,
    DriveHomeUnavailableError,
    DriveRouteCache,
    DriveRouteIntegrityError,
    FencingRegistry,
    fanout_list_views,
    monotonic_token_counter,
    resolve_delivery_home,
    resolve_drive_home,
    route_drive_write,
)


def _view(drive_id: str):
    return types.SimpleNamespace(record=types.SimpleNamespace(drive_id=drive_id))


class _FakeRemote:
    def __init__(
        self,
        node_id: str,
        drives: dict | None = None,
        deliveries: set | None = None,
    ) -> None:
        self.node_id = node_id
        self._drives = dict(drives or {})
        self._deliveries = set(deliveries or ())

    async def get_drive(self, drive_id, *, actor, is_privileged=False):
        return self._drives.get(drive_id)

    async def list_drives(self, **_kw):
        return tuple(self._drives.values())

    async def locate_drive_delivery(self, delivery_id):
        return delivery_id in self._deliveries


class _FakeService:
    def __init__(self) -> None:
        self._remotes: dict[str, _FakeRemote] = {}
        self._drive_routes = DriveRouteCache()

    def add(
        self,
        node_id: str,
        drives: dict | None = None,
        deliveries: set | None = None,
    ) -> _FakeRemote:
        remote = _FakeRemote(node_id, drives, deliveries)
        self._remotes[node_id] = remote
        return remote

    def service_for(self, node_id: str) -> _FakeRemote:
        return self._remotes[node_id]


# ---------------------------------------------------------------------------
# Route cache
# ---------------------------------------------------------------------------


class TestDriveRouteCache:
    def test_put_get_and_sticky_last_home(self):
        cache = DriveRouteCache()
        cache.put_drive_home("d1", "A")
        assert cache.get_drive_home("d1") == "A"
        assert cache.last_drive_home("d1") == "A"

    def test_purge_drops_active_keeps_last(self):
        cache = DriveRouteCache()
        cache.put_drive_home("d1", "A")
        cache.put_delivery_home("x1", "A")
        cache.put_proposal_home("p1", "A")
        cache.purge_node("A")
        assert cache.get_drive_home("d1") is None
        assert cache.last_drive_home("d1") == "A"
        assert cache.get_delivery_home("x1") is None
        assert cache.get_proposal_home("p1") is None

    def test_invalidate_drive_only_clears_active(self):
        cache = DriveRouteCache()
        cache.put_drive_home("d1", "A")
        cache.invalidate_drive("d1")
        assert cache.get_drive_home("d1") is None
        assert cache.last_drive_home("d1") == "A"


# ---------------------------------------------------------------------------
# Fencing tokens
# ---------------------------------------------------------------------------


class TestFencingRegistry:
    def test_issue_is_monotonic_and_bumps_on_reissue(self):
        reg = FencingRegistry(counter=itertools.count(1))
        t1 = reg.issue("g")
        t2 = reg.issue("g")
        assert t1 == 1 and t2 == 2
        assert reg.current("g") == 2

    def test_validate_rejects_stale_token(self):
        reg = FencingRegistry(counter=itertools.count(1))
        old = reg.issue("g")
        reg.issue("g")  # move → new token
        with pytest.raises(DriveConflictError):
            reg.validate("g", old)

    def test_validate_accepts_live_token(self):
        reg = FencingRegistry()
        token = reg.issue("g")
        reg.validate("g", token)  # no raise
        assert reg.is_current("g", token)

    def test_default_counter_starts_at_one(self):
        counter = monotonic_token_counter()
        assert next(counter) == 1

    def test_graph_home_move_fences_old_token(self):
        # The fencing mechanism (design §10.3): after a graph's home moves, the
        # new home holds the current token; a delayed old-home request presenting
        # the stale token is rejected, and the current token is accepted.
        reg = FencingRegistry(counter=itertools.count(1))
        old_home_token = reg.issue("graph-g")
        new_home_token = reg.issue("graph-g")  # home moved → re-issue
        with pytest.raises(DriveConflictError):
            reg.validate("graph-g", old_home_token)
        reg.validate("graph-g", new_home_token)  # current home succeeds


# ---------------------------------------------------------------------------
# Home resolution
# ---------------------------------------------------------------------------


class TestResolveDriveHome:
    async def test_resolves_and_caches_single_home(self):
        svc = _FakeService()
        svc.add("A", {"d1": _view("d1")})
        svc.add("B", {})
        node = await resolve_drive_home(svc, "d1")
        assert node == "A"
        # second call hits the cache (still returns A even if we clear B's data)
        assert svc._drive_routes.get_drive_home("d1") == "A"

    async def test_duplicate_home_is_integrity_error(self):
        svc = _FakeService()
        svc.add("A", {"d1": _view("d1")})
        svc.add("B", {"d1": _view("d1")})
        with pytest.raises(DriveRouteIntegrityError):
            await resolve_drive_home(svc, "d1")

    async def test_missing_drive_raises_not_found(self):
        svc = _FakeService()
        svc.add("A", {})
        with pytest.raises(DriveNotFoundError):
            await resolve_drive_home(svc, "ghost")

    async def test_home_loss_reports_unavailable(self):
        svc = _FakeService()
        svc.add("A", {"d1": _view("d1")})
        await resolve_drive_home(svc, "d1")  # warms the sticky last-home
        del svc._remotes["A"]  # home node drops
        with pytest.raises(DriveHomeUnavailableError) as exc:
            await resolve_drive_home(svc, "d1")
        assert exc.value.home_node == "A"

    async def test_stale_active_cache_repairs_to_same_connected_home(self):
        # Active route cleared (stale-route repair) but the drive's last home is
        # still connected and still hosts it → re-probe resolves to the SAME home.
        svc = _FakeService()
        svc.add("A", {"d1": _view("d1")})
        svc._drive_routes.put_drive_home("d1", "A")
        svc._drive_routes.invalidate_drive("d1")  # active cleared, last=A
        assert await resolve_drive_home(svc, "d1") == "A"

    async def test_departed_home_replacement_is_refused_unfenced(self):
        # A hosts d1 (last=A). A departs and a DIFFERENT worker B now claims d1
        # — an unfenced home movement. With no integrated fencing (R1-16) the
        # host cannot reject a late old-home dispatch, so it refuses the move
        # itself rather than silently route to B.
        svc = _FakeService()
        svc.add("A", {"d1": _view("d1")})
        await resolve_drive_home(svc, "d1")  # warms last=A
        del svc._remotes["A"]
        svc._drive_routes.purge_node("A")  # real disconnect clears active, keeps last
        svc.add("B", {"d1": _view("d1")})
        with pytest.raises(DriveHomeMovedError) as exc:
            await resolve_drive_home(svc, "d1")
        assert exc.value.old_home == "A" and exc.value.new_home == "B"

    async def test_topology_change_reprobes_and_catches_new_duplicate(self):
        # A cache entry is bound to the topology generation it was warmed at. When
        # a node joins (topology changes) AND now also double-claims the Drive, the
        # stale-generation entry must NOT be trusted: the resolver re-probes and
        # catches the duplicate. Pre-fix the connected cache hit returns A and
        # silently hides the second writer.
        svc = _FakeService()
        svc.add("A", {"d1": _view("d1")})
        assert await resolve_drive_home(svc, "d1") == "A"  # binds generation {A}
        svc.add("B", {"d1": _view("d1")})  # B joins AND double-claims d1
        with pytest.raises(DriveRouteIntegrityError):
            await resolve_drive_home(svc, "d1")


# ---------------------------------------------------------------------------
# Delivery-home resolution (admin replay carries no graph id)
# ---------------------------------------------------------------------------


class TestResolveDeliveryHome:
    async def test_single_claimant_resolves_and_caches(self):
        svc = _FakeService()
        svc.add("A", deliveries={"x1"})
        svc.add("B")
        assert await resolve_delivery_home(svc, "x1") == "A"
        assert svc._drive_routes.get_delivery_home("x1") == "A"

    async def test_duplicate_delivery_is_integrity_not_first_wins(self):
        # 1b: pre-fix _resolve_delivery_home returns and caches the FIRST worker
        # reporting the delivery; it never probes the rest for a second claimant.
        # The shared resolver probes ALL, refuses the duplicate, and quarantines
        # the id instead of accepting/caching the first claimant.
        svc = _FakeService()
        svc.add("A", deliveries={"x1"})
        svc.add("B", deliveries={"x1"})
        with pytest.raises(DriveRouteIntegrityError):
            await resolve_delivery_home(svc, "x1")
        assert svc._drive_routes.get_delivery_home("x1") is None
        assert svc._drive_routes.is_quarantined("delivery", "x1")

    async def test_missing_delivery_raises(self):
        svc = _FakeService()
        svc.add("A")
        with pytest.raises(DriveDeliveryError):
            await resolve_delivery_home(svc, "ghost")


# ---------------------------------------------------------------------------
# Routed writes + repair-once
# ---------------------------------------------------------------------------


class TestRouteDriveWrite:
    async def test_routes_to_home(self):
        svc = _FakeService()
        svc.add("A", {"d1": _view("d1")})
        seen = {}

        async def fn(remote):
            seen["node"] = remote.node_id
            return "ok"

        result = await route_drive_write(svc, "d1", fn)
        assert result == "ok"
        assert seen["node"] == "A"

    async def test_refuses_move_to_different_connected_worker(self):
        # Stale cache points at A, but only B hosts d1 now while A is STILL
        # connected. Re-resolving to B is an unfenced A->B home movement; until
        # fencing is integrated route_drive_write refuses it rather than retry on
        # the new worker (R1-16). The write is never dispatched to B.
        svc = _FakeService()
        svc.add("A", {})
        svc.add("B", {"d1": _view("d1")})
        svc._drive_routes.put_drive_home("d1", "A")  # stale cache points at A
        calls: list[str] = []

        async def fn(remote):
            calls.append(remote.node_id)
            if remote.node_id == "A":
                raise DriveNotFoundError("moved away")
            return "ok"

        with pytest.raises(DriveHomeMovedError) as exc:
            await route_drive_write(svc, "d1", fn)
        assert exc.value.old_home == "A" and exc.value.new_home == "B"
        assert calls == []  # fresh arbitration refuses before dispatching any mutation

    async def test_refused_move_stays_refused_on_every_later_write(self):
        # R1-16 second-call bypass: a refused move must NOT write the new worker
        # into the active/sticky home caches, or the NEXT write sees B as the
        # ordinary cached home and executes there unfenced. The reviewer's exact
        # repro: attempt 1 raises, attempt 2 MUST ALSO raise, the write never
        # reaches B, and the cache never learns B as the home.
        svc = _FakeService()
        svc.add("A", {})
        svc.add("B", {"d1": _view("d1")})
        svc._drive_routes.put_drive_home("d1", "A")  # stale cache points at A
        calls: list[str] = []

        async def fn(remote):
            calls.append(remote.node_id)
            if remote.node_id == "A":
                raise DriveNotFoundError("moved away")
            return "ok"

        with pytest.raises(DriveHomeMovedError):
            await route_drive_write(svc, "d1", fn)
        # The refused move left the caches unpoisoned: active cleared, sticky = A.
        assert svc._drive_routes.get_drive_home("d1") is None
        assert svc._drive_routes.last_drive_home("d1") == "A"
        # Every later write stays refused, and B is never dispatched to.
        with pytest.raises(DriveHomeMovedError) as exc2:
            await route_drive_write(svc, "d1", fn)
        assert exc2.value.old_home == "A" and exc2.value.new_home == "B"
        assert calls == []  # both fresh arbitrations refuse before mutation dispatch
        assert svc._drive_routes.get_drive_home("d1") is None
        assert svc._drive_routes.last_drive_home("d1") == "A"

    async def test_list_fanout_cannot_bypass_refused_move(self):
        # R1-16 fan-out bypass: after a write is refused for an unfenced A->B move,
        # an intervening list fan-out must NOT repopulate active/sticky with B, or
        # the next write silently executes on B unfenced. The fan-out warm must
        # preserve the home-movement invariant, not just route_drive_write.
        svc = _FakeService()
        svc.add("A", {})
        svc.add("B", {"d1": _view("d1")})
        svc._drive_routes.put_drive_home("d1", "A")  # stale cache points at A
        calls: list[str] = []

        async def fn(remote):
            calls.append(remote.node_id)
            if remote.node_id == "A":
                raise DriveNotFoundError("moved away")
            return "WROTE-B"

        # first write: stale home A, drive now on B -> refused
        with pytest.raises(DriveHomeMovedError) as exc1:
            await route_drive_write(svc, "d1", fn)
        assert exc1.value.old_home == "A" and exc1.value.new_home == "B"
        # after_first: active cleared, sticky/last still A
        assert svc._drive_routes.get_drive_home("d1") is None
        assert svc._drive_routes.last_drive_home("d1") == "A"

        # a list fan-out sees d1 on B; it must NOT rewrite active/sticky to B
        await fanout_list_views(svc, lambda s: s.list_drives())
        assert svc._drive_routes.get_drive_home("d1") is None  # active NOT B
        assert svc._drive_routes.last_drive_home("d1") == "A"  # sticky stays A

        # second write STILL refused; WROTE-B never executes on B
        with pytest.raises(DriveHomeMovedError) as exc2:
            await route_drive_write(svc, "d1", fn)
        assert exc2.value.old_home == "A" and exc2.value.new_home == "B"
        assert calls == []  # both fresh arbitrations refuse before mutation dispatch

    async def test_genuinely_missing_reraises_after_repair(self):
        svc = _FakeService()
        svc.add("A", {"d1": _view("d1")})
        svc._drive_routes.put_drive_home("d1", "A")

        async def fn(remote):
            raise DriveNotFoundError("gone")

        with pytest.raises(DriveNotFoundError):
            await route_drive_write(svc, "d1", fn)


# ---------------------------------------------------------------------------
# Fan-out list dedupe
# ---------------------------------------------------------------------------


class TestFanoutListViews:
    async def test_unions_and_dedupes_by_drive_id(self):
        svc = _FakeService()
        svc.add("A", {"d1": _view("d1")})
        svc.add("B", {"d2": _view("d2")})
        views = await fanout_list_views(svc, lambda s: s.list_drives())
        ids = sorted(v.record.drive_id for v in views)
        assert ids == ["d1", "d2"]
        # cache warmed by the fan-out
        assert svc._drive_routes.get_drive_home("d1") == "A"
        assert svc._drive_routes.get_drive_home("d2") == "B"

    async def test_duplicate_home_in_listing_is_integrity_error(self):
        svc = _FakeService()
        svc.add("A", {"d1": _view("d1")})
        svc.add("B", {"d1": _view("d1")})
        with pytest.raises(DriveRouteIntegrityError):
            await fanout_list_views(svc, lambda s: s.list_drives())

    async def test_duplicate_home_fanout_leaves_no_trusted_route(self):
        # R3b: collection must fully validate BEFORE any cache mutation. Pre-fix
        # the FIRST home (A) is warmed before the duplicate on B is discovered, so
        # the integrity error fires but A stays cached — the next write trusts the
        # cache and executes WROTE-A on A without re-probing. The fix warms nothing
        # for a duplicated Drive, leaving it fail-closed so the write re-probes.
        svc = _FakeService()
        svc.add("A", {"d1": _view("d1")})
        svc.add("B", {"d1": _view("d1")})

        with pytest.raises(DriveRouteIntegrityError):
            await fanout_list_views(svc, lambda s: s.list_drives())

        # The duplicated Drive is NOT cached: active stays None (pre-fix it was A),
        # and the sticky last-home never learned A either.
        assert svc._drive_routes.get_drive_home("d1") is None
        assert svc._drive_routes.last_drive_home("d1") is None

        calls: list[str] = []

        async def fn(remote):
            calls.append(remote.node_id)
            return "WROTE-A"

        # A subsequent write does NOT trust a cached home; it re-probes, hits the
        # duplicate again, and refuses — no trusted single-home write reaches A.
        with pytest.raises(DriveRouteIntegrityError):
            await route_drive_write(svc, "d1", fn)
        assert calls == []  # WROTE-A never executed on a trusted cached home

    async def test_prewarmed_route_duplicate_fanout_quarantines_and_reprobes(self):
        # 1a: the round-3c collect→validate→warm fix only covered a COLD cache.
        # With d1 ALREADY cached to A, a duplicate fan-out must INVALIDATE and
        # quarantine that trusted route (not leave it at A) so the next write
        # re-probes and refuses. Pre-fix the fan-out raises but leaves d1 cached
        # at A, and the next write executes WROTE-A on the stale trusted home.
        svc = _FakeService()
        svc.add("A", {"d1": _view("d1")})
        svc.add("B", {"d1": _view("d1")})
        svc._drive_routes.put_drive_home("d1", "A")  # PRE-WARMED trusted route
        assert svc._drive_routes.get_drive_home("d1") == "A"

        with pytest.raises(DriveRouteIntegrityError):
            await fanout_list_views(svc, lambda s: s.list_drives())

        # The pre-warmed route is cleared and the id quarantined (pre-fix: stays A).
        assert svc._drive_routes.get_drive_home("d1") is None
        assert svc._drive_routes.is_quarantined("drive", "d1")

        calls: list[str] = []

        async def fn(remote):
            calls.append(remote.node_id)
            return "WROTE-A"

        # Next write re-probes (quarantine forbids the cache fast-path), hits the
        # duplicate again, and refuses — no WROTE-A on the stale trusted home.
        with pytest.raises(DriveRouteIntegrityError):
            await route_drive_write(svc, "d1", fn)
        assert calls == []

    async def test_unreachable_worker_is_skipped(self):
        svc = _FakeService()
        svc.add("A", {"d1": _view("d1")})
        broken = svc.add("B", {})

        async def boom(**_kw):
            raise RuntimeError("worker down")

        broken.list_drives = boom
        views = await fanout_list_views(svc, lambda s: s.list_drives())
        assert [v.record.drive_id for v in views] == ["d1"]
