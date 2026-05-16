"""Unit tests for :mod:`kohakuterrarium.terrarium` __init__ deferred attrs."""

import pytest

import kohakuterrarium.terrarium as terr


def test_remote_service_lazy_import():
    out = terr.RemoteTerrariumService
    # Same import via the submodule path resolves to the same object.
    from kohakuterrarium.terrarium.remote_service import RemoteTerrariumService

    assert out is RemoteTerrariumService


def test_multi_node_service_lazy_import():
    out = terr.MultiNodeTerrariumService
    from kohakuterrarium.terrarium.multi_node_service import (
        MultiNodeTerrariumService,
    )

    assert out is MultiNodeTerrariumService


def test_unknown_attribute_raises():
    with pytest.raises(AttributeError):
        _ = terr.NotARealName


def test_all_contains_core_exports():
    for name in (
        "Terrarium",
        "Creature",
        "CreatureInfo",
        "TerrariumService",
        "LocalTerrariumService",
    ):
        assert name in terr.__all__
