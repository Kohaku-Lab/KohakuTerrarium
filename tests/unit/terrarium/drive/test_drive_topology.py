"""Unit tests for :mod:`terrarium.drive.topology` — merge / split / fork row
movement (design §6.6-6.8, Phase F).

Driven against a minimal fake engine plus a REAL :class:`DriveRuntime` and its
per-graph :class:`MemoryDriveRepository` / :class:`SqliteDriveRepository`. Fake
creatures are deliberately NOT restoration-ready so reconcile never delivers —
these tests pin ROW MOVEMENT + placement + integrity, not delivery (that lives
in the integration tier). One persistent merge exercises the sqlite path.
"""

from types import SimpleNamespace

import pytest

from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.terrarium.drive import topology as drive_topology
from kohakuterrarium.terrarium.drive.config import (
    DriveRuntimeConfig,
    default_registrations,
)
from kohakuterrarium.terrarium.drive.errors import (
    DriveError,
    DriveIdempotencyConflictError,
    DriveProposalConflictError,
)
from kohakuterrarium.terrarium.drive.models import ActorRef, DriveStatus
from kohakuterrarium.terrarium.drive.repository import Mutation
from kohakuterrarium.terrarium.drive.requests import (
    CreateDriveRequest,
    DriveQuery,
    DriveTransitionProposal,
)
from kohakuterrarium.terrarium.drive.runtime import DriveRuntime
from kohakuterrarium.terrarium.topology import GraphTopology, TopologyState

USER = ActorRef("user", "alice")

pytestmark = pytest.mark.timeout(30)


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------


class _FakeCreature:
    def __init__(self, cid: str, gid: str) -> None:
        self.creature_id = cid
        self.graph_id = gid
        # Defaults to the id; a resume-remap test overrides it with the config
        # name so the decoded old-id name matches a resumed creature.
        self.name = cid
        self.is_running = True
        # Not restoration-ready → reconcile skips delivery (§6.5); we assert on
        # repository state, which is deterministic regardless of the dispatcher.
        self.restoration_ready = False
        self.agent = SimpleNamespace(has_pending_mid_turn_inputs=False)


class _FakeEngine:
    def __init__(self) -> None:
        self._creatures: dict[str, _FakeCreature] = {}
        self._session_stores: dict = {}
        self._topology = TopologyState()
        self._pending_drive_topology = None
        self._drive_runtime = None
        self.emitted: list = []

    def _emit(self, ev) -> None:
        self.emitted.append(ev)

    def add_graph(self, gid: str, creature_ids: set[str]) -> None:
        self._topology.graphs[gid] = GraphTopology(
            graph_id=gid, creature_ids=set(creature_ids)
        )
        for cid in creature_ids:
            self._creatures[cid] = _FakeCreature(cid, gid)
            self._topology.creature_to_graph[cid] = gid


def _make(engine=None) -> tuple[_FakeEngine, DriveRuntime]:
    engine = engine or _FakeEngine()
    rt = DriveRuntime(
        engine, DriveRuntimeConfig(enabled=True), tuple(default_registrations())
    )
    engine._drive_runtime = rt
    return engine, rt


def _graph_req(scope_id: str, *, assignee=None, deps=(), policy_options=None):
    return CreateDriveRequest(
        kind="generic",
        title="watch",
        scope_type="graph",
        scope_id=scope_id,
        owner=USER,
        owner_scope="graph",
        created_by=USER,
        assignee_creature_id=assignee,
        dependency_ids=tuple(deps),
        policy_options=policy_options or {},
    )


def _creature_req(creature_id: str):
    actor = ActorRef("creature", creature_id)
    return CreateDriveRequest(
        kind="generic",
        title="watch",
        scope_type="creature",
        scope_id=creature_id,
        owner=actor,
        owner_scope="creature",
        created_by=actor,
        assignee_creature_id=creature_id,
    )


async def _mk_graph(mgr, scope_id, *, gid=None, **req_kw):
    """Create a graph-scoped Drive (needs privilege for ``create_graph``)."""
    return await mgr.create_drive(
        _graph_req(scope_id, **req_kw),
        actor=USER,
        graph_id=gid or scope_id,
        is_privileged=True,
    )


async def _drain(engine, rt):
    await rt.drain_topology()


async def _add_proposal(mgr, drive_id, *, proposal_id):
    """Persist a pending terminal proposal row directly on ``mgr``'s repo — the
    row that a merge/split must carry with its parent Drive (R1-08)."""
    prop = DriveTransitionProposal(
        proposal_id=proposal_id,
        drive_id=drive_id,
        target_status=DriveStatus.COMPLETED,
        proposed_by=USER,
        created_at=mgr.now(),
        expected_revision=1,
        lifecycle_epoch=0,
    )
    async with mgr.repository.transaction() as txn:
        await txn.apply(Mutation(proposals=[prop]))
    return prop


# ---------------------------------------------------------------------------
# merge (§6.6)
# ---------------------------------------------------------------------------


class TestMerge:
    async def test_ephemeral_merge_unions_into_survivor(self):
        engine, rt = _make()
        engine.add_graph("gA", {"wa"})
        rec_a = await _mk_graph(rt.manager_for("gA"), "gA", assignee="wa")
        rec_b = await _mk_graph(rt.manager_for("gB"), "gB", assignee="wb")
        try:
            drive_topology.stash_merge(engine, "gA", ["gA", "gB"])
            await _drain(engine, rt)
            survivor = rt.manager_for("gA")
            assert await survivor.get_drive(rec_a.drive_id) is not None
            # gB's Drive moved into the survivor and was rehomed to gA (§6.6).
            moved = await survivor.get_drive(rec_b.drive_id)
            assert moved is not None and moved.scope_id == "gA"
            # gB's manager is gone — one canonical repository per Drive.
            assert rt.peek_manager("gB") is None
        finally:
            await rt.stop()

    async def test_duplicate_drive_id_is_integrity_error(self):
        engine, rt = _make()
        mgr_a = rt.manager_for("gA")
        mgr_b = rt.manager_for("gB")
        rec = await _mk_graph(mgr_a, "gA", assignee="wa")
        # Force the same drive_id into graph B's repo (corruption): import the
        # exact row so the merge sees a collision.
        payload = await mgr_a.repository.export_rows()
        await mgr_b.repository.import_rows(payload)
        assert await mgr_b.get_drive(rec.drive_id) is not None
        try:
            drive_topology.stash_merge(engine, "gA", ["gA", "gB"])
            with pytest.raises(DriveError, match="duplicate drive_id"):
                await _drain(engine, rt)
        finally:
            await rt.stop()

    async def test_persistent_merge_moves_rows_into_survivor_file(self, tmp_path):
        engine, rt = _make()
        store_a = SessionStore(str(tmp_path / "a.kohakutr"), writer_lock=True)
        store_b = SessionStore(str(tmp_path / "b.kohakutr"), writer_lock=True)
        engine._session_stores["gA"] = store_a
        engine._session_stores["gB"] = store_b
        try:
            await rt.bind_graph_store("gA", store_a)
            await rt.bind_graph_store("gB", store_b)
            assert rt.durability == "persistent"
            rec_a = await _mk_graph(rt.manager_for("gA"), "gA", assignee="wa")
            rec_b = await _mk_graph(rt.manager_for("gB"), "gB", assignee="wb")
            drive_topology.stash_merge(engine, "gA", ["gA", "gB"])
            await _drain(engine, rt)
            survivor = rt.manager_for("gA")
            assert await survivor.get_drive(rec_a.drive_id) is not None
            assert await survivor.get_drive(rec_b.drive_id) is not None
            assert rt.peek_manager("gB") is None
        finally:
            await rt.stop()
            store_a.close()
            store_b.close()

    async def test_merge_movement_failure_leaves_survivor_consistent(self, monkeypatch):
        engine, rt = _make()
        rec_a = await _mk_graph(rt.manager_for("gA"), "gA", assignee="wa")
        await _mk_graph(rt.manager_for("gB"), "gB", assignee="wb")
        # Force the destination import to fail mid-merge.
        from kohakuterrarium.terrarium.drive.memory import MemoryDriveRepository

        async def _boom(self, payload):
            raise RuntimeError("disk full")

        monkeypatch.setattr(MemoryDriveRepository, "import_rows", _boom)
        try:
            drive_topology.stash_merge(engine, "gA", ["gA", "gB"])
            with pytest.raises(RuntimeError, match="disk full"):
                await _drain(engine, rt)
            # The survivor's original repo/manager is untouched — its Drive is
            # still readable (rollback / no visible mutation, §6.6).
            monkeypatch.undo()
            assert await rt.manager_for("gA").get_drive(rec_a.drive_id) is not None
        finally:
            await rt.stop()


# ---------------------------------------------------------------------------
# split (§6.7)
# ---------------------------------------------------------------------------


class TestSplit:
    async def _split_setup(self, rt, engine, drives_factory):
        """Create Drives in parent gP, then simulate a gP -> {gP, gC} split."""
        drives = await drives_factory(rt.manager_for("gP"))
        # Post-split topology: gP keeps {w1} (largest by convention), gC gets {w2}.
        engine.add_graph("gP", {"w1"})
        engine.add_graph("gC", {"w2"})
        drive_topology.stash_split(engine, "gP", ["gP", "gC"])
        await _drain(engine, rt)
        return drives

    async def test_creature_scoped_follows_its_creature(self):
        engine, rt = _make()

        async def factory(mgr):
            return {
                "w2": await mgr.create_drive(
                    _creature_req("w2"), actor=ActorRef("creature", "w2"), graph_id="gP"
                )
            }

        drives = await self._split_setup(rt, engine, factory)
        try:
            # A creature-scoped Drive follows its creature into gC.
            assert await rt.manager_for("gC").get_drive(drives["w2"].drive_id)
            assert await rt.manager_for("gP").get_drive(drives["w2"].drive_id) is None
        finally:
            await rt.stop()

    async def test_assigned_graph_drive_follows_assignee(self):
        engine, rt = _make()

        async def factory(mgr):
            return {"d": await _mk_graph(mgr, "gP", assignee="w2")}

        drives = await self._split_setup(rt, engine, factory)
        try:
            moved = await rt.manager_for("gC").get_drive(drives["d"].drive_id)
            assert moved is not None and moved.scope_id == "gC"
            assert await rt.manager_for("gP").get_drive(drives["d"].drive_id) is None
        finally:
            await rt.stop()

    async def test_unassigned_largest_component_default(self):
        engine, rt = _make()

        async def factory(mgr):
            return {"d": await _mk_graph(mgr, "gP")}

        # gP keeps {w1, w3} (largest), gC gets {w2}.
        drives = await self._split_with(rt, engine, factory, gp={"w1", "w3"}, gc={"w2"})
        try:
            assert await rt.manager_for("gP").get_drive(drives["d"].drive_id)
            assert await rt.manager_for("gC").get_drive(drives["d"].drive_id) is None
        finally:
            await rt.stop()

    async def test_unassigned_anchor_policy(self):
        engine, rt = _make()

        async def factory(mgr):
            return {
                "d": await _mk_graph(
                    mgr, "gP", policy_options={"split_policy": "anchor:w2"}
                )
            }

        drives = await self._split_setup(rt, engine, factory)
        try:
            assert await rt.manager_for("gC").get_drive(drives["d"].drive_id)
        finally:
            await rt.stop()

    async def test_unassigned_orphan_stays_with_largest(self):
        engine, rt = _make()

        async def factory(mgr):
            return {
                "d": await _mk_graph(
                    mgr, "gP", policy_options={"split_policy": "orphan"}
                )
            }

        drives = await self._split_setup(rt, engine, factory)
        did = drives["d"].drive_id
        try:
            # The largest child is the sole canonical owner (v1 orphan scope) BUT
            # the Drive is explicitly orphaned: BLOCKED + reason, never silently
            # continued (§6.7 / §16 invariant 8).
            mgr = rt.manager_for("gP")
            placed = await mgr.get_drive(did)
            assert placed is not None
            assert placed.status is DriveStatus.BLOCKED
            assert (
                placed.status_reason == "split_orphaned: administrator action required"
            )
            # It is not in the other child, and no delivery was admitted.
            assert await rt.manager_for("gC").get_drive(did) is None
            deliveries = await mgr.list_deliveries(did)
            assert not any(d.state in ("admitted", "acknowledged") for d in deliveries)
        finally:
            await rt.stop()

    async def test_clone_mints_child_ids_with_lineage(self):
        engine, rt = _make()

        async def factory(mgr):
            return {
                "d": await _mk_graph(
                    mgr, "gP", policy_options={"split_policy": "clone"}
                )
            }

        drives = await self._split_setup(rt, engine, factory)
        original = drives["d"].drive_id
        try:
            # The original id survives in NO child (never duplicated, §6.7).
            assert await rt.manager_for("gP").get_drive(original) is None
            assert await rt.manager_for("gC").get_drive(original) is None
            # Each child holds a fresh clone carrying parent_drive_id lineage.
            for gid in ("gP", "gC"):
                drives_here = await rt.manager_for(gid).list_drives(_all_query())
                assert len(drives_here) == 1
                assert drives_here[0].metadata.get("parent_drive_id") == original
                assert drives_here[0].drive_id != original
        finally:
            await rt.stop()

    async def test_cross_graph_dependency_blocks(self):
        engine, rt = _make()

        async def factory(mgr):
            dep = await _mk_graph(mgr, "gP", assignee="w2")
            dependent = await _mk_graph(mgr, "gP", assignee="w1", deps=(dep.drive_id,))
            return {"dep": dep, "dependent": dependent}

        drives = await self._split_setup(rt, engine, factory)
        try:
            # dep followed w2 -> gC; dependent followed w1 -> gP; now cross-graph.
            dependent = await rt.manager_for("gP").get_drive(
                drives["dependent"].drive_id
            )
            assert dependent is not None
            assert dependent.status is DriveStatus.BLOCKED
            assert dependent.status_reason == "cross_graph_dependency"
        finally:
            await rt.stop()

    async def _split_with(self, rt, engine, factory, *, gp, gc):
        drives = await factory(rt.manager_for("gP"))
        engine.add_graph("gP", gp)
        engine.add_graph("gC", gc)
        drive_topology.stash_split(engine, "gP", ["gP", "gC"])
        await _drain(engine, rt)
        return drives


# ---------------------------------------------------------------------------
# per-graph durability (R1-41) + explicit provider store (R1-10)
# ---------------------------------------------------------------------------


class TestPerGraphDurability:
    async def _mixed(self, engine, rt, tmp_path):
        store_a = SessionStore(str(tmp_path / "a.kohakutr"), writer_lock=True)
        engine._session_stores["gA"] = store_a
        await rt.bind_graph_store("gA", store_a)  # persistent
        rt.manager_for("gB")  # ephemeral (no store)
        return store_a

    async def test_durability_reported_per_graph_and_mixed(self, tmp_path):
        engine, rt = _make()
        store_a = await self._mixed(engine, rt, tmp_path)
        try:
            assert rt.durability_for("gA") == "persistent"
            assert rt.durability_for("gB") == "ephemeral"
            # aggregate is an explicit "mixed", not the first graph's mode.
            assert rt.durability == "mixed"
        finally:
            await rt.stop()
            store_a.close()

    async def test_durability_mixed_regardless_of_insertion_order(self, tmp_path):
        engine, rt = _make()
        rt.manager_for("gB")  # ephemeral first
        store_a = SessionStore(str(tmp_path / "a.kohakutr"), writer_lock=True)
        engine._session_stores["gA"] = store_a
        try:
            await rt.bind_graph_store("gA", store_a)
            assert rt.durability == "mixed"
            assert rt.durability_for("gA") == "persistent"
            assert rt.durability_for("gB") == "ephemeral"
        finally:
            await rt.stop()
            store_a.close()


class _CountingProvider:
    """A single-repository provider that records how often it is consulted."""

    def __init__(self, repo):
        self._repo = repo
        self.calls = 0
        self._claimed = False

    def __call__(self, graph_id):
        self.calls += 1
        if self._claimed:
            return None
        self._claimed = True
        return self._repo


class TestExplicitProviderStore:
    async def test_provider_repo_survives_session_store_attach(self, tmp_path):
        from kohakuterrarium.terrarium.drive.memory import MemoryDriveRepository

        provider_repo = MemoryDriveRepository()
        engine = _FakeEngine()
        rt = DriveRuntime(
            engine,
            DriveRuntimeConfig(enabled=True),
            tuple(default_registrations()),
            store=provider_repo,
        )
        engine._drive_runtime = rt
        store_a = SessionStore(str(tmp_path / "a.kohakutr"), writer_lock=True)
        engine._session_stores["gA"] = store_a
        try:
            # An explicit provider repo must survive a session-store attach: the
            # presence check must not consume the single-store provider (R1-10).
            await rt.bind_graph_store("gA", store_a)
            assert rt.manager_for("gA").repository is provider_repo
        finally:
            await rt.stop()
            store_a.close()

    async def test_stateful_provider_called_once_per_graph(self, tmp_path):
        from kohakuterrarium.terrarium.drive.memory import MemoryDriveRepository

        provider = _CountingProvider(MemoryDriveRepository())
        engine = _FakeEngine()
        rt = DriveRuntime(
            engine,
            DriveRuntimeConfig(enabled=True),
            tuple(default_registrations()),
            store=provider,
        )
        engine._drive_runtime = rt
        store_a = SessionStore(str(tmp_path / "a.kohakutr"), writer_lock=True)
        engine._session_stores["gA"] = store_a
        try:
            await rt.bind_graph_store("gA", store_a)
            rt.manager_for("gA")
            rt.manager_for("gA")
            assert rt.manager_for("gA").repository is provider._repo
            # The single-store provider was claimed once, not re-consumed.
            assert provider.calls == 1
        finally:
            await rt.stop()
            store_a.close()

    async def test_merge_preserves_provider_destination_and_does_not_close_it(
        self, tmp_path
    ):
        # R1-10: a merge uses the shared destination resolver, so a provider-backed
        # survivor keeps its provider repo (no replace by the session sidecar) and
        # the provider-owned repo is never closed by the mover.
        from kohakuterrarium.terrarium.drive.store import SqliteDriveRepository

        provider_repo = SqliteDriveRepository(str(tmp_path / "prov.drives"))
        closes = {"n": 0}
        real_close = provider_repo.close_blocking

        def counting_close():
            closes["n"] += 1
            return real_close()

        provider_repo.close_blocking = counting_close
        engine = _FakeEngine()
        rt = DriveRuntime(
            engine,
            DriveRuntimeConfig(enabled=True),
            tuple(default_registrations()),
            store=provider_repo,
        )
        engine._drive_runtime = rt
        engine.add_graph("gA", {"wa"})
        try:
            rec_a = await _mk_graph(rt.manager_for("gA"), "gA", assignee="wa")
            rec_b = await _mk_graph(rt.manager_for("gB"), "gB", assignee="wb")
            drive_topology.stash_merge(engine, "gA", ["gA", "gB"])
            await _drain(engine, rt)
            survivor = rt.manager_for("gA")
            assert survivor.repository is provider_repo  # provider destination kept
            assert await survivor.get_drive(rec_a.drive_id) is not None
            assert await survivor.get_drive(rec_b.drive_id) is not None
            assert closes["n"] == 0  # provider-owned repo not closed by the mover
        finally:
            await rt.stop()
            provider_repo.close_blocking = real_close
            provider_repo.close_blocking()

    async def test_split_uses_provider_destination_repos(self):
        # R1-10: a split routes each child through the SAME provider-aware resolver,
        # so a child lands in the provider factory's repo, not a default memory one.
        from kohakuterrarium.terrarium.drive.memory import MemoryDriveRepository

        repos: dict[str, MemoryDriveRepository] = {}

        def factory(gid):
            return repos.setdefault(gid, MemoryDriveRepository())

        engine = _FakeEngine()
        rt = DriveRuntime(
            engine,
            DriveRuntimeConfig(enabled=True),
            tuple(default_registrations()),
            store=factory,
        )
        engine._drive_runtime = rt

        async def make(mgr):
            actor = ActorRef("creature", "w2")
            return {
                "rec": await mgr.create_drive(
                    _creature_req("w2"), actor=actor, graph_id="gP"
                )
            }

        drives = await TestSplit()._split_setup(rt, engine, make)
        try:
            child = rt.manager_for("gC")
            assert child.repository is repos["gC"]  # provider factory's gC repo
            assert await child.get_drive(drives["rec"].drive_id) is not None
        finally:
            await rt.stop()

    async def test_split_single_store_provider_retained_child_prunes_moved_drives(self):
        # R1-10/R1-11: a SINGLE-store provider is claimed by the parent gP; on split
        # gP is the RETAINED child and resolves to that SAME provider repo
        # (same_repo), which still holds EVERY parent row. An upsert-only import
        # would leave a Drive that moved to gC duplicated in BOTH repos. Assert
        # every Drive — and every per-drive row — ends up in EXACTLY one child,
        # ABSENT from the retained parent repo, and the graph-global idempotency
        # ledger is replicated to both.
        from dataclasses import replace as _replace

        from kohakuterrarium.terrarium.drive.memory import MemoryDriveRepository

        provider_repo = MemoryDriveRepository()
        engine = _FakeEngine()
        rt = DriveRuntime(
            engine,
            DriveRuntimeConfig(enabled=True),
            tuple(default_registrations()),
            store=provider_repo,
        )
        engine._drive_runtime = rt

        mgr = rt.manager_for("gP")  # the single-store provider is claimed by gP
        assert mgr.repository is provider_repo
        w1, w2 = ActorRef("creature", "w1"), ActorRef("creature", "w2")
        stay = await mgr.create_drive(_creature_req("w1"), actor=w1, graph_id="gP")
        move = await mgr.create_drive(
            _replace(_creature_req("w2"), idempotency_key="idem-move"),
            actor=w2,
            graph_id="gP",
        )
        # Per-drive side rows on BOTH Drives so cardinality spans every table.
        # create_drive already enqueued one activation delivery per Drive; add a
        # progress row + a pending proposal so every per-drive table is populated.
        await provider_repo.report_progress(
            move.drive_id, summary="p", evidence=None, actor=w2
        )
        await _add_proposal(mgr, stay.drive_id, proposal_id="prop-stay")
        await _add_proposal(mgr, move.drive_id, proposal_id="prop-move")

        engine.add_graph("gP", {"w1"})  # gP keeps w1 (retained), gC takes w2
        engine.add_graph("gC", {"w2"})
        drive_topology.stash_split(engine, "gP", ["gP", "gC"])
        await _drain(engine, rt)
        try:
            gp = rt.manager_for("gP").repository
            gc = rt.manager_for("gC").repository
            assert gp is provider_repo  # retained child kept the provider repo
            assert gc is not provider_repo

            q = DriveQuery(include_terminal=True)
            gp_ids = {r.drive_id for r in await gp.list_drives(q)}
            gc_ids = {r.drive_id for r in await gc.list_drives(q)}
            # The moved Drive is ABSENT from the retained parent repo (the defect),
            # and every Drive appears in EXACTLY one child.
            assert move.drive_id not in gp_ids
            assert gp_ids == {stay.drive_id}
            assert gc_ids == {move.drive_id}
            assert gp_ids & gc_ids == set()

            # assignments follow their Drive, present in exactly one child.
            assert await gp.get_assignment(move.drive_id) is None
            assert await gc.get_assignment(move.drive_id) is not None
            assert await gp.get_assignment(stay.drive_id) is not None
            assert await gc.get_assignment(stay.drive_id) is None

            # deliveries / progress / audit for the moved Drive: only in gC.
            assert await gp.list_deliveries(move.drive_id) == ()
            assert len(await gc.list_deliveries(move.drive_id)) == 1
            assert await gp.list_progress(move.drive_id) == ()
            assert len(await gc.list_progress(move.drive_id)) == 1
            assert await gp.list_audit(move.drive_id) == ()
            assert len(await gc.list_audit(move.drive_id)) >= 1
            # the staying Drive's delivery is only in gP.
            assert len(await gp.list_deliveries(stay.drive_id)) == 1
            assert await gc.list_deliveries(stay.drive_id) == ()

            # proposals: each in exactly one child, none stranded in the parent.
            gp_props = {p.proposal_id for p in await gp.list_proposals()}
            gc_props = {p.proposal_id for p in await gc.list_proposals()}
            assert gp_props == {"prop-stay"}
            assert gc_props == {"prop-move"}

            # idempotency is graph-global → replicated to BOTH children (R1-11).
            gp_idem = (await gp.export_rows())["idempotency"]
            gc_idem = (await gc.export_rows())["idempotency"]
            assert len(gp_idem) == 1 and len(gc_idem) == 1
        finally:
            await rt.stop()


# ---------------------------------------------------------------------------
# idempotency + append-only history across topology movement (R1-11)
# ---------------------------------------------------------------------------


class TestIdempotencyAcrossMovement:
    async def test_idempotency_survives_merge_no_second_audit(self):
        engine, rt = _make()
        engine.add_graph("gA", {"wa"})
        req = _graph_req("gB", assignee="wb")
        from dataclasses import replace as _replace

        keyed = _replace(req, idempotency_key="k-move")
        rec_b = await rt.manager_for("gB").create_drive(
            keyed, actor=USER, graph_id="gB", is_privileged=True
        )
        await _mk_graph(rt.manager_for("gA"), "gA", assignee="wa")
        try:
            drive_topology.stash_merge(engine, "gA", ["gA", "gB"])
            await _drain(engine, rt)
            survivor = rt.manager_for("gA")
            audit_before = len(await survivor.repository.list_audit(rec_b.drive_id))
            # replaying the SAME key + SAME operation on the survivor returns the
            # original result and writes no second audit (ledger moved, R1-11).
            replayed = await survivor.create_drive(
                keyed, actor=USER, graph_id="gA", is_privileged=True
            )
            assert replayed.drive_id == rec_b.drive_id
            assert (
                len(await survivor.repository.list_audit(rec_b.drive_id))
                == audit_before
            )
        finally:
            await rt.stop()

    async def test_survivor_history_not_duplicated_on_persistent_merge(self, tmp_path):
        engine, rt = _make()
        store_a = SessionStore(str(tmp_path / "a.kohakutr"), writer_lock=True)
        store_b = SessionStore(str(tmp_path / "b.kohakutr"), writer_lock=True)
        engine._session_stores["gA"] = store_a
        engine._session_stores["gB"] = store_b
        try:
            await rt.bind_graph_store("gA", store_a)
            await rt.bind_graph_store("gB", store_b)
            rec_a = await _mk_graph(rt.manager_for("gA"), "gA", assignee="wa")
            await rt.manager_for("gA").report_progress(
                rec_a.drive_id, summary="p1", evidence={}, actor=USER
            )
            await _mk_graph(rt.manager_for("gB"), "gB", assignee="wb")
            audit_before = len(
                await rt.manager_for("gA").repository.list_audit(rec_a.drive_id)
            )
            progress_before = len(
                await rt.manager_for("gA").list_progress(rec_a.drive_id)
            )
            drive_topology.stash_merge(engine, "gA", ["gA", "gB"])
            await _drain(engine, rt)
            survivor = rt.manager_for("gA")
            # the persistent survivor's own append-only history is not
            # re-imported into its existing sidecar (R1-11 duplication).
            assert (
                len(await survivor.repository.list_audit(rec_a.drive_id))
                == audit_before
            )
            assert len(await survivor.list_progress(rec_a.drive_id)) == progress_before
        finally:
            await rt.stop()
            store_a.close()
            store_b.close()

    async def test_idempotency_replicated_to_children_on_split(self):
        # R1-11: the (actor, key) idempotency ledger is graph-global, so a split
        # replicates it to every child — a retry of a pre-split mutation still
        # returns the original result in the child where its Drive landed.
        from dataclasses import replace as _replace

        engine, rt = _make()
        keyed = _replace(_graph_req("gP", assignee="w2"), idempotency_key="k-split")

        async def factory(mgr):
            return {
                "rec": await mgr.create_drive(
                    keyed, actor=USER, graph_id="gP", is_privileged=True
                )
            }

        drives = await TestSplit()._split_setup(rt, engine, factory)
        try:
            child = rt.manager_for("gC")  # the assigned Drive follows w2 into gC
            rec = drives["rec"]
            assert await child.get_drive(rec.drive_id) is not None
            audit_before = len(await child.repository.list_audit(rec.drive_id))
            replayed = await child.create_drive(
                keyed, actor=USER, graph_id="gC", is_privileged=True
            )
            assert replayed.drive_id == rec.drive_id  # original result, no new drive
            assert len(await child.repository.list_audit(rec.drive_id)) == audit_before
        finally:
            await rt.stop()

    async def test_idempotency_collision_on_merge_is_conflict(self):
        # R1-11: the same (actor, key) reused for a DIFFERENT operation across the
        # two merged graphs is a transactional conflict, never a silent overwrite.
        from dataclasses import replace as _replace

        engine, rt = _make()
        engine.add_graph("gA", {"wa"})
        a = _replace(_graph_req("gA", assignee="wa"), idempotency_key="dup", title="A")
        b = _replace(_graph_req("gB", assignee="wb"), idempotency_key="dup", title="B")
        await rt.manager_for("gA").create_drive(
            a, actor=USER, graph_id="gA", is_privileged=True
        )
        await rt.manager_for("gB").create_drive(
            b, actor=USER, graph_id="gB", is_privileged=True
        )
        try:
            drive_topology.stash_merge(engine, "gA", ["gA", "gB"])
            with pytest.raises(DriveIdempotencyConflictError):
                await _drain(engine, rt)
        finally:
            await rt.stop()

    async def test_repeated_persistent_merges_do_not_multiply_history(self, tmp_path):
        # R1-11: merging repeatedly into the same persistent survivor must not
        # duplicate its own append-only audit/progress each time.
        engine, rt = _make()
        stores = {}
        for g in ("gA", "gB", "gC"):
            s = SessionStore(str(tmp_path / f"{g}.kohakutr"), writer_lock=True)
            engine._session_stores[g] = s
            stores[g] = s
        try:
            for g in ("gA", "gB", "gC"):
                await rt.bind_graph_store(g, stores[g])
            rec_a = await _mk_graph(rt.manager_for("gA"), "gA", assignee="wa")
            await rt.manager_for("gA").report_progress(
                rec_a.drive_id, summary="p1", evidence={}, actor=USER
            )
            await _mk_graph(rt.manager_for("gB"), "gB", assignee="wb")
            await _mk_graph(rt.manager_for("gC"), "gC", assignee="wc")
            audit_before = len(
                await rt.manager_for("gA").repository.list_audit(rec_a.drive_id)
            )
            progress_before = len(
                await rt.manager_for("gA").list_progress(rec_a.drive_id)
            )
            drive_topology.stash_merge(engine, "gA", ["gA", "gB"])
            await _drain(engine, rt)
            drive_topology.stash_merge(engine, "gA", ["gA", "gC"])
            await _drain(engine, rt)
            survivor = rt.manager_for("gA")
            assert (
                len(await survivor.repository.list_audit(rec_a.drive_id))
                == audit_before
            )
            assert len(await survivor.list_progress(rec_a.drive_id)) == progress_before
        finally:
            await rt.stop()
            for s in stores.values():
                s.close()


# ---------------------------------------------------------------------------
# pending terminal proposals across topology movement (R1-08)
# ---------------------------------------------------------------------------


class TestProposalMovement:
    async def test_merge_preserves_pending_proposal(self):
        engine, rt = _make()
        engine.add_graph("gA", {"wa"})
        rec_a = await _mk_graph(rt.manager_for("gA"), "gA", assignee="wa")
        rec_b = await _mk_graph(rt.manager_for("gB"), "gB", assignee="wb")
        await _add_proposal(rt.manager_for("gB"), rec_b.drive_id, proposal_id="p-b")
        try:
            drive_topology.stash_merge(engine, "gA", ["gA", "gB"])
            await _drain(engine, rt)
            survivor = rt.manager_for("gA")
            got = await survivor.repository.get_proposal("p-b")
            assert got is not None and got.drive_id == rec_b.drive_id
            # the survivor's own drive is intact alongside the moved proposal.
            assert await survivor.get_drive(rec_a.drive_id) is not None
        finally:
            await rt.stop()

    async def test_split_carries_proposal_with_its_drive(self):
        engine, rt = _make()

        async def factory(mgr):
            actor = ActorRef("creature", "w2")
            rec = await mgr.create_drive(
                _creature_req("w2"), actor=actor, graph_id="gP"
            )
            await _add_proposal(mgr, rec.drive_id, proposal_id="p-split")
            return {"w2": rec}

        drives = await TestSplit()._split_setup(rt, engine, factory)
        try:
            # w2 (and its creature-scoped Drive) follows into child gC; the pending
            # proposal must travel with it, not stay behind in gP (R1-08).
            child = rt.manager_for("gC")
            got = await child.repository.get_proposal("p-split")
            assert got is not None and got.drive_id == drives["w2"].drive_id
            assert await rt.manager_for("gP").repository.get_proposal("p-split") is None
        finally:
            await rt.stop()

    async def test_merge_proposal_id_collision_is_conflict(self):
        engine, rt = _make()
        engine.add_graph("gA", {"wa"})
        rec_a = await _mk_graph(rt.manager_for("gA"), "gA", assignee="wa")
        rec_b = await _mk_graph(rt.manager_for("gB"), "gB", assignee="wb")
        # Same proposal id on DIFFERENT Drives across the two merged graphs.
        await _add_proposal(rt.manager_for("gA"), rec_a.drive_id, proposal_id="dup")
        await _add_proposal(rt.manager_for("gB"), rec_b.drive_id, proposal_id="dup")
        try:
            drive_topology.stash_merge(engine, "gA", ["gA", "gB"])
            with pytest.raises(DriveProposalConflictError):
                await _drain(engine, rt)
        finally:
            await rt.stop()


# ---------------------------------------------------------------------------
# reconcile revalidates the assignment against live topology (R1-06)
# ---------------------------------------------------------------------------


class TestReconcileTopologyValidation:
    async def test_reconcile_orphans_assignee_not_in_live_graph(self):
        engine, rt = _make()
        engine.add_graph("g1", {"wa"})  # live members: only "wa"
        mgr = rt.manager_for("g1")
        # A persisted graph Drive assigned to a creature NOT in the live graph:
        # the registry-built validator rejects it and reconcile orphans+blocks it.
        rec = await _mk_graph(mgr, "g1", assignee="ghost")
        try:
            await mgr.reconcile(graph_id="g1")
            drive = await mgr.get_drive(rec.drive_id)
            assert drive.status is DriveStatus.BLOCKED
            assert drive.status_reason == "reconcile_invalid_topology"
        finally:
            await rt.stop()

    async def test_reconcile_leaves_valid_member_assignee(self):
        engine, rt = _make()
        engine.add_graph("g1", {"wa"})
        mgr = rt.manager_for("g1")
        rec = await _mk_graph(mgr, "g1", assignee="wa")  # a live member
        try:
            await mgr.reconcile(graph_id="g1")
            drive = await mgr.get_drive(rec.drive_id)
            assert drive.status is DriveStatus.ACTIVE  # not orphaned
        finally:
            await rt.stop()


# ---------------------------------------------------------------------------
# resume assignee remap after creature-id re-mint (R1-43)
# ---------------------------------------------------------------------------


class TestResumeAssigneeRemap:
    async def test_stale_graph_assignee_remapped_to_resumed_creature(self):
        engine, rt = _make()
        engine.add_graph("g1", {"wa_9f9f9f9f"})  # resumed creature, fresh id
        engine._creatures["wa_9f9f9f9f"].name = "wa"
        mgr = rt.manager_for("g1")
        # a persisted Drive still names the OLD runtime id.
        rec = await _mk_graph(mgr, "g1", assignee="wa_11111111")
        try:
            await rt._remap_resumed_assignees("g1")
            assignment = await mgr.get_assignment(rec.drive_id)
            assert assignment.assignee_creature_id == "wa_9f9f9f9f"
            drive = await mgr.get_drive(rec.drive_id)
            assert drive.status is DriveStatus.ACTIVE  # remapped, still deliverable
        finally:
            await rt.stop()

    async def test_creature_scoped_scope_id_rebound(self):
        engine, rt = _make()
        engine.add_graph("g1", {"wa_9f9f9f9f"})
        engine._creatures["wa_9f9f9f9f"].name = "wa"
        mgr = rt.manager_for("g1")
        actor = ActorRef("creature", "wa_11111111")
        rec = await mgr.create_drive(
            CreateDriveRequest(
                kind="generic",
                title="w",
                scope_type="creature",
                scope_id="wa_11111111",
                owner=actor,
                owner_scope="creature",
                created_by=actor,
            ),
            actor=actor,
            graph_id="g1",
        )
        try:
            await rt._remap_resumed_assignees("g1")
            drive = await mgr.get_drive(rec.drive_id)
            assignment = await mgr.get_assignment(rec.drive_id)
            assert drive.scope_id == "wa_9f9f9f9f"
            assert assignment.assignee_creature_id == "wa_9f9f9f9f"
        finally:
            await rt.stop()

    async def test_ambiguous_assignee_is_orphaned_not_silent(self):
        engine, rt = _make()
        engine.add_graph("g1", {"wa_aaaaaaaa", "wa_bbbbbbbb"})  # two "wa"
        engine._creatures["wa_aaaaaaaa"].name = "wa"
        engine._creatures["wa_bbbbbbbb"].name = "wa"
        mgr = rt.manager_for("g1")
        rec = await _mk_graph(mgr, "g1", assignee="wa_11111111")
        try:
            await rt._remap_resumed_assignees("g1")
            drive = await mgr.get_drive(rec.drive_id)
            assignment = await mgr.get_assignment(rec.drive_id)
            assert drive.status is DriveStatus.BLOCKED
            assert drive.status_reason == "resume_unresolved_assignee"
            assert assignment.assignment_state == "orphaned"
        finally:
            await rt.stop()

    async def test_missing_assignee_is_orphaned(self):
        engine, rt = _make()
        engine.add_graph("g1", {"other_cccccccc"})  # no "wa" resumed
        engine._creatures["other_cccccccc"].name = "other"
        mgr = rt.manager_for("g1")
        rec = await _mk_graph(mgr, "g1", assignee="wa_11111111")
        try:
            await rt._remap_resumed_assignees("g1")
            drive = await mgr.get_drive(rec.drive_id)
            assert drive.status is DriveStatus.BLOCKED
        finally:
            await rt.stop()

    async def test_current_assignee_is_left_alone(self):
        engine, rt = _make()
        engine.add_graph("g1", {"wa_9f9f9f9f"})
        engine._creatures["wa_9f9f9f9f"].name = "wa"
        mgr = rt.manager_for("g1")
        rec = await _mk_graph(mgr, "g1", assignee="wa_9f9f9f9f")
        before = await mgr.get_drive(rec.drive_id)
        try:
            await rt._remap_resumed_assignees("g1")
            after = await mgr.get_drive(rec.drive_id)
            # a live assignee is not stale: no spurious revision bump / remap.
            assert after.revision == before.revision
            assert (
                await mgr.get_assignment(rec.drive_id)
            ).assignee_creature_id == "wa_9f9f9f9f"
        finally:
            await rt.stop()


# ---------------------------------------------------------------------------
# fork (§6.8)
# ---------------------------------------------------------------------------


class TestForkContract:
    def test_fork_carries_no_drives_by_default(self):
        assert drive_topology.fork_carries_no_drives() is True

    def test_explicit_drive_fork_is_unsupported(self):
        with pytest.raises(DriveError, match="not supported"):
            drive_topology.clone_drives_for_fork()

    async def test_forked_session_opens_with_zero_drives(self, tmp_path):
        from kohakuterrarium.terrarium.drive.requests import DriveQuery
        from kohakuterrarium.terrarium.drive.store import (
            open_session_drive_repository,
        )

        parent = SessionStore(str(tmp_path / "parent.kohakutr"), writer_lock=True)
        parent.init_meta("parent", "agent", "/p", str(tmp_path), ["alice"])
        parent.append_event("alice", "user_message", {"content": "hi"})
        parent.flush()
        engine, rt = _make()
        engine._session_stores["gA"] = parent
        try:
            await rt.bind_graph_store("gA", parent)
            rec = await _mk_graph(rt.manager_for("gA"), "gA", assignee="wa")
            assert await rt.manager_for("gA").get_drive(rec.drive_id) is not None
            child = parent.fork(str(tmp_path / "child.kohakutr"), at_event_id=1)
            try:
                # The forked conversation carries ZERO active Drives (§6.8).
                child_repo = open_session_drive_repository(child)
                assert await child_repo.list_drives(DriveQuery()) == ()
                child_repo.close_blocking()
            finally:
                child.close(update_status=False)
        finally:
            await rt.stop()
            parent.close()


def _all_query():
    from kohakuterrarium.terrarium.drive.requests import DriveQuery

    return DriveQuery()
