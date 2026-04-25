"""Wave B — ``hotplug_transition`` additive event.

Covers the storage layer for the event: the emitter lives in
``terrarium/hotplug.py`` and fires on add / remove. We hit the event
row path directly without spinning a full terrarium.
"""

import pytest

from kohakuterrarium.session.store import SessionStore


@pytest.fixture
def store(tmp_path):
    s = SessionStore(tmp_path / "hotplug_evt.kohakutr")
    s.init_meta(
        session_id="hotplug_evt",
        config_type="terrarium",
        config_path="/tmp",
        pwd=str(tmp_path),
        agents=["root", "swe"],
    )
    yield s
    s.close()


class TestHotplugTransitionStored:
    def test_add_creature_event_round_trip(self, store):
        store.append_event(
            "terrarium",
            "hotplug_transition",
            {"action": "add", "creature": "reviewer"},
        )
        evts = [
            e
            for e in store.get_events("terrarium")
            if e["type"] == "hotplug_transition"
        ]
        assert len(evts) == 1
        assert evts[0]["action"] == "add"
        assert evts[0]["creature"] == "reviewer"

    def test_remove_creature_event(self, store):
        store.append_event(
            "terrarium",
            "hotplug_transition",
            {"action": "remove", "creature": "old"},
        )
        evts = [
            e
            for e in store.get_events("terrarium")
            if e["type"] == "hotplug_transition"
        ]
        assert evts[0]["action"] == "remove"
        assert evts[0]["creature"] == "old"

    def test_add_channel_event(self, store):
        store.append_event(
            "terrarium",
            "hotplug_transition",
            {"action": "add", "channel": "new_chan"},
        )
        evts = [
            e
            for e in store.get_events("terrarium")
            if e["type"] == "hotplug_transition"
        ]
        assert evts[0]["action"] == "add"
        assert evts[0]["channel"] == "new_chan"
