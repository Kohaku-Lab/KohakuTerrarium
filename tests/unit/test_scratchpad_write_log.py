"""Wave B — ``scratchpad_write`` additive event.

Scratchpad fires a fire-and-forget observer on every set / delete.
The agent wires that observer to emit a ``scratchpad_write`` activity
through the output router; SessionOutput stores it as an event row.
"""

from types import SimpleNamespace

import pytest

from kohakuterrarium.core.scratchpad import Scratchpad
from kohakuterrarium.session.output import SessionOutput
from kohakuterrarium.session.store import SessionStore


@pytest.fixture
def store(tmp_path):
    s = SessionStore(tmp_path / "pad.kohakutr")
    s.init_meta(
        session_id="pad",
        config_type="agent",
        config_path="/tmp",
        pwd=str(tmp_path),
        agents=["agent"],
    )
    yield s
    s.close()


class TestScratchpadObserver:
    def test_set_fires_observer_with_size(self):
        pad = Scratchpad()
        records: list[tuple[str, str, int]] = []
        pad.set_write_observer(lambda k, a, s: records.append((k, a, s)))
        pad.set("plan", "do stuff")
        assert records == [("plan", "set", len("do stuff".encode("utf-8")))]

    def test_delete_fires_observer_only_when_key_exists(self):
        pad = Scratchpad()
        records: list[tuple[str, str, int]] = []
        pad.set_write_observer(lambda k, a, s: records.append((k, a, s)))
        pad.set("plan", "value")
        records.clear()
        assert pad.delete("plan") is True
        assert records == [("plan", "delete", 0)]
        records.clear()
        assert pad.delete("missing") is False
        assert records == []

    def test_observer_errors_swallowed(self):
        pad = Scratchpad()

        def _broken(k, a, s):
            raise RuntimeError("boom")

        pad.set_write_observer(_broken)
        # Should not raise.
        pad.set("k", "v")


class TestScratchpadWriteStoredAsEvent:
    def test_activity_round_trips_into_event(self, store):
        agent_stub = SimpleNamespace(controller=None, session=None)
        output = SessionOutput("agent", store, agent_stub)

        output.on_activity_with_metadata(
            "scratchpad_write",
            "[agent] set plan",
            {"agent": "agent", "key": "plan", "action": "set", "size_bytes": 42},
        )

        evts = [e for e in store.get_events("agent") if e["type"] == "scratchpad_write"]
        assert len(evts) == 1
        assert evts[0]["key"] == "plan"
        assert evts[0]["action"] == "set"
        assert evts[0]["size_bytes"] == 42
