"""Deletion lock identities follow lock files, including directory aliases."""

import os
from pathlib import Path

import pytest

from kohakuterrarium.errors import SessionLockedError
from kohakuterrarium.session.occupancy.gate import GATE, try_fast
from kohakuterrarium.studio.persistence import store as persistence
from kohakuterrarium.utils.file_lock import FileLock


@pytest.mark.parametrize("suffix", [".lock", ".drives.migrate-lock"])
def test_file_symlink_unlink_locks_only_its_own_family(tmp_path, monkeypatch, suffix):
    target = tmp_path / "target.kohakutr"
    target.write_bytes(b"retained target")
    target_drive = Path(str(target) + ".drives")
    target_drive.write_bytes(b"retained drive")
    alias = tmp_path / "alias.kohakutr"
    try:
        alias.symlink_to(target)
    except OSError:
        pytest.skip("file symlinks unavailable")
    alias_drive = Path(str(alias) + ".drives")
    alias_drive.write_bytes(b"alias drive")
    monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path))
    monkeypatch.setattr(persistence, "_purge_index_entries", lambda _: None)
    monkeypatch.setattr(persistence._live, "forget", lambda _: None)
    monkeypatch.setattr(persistence.drive_migration_lock, "_MIGRATE_LOCK_TIMEOUT_S", 0)
    original_detach = persistence.detach_file_family

    def detach(paths):
        assert try_fast(target, eligible=True).reason == "legacy_enrolled"
        assert target not in paths and target_drive not in paths
        return original_detach(paths)

    monkeypatch.setattr(persistence, "detach_file_family", detach)
    target_lock = FileLock(str(target) + suffix)
    alias_lock = FileLock(str(alias) + suffix)
    target_lock.acquire()
    try:
        alias_lock.acquire()
        try:
            error = SessionLockedError if suffix == ".lock" else TimeoutError
            with pytest.raises(error):
                persistence.delete_session_files("alias")
            assert alias.is_symlink() and alias_drive.exists()
            assert not GATE.snapshot(target).registered
        finally:
            alias_lock.release()
        assert set(persistence.delete_session_files("alias")) == {alias, alias_drive}
        assert not alias.is_symlink() and not alias_drive.exists()
        assert target.read_bytes() == b"retained target"
        assert target_drive.read_bytes() == b"retained drive"
        assert not GATE.snapshot(target).registered
    finally:
        target_lock.release()


def test_lock_selection_preserves_resolved_parent_before_filename(
    tmp_path, monkeypatch
):
    spelling = tmp_path / "link" / ".." / "session.kohakutr"
    resolved_parent = tmp_path / "real"
    original_resolve = Path.resolve

    def resolve(path, *args, **kwargs):
        if path == spelling.parent:
            return resolved_parent
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    for suffix in (".lock", ".drives.migrate-lock"):
        assert persistence._unique_lock_authorities([spelling], suffix) == [
            os.path.normcase(str(resolved_parent / spelling.name))
        ]


@pytest.mark.parametrize("parent_traversal", [False, True])
def test_directory_symlink_does_not_acquire_same_lock_twice(
    tmp_path, monkeypatch, parent_traversal
):
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    alias_dir = tmp_path / "alias"
    linked_dir = real_dir / "child" if parent_traversal else real_dir
    linked_dir.mkdir(exist_ok=True)
    try:
        alias_dir.symlink_to(linked_dir, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks unavailable")
    target = real_dir / "session.kohakutr"
    target.write_bytes(b"session")
    alias = (
        alias_dir / ".." / target.name if parent_traversal else alias_dir / target.name
    )
    if not alias.exists():
        pytest.skip(
            "platform normalizes parent traversal before resolving directory links"
        )
    assert alias.samefile(target)
    monkeypatch.setattr(
        persistence, "all_versions_for_session_default", lambda _: [alias]
    )
    monkeypatch.setattr(persistence, "_purge_index_entries", lambda _: None)
    monkeypatch.setattr(persistence._live, "forget", lambda _: None)
    monkeypatch.setattr(persistence.drive_migration_lock, "_MIGRATE_LOCK_TIMEOUT_S", 0)
    for suffix, error in (
        (".lock", SessionLockedError),
        (".drives.migrate-lock", TimeoutError),
    ):
        holder = FileLock(str(target) + suffix)
        holder.acquire()
        try:
            with pytest.raises(error):
                persistence.delete_session_files("session")
            assert target.read_bytes() == b"session"
            assert not GATE.snapshot(target).registered
        finally:
            holder.release()
    monkeypatch.setattr(
        persistence, "all_versions_for_session_default", lambda _: [alias, target]
    )
    acquired = []
    migrations = []
    original_migration = persistence.drive_migration_lock.drive_migration_guard
    original = persistence.acquire_writer_lock

    def acquire(path):
        acquired.append(Path(str(path) + ".lock").resolve())
        return original(path)

    def migration(path):
        migrations.append(Path(str(path) + ".drives.migrate-lock").resolve())
        return original_migration(path)

    monkeypatch.setattr(persistence, "acquire_writer_lock", acquire)
    monkeypatch.setattr(
        persistence.drive_migration_lock, "drive_migration_guard", migration
    )
    assert persistence.delete_session_files("session") == [alias]
    assert not target.exists()
    assert len(acquired) == len(set(acquired)) == 1
    assert len(migrations) == len(set(migrations)) == 1
    assert not GATE.snapshot(target).registered
