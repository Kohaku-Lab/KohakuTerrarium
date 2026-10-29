"""Native metadata reads select keys before fetching and decoding values."""

import sqlite3

import pytest
from kohakuvault import KVault
from kohakuvault.errors import InvalidArgument, KohakuVaultError

from kohakuterrarium.session import readonly as readonly_mod
from kohakuterrarium.session.metadata_candidate import (
    MetadataReadError,
    read_metadata_candidate,
)
from kohakuterrarium.session.readonly import read_session_meta, read_strict_session_meta
from kohakuterrarium.session.resource_gate import (
    GATE,
    ResourceReleaseError,
    acquire_legacy,
)
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.session.version import SessionFormatError, detect_format_version


def _seed(path):
    store = SessionStore(path)
    store.init_meta("selected", "agent", "", "", [])
    store.meta["present_none"] = None
    store.close(update_status=False)


def _insert_bad_value(path, key):
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?)",
            (key, b"\x89\x4b\x01\x07\x00\x00\x56\x4bgarbage"),
        )


@pytest.mark.parametrize("eligible", [False, True])
def test_selection_avoids_unselected_bad_value_and_deduplicates(tmp_path, eligible):
    path = tmp_path / "session.kohakutr"
    _seed(path)
    _insert_bad_value(path, b"broken")
    result = read_metadata_candidate(
        path,
        eligible=eligible,
        keys=("format_version", "present_none", "missing", "format_version"),
    )
    assert result.meta == {"format_version": 2, "present_none": None}
    with pytest.raises(InvalidArgument, match="magic"):
        read_session_meta(path)
    assert not GATE.snapshot(path).registered


def test_selection_reads_each_distinct_key_once(tmp_path, monkeypatch):
    path = tmp_path / "session.kohakutr"
    _seed(path)
    real_get = KVault.__getitem__
    reads = []

    def counted(vault, key):
        reads.append(key)
        return real_get(vault, key)

    monkeypatch.setattr(KVault, "__getitem__", counted)
    assert read_strict_session_meta(
        path, keys=("present_none", "missing", "present_none")
    ) == {"present_none": None}
    assert reads == ["present_none", "missing"]


def test_format_gate_fetches_only_marker(tmp_path):
    path = tmp_path / "session.kohakutr"
    _seed(path)
    _insert_bad_value(path, b"broken")
    assert detect_format_version(path) == 2
    assert read_strict_session_meta(path, keys=("format_version",)) == {
        "format_version": 2
    }


@pytest.mark.parametrize(
    "invalid", ["format_version", ["format_version"], ("ok", 1), 3]
)
def test_invalid_keys_rejected_before_missing_path_or_gate(tmp_path, invalid):
    path = tmp_path / "missing.kohakutr"
    with pytest.raises(TypeError):
        read_metadata_candidate(path, eligible=True, keys=invalid)
    with pytest.raises(TypeError):
        read_session_meta(path, keys=invalid)
    with pytest.raises(TypeError):
        read_strict_session_meta(path, keys=invalid)
    assert not path.exists()
    assert not GATE.snapshot(path).registered


@pytest.mark.parametrize("eligible", [False, True])
def test_selected_bad_value_is_not_missing(tmp_path, eligible):
    path = tmp_path / "session.kohakutr"
    _seed(path)
    _insert_bad_value(path, b"broken")
    expected = MetadataReadError if eligible else InvalidArgument
    with pytest.raises(expected) as caught:
        read_metadata_candidate(path, eligible=eligible, keys=("broken",))
    if eligible:
        assert caught.value.stage == "sql"
        assert isinstance(caught.value.__cause__, InvalidArgument)
    with sqlite3.connect(path) as db:
        db.execute(
            "UPDATE meta SET value = ? WHERE key = ?",
            (b"\x89\x4b\x01\x07\x00\x00\x56\x4bgarbage", b"format_version"),
        )
    with pytest.raises(SessionFormatError, match="magic"):
        detect_format_version(path)


def test_format_detection_reads_exactly_the_marker(tmp_path, monkeypatch):
    path = tmp_path / "session.kohakutr"
    _seed(path)
    original = KVault.__getitem__
    reads = []

    def counted(vault, key):
        reads.append(key)
        return original(vault, key)

    monkeypatch.setattr(KVault, "__getitem__", counted)
    assert detect_format_version(path) == 2
    assert reads == ["format_version"]


def test_empty_keys_still_respects_table_and_cleanup(tmp_path, monkeypatch):
    missing = tmp_path / "missing-meta.kohakutr"
    with sqlite3.connect(missing) as db:
        db.execute("CREATE TABLE other (data TEXT)")
    assert read_session_meta(missing, keys=()) == {}
    with pytest.raises(KohakuVaultError, match="no such table: meta"):
        read_strict_session_meta(missing, keys=())
    assert not GATE.snapshot(missing).registered

    path = tmp_path / "session.kohakutr"
    _seed(path)
    close = readonly_mod.dispose
    failure = RuntimeError("close failed")

    def close_then_fail(handle):
        close(handle)
        raise failure

    monkeypatch.setattr(readonly_mod, "dispose", close_then_fail)
    with pytest.raises(ResourceReleaseError) as caught:
        read_strict_session_meta(path, keys=())
    assert caught.value.__cause__ is failure
    assert GATE.snapshot(path).legacy_uncertain


def test_selected_live_writer_falls_back_and_sees_committed_rows(tmp_path):
    path = tmp_path / "session.kohakutr"
    _seed(path)
    store = SessionStore(path)
    try:
        store.meta["late"] = "committed"
        result = read_metadata_candidate(path, eligible=True, keys=("late",))
        assert result.reason == "legacy_enrolled"
        assert result.meta == {"late": "committed"}
    finally:
        store.close(update_status=False)
    assert not GATE.snapshot(path).registered


def test_selected_eligible_read_respects_enrolled_lease(tmp_path):
    path = tmp_path / "session.kohakutr"
    _seed(path)
    lease = acquire_legacy(path, "test")
    try:
        result = read_metadata_candidate(path, eligible=True, keys=("format_version",))
        assert result.reason == "legacy_enrolled"
        assert result.meta == {"format_version": 2}
    finally:
        lease.finish(source_released=True)
