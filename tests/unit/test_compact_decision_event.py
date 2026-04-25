"""Wave B — ``compact_decision`` additive event.

SessionOutput maps the activity to an event row. CompactManager emits
the activity from its triggered + skipped paths (see ``core/compact.py``).
"""

from types import SimpleNamespace

import pytest

from kohakuterrarium.session.output import SessionOutput
from kohakuterrarium.session.store import SessionStore


@pytest.fixture
def store(tmp_path):
    s = SessionStore(tmp_path / "compact.kohakutr")
    s.init_meta(
        session_id="compact",
        config_type="agent",
        config_path="/tmp",
        pwd=str(tmp_path),
        agents=["agent"],
    )
    yield s
    s.close()


class TestCompactDecisionHandler:
    def test_triggered_decision(self, store):
        agent_stub = SimpleNamespace(controller=None, session=None)
        output = SessionOutput("agent", store, agent_stub)

        output.on_activity_with_metadata(
            "compact_decision",
            "[agent] triggered",
            {
                "reason": "threshold",
                "tokens_before": 40000,
                "tokens_after": 16000,
                "skipped": False,
            },
        )
        evts = [e for e in store.get_events("agent") if e["type"] == "compact_decision"]
        assert len(evts) == 1
        assert evts[0]["reason"] == "threshold"
        assert evts[0]["skipped"] is False
        assert evts[0]["tokens_before"] == 40000

    def test_skipped_decision(self, store):
        agent_stub = SimpleNamespace(controller=None, session=None)
        output = SessionOutput("agent", store, agent_stub)

        output.on_activity_with_metadata(
            "compact_decision",
            "[agent] skipped (too_short)",
            {
                "reason": "too_short",
                "tokens_before": 0,
                "tokens_after": 0,
                "skipped": True,
            },
        )
        evts = [e for e in store.get_events("agent") if e["type"] == "compact_decision"]
        assert len(evts) == 1
        assert evts[0]["skipped"] is True
        assert evts[0]["reason"] == "too_short"
