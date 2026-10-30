"""Unit tests for :mod:`kohakuterrarium.session.store.lifecycle.table_policy`."""

import dataclasses
from contextlib import closing

import pytest
from kohakuvault import KVault

from kohakuterrarium.session.store.lifecycle.tables import TABLE_NAMES
from kohakuterrarium.session.store.lifecycle.table_policy import (
    BULK_TABLE,
    DEFAULT_TABLE_POLICY,
    SMALL_TABLE,
    TablePolicy,
    resolve_table_policy,
)


def test_policy_maps_to_kvault_options(tmp_path):
    policy = TablePolicy(
        cache_kb=512, reader_cache_kb=128, reader_idle_limit=3, mmap_size=1 << 20
    )
    assert policy.kvault_options() == {
        "cache_kb": 512,
        "reader_cache_kb": 128,
        "reader_idle_limit": 3,
        "mmap_size": 1 << 20,
    }
    with closing(KVault(str(tmp_path / "p.db"), **policy.kvault_options())) as vault:
        vault["k"] = b"v"
        assert vault["k"] == b"v"
        assert vault.reader_pool_stats()["idle_connections"] <= 3
    with pytest.raises(dataclasses.FrozenInstanceError):
        policy.cache_kb = 1


def test_default_covers_every_store_table():
    assert set(DEFAULT_TABLE_POLICY) == set(TABLE_NAMES)
    assert DEFAULT_TABLE_POLICY["events"] is BULK_TABLE
    assert DEFAULT_TABLE_POLICY["meta"] is SMALL_TABLE


def test_resolve_applies_overrides_without_mutating_default():
    custom = TablePolicy(cache_kb=64)
    resolved = resolve_table_policy({"events": custom})
    assert resolved["events"] is custom
    assert resolved["meta"] is SMALL_TABLE
    assert DEFAULT_TABLE_POLICY["events"] is BULK_TABLE
    assert resolve_table_policy() == dict(DEFAULT_TABLE_POLICY)
    with pytest.raises(ValueError, match="unknown session table 'evnets'"):
        resolve_table_policy({"evnets": custom})
