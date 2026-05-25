"""Unit tests for the ``session_index`` singleton + ``__init__`` glue."""

from pathlib import Path

import pytest

from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence import session_index as pkg
from kohakuterrarium.studio.persistence.session_index import (
    close_session_index,
    get_session_index_default,
    sidecar_path_for,
)


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Drop the module singleton between tests so each fixture sees
    a clean slate without inheriting another test's open sidecar."""
    pkg._reset_singleton_for_tests()
    yield
    pkg._reset_singleton_for_tests()


def _make_session(session_dir: Path, name: str, *, preview: str = "") -> Path:
    path = session_dir / f"{name}.kohakutr"
    s = SessionStore(str(path))
    try:
        s.init_meta(f"sid-{name}", "agent", "/p.yaml", "/w", [name])
        if preview:
            s.append_event(name, "user_input", {"content": preview})
        s.flush()
    finally:
        s.close()
    return path


# ── Helpers ──────────────────────────────────────────────────────


class TestSidecarPath:
    def test_canonical_filename(self, tmp_path):
        assert sidecar_path_for(tmp_path).name == ".kt-index.kvault"
        assert sidecar_path_for(tmp_path).parent == tmp_path


# ── Singleton ────────────────────────────────────────────────────


class TestSingleton:
    def test_first_open_bootstraps_from_disk(self, tmp_path, monkeypatch):
        sdir = tmp_path / "sessions"
        sdir.mkdir()
        _make_session(sdir, "alice", preview="hello")
        idx = get_session_index_default(sdir)
        assert idx.list().total == 1
        close_session_index()

    def test_returns_same_instance_on_second_call(self, tmp_path):
        sdir = tmp_path / "sessions"
        sdir.mkdir()
        a = get_session_index_default(sdir)
        b = get_session_index_default(sdir)
        assert a is b
        close_session_index()

    def test_rotates_when_session_dir_changes(self, tmp_path):
        d1 = tmp_path / "d1"
        d2 = tmp_path / "d2"
        d1.mkdir()
        d2.mkdir()
        a = get_session_index_default(d1)
        b = get_session_index_default(d2)
        # New instance for the new directory.
        assert a is not b
        # Old one was closed during the rotate; verify by accessing
        # the ``_closed`` flag.
        assert a._closed is True
        close_session_index()

    def test_close_is_idempotent(self):
        close_session_index()  # no singleton yet — must not raise
        close_session_index()

    def test_close_drops_singleton(self, tmp_path):
        sdir = tmp_path / "s"
        sdir.mkdir()
        a = get_session_index_default(sdir)
        close_session_index()
        # Next call gets a fresh instance.
        b = get_session_index_default(sdir)
        assert a is not b
        close_session_index()

    def test_rotate_swallows_old_close_exception(self, tmp_path, monkeypatch):
        d1 = tmp_path / "d1"
        d2 = tmp_path / "d2"
        d1.mkdir()
        d2.mkdir()
        a = get_session_index_default(d1)

        # Replace ``close`` on the existing singleton with a raise
        # — the rotate path must swallow it and keep going.
        def boom():
            raise RuntimeError("close blew up")

        monkeypatch.setattr(a, "close", boom)
        b = get_session_index_default(d2)
        assert b is not a
        close_session_index()

    def test_close_session_index_swallows_close_exception(self, tmp_path, monkeypatch):
        sdir = tmp_path / "s"
        sdir.mkdir()
        a = get_session_index_default(sdir)

        def boom():
            raise RuntimeError("close blew up")

        monkeypatch.setattr(a, "close", boom)
        # Must not raise.
        close_session_index()

    def test_bootstrap_failure_logged_not_raised(self, tmp_path, monkeypatch):
        # Force reconcile to raise; the singleton must still return.
        def boom(*a, **kw):
            raise RuntimeError("disk explosion")

        monkeypatch.setattr(
            "kohakuterrarium.studio.persistence.session_index.reconcile.reconcile",
            boom,
        )
        # Also patch the alias the __init__ imported at module load.
        monkeypatch.setattr(pkg, "_run_reconcile", boom)
        sdir = tmp_path / "s"
        sdir.mkdir()
        idx = get_session_index_default(sdir)
        assert idx is not None
        assert idx.list().total == 0
        # Bootstrap flag wasn't set; a second call retries.
        assert idx.meta_get("bootstrap_completed") != "1"
        close_session_index()

    def test_bootstrap_flag_persisted_across_singletons(self, tmp_path):
        # Flag pins the FIRST-bootstrap state so subsequent server
        # starts skip the full re-scan, but they still run an
        # incremental reconcile (see ``test_restart_picks_up_new_sessions``).
        sdir = tmp_path / "s"
        sdir.mkdir()
        _make_session(sdir, "alice")
        idx1 = get_session_index_default(sdir)
        assert idx1.meta_get("bootstrap_completed") == "1"
        close_session_index()

        # Track the ``full=`` arg on every reconcile call so we can
        # assert "second open did INCREMENTAL not FULL".  Patch the
        # alias the package's ``__init__`` already captured —
        # patching ``reconcile.reconcile`` directly is too late,
        # the binding was frozen at module load.
        full_args: list[bool] = []
        original = pkg._run_reconcile

        def counting_reconcile(*a, **kw):
            full_args.append(bool(kw.get("full", False)))
            return original(*a, **kw)

        pkg._run_reconcile = counting_reconcile
        try:
            idx2 = get_session_index_default(sdir)
            assert idx2.list().total == 1  # entry preserved
            # One reconcile call, and it was incremental.
            assert full_args == [False]
        finally:
            pkg._run_reconcile = original
            close_session_index()

    def test_restart_picks_up_new_sessions(self, tmp_path):
        # The scenario the user asked about: API server starts, listing
        # populates the sidecar.  Server stops.  Meanwhile, a CLI
        # ``kt run`` writes a brand-new ``.kohakutr`` to disk.  Server
        # starts again — the new session must appear in the next list
        # without a manual ``?refresh=true``.
        sdir = tmp_path / "s"
        sdir.mkdir()
        _make_session(sdir, "alice")
        idx1 = get_session_index_default(sdir)
        assert idx1.list().total == 1
        close_session_index()

        # Simulate cross-process activity while server was down.
        _make_session(sdir, "bob", preview="cli-created")

        # New server start: incremental reconcile picks bob up.
        idx2 = get_session_index_default(sdir)
        names = {r["name"] for r in idx2.list().rows}
        assert names == {"alice", "bob"}
        close_session_index()

    def test_restart_drops_externally_deleted_sessions(self, tmp_path):
        # Symmetric case: a session on disk is gone (user ran
        # ``rm`` or the file was deleted in another window).
        # Incremental reconcile on startup drops it.
        sdir = tmp_path / "s"
        sdir.mkdir()
        path = _make_session(sdir, "alice")
        idx1 = get_session_index_default(sdir)
        assert idx1.list().total == 1
        close_session_index()

        path.unlink()
        for sf in sdir.glob("alice.kohakutr-*"):
            sf.unlink()

        idx2 = get_session_index_default(sdir)
        assert idx2.list().total == 0
        close_session_index()

    def test_startup_heals_legacy_sidecar_with_lying_schema_version(self, tmp_path):
        """Regression for production crash 2026-05-26.

        End-to-end via the singleton — the actual code path that
        crashed.  Pre-state: a sidecar on disk whose ``meta`` claims
        to be at the current schema version but whose FTS table
        still has the OLD column set (legacy ``_ensure_schema``
        cleared row content + bumped the version scalar but never
        recreated the FTS table).  Pre-fix, ``get_session_index_default``
        opened the sidecar, ran the post-bootstrap incremental
        reconcile, and the first sidecar upsert raised
        ``table search has no column named terrarium_name``.

        With the fix the singleton's purge step (driven by the
        ``search_columns`` drift check, not the version scalar)
        deletes the corrupted sidecar before the reconcile runs;
        startup completes cleanly and a new session is picked up
        on the rebuild.
        """
        import gc

        from kohakuvault import KVault, TextVault

        from kohakuterrarium.studio.persistence.session_index.entry import (
            SCHEMA_VERSION,
        )

        sdir = tmp_path / "s"
        sdir.mkdir()
        side = sdir / ".kt-index.kvault"

        # Forge the corrupted state.  ``schema_version`` lies +
        # ``bootstrap_completed`` set so the singleton would take the
        # incremental-reconcile branch (the one that crashed in prod).
        # ``search_columns`` deliberately NOT written — that's the
        # "missing" state the broken legacy upgrade leaves behind.
        old_meta = KVault(str(side), table="meta")
        old_meta.put("schema_version", SCHEMA_VERSION)
        old_meta.put("bootstrap_completed", "1")
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

        # Drop a session on disk so reconcile has something to read.
        _make_session(sdir, "alice", preview="post-heal")

        # The line that crashed in production.  Must not raise; must
        # serve the fresh state after the self-heal.
        idx = get_session_index_default(sdir)
        try:
            names = {r["name"] for r in idx.list().rows}
            assert "alice" in names
            # Sidecar was rebuilt — search_columns now reflects the
            # current set.
            from kohakuterrarium.studio.persistence.session_index.store import (
                SEARCH_COLUMNS,
            )

            assert idx.meta_get("search_columns") == list(SEARCH_COLUMNS)
        finally:
            close_session_index()

    def test_startup_reconcile_failure_is_logged_not_raised(
        self, tmp_path, monkeypatch
    ):
        # Same protection as bootstrap_failure_logged_not_raised but
        # for the post-bootstrap incremental-reconcile path.
        sdir = tmp_path / "s"
        sdir.mkdir()
        _make_session(sdir, "alice")
        idx1 = get_session_index_default(sdir)
        assert idx1.meta_get("bootstrap_completed") == "1"
        close_session_index()

        def boom(*a, **kw):
            raise RuntimeError("disk fault during reconcile")

        monkeypatch.setattr(pkg, "_run_reconcile", boom)
        idx2 = get_session_index_default(sdir)
        # Singleton still serves whatever state was already in the
        # sidecar — alice survives.
        assert idx2 is not None
        assert idx2.list().total == 1
        close_session_index()


class TestDefaultSessionDirResolver:
    def test_env_var_wins(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "envdir"))
        assert pkg._default_session_dir() == tmp_path / "envdir"

    def test_falls_back_to_config_dir(self, tmp_path, monkeypatch):
        monkeypatch.delenv("KT_SESSION_DIR", raising=False)
        monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path / "kconfig"))
        out = pkg._default_session_dir()
        assert out == tmp_path / "kconfig" / "sessions"

    def test_get_default_uses_default_resolver_when_none(self, tmp_path, monkeypatch):
        # When the caller passes None, the resolver must be consulted.
        monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "auto"))
        (tmp_path / "auto").mkdir()
        idx = get_session_index_default(session_dir=None)
        assert idx.path == str(sidecar_path_for(tmp_path / "auto"))
        close_session_index()


class TestPackageExports:
    def test_all_names_exist(self):
        for name in pkg.__all__:
            assert hasattr(pkg, name), name

    def test_reconcile_submodule_survives(self):
        # The submodule attribute must NOT have been shadowed by a
        # function of the same name (regression guard — see the
        # comment in __init__.py).
        import types

        assert isinstance(pkg.reconcile, types.ModuleType)
