"""Unit tests for :mod:`kohakuterrarium.session.store.lifecycle.tables`."""

from types import SimpleNamespace

import pytest
from kohakuvault import KVault, TextVault

import kohakuterrarium.session.store.lifecycle.tables as store_tables_mod
from kohakuterrarium.session.store.lifecycle.tables import (
    TABLE_NAMES,
    open_store_tables,
)
from kohakuterrarium.session.table_policy import TablePolicy, resolve_table_policy
from kohakuterrarium.session.vault_handles import dispose
from kohakuterrarium.session.vault_read import AbsentTable


def _release(store):
    for name in (*TABLE_NAMES, "fts"):
        handle = getattr(store, name, None)
        if handle is not None:
            dispose(handle)


def test_writable_open_assigns_every_table_with_its_policy(tmp_path, monkeypatch):
    path = str(tmp_path / "s.kohakutr")
    seen = {}
    real = store_tables_mod.KVault

    def recording(target, *, table, **options):
        seen[table] = options
        return real(target, table=table, **options)

    monkeypatch.setattr(store_tables_mod, "KVault", recording)
    policy = resolve_table_policy({"jobs": TablePolicy(cache_kb=64)})
    store = SimpleNamespace()
    open_store_tables(store, path, read_only=False, policy=policy)
    try:
        assert list(seen) == list(TABLE_NAMES)
        assert seen["jobs"]["cache_kb"] == 64
        assert seen["events"] == policy["events"].kvault_options()
        store.meta["k"] = {"packed": True}
        assert store.meta["k"] == {"packed": True}
        assert isinstance(store.fts, TextVault)
    finally:
        _release(store)


def test_read_only_open_never_creates_tables(tmp_path):
    path = tmp_path / "s.kohakutr"
    meta = KVault(str(path), table="meta")
    meta.enable_auto_pack()
    meta["k"] = 1
    meta.close()
    before = path.read_bytes()
    store = SimpleNamespace()
    open_store_tables(store, str(path), read_only=True, policy=resolve_table_policy())
    try:
        assert store.fts is None
        assert store.meta.get("k") == 1
        assert all(
            isinstance(getattr(store, name), AbsentTable)
            for name in TABLE_NAMES
            if name != "meta"
        )
    finally:
        _release(store)
    assert path.read_bytes() == before


def test_failed_open_leaves_earlier_tables_assigned(tmp_path, monkeypatch):
    real = store_tables_mod.KVault

    def failing(target, *, table, **options):
        if table == "events":
            raise OSError("events open failed")
        return real(target, table=table, **options)

    monkeypatch.setattr(store_tables_mod, "KVault", failing)
    store = SimpleNamespace()
    with pytest.raises(OSError, match="events open failed"):
        open_store_tables(
            store,
            str(tmp_path / "s.kohakutr"),
            read_only=False,
            policy=resolve_table_policy(),
        )
    try:
        assert isinstance(store.meta, KVault) and isinstance(store.state, KVault)
        assert not hasattr(store, "events")
    finally:
        _release(store)
