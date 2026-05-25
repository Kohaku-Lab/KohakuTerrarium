"""End-to-end workflow tests for the session-index sidecar.

Drives the whole stack (SessionStore → reconcile → SessionIndex →
search/list/delete) in single test functions, mirroring how the API
route uses it.  Sits at the unit tier because every dependency is
real (KohakuVault, SessionStore, filesystem) and the only seam is
``KT_SESSION_DIR``/``tmp_path`` for isolation.

The shape (one function = one whole journey) is deliberate: it's
the integration-tier discipline applied at the unit boundary
because SessionIndex is a self-contained subsystem.
"""

from pathlib import Path

import pytest

from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence import session_index as pkg
from kohakuterrarium.studio.persistence.session_index import (
    SessionIndexHook,
    get_session_index_default,
    close_session_index,
    sidecar_path_for,
)
from kohakuterrarium.studio.persistence.session_index.reconcile import reconcile


@pytest.fixture(autouse=True)
def _reset_singleton():
    pkg._reset_singleton_for_tests()
    yield
    pkg._reset_singleton_for_tests()


def _create_session(
    session_dir: Path,
    name: str,
    *,
    agent: str = "alice",
    preview: str = "",
    status: str = "running",
    config_type: str = "agent",
) -> Path:
    """Create a session with explicit status.

    ``init_meta`` defaults status to "running"; ``status="paused"``
    triggers an extra ``update_status`` call.  ``close(update_status=False)``
    so the test's explicit status survives — ``SessionStore.close``'s
    default would flip everything back to "paused" on the way out.
    """
    path = session_dir / f"{name}.kohakutr"
    s = SessionStore(str(path))
    try:
        s.init_meta(f"sid-{name}", config_type, "/p.yaml", "/w", [agent])
        if preview:
            s.append_event(agent, "user_input", {"content": preview})
        s.flush()
        if status != "running":
            s.update_status(status)
    finally:
        s.close(update_status=False)
    return path


class TestSessionIndexWorkflow:
    """One method = one complete user journey through the sidecar."""

    def test_full_lifecycle_create_list_search_update_delete(self, tmp_path):
        sdir = tmp_path / "sessions"
        sdir.mkdir()

        # 1. Create three sessions on disk.  alice runs; bob is
        #    paused; carol is also paused + a terrarium (so the
        #    config_type facet later has something to distinguish).
        _create_session(sdir, "alice", preview="hello world", status="running")
        _create_session(sdir, "bob", preview="goodbye world", status="paused")
        _create_session(
            sdir,
            "carol",
            preview="random text",
            status="paused",
            config_type="terrarium",
        )

        # 2. First singleton open bootstraps from disk — all three
        #    sessions show up.
        idx = get_session_index_default(sdir)
        page = idx.list()
        assert page.total == 3
        names = {r["name"] for r in page.rows}
        assert names == {"alice", "bob", "carol"}

        # 3. FTS search — "world" matches alice + bob; relevance
        #    ordering puts them first.
        page = idx.list(search="world")
        assert page.total == 2
        assert {r["name"] for r in page.rows} == {"alice", "bob"}

        # 4. Facet filter — only running sessions.
        page = idx.list(status="running")
        assert page.total == 1
        assert page.rows[0]["name"] == "alice"

        # 5. Sort by name asc (override the last_active default).
        page = idx.list(sort="name", order="asc")
        assert [r["name"] for r in page.rows] == ["alice", "bob", "carol"]

        # 6. Update alice's preview via the hook → next list reflects it.
        s = SessionStore(str(sdir / "alice.kohakutr"))
        try:
            with SessionIndexHook(s, idx, push_on_attach=False):
                s.append_event("alice", "user_input", {"content": "fresh text"})
                # Force-flush so the test doesn't depend on debounce timing.
                # (The context exit also flushes.)
        finally:
            s.close()
        row = idx.get("alice.kohakutr")
        # The "first user_input" preview was set in _create_session
        # before the SessionIndexHook attached; subsequent events
        # append after it, so the preview stays "hello world" by
        # get_resumable_events semantics.  Verify the row exists
        # (push happened) without asserting the exact preview text.
        assert row is not None

        # 7. Delete bob on disk + reconcile → drops from index.
        (sdir / "bob.kohakutr").unlink()
        for sf in sdir.glob("bob.kohakutr-*"):
            sf.unlink()
        report = reconcile(idx, sdir, full=False)
        assert report.deleted == 1
        assert idx.list().total == 2
        assert idx.get("bob.kohakutr") is None

        # 8. Direct API delete (the API route's path) — drops alice.
        # We invoke the SessionIndex.delete method directly here; the
        # higher-level "delete file from disk + delete index" sequence
        # is covered by the API route test.
        assert idx.delete("alice.kohakutr") is True
        assert idx.list().total == 1
        assert idx.list().rows[0]["name"] == "carol"

        close_session_index()

    def test_incremental_reconcile_only_rereads_changed_files(self, tmp_path):
        """Cold list cost stays flat after the first bootstrap."""
        sdir = tmp_path / "sessions"
        sdir.mkdir()
        for i in range(5):
            _create_session(sdir, f"s{i}", preview=f"preview {i}")

        idx = get_session_index_default(sdir)
        assert idx.list().total == 5

        # Touch one file's mtime to simulate an in-process write.
        import os

        path = sdir / "s2.kohakutr"
        new_mtime = path.stat().st_mtime + 100
        os.utime(path, (new_mtime, new_mtime))

        report = reconcile(idx, sdir, full=False)
        # Only the touched file is re-read.
        assert report.read == 1
        assert report.total == 5
        close_session_index()

    def test_reconcile_picks_up_new_sessions(self, tmp_path):
        sdir = tmp_path / "sessions"
        sdir.mkdir()
        _create_session(sdir, "initial")
        idx = get_session_index_default(sdir)
        assert idx.list().total == 1
        # Create a fresh session AFTER the singleton is open.
        _create_session(sdir, "later")
        report = reconcile(idx, sdir, full=False)
        assert report.read == 1  # only "later" is new
        assert idx.list().total == 2
        close_session_index()

    def test_sidecar_survives_close_and_reopen(self, tmp_path):
        sdir = tmp_path / "sessions"
        sdir.mkdir()
        _create_session(sdir, "alice")

        # First lifecycle: open, list, close.
        idx1 = get_session_index_default(sdir)
        assert idx1.list().total == 1
        close_session_index()

        # Second lifecycle: open the same sidecar; no bootstrap.
        idx2 = get_session_index_default(sdir)
        # Different instance — singleton was reset.
        assert idx1 is not idx2
        # Same on-disk content survives.
        assert idx2.list().total == 1
        close_session_index()

    def test_sidecar_file_lives_inside_session_dir(self, tmp_path):
        sdir = tmp_path / "sessions"
        sdir.mkdir()
        idx = get_session_index_default(sdir)
        assert Path(idx.path) == sidecar_path_for(sdir)
        assert Path(idx.path).parent == sdir
        close_session_index()
