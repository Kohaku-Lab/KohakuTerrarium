"""Unit tests for B1 — multi-node cluster fold in
:mod:`kohakuterrarium.studio.sessions.lifecycle`.

After a cross-node connect records a ``_cluster_links`` entry on the
``MultiNodeTerrariumService``, the studio sessions layer MUST collapse
the two per-spawn ``_meta`` listings into ONE cluster listing
addressed by the lex-smallest sid. ``get_session`` must likewise
return a Session that unions creatures across cluster members so the
frontend can render a single chat tab for the cluster.
"""

from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.sessions import lifecycle


class _FakeMultiNodeService:
    """Minimal stand-in for ``MultiNodeTerrariumService``."""

    def __init__(
        self,
        cluster_links: set[frozenset[tuple[str, str]]],
        connected: set[str] | None = None,
    ):
        self._cluster_links = cluster_links
        self._connected = connected or set()

    def connected_nodes(self) -> set[str]:
        return set(self._connected)


class TestClusterGroups:
    def test_empty_when_no_links(self):
        svc = _FakeMultiNodeService(set())
        assert lifecycle._cluster_groups(svc) == {}

    def test_two_member_cluster(self):
        svc = _FakeMultiNodeService({frozenset({("w1", "graph_a"), ("w2", "graph_b")})})
        groups = lifecycle._cluster_groups(svc)
        primary = min(["graph_a", "graph_b"])
        assert primary in groups
        assert groups[primary] == {"graph_a", "graph_b"}

    def test_transitive_three_node_cluster(self):
        svc = _FakeMultiNodeService(
            {
                frozenset({("w1", "graph_a"), ("w2", "graph_b")}),
                frozenset({("w2", "graph_b"), ("w3", "graph_c")}),
            }
        )
        groups = lifecycle._cluster_groups(svc)
        assert sum(len(m) for m in groups.values()) == 3
        assert any(m == {"graph_a", "graph_b", "graph_c"} for m in groups.values())


class TestFoldSessionListings:
    def test_collapses_pair_to_primary(self):
        svc = _FakeMultiNodeService({frozenset({("w1", "graph_a"), ("w2", "graph_b")})})
        listings = [
            lifecycle.SessionListing(
                session_id="graph_a", name="alpha", creatures=1, node_id="w1"
            ),
            lifecycle.SessionListing(
                session_id="graph_b", name="bravo", creatures=1, node_id="w2"
            ),
        ]
        folded = lifecycle._fold_session_listings(listings, svc)
        assert len(folded) == 1
        primary = min(["graph_a", "graph_b"])
        assert folded[0].session_id == primary
        assert folded[0].creatures == 2

    def test_passthrough_for_non_clustered(self):
        svc = _FakeMultiNodeService(set())
        listings = [
            lifecycle.SessionListing(
                session_id="solo", name="solo", creatures=1, node_id="w1"
            )
        ]
        folded = lifecycle._fold_session_listings(listings, svc)
        assert folded == listings

    def test_mixed_clustered_and_solo(self):
        svc = _FakeMultiNodeService({frozenset({("w1", "ga"), ("w2", "gb")})})
        listings = [
            lifecycle.SessionListing(
                session_id="ga", name="alpha", creatures=1, node_id="w1"
            ),
            lifecycle.SessionListing(
                session_id="gb", name="bravo", creatures=1, node_id="w2"
            ),
            lifecycle.SessionListing(
                session_id="solo", name="solo", creatures=1, node_id="w3"
            ),
        ]
        folded = lifecycle._fold_session_listings(listings, svc)
        assert len(folded) == 2
        sids = {f.session_id for f in folded}
        assert "solo" in sids
        assert min(["ga", "gb"]) in sids


class TestFoldSessionCreatures:
    def test_unions_members_with_distinct_home_nodes(self, monkeypatch):
        svc = _FakeMultiNodeService({frozenset({("w1", "ga"), ("w2", "gb")})})
        monkeypatch.setattr(
            lifecycle,
            "_meta",
            {
                "ga": {
                    "name": "alpha",
                    "on_node": "w1",
                    "creature_id": "alpha_cid",
                },
                "gb": {
                    "name": "bravo",
                    "on_node": "w2",
                    "creature_id": "bravo_cid",
                },
            },
        )
        primary = min(["ga", "gb"])
        creatures = lifecycle._fold_session_creatures(svc, primary)
        assert creatures is not None
        cids = {c["creature_id"] for c in creatures}
        assert cids == {"alpha_cid", "bravo_cid"}
        # home_node preserved per-creature (NOT collapsed to one node).
        homes = {c["home_node"] for c in creatures}
        assert homes == {"w1", "w2"}

    def test_returns_none_for_non_cluster(self):
        svc = _FakeMultiNodeService(set())
        assert lifecycle._fold_session_creatures(svc, "solo") is None

    def test_live_creatures_supersede_stale_meta(self, monkeypatch):
        """CF-12: a freshly spawned cluster peer may not yet have its
        ``_meta`` row populated on the host. When the caller supplies a
        live ``service.list_creatures()`` roster, the fold MUST surface
        every live cluster member, not just the ones whose ``_meta``
        rows have already been written."""
        svc = _FakeMultiNodeService({frozenset({("w1", "ga"), ("w2", "gb")})})
        # Only one member's _meta row exists (race window after spawn).
        monkeypatch.setattr(
            lifecycle,
            "_meta",
            {
                "ga": {
                    "name": "alpha",
                    "on_node": "w1",
                    "creature_id": "alpha_cid",
                },
                # "gb" intentionally absent — host hasn't cached it yet.
            },
        )
        primary = min(["ga", "gb"])
        # Without live data, the fold sees only alpha (CF-12 reproducer).
        only_meta = lifecycle._fold_session_creatures(svc, primary)
        assert only_meta is not None
        assert {c["creature_id"] for c in only_meta} == {"alpha_cid"}

        # With live data, the fold surfaces bravo too.
        live = [
            {
                "creature_id": "bravo_cid",
                "name": "bravo",
                "home_node": "w2",
                "running": True,
                "is_privileged": False,
            },
        ]
        out = lifecycle._fold_session_creatures(svc, primary, live_creatures=live)
        assert out is not None
        cids = {c["creature_id"] for c in out}
        assert cids == {"alpha_cid", "bravo_cid"}
        # Live entry preserved its home_node — not collapsed.
        homes = {c["home_node"] for c in out}
        assert homes == {"w1", "w2"}


class TestGetSessionClusterFold:
    def test_primary_returns_all_creatures(self, monkeypatch):
        svc = _FakeMultiNodeService({frozenset({("w1", "ga"), ("w2", "gb")})})
        monkeypatch.setattr(
            lifecycle,
            "_meta",
            {
                "ga": {
                    "name": "alpha",
                    "on_node": "w1",
                    "creature_id": "alpha_cid",
                    "pwd": "",
                    "created_at": "",
                    "config_path": "",
                },
                "gb": {
                    "name": "bravo",
                    "on_node": "w2",
                    "creature_id": "bravo_cid",
                    "pwd": "",
                    "created_at": "",
                    "config_path": "",
                },
            },
        )
        primary = min(["ga", "gb"])
        sess = lifecycle.get_session(svc, primary)
        cids = {c["creature_id"] for c in sess.creatures}
        assert cids == {"alpha_cid", "bravo_cid"}
        assert sess.session_id == primary

    def test_non_primary_member_redirects_to_primary(self, monkeypatch):
        svc = _FakeMultiNodeService({frozenset({("w1", "ga"), ("w2", "gb")})})
        primary, secondary = sorted(["ga", "gb"])
        monkeypatch.setattr(
            lifecycle,
            "_meta",
            {
                primary: {
                    "name": "alpha",
                    "on_node": "w1",
                    "creature_id": "alpha_cid",
                    "pwd": "",
                    "created_at": "",
                    "config_path": "",
                },
                # secondary _meta intentionally absent (worker pruned).
            },
        )
        sess = lifecycle.get_session(svc, secondary)
        assert sess.session_id == primary


class TestPersistClusterMembersToMirror:
    """CF-6 — persistence helper writes cluster_members to each member's
    mirror SessionStore meta so a subsequent resume can rebuild the
    cluster. Without this the cluster session resumes as a singleton.
    """

    def test_persists_two_member_cluster_into_every_mirror(self, monkeypatch, tmp_path):
        # Point the lifecycle module at a temp session dir so the
        # mirror path is fully under our control.
        monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path))
        mirror_dir = tmp_path / "mirror"
        mirror_dir.mkdir(parents=True)
        # Seed mirror files (the resume route reads cluster_members
        # off these).  Pre-populate each meta minimally so the open
        # succeeds without surprise side effects.
        for sid in ("ga", "gb"):
            store = SessionStore(mirror_dir / f"{sid}.kohakutr")
            store.meta["session_id"] = sid
            store.close()

        svc = _FakeMultiNodeService({frozenset({("w1", "ga"), ("w2", "gb")})})
        lifecycle._persist_cluster_members_to_mirror(svc, "ga")

        # Behavior: BOTH members carry the cluster_members payload,
        # not just the one we called the helper on. The resume route
        # reads it off whichever member the user opens — both must be
        # populated symmetrically.
        for sid in ("ga", "gb"):
            store = SessionStore(mirror_dir / f"{sid}.kohakutr")
            try:
                members = store.meta.get("cluster_members")
            finally:
                store.close()
            assert isinstance(members, list)
            recorded = {(m["sid"], m["on_node"]) for m in members}
            assert recorded == {("ga", "w1"), ("gb", "w2")}

    def test_no_persistence_when_session_not_clustered(self, monkeypatch, tmp_path):
        monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path))
        mirror_dir = tmp_path / "mirror"
        mirror_dir.mkdir(parents=True)
        store = SessionStore(mirror_dir / "solo.kohakutr")
        store.meta["session_id"] = "solo"
        store.close()

        # No links at all — helper must be a no-op.
        svc = _FakeMultiNodeService(set())
        lifecycle._persist_cluster_members_to_mirror(svc, "solo")
        store = SessionStore(mirror_dir / "solo.kohakutr")
        try:
            assert store.meta.get("cluster_members") is None
        finally:
            store.close()


class TestListSessionsClusterFold:
    def test_cross_node_cluster_folds_into_one_listing(self, monkeypatch):
        svc = _FakeMultiNodeService(
            {frozenset({("w1", "ga"), ("w2", "gb")})},
            connected={"w1", "w2"},
        )
        monkeypatch.setattr(
            lifecycle,
            "_meta",
            {
                "ga": {
                    "name": "alpha",
                    "on_node": "w1",
                    "creature_id": "alpha_cid",
                },
                "gb": {
                    "name": "bravo",
                    "on_node": "w2",
                    "creature_id": "bravo_cid",
                },
            },
        )
        listings = lifecycle.list_sessions(svc)
        # B1 invariant: ONE listing covers both members.
        assert len(listings) == 1
        primary = min(["ga", "gb"])
        assert listings[0].session_id == primary
        assert listings[0].creatures == 2
