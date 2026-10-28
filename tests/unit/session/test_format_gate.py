"""Writable session consumers reject unsupported and unreadable archives."""

import asyncio
import sqlite3
from pathlib import Path

import pytest
from kohakuvault import KVault, VectorKVault
from kohakuvault.errors import KohakuVaultError

from kohakuterrarium.session import readonly as readonly_mod
from kohakuterrarium.session import version as version_mod
from kohakuterrarium.session.memory import SessionMemory
from kohakuterrarium.session.migrations import (
    discover_versions,
    ensure_latest_version,
    migrate,
)
from kohakuterrarium.session.resource_gate import GATE, ResourceReleaseError
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.session.version import SessionFormatError, detect_format_version


def _meta(path, version):
    vault = KVault(path, table="meta")
    try:
        vault.enable_auto_pack()
        vault["format_version"] = version
    finally:
        vault.close()


def _snapshot(path):
    return path.read_bytes(), path.stat().st_mtime_ns


@pytest.mark.parametrize("version", [3, "3", None, 2.5, 0, -1, "broken"])
@pytest.mark.parametrize("consumer", [SessionStore, SessionMemory])
def test_unsupported_format_never_opens_writable(
    tmp_path, monkeypatch, consumer, version
):
    path = tmp_path / "session.kohakutr"
    _meta(path, version)
    before = _snapshot(path)

    def forbidden(*args, **kwargs):
        raise AssertionError("writable open reached")

    monkeypatch.setattr(
        consumer,
        "_open_tables" if consumer is SessionStore else "_open_indexes",
        forbidden,
    )
    with pytest.raises(SessionFormatError):
        result = consumer(str(path))
        result.close()
    assert _snapshot(path) == before


@pytest.mark.parametrize("consumer", [SessionStore, SessionMemory])
def test_future_family_cannot_fall_back(tmp_path, consumer):
    path = tmp_path / "session.kohakutr.v1"
    future = tmp_path / "session.kohakutr.v3"
    _meta(path, 1)
    _meta(future, 3)
    before = (_snapshot(path), _snapshot(future))
    with pytest.raises(SessionFormatError):
        result = consumer(str(path))
        result.close()
    assert (_snapshot(path), _snapshot(future)) == before


def test_migration_does_not_select_an_older_family_member(tmp_path):
    source = tmp_path / "session.kohakutr"
    _meta(source, 1)
    _meta(Path(f"{source}.v2"), 3)
    for call in (lambda: migrate(source, 2), lambda: ensure_latest_version(source)):
        with pytest.raises(SessionFormatError):
            call()


@pytest.mark.parametrize("kind", ["malformed", "corrupt", "future_sibling"])
def test_readonly_discovery_is_not_writable_admission(tmp_path, monkeypatch, kind):
    path = tmp_path / "session.kohakutr"
    if kind == "corrupt":
        path.write_bytes(b"not a database")
    else:
        _meta(path, "broken" if kind == "malformed" else 1)
    if kind == "future_sibling":
        _meta(Path(f"{path}.v3"), 3)
    before = {p.name: _snapshot(p) for p in tmp_path.iterdir()}
    assert discover_versions(path)
    assert {p.name: _snapshot(p) for p in tmp_path.iterdir()} == before

    def forbidden(*args, **kwargs):
        raise AssertionError("writable native open reached")

    monkeypatch.setattr(SessionStore, "_open_tables", forbidden)
    monkeypatch.setattr(SessionMemory, "_open_indexes", forbidden)
    expected = ResourceReleaseError if kind == "corrupt" else SessionFormatError
    for call in (
        lambda: SessionStore(path),
        lambda: SessionMemory(str(path)),
        lambda: ensure_latest_version(path),
        lambda: migrate(path, 2),
    ):
        with pytest.raises(expected):
            call()
    assert {
        p.name: _snapshot(p)
        for p in tmp_path.iterdir()
        if not p.name.endswith(("-wal", "-shm"))
    } == before


def test_unrelated_database_is_not_unversioned_session(tmp_path):
    path = tmp_path / "other.kohakutr"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE other (data TEXT)")
    before = _snapshot(path)
    with pytest.raises(SessionFormatError):
        detect_format_version(path)
    for consumer in (SessionStore, SessionMemory):
        with pytest.raises(SessionFormatError):
            consumer(path)
    assert _snapshot(path) == before


def test_read_and_close_failures_keep_both_causes(tmp_path, monkeypatch):
    path = tmp_path / "both.kohakutr"
    _meta(path, 2)
    primary = ValueError("read failed")
    cleanup = RuntimeError("close failed")
    real_dispose = readonly_mod.dispose

    def fail_read(*args, **kwargs):
        raise primary

    def fail_close(handle):
        real_dispose(handle)
        raise cleanup

    monkeypatch.setattr(readonly_mod.KVault, "__getitem__", fail_read)
    monkeypatch.setattr(readonly_mod, "dispose", fail_close)
    with pytest.raises(ResourceReleaseError) as caught:
        detect_format_version(path)
    assert caught.value.__cause__ is primary
    assert caught.value.cleanup_errors == (cleanup,)
    assert GATE.snapshot(path).legacy_uncertain


@pytest.mark.parametrize(
    "interrupt",
    [KeyboardInterrupt("stop"), SystemExit("stop"), asyncio.CancelledError("stop")],
)
def test_interrupt_survives_close_failure(tmp_path, monkeypatch, interrupt):
    path = tmp_path / "interrupt.kohakutr"
    _meta(path, 2)
    cleanup = RuntimeError("close failed")
    real_dispose = readonly_mod.dispose

    def fail_read(*args, **kwargs):
        raise interrupt

    def fail_close(handle):
        real_dispose(handle)
        raise cleanup

    monkeypatch.setattr(readonly_mod.KVault, "__getitem__", fail_read)
    monkeypatch.setattr(readonly_mod, "dispose", fail_close)
    with pytest.raises(type(interrupt)) as caught:
        detect_format_version(path)
    assert caught.value is interrupt
    assert caught.value.metadata_cleanup_errors == (cleanup,)
    assert GATE.snapshot(path).legacy_uncertain


def test_close_control_flow_exception_is_not_wrapped(tmp_path, monkeypatch):
    path = tmp_path / "close-interrupt.kohakutr"
    _meta(path, 2)
    interrupt = KeyboardInterrupt("stop close")
    real_dispose = readonly_mod.dispose

    def fail_close(handle):
        real_dispose(handle)
        raise interrupt

    monkeypatch.setattr(readonly_mod, "dispose", fail_close)
    with pytest.raises(KeyboardInterrupt) as caught:
        detect_format_version(path)
    assert caught.value is interrupt
    assert GATE.snapshot(path).legacy_uncertain


def test_missing_table_from_read_does_not_trigger_legacy_exception(
    tmp_path, monkeypatch
):
    path = tmp_path / "session.kohakutr"
    _meta(path, 2)
    called = []
    monkeypatch.setattr(
        readonly_mod, "copy_legacy_memory_only", lambda _: called.append(True) or True
    )

    def fail_read(*args, **kwargs):
        raise KohakuVaultError("SQLite error: no such table: meta")

    monkeypatch.setattr(readonly_mod.KVault, "__getitem__", fail_read)
    with pytest.raises(SessionFormatError) as caught:
        detect_format_version(path)
    assert type(caught.value.__cause__) is KohakuVaultError
    assert called == []


def test_general_reader_keeps_missing_and_close_policy(tmp_path, monkeypatch):
    missing = tmp_path / "missing-meta.kohakutr"
    with sqlite3.connect(missing) as db:
        db.execute("CREATE TABLE other (data TEXT)")
    assert readonly_mod.read_session_meta(missing) == {}
    path = tmp_path / "session.kohakutr"
    _meta(path, 2)
    real_dispose = readonly_mod.dispose

    def fail_close(handle):
        real_dispose(handle)
        raise RuntimeError("close failed")

    monkeypatch.setattr(readonly_mod, "dispose", fail_close)
    assert readonly_mod.read_session_meta(path)["format_version"] == 2
    assert GATE.snapshot(path).legacy_uncertain


def test_decoded_boolean_is_rejected(tmp_path, monkeypatch):
    path = tmp_path / "bool.kohakutr"
    path.touch()
    monkeypatch.setattr(
        version_mod,
        "read_strict_session_meta",
        lambda _, **kwargs: {"format_version": True},
    )
    with pytest.raises(SessionFormatError, match="Invalid format_version"):
        detect_format_version(path)


def test_native_boolean_encoding_reads_as_integer(tmp_path):
    path = tmp_path / "bool.kohakutr"
    _meta(path, True)
    vault = KVault(path, table="meta", read_only=True)
    try:
        vault.enable_auto_pack()
        value = vault["format_version"]
        assert type(value) is int and value == 1
    finally:
        vault.close()
    assert detect_format_version(path) == 1


def test_unversioned_meta_and_standalone_memory_are_supported(tmp_path):
    old = tmp_path / "old.kohakutr"
    KVault(old, table="meta").close()
    assert detect_format_version(old) == 1
    store = SessionStore(old)
    store.close(update_status=False)
    memory_path = tmp_path / "memory.kohakutr"
    memory = SessionMemory(str(memory_path))
    memory.close()
    assert detect_format_version(memory_path) == 1
    memory = SessionMemory(str(memory_path))
    memory.close()


def test_standalone_vector_memory_has_no_triggers_or_views(tmp_path):
    path = tmp_path / "vector-memory.kohakutr"
    memory = SessionMemory(str(path))
    memory.close()
    vectors = VectorKVault(str(path), table="memory_vec_4d", dimensions=4)
    vectors.close()
    with sqlite3.connect(path) as db:
        assert (
            db.execute(
                "SELECT type, name FROM sqlite_master WHERE type IN ('trigger', 'view')"
            ).fetchall()
            == []
        )
    assert detect_format_version(path) == 1
    memory = SessionMemory(str(path))
    memory.close()


@pytest.mark.parametrize("kind", ["trigger", "view"])
def test_standalone_memory_rejects_extra_schema_objects(tmp_path, kind):
    path = tmp_path / "memory.kohakutr"
    memory = SessionMemory(str(path))
    memory.close()
    with sqlite3.connect(path) as db:
        extra = db.execute(
            "SELECT type, name FROM sqlite_master WHERE type IN ('trigger', 'view')"
        ).fetchall()
        assert extra == []
        if kind == "trigger":
            db.execute(
                "CREATE TRIGGER rogue AFTER INSERT ON memory_state BEGIN DELETE FROM memory_state; END"
            )
        else:
            db.execute("CREATE VIEW rogue AS SELECT * FROM memory_state")
    before = _snapshot(path)
    with pytest.raises(SessionFormatError):
        detect_format_version(path)
    for consumer in (SessionStore, SessionMemory):
        with pytest.raises(SessionFormatError):
            consumer(str(path))
    assert _snapshot(path) == before


@pytest.mark.parametrize(
    "kind", ["unknown_table", "fake_fts", "fake_vector", "vector_dimensions"]
)
def test_standalone_memory_rejects_spoofed_schema(tmp_path, kind):
    path = tmp_path / "memory.kohakutr"
    memory = SessionMemory(str(path))
    memory.close()
    if kind == "vector_dimensions":
        vector = VectorKVault(str(path), table="memory_vec_3d", dimensions=4)
        vector.close()
    else:
        with sqlite3.connect(path) as db:
            if kind == "unknown_table":
                db.execute("CREATE TABLE mystery (data TEXT)")
            elif kind == "fake_fts":
                db.execute("DROP TABLE memory_fts")
                db.execute(
                    "CREATE TABLE memory_fts (content DEFAULT 'USING fts5(', value_ref)"
                )
            else:
                db.execute(
                    "CREATE TABLE memory_vec_3d (data TEXT DEFAULT 'USING vec0(vector float[3])')"
                )
    before = _snapshot(path)
    with pytest.raises(SessionFormatError):
        detect_format_version(path)
    for consumer in (SessionStore, SessionMemory):
        with pytest.raises(SessionFormatError):
            consumer(str(path))
    assert _snapshot(path) == before


def test_readonly_store_does_not_require_writable_format(tmp_path):
    path = tmp_path / "future.kohakutr"
    _meta(path, 3)
    before = _snapshot(path)
    store = SessionStore(path, read_only=True)
    try:
        assert store.meta["format_version"] == 3
        with pytest.raises(PermissionError):
            store.append_event("alice", "text", {"content": "no"})
    finally:
        store.close(update_status=False)
    assert _snapshot(path) == before
