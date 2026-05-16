"""Unit tests for :mod:`kohakuterrarium.terrarium.root`."""

from kohakuterrarium.terrarium import root as root_mod
from kohakuterrarium.testing.terrarium import TestTerrariumBuilder


class TestAssignRootTo:
    async def test_assign_to_lone_creature(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        try:
            creature = t.get_creature("alice")
            out = await root_mod.assign_root_to(t, creature)
            assert out.root_id == "alice"
            assert out.report_channel == "report_to_root"
            assert "report_to_root" in out.channels_created
            assert creature.is_privileged is True
        finally:
            await t.shutdown()

    async def test_assign_with_existing_peers(self):
        t = await (
            TestTerrariumBuilder()
            .with_creature("alice")
            .with_creature("bob")
            .with_creature("carol")
            .build()
        )
        try:
            alice = t.get_creature("alice")
            out = await root_mod.assign_root_to(t, alice)
            # Bob and carol should both become senders.
            assert "bob" in out.senders_added
            assert "carol" in out.senders_added
        finally:
            await t.shutdown()

    async def test_assign_listens_on_existing_channels(self):
        t = await (
            TestTerrariumBuilder()
            .with_creature("alice")
            .with_creature("bob")
            .with_channel("chat")
            .build()
        )
        try:
            alice = t.get_creature("alice")
            out = await root_mod.assign_root_to(t, alice)
            assert "chat" in out.channels_listened
        finally:
            await t.shutdown()

    async def test_assign_skips_already_listening_channels(self):
        t = await (
            TestTerrariumBuilder()
            .with_creature("alice")
            .with_creature("bob")
            .with_channel("chat")
            .with_connection("bob", "alice", channel="chat")
            .build()
        )
        try:
            alice = t.get_creature("alice")
            out = await root_mod.assign_root_to(t, alice)
            # Alice already listens on "chat" via the connect → not in
            # channels_listened (only "report_to_root").
            assert out.channels_listened == ["report_to_root"]
        finally:
            await t.shutdown()

    async def test_assign_uses_custom_report_channel(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        try:
            alice = t.get_creature("alice")
            out = await root_mod.assign_root_to(t, alice, report_channel="status")
            assert out.report_channel == "status"
        finally:
            await t.shutdown()

    async def test_assign_reuses_existing_report_channel(self):
        t = await (
            TestTerrariumBuilder()
            .with_creature("alice")
            .with_channel("report_to_root")
            .build()
        )
        try:
            alice = t.get_creature("alice")
            out = await root_mod.assign_root_to(t, alice)
            # Existing channel — no creation.
            assert out.channels_created == []
        finally:
            await t.shutdown()
