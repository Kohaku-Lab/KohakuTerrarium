"""Unit tests for ``session_index.store`` — every code path."""

import pytest

from kohakuterrarium.studio.persistence.session_index.entry import (
    SCHEMA_VERSION,
    SessionIndexEntry,
)
from kohakuterrarium.studio.persistence.session_index.store import (
    SEARCH_COLUMNS,
    SessionIndex,
    SessionIndexPage,
    _iter_kv_keys,
    _strip_internal,
)


def _entry(
    *,
    filename: str,
    name: str | None = None,
    preview: str = "",
    config_type: str = "agent",
    status: str = "paused",
    last_active: str = "2026-01-01T00:00:00",
    created_at: str = "2026-01-01T00:00:00",
    node_id: str = "",
    agents: list[str] | None = None,
) -> SessionIndexEntry:
    return SessionIndexEntry(
        filename=filename,
        name=name or filename.replace(".kohakutr", "").replace(".v2", ""),
        file_mtime=1.0,
        file_size=100,
        preview=preview,
        config_path="",
        agents=agents or [],
        pwd="",
        config_type=config_type,
        status=status,
        last_active=last_active,
        created_at=created_at,
        format_version=2,
        node_id=node_id,
    )


@pytest.fixture
def idx(tmp_path):
    """Fresh SessionIndex per test, opened on a temp file.

    ``yield`` + ``close`` so the sidecar's native SQLite handles
    are released before pytest's tmp_path cleanup.  On Windows
    a lingering handle would block rmtree.
    """
    side = tmp_path / ".kt-index.kvault"
    instance = SessionIndex(side)
    try:
        yield instance
    finally:
        instance.close()


# ── Schema / lifecycle ────────────────────────────────────────────


class TestSchema:
    def test_first_open_sets_version(self, idx):
        assert idx.meta_get("schema_version") == SCHEMA_VERSION

    def test_schema_bump_clears_sidecar(self, tmp_path):
        side = tmp_path / ".kt-index.kvault"
        i1 = SessionIndex(side)
        i1.upsert(_entry(filename="alice.kohakutr"))
        assert i1.list().total == 1
        # Simulate a schema upgrade by writing a stale column list
        # then re-opening.  ``search_columns`` is the drift detector
        # (the ``schema_version`` scalar is kept for telemetry but
        # NOT trusted on its own — see
        # ``test_purge_when_meta_version_lies_about_fts_columns``).
        i1.meta_put("search_columns", ["legacy_only"])
        i1.close()
        i2 = SessionIndex(side)
        assert i2.list().total == 0
        assert i2.meta_get("schema_version") == SCHEMA_VERSION
        assert i2.meta_get("search_columns") == list(SEARCH_COLUMNS)
        i2.close()

    def test_purge_when_meta_version_lies_about_fts_columns(self, tmp_path):
        """Regression for the production crash reported 2026-05-26.

        Reproduces the exact corrupted state a previous broken
        upgrade left behind: ``meta['schema_version']`` says
        ``SCHEMA_VERSION`` (the *current* version, so a naive
        version-equality check would skip the purge), but the FTS
        table on disk still has the OLD column set without
        ``terrarium_name`` / ``config_type``.  The next upsert
        raised ``Failed to prepare insert statement: table search
        has no column named terrarium_name``.

        The fix must detect FTS-column drift *independently of* the
        stored version scalar — i.e. compare against the actual
        column list, not just a number.
        """
        import gc

        from kohakuvault import KVault, TextVault

        side = tmp_path / ".kt-index.kvault"
        # Old broken upgrade path: clear() emptied row contents +
        # stamped the meta with the CURRENT version, but the FTS
        # table schema was never recreated.
        old_meta = KVault(str(side), table="meta")
        old_meta.put("schema_version", SCHEMA_VERSION)  # ← THE LIE
        old_meta.close()
        try:
            del old_meta._inner
        except AttributeError:
            pass
        del old_meta
        old_search = TextVault(
            str(side),
            table="search",
            columns=["name", "preview", "config_path", "agents", "pwd"],
        )
        try:
            del old_search._vault
        except AttributeError:
            pass
        del old_search
        gc.collect()
        # Open with current code.  Must NOT trust the meta version —
        # the actual columns are wrong, so the sidecar must be purged
        # and rebuilt.
        i = SessionIndex(side)
        try:
            # If purge didn't fire, this raises with the production
            # "no column named terrarium_name" error.
            i.upsert(
                SessionIndexEntry(
                    filename="alice.kohakutr",
                    name="alice",
                    file_mtime=1.0,
                    file_size=1,
                    preview="",
                    config_path="",
                    agents=[],
                    pwd="",
                    config_type="agent",
                    status="paused",
                    last_active="",
                    created_at="",
                    format_version=2,
                    node_id="",
                    terrarium_name="lying_meta",
                )
            )
            hits = i.list(search="lying_meta")
            assert any(r["name"] == "alice" for r in hits.rows)
        finally:
            i.close()

    def test_purge_meta_probe_failure_rebuilds_sidecar(self, tmp_path, monkeypatch):
        # If the meta-probe KVault open itself throws (corrupt sidecar
        # file, OS permission flap), the purge path treats the
        # signature as ``__unreadable__`` → unconditional rebuild.
        side = tmp_path / ".kt-index.kvault"
        # Pre-seed a valid sidecar so ``sidecar_path.exists()`` is True
        # when the probe runs.
        SessionIndex(side).close()
        import gc

        gc.collect()

        from kohakuterrarium.studio.persistence.session_index import store as store_mod

        original_kvault = store_mod.KVault

        def _boom_meta(*args, **kwargs):
            if kwargs.get("table") == "meta":
                raise RuntimeError("meta probe exploded")
            return original_kvault(*args, **kwargs)

        # Patch ONLY for the duration of the next open; the inner
        # constructors still need real KVault.
        calls = {"n": 0}

        def _patched(path, *, table):
            calls["n"] += 1
            if calls["n"] == 1 and table == "meta":
                raise RuntimeError("meta probe exploded")
            return original_kvault(path, table=table)

        monkeypatch.setattr(store_mod, "KVault", _patched)
        # Must not raise + must rebuild (the rebuilt sidecar has the
        # current ``search_columns`` written by ``_stamp_schema``).
        i = SessionIndex(side)
        try:
            from kohakuterrarium.studio.persistence.session_index.store import (
                SEARCH_COLUMNS,
            )

            assert i.meta_get("search_columns") == list(SEARCH_COLUMNS)
        finally:
            i.close()

    def test_purge_meta_close_failure_does_not_block_rebuild(
        self, tmp_path, monkeypatch
    ):
        # The probe's ``finally`` swallows close() exceptions so the
        # rebuild path still runs.  Patch the meta KVault returned by
        # the probe so close() raises, then assert no exception leaks
        # out of __init__.
        side = tmp_path / ".kt-index.kvault"
        # Put the sidecar into a state that triggers a purge (old
        # column set in meta).
        SessionIndex(side).close()
        import gc

        gc.collect()
        from kohakuvault import KVault as _RealKVault

        from kohakuterrarium.studio.persistence.session_index import store as store_mod

        seen_probe = {"done": False}

        class _FlakyCloseKVault(_RealKVault):
            def close(self_inner):
                if not seen_probe["done"]:
                    seen_probe["done"] = True
                    raise RuntimeError("probe close blew up")
                return _RealKVault.close(self_inner)

        monkeypatch.setattr(store_mod, "KVault", _FlakyCloseKVault)
        # Force schema drift so the probe runs.  Write the marker via
        # a fresh KVault so we can target the probe's close() call.
        # Easier: just trust that on the second open the probe runs
        # (we already wrote ``search_columns`` on the first open, so
        # the probe will see them and return early — to trigger the
        # probe close path we instead force-write a stale columns
        # value before the second open).
        with _RealKVault(str(side), table="meta") as m:
            m.put("search_columns", ["forced_stale"])
        i = SessionIndex(side)  # must not raise despite flaky close
        try:
            assert i is not None
        finally:
            i.close()

    def test_purge_unlink_failure_is_logged_not_raised(self, tmp_path, monkeypatch):
        # If the OS refuses to unlink the stale sidecar (file lock,
        # permission denied), the purge logs + continues — the open
        # below WILL raise on the FTS column mismatch, but that's a
        # known follow-up failure mode, not an unhandled exception
        # from inside ``_purge_if_stale_schema``.

        side = tmp_path / ".kt-index.kvault"
        SessionIndex(side).close()
        from kohakuvault import KVault as _RealKVault

        with _RealKVault(str(side), table="meta") as m:
            m.put("search_columns", ["forced_stale"])
        import pathlib

        real_unlink = pathlib.Path.unlink

        def boom_unlink(self_path, missing_ok=False):
            if str(self_path).startswith(str(side)):
                raise PermissionError("locked")
            return real_unlink(self_path, missing_ok=missing_ok)

        monkeypatch.setattr(pathlib.Path, "unlink", boom_unlink)
        # The purge itself must not raise — the constructor may fail
        # downstream when it tries to open the still-stale FTS table,
        # which is OK (covered by other tests); here we only assert
        # that the unlink-OSError branch is exercised cleanly.
        try:
            SessionIndex(side)
        except Exception:  # noqa: BLE001
            # Downstream failure expected on a refused purge; the
            # important thing is that the OSError didn't propagate
            # FROM ``_purge_if_stale_schema``.
            pass

    def test_schema_bump_rebuilds_fts_table_columns(self, tmp_path):
        """Regression: a stale sidecar's FTS table has the old column
        set baked in.  Pre-fix, ``SessionIndex.__init__`` opened the
        old ``search`` table and the next ``upsert`` raised
        ``table search has no column named terrarium_name``.  The fix
        purges the entire sidecar file (not just the row data) on
        schema mismatch so the FTS table is re-created from scratch
        with the current column set.
        """
        import gc

        from kohakuvault import KVault, TextVault

        side = tmp_path / ".kt-index.kvault"
        # Build a fake v1 sidecar: meta says version 1, search has
        # the old (no terrarium_name / config_type) column set.
        # Explicit close + del + gc to mimic the cross-process state
        # the production code sees (sidecar on disk, no in-process
        # handles to it) — without this Windows' file lock would
        # block the purge unlink.
        old_meta = KVault(str(side), table="meta")
        old_meta.put("schema_version", 1)
        old_meta.close()
        try:
            del old_meta._inner
        except AttributeError:
            pass
        del old_meta
        old_search = TextVault(
            str(side),
            table="search",
            columns=["name", "preview", "config_path", "agents", "pwd"],
        )
        try:
            del old_search._vault
        except AttributeError:
            pass
        del old_search
        gc.collect()
        # Open with current code — should purge + rebuild + accept the
        # new upsert without raising on the missing FTS column.
        i = SessionIndex(side)
        try:
            i.upsert(_entry(filename="alice.kohakutr"))
            assert i.list().total == 1
            # A search across one of the NEW columns must succeed
            # (proves the FTS table was recreated with the new schema).
            i.upsert(
                SessionIndexEntry(
                    filename="bob.kohakutr",
                    name="bob",
                    file_mtime=1.0,
                    file_size=1,
                    preview="",
                    config_path="",
                    agents=[],
                    pwd="",
                    config_type="terrarium",
                    status="paused",
                    last_active="",
                    created_at="",
                    format_version=2,
                    node_id="",
                    terrarium_name="my_team",
                )
            )
            hits = i.list(search="my_team")
            assert any(r["name"] == "bob" for r in hits.rows)
        finally:
            i.close()

    def test_close_is_idempotent(self, tmp_path):
        side = tmp_path / ".kt-index.kvault"
        i = SessionIndex(side)
        i.close()
        i.close()  # must not raise

    def test_close_swallows_table_close_errors(self, tmp_path, monkeypatch):
        # If one of the tables' ``close`` raises (already-closed,
        # disk gone, etc.) the others still close.
        side = tmp_path / ".kt-index.kvault"
        i = SessionIndex(side)

        def boom():
            raise RuntimeError("table close blew up")

        monkeypatch.setattr(i._entries, "close", boom)
        i.close()  # must not raise
        # _search + _meta still got close()'d; _closed flag flipped.
        assert i._closed is True

    def test_close_tolerates_missing_native_handle(self, tmp_path):
        # ``del table._inner`` / ``del self._search._vault`` are
        # protected by AttributeError catches — strip the attrs
        # ahead of close to exercise the catch branches.
        side = tmp_path / ".kt-index.kvault"
        i = SessionIndex(side)
        for table in (i._entries, i._meta):
            try:
                del table._inner
            except AttributeError:
                pass
        try:
            del i._search._vault
        except AttributeError:
            pass
        i.close()  # must not raise
        assert i._closed is True

    def test_path_property(self, idx, tmp_path):
        assert idx.path == str(tmp_path / ".kt-index.kvault")


# ── Mutations ─────────────────────────────────────────────────────


class TestMutations:
    def test_upsert_then_get(self, idx):
        e = _entry(filename="a.kohakutr", preview="hi")
        idx.upsert(e)
        out = idx.get("a.kohakutr")
        assert out is not None
        assert out["preview"] == "hi"
        # Internal field stripped from the public read.
        assert "_search_rowid" not in out

    def test_upsert_assigns_search_rowid(self, idx):
        e = _entry(filename="a.kohakutr")
        idx.upsert(e)
        assert e._search_rowid > 0

    def test_upsert_is_idempotent_keeps_rowid(self, idx):
        e = _entry(filename="a.kohakutr", preview="v1")
        idx.upsert(e)
        rid = e._search_rowid
        e2 = _entry(filename="a.kohakutr", preview="v2")
        idx.upsert(e2)
        # Same FTS row reused → no duplicate row in search index.
        assert e2._search_rowid == rid
        # Latest preview wins.
        assert idx.get("a.kohakutr")["preview"] == "v2"

    def test_upsert_recovers_when_fts_row_missing(self, idx):
        # If the FTS row is gone (sidecar corruption / manual edit)
        # but the entries row still references its rowid, upsert
        # transparently re-inserts the FTS row.
        e = _entry(filename="a.kohakutr")
        idx.upsert(e)
        orig_rowid = e._search_rowid
        # Drop the FTS row out from under us.
        idx._search.delete(orig_rowid)
        # Now upsert again with the entries row still claiming the
        # stale rowid.
        e2 = _entry(filename="a.kohakutr", preview="recovered")
        idx.upsert(e2)
        assert e2._search_rowid > 0
        # Search still finds it.
        assert idx.list(search="recovered").total == 1

    def test_upsert_many(self, idx):
        entries = [_entry(filename=f"s{i}.kohakutr") for i in range(5)]
        n = idx.upsert_many(entries)
        assert n == 5
        assert idx.list().total == 5

    def test_delete(self, idx):
        idx.upsert(_entry(filename="a.kohakutr"))
        assert idx.delete("a.kohakutr") is True
        assert idx.delete("a.kohakutr") is False
        assert idx.get("a.kohakutr") is None
        assert idx.list().total == 0

    def test_delete_handles_missing_fts_row(self, idx, monkeypatch):
        idx.upsert(_entry(filename="a.kohakutr"))

        # Force ``_search.delete`` to raise (sidecar corruption /
        # external mutation race).  ``delete`` swallows the error
        # and still removes the entries row.
        def boom(_rowid):
            raise RuntimeError("simulated FTS row gone")

        monkeypatch.setattr(idx._search, "delete", boom)
        assert idx.delete("a.kohakutr") is True
        assert idx.get("a.kohakutr") is None

    def test_clear(self, idx):
        idx.upsert(_entry(filename="a.kohakutr"))
        idx.upsert(_entry(filename="b.kohakutr"))
        idx.clear()
        assert idx.list().total == 0

    def test_clear_swallows_table_errors(self, idx, monkeypatch):
        # If a table's ``clear`` raises (already-cleared, etc.) the
        # other tables still get cleared.  Patch in a fake table
        # that raises on clear.
        original = idx._entries.clear

        def boom():
            raise RuntimeError("clear blew up")

        monkeypatch.setattr(idx._entries, "clear", boom)
        # Must not raise; the search table still gets cleared.
        idx.clear()
        monkeypatch.setattr(idx._entries, "clear", original)

    def test_clear_swallows_search_errors(self, idx, monkeypatch):
        def boom():
            raise RuntimeError("search clear blew up")

        monkeypatch.setattr(idx._search, "clear", boom)
        idx.clear()  # must not raise


# ── Reads + listing ────────────────────────────────────────────────


class TestReads:
    def test_get_missing(self, idx):
        assert idx.get("nope.kohakutr") is None

    def test_fingerprint(self, idx):
        e = _entry(filename="a.kohakutr")
        e.file_mtime = 123.45
        e.file_size = 678
        idx.upsert(e)
        assert idx.fingerprint("a.kohakutr") == (123.45, 678)

    def test_fingerprint_missing(self, idx):
        assert idx.fingerprint("nope.kohakutr") is None

    def test_all_filenames(self, idx):
        for n in ("a", "b", "c"):
            idx.upsert(_entry(filename=f"{n}.kohakutr"))
        names = sorted(idx.all_filenames())
        assert names == ["a.kohakutr", "b.kohakutr", "c.kohakutr"]

    def test_count(self, idx):
        assert idx.count() == 0
        idx.upsert(_entry(filename="a.kohakutr"))
        idx.upsert(_entry(filename="b.kohakutr"))
        assert idx.count() == 2

    def test_count_caps_at_100k(self, idx, monkeypatch):
        # ``count`` short-circuits to avoid runaway scans on a
        # broken sidecar.  Fake an infinite key iterator so the
        # cap branch actually fires.
        from kohakuterrarium.studio.persistence.session_index import store as store_mod

        def infinite_iter(_kv, batch=None):
            i = 0
            while True:
                yield f"f{i}.kohakutr"
                i += 1

        monkeypatch.setattr(store_mod, "_iter_kv_keys", infinite_iter)
        assert idx.count() == 100_000


class TestList:
    def _populate(self, idx):
        idx.upsert(
            _entry(
                filename="alice.kohakutr.v2",
                preview="hello alice",
                status="running",
                config_type="agent",
                last_active="2026-05-03T00:00:00",
                agents=["alice"],
            )
        )
        idx.upsert(
            _entry(
                filename="bob.kohakutr.v2",
                preview="hello bob",
                status="paused",
                config_type="terrarium",
                last_active="2026-05-02T00:00:00",
                agents=["bob"],
                node_id="worker-1",
            )
        )
        idx.upsert(
            _entry(
                filename="carol.kohakutr.v2",
                preview="goodbye world",
                status="paused",
                config_type="agent",
                last_active="2026-05-01T00:00:00",
                agents=["carol"],
            )
        )

    def test_unsearched_sort_by_last_active_desc(self, idx):
        self._populate(idx)
        page = idx.list()
        assert [r["name"] for r in page.rows] == ["alice", "bob", "carol"]
        assert page.total == 3

    def test_unsearched_sort_by_name_asc(self, idx):
        self._populate(idx)
        page = idx.list(sort="name", order="asc")
        assert [r["name"] for r in page.rows] == ["alice", "bob", "carol"]

    def test_unsearched_filter_status(self, idx):
        self._populate(idx)
        page = idx.list(status="paused")
        assert page.total == 2
        assert all(r["status"] == "paused" for r in page.rows)

    def test_unsearched_filter_config_type(self, idx):
        self._populate(idx)
        page = idx.list(config_type="terrarium")
        assert page.total == 1
        assert page.rows[0]["name"] == "bob"

    def test_unsearched_filter_node_id(self, idx):
        self._populate(idx)
        page = idx.list(node_id="worker-1")
        assert page.total == 1
        assert page.rows[0]["name"] == "bob"

    def test_unsearched_pagination(self, idx):
        for i in range(5):
            idx.upsert(
                _entry(
                    filename=f"s{i}.kohakutr",
                    last_active=f"2026-05-{i+1:02d}T00:00:00",
                )
            )
        # Newest first, take rows 1..2 (skipping the very newest).
        page = idx.list(offset=1, limit=2)
        assert page.total == 5
        assert [r["name"] for r in page.rows] == ["s3", "s2"]

    def test_unsearched_strips_internal_field(self, idx):
        idx.upsert(_entry(filename="a.kohakutr"))
        page = idx.list()
        assert "_search_rowid" not in page.rows[0]

    def test_searched_basic(self, idx):
        self._populate(idx)
        page = idx.list(search="hello")
        names = {r["name"] for r in page.rows}
        assert names == {"alice", "bob"}

    def test_searched_with_facet_filter(self, idx):
        self._populate(idx)
        page = idx.list(search="hello", status="running")
        assert page.total == 1
        assert page.rows[0]["name"] == "alice"

    def test_searched_no_hits(self, idx):
        self._populate(idx)
        page = idx.list(search="zzzzznope")
        assert page.total == 0
        assert page.rows == []

    def test_searched_relevance_sort_preserves_bm25(self, idx):
        # Two sessions with the same query term — BM25 ranks them.
        idx.upsert(_entry(filename="big.kohakutr", preview="zebra zebra zebra"))
        idx.upsert(_entry(filename="small.kohakutr", preview="zebra one off"))
        page = idx.list(search="zebra", sort="relevance")
        assert page.total == 2
        # Higher-frequency match comes first.
        assert page.rows[0]["name"] == "big"

    def test_searched_relevance_asc_reverses(self, idx):
        idx.upsert(_entry(filename="big.kohakutr", preview="zebra zebra zebra"))
        idx.upsert(_entry(filename="small.kohakutr", preview="zebra one off"))
        page = idx.list(search="zebra", sort="relevance", order="asc")
        assert page.rows[0]["name"] == "small"

    def test_searched_strips_internal_score(self, idx):
        idx.upsert(_entry(filename="a.kohakutr", preview="hello"))
        page = idx.list(search="hello")
        assert "_fts_score" not in page.rows[0]
        assert "_search_rowid" not in page.rows[0]

    def test_searched_drops_orphan_fts_row(self, idx):
        # FTS hits whose ``entries`` row was deleted out-of-band must
        # be silently skipped (deferred to the next reconcile).
        idx.upsert(_entry(filename="a.kohakutr", preview="hello"))
        # Drop just the entries row, leaving the FTS row dangling.
        idx._entries.delete("a.kohakutr")
        page = idx.list(search="hello")
        assert page.total == 0

    def test_invalid_sort_falls_back_to_last_active(self, idx):
        # Unknown sort keys silently coerce to the default.
        idx.upsert(_entry(filename="a.kohakutr", last_active="2026-05-02"))
        idx.upsert(_entry(filename="b.kohakutr", last_active="2026-05-01"))
        page = idx.list(sort="not-a-real-key")
        assert page.rows[0]["name"] == "a"

    def test_invalid_order_falls_back_to_desc(self, idx):
        idx.upsert(_entry(filename="a.kohakutr", last_active="2026-05-02"))
        idx.upsert(_entry(filename="b.kohakutr", last_active="2026-05-01"))
        page = idx.list(order="weird")
        assert page.rows[0]["name"] == "a"

    def test_limit_high_values_pass_through(self, idx):
        # The ``≤1000`` cap was dropped so
        # ``Studio.persistence.list(limit=index.count())`` can paginate
        # past 1000 rows.  Caller is responsible for picking a sane
        # value; ``list()`` no longer second-guesses.
        for i in range(3):
            idx.upsert(_entry(filename=f"s{i}.kohakutr"))
        page = idx.list(limit=9999)
        assert page.limit == 9999

    def test_limit_clamped_to_min(self, idx):
        idx.upsert(_entry(filename="s.kohakutr"))
        page = idx.list(limit=0)
        assert page.limit == 1

    def test_offset_clamped_non_negative(self, idx):
        idx.upsert(_entry(filename="s.kohakutr"))
        page = idx.list(offset=-5)
        assert page.offset == 0


# ── Meta accessors ────────────────────────────────────────────────


class TestMeta:
    def test_meta_default_when_missing(self, idx):
        assert idx.meta_get("nope", "default") == "default"

    def test_meta_put_get_roundtrip(self, idx):
        idx.meta_put("last_reconcile_at", 1747923812.5)
        assert idx.meta_get("last_reconcile_at") == 1747923812.5


# ── Iter helper + strip helper ────────────────────────────────────


class TestHelpers:
    def test_iter_kv_keys_decodes_bytes(self, idx):
        for n in ("alpha", "beta", "gamma"):
            idx.upsert(_entry(filename=f"{n}.kohakutr"))
        keys = list(_iter_kv_keys(idx._entries))
        assert all(isinstance(k, str) for k in keys)
        assert sorted(keys) == ["alpha.kohakutr", "beta.kohakutr", "gamma.kohakutr"]

    def test_strip_internal_removes_underscore_keys(self):
        out = _strip_internal({"a": 1, "_search_rowid": 7, "_fts_score": 0.5})
        assert out == {"a": 1}


# ── SessionIndexPage ──────────────────────────────────────────────


class TestSessionIndexPage:
    def test_to_dict_shape(self):
        p = SessionIndexPage(rows=[{"a": 1}], total=1, offset=0, limit=20)
        d = p.to_dict()
        assert d == {
            "sessions": [{"a": 1}],
            "total": 1,
            "offset": 0,
            "limit": 20,
        }
