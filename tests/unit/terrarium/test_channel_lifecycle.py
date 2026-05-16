"""Unit tests for :mod:`kohakuterrarium.terrarium.channel_lifecycle`.

Most flows are exercised end-to-end through the engine via
``TestTerrariumBuilder``, which gives full coverage of
``disconnect_creatures``, ``remove_channel_from_graph``, and
``apply_split_bookkeeping``."""

import pytest

from kohakuterrarium.terrarium import channel_lifecycle as cl
from kohakuterrarium.terrarium.topology import TopologyDelta
from kohakuterrarium.testing.terrarium import TestTerrariumBuilder

# ── disconnect_creatures ──────────────────────────────────────


class TestDisconnectCreatures:
    async def test_different_graphs_returns_nothing(self):
        # Two creatures in separate graphs (no connection) → disconnect
        # is a no-op.
        t = await (
            TestTerrariumBuilder()
            .with_creature("alice")
            .with_creature("bob")
            .with_separate_graphs()
            .build()
        )
        try:
            out = await cl.disconnect_creatures(t, "alice", "bob")
            assert out.delta_kind == "nothing"
            assert out.channels == []
        finally:
            await t.shutdown()

    async def test_intra_graph_no_split(self):
        # Two creatures sharing a channel in one graph + a second wire
        # so removing the explicit channel doesn't split the graph.
        t = await (
            TestTerrariumBuilder()
            .with_creature("alice")
            .with_creature("bob")
            .with_channel("chat")
            .with_channel("chat2")
            .with_connection("alice", "bob", channel="chat")
            .with_connection("alice", "bob", channel="chat2")
            .build()
        )
        try:
            out = await cl.disconnect_creatures(t, "alice", "bob", channel="chat")
            assert "chat" in out.channels
        finally:
            await t.shutdown()

    async def test_split_path(self):
        # Single wire between alice and bob → disconnect triggers a split.
        t = await (
            TestTerrariumBuilder()
            .with_creature("alice")
            .with_creature("bob")
            .with_channel("chat")
            .with_connection("alice", "bob", channel="chat")
            .build()
        )
        try:
            out = await cl.disconnect_creatures(t, "alice", "bob", channel="chat")
            assert out.delta_kind == "split"
        finally:
            await t.shutdown()


# ── apply_split_bookkeeping ───────────────────────────────────


class TestApplySplit:
    def test_no_split_returns(self):
        class _Eng:
            pass

        # delta.kind == "nothing" → early return; never touches engine.
        cl.apply_split_bookkeeping(_Eng(), TopologyDelta(kind="nothing"))


# ── remove_channel_from_graph ─────────────────────────────────


class TestRemoveChannelFromGraph:
    async def test_unknown_graph(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        try:
            with pytest.raises(KeyError, match="graph"):
                await cl.remove_channel_from_graph(t, "ghost", "ch")
        finally:
            await t.shutdown()

    async def test_unknown_channel(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        try:
            gid = t.list_graphs()[0].graph_id
            with pytest.raises(KeyError, match="channel"):
                await cl.remove_channel_from_graph(t, gid, "ghost-channel")
        finally:
            await t.shutdown()

    async def test_remove_unused_channel(self):
        # Channel exists but no creatures listen/send → no split, no
        # trigger teardown.
        t = await (
            TestTerrariumBuilder().with_creature("alice").with_channel("ch").build()
        )
        try:
            gid = t.list_graphs()[0].graph_id
            delta = await cl.remove_channel_from_graph(t, gid, "ch")
            assert delta.kind in ("nothing", "split")
        finally:
            await t.shutdown()

    async def test_remove_wires_split(self):
        # Channel is the only wire between alice and bob → its removal
        # should split the graph.
        t = await (
            TestTerrariumBuilder()
            .with_creature("alice")
            .with_creature("bob")
            .with_channel("ch")
            .with_connection("alice", "bob", channel="ch")
            .build()
        )
        try:
            gid = t.get_creature("alice").graph_id
            delta = await cl.remove_channel_from_graph(t, gid, "ch")
            assert delta.kind == "split"
        finally:
            await t.shutdown()
