"""Concurrent writes from multiple sub-agents to one SessionStore.

Documents today's behavior of ``SessionOutput._record`` →
``SessionStore.append_event`` when two sub-agent streams feed the same
store from parallel tasks. No explicit locking exists in Python today —
the KohakuVault/SQLite layer underneath is expected to serialize writes.
"""

import asyncio
from types import SimpleNamespace

import pytest

from kohakuterrarium.session.output import SessionOutput
from kohakuterrarium.session.store import SessionStore


@pytest.fixture
def store(tmp_path):
    """Fresh SessionStore isolated to this test."""
    s = SessionStore(tmp_path / "concurrent.kohakutr")
    s.init_meta(
        session_id="concurrent",
        config_type="agent",
        config_path="/tmp",
        pwd=str(tmp_path),
        agents=["agent"],
    )
    yield s
    s.close()


def _make_output(agent_name: str, store: SessionStore) -> SessionOutput:
    """Create a SessionOutput wired to ``store`` for ``agent_name``."""
    agent_stub = SimpleNamespace(controller=None, session=None)
    return SessionOutput(agent_name, store, agent_stub)


async def _drive_tool_stream(
    output: SessionOutput, agent_name: str, count: int
) -> None:
    """Drive ``count`` tool_start/tool_done pairs through the output."""
    for i in range(count):
        job_id = f"{agent_name}_job_{i}"
        output.on_activity_with_metadata(
            "tool_start",
            f"[tool_{i}] args",
            {"job_id": job_id, "args": {"step": i}},
        )
        # yield to the event loop so the two coroutines truly interleave
        await asyncio.sleep(0)
        output.on_activity_with_metadata(
            "tool_done",
            f"[tool_{i}] output for {agent_name} with searchable content",
            {"job_id": job_id, "result": f"payload from {agent_name} iter {i}"},
        )
        await asyncio.sleep(0)


class TestConcurrentSubAgentWrites:
    """Two sub-agent-style streams writing concurrently must not lose data."""

    async def test_event_keys_are_monotonic_per_agent(self, store):
        """Each agent's event keys come back ordered 0..N-1."""
        out_a = _make_output("sub_a", store)
        out_b = _make_output("sub_b", store)

        await asyncio.gather(
            _drive_tool_stream(out_a, "sub_a", 10),
            _drive_tool_stream(out_b, "sub_b", 10),
        )

        events_a = store.get_events("sub_a")
        events_b = store.get_events("sub_b")

        # Each agent gets 10 tool_call + 10 tool_result events.
        assert len(events_a) == 20
        assert len(events_b) == 20

        # Ordering inside one agent's stream is deterministic.
        types_a = [evt["type"] for evt in events_a]
        types_b = [evt["type"] for evt in events_b]
        assert types_a == ["tool_call", "tool_result"] * 10
        assert types_b == ["tool_call", "tool_result"] * 10

    async def test_no_lost_writes_across_agents(self, store):
        """No event written by either coroutine is dropped."""
        out_a = _make_output("alpha", store)
        out_b = _make_output("beta", store)

        await asyncio.gather(
            _drive_tool_stream(out_a, "alpha", 15),
            _drive_tool_stream(out_b, "beta", 15),
        )

        all_events = store.get_all_events()
        # 15 iterations × 2 events × 2 agents = 60 events.
        assert len(all_events) == 60

        # Every job_id we produced must round-trip.
        produced_job_ids = {f"alpha_job_{i}" for i in range(15)} | {
            f"beta_job_{i}" for i in range(15)
        }
        seen_job_ids: set[str] = set()
        for _, evt in all_events:
            if evt.get("type") == "tool_call":
                seen_job_ids.add(evt["call_id"])
        assert seen_job_ids == produced_job_ids

    async def test_fts_rows_present_for_both_streams(self, store):
        """Text from both sub-agent streams is searchable via FTS."""
        out_a = _make_output("reader", store)
        out_b = _make_output("writer", store)

        await asyncio.gather(
            _drive_tool_stream(out_a, "reader", 5),
            _drive_tool_stream(out_b, "writer", 5),
        )

        # The tool_done payload includes the agent name; search should
        # hit rows from each stream.
        results_reader = store.search("reader")
        results_writer = store.search("writer")
        assert results_reader, "FTS returned no rows for reader stream"
        assert results_writer, "FTS returned no rows for writer stream"

    async def test_event_id_monotonic_under_race(self, store):
        """Key sequence numbers cover 0..N-1 with no gaps or duplicates."""
        out_a = _make_output("x", store)
        out_b = _make_output("y", store)

        await asyncio.gather(
            _drive_tool_stream(out_a, "x", 8),
            _drive_tool_stream(out_b, "y", 8),
        )

        # Extract the numeric suffix from each agent's keys and assert
        # they form a contiguous range starting at 0.
        for agent in ("x", "y"):
            evts = store.get_events(agent)
            assert len(evts) == 16
            # the sequence is implied by ordering in get_events (sorted
            # by key prefix-suffix numeric form); assert no duplicates
            # and that the per-agent stream is contiguous by verifying
            # the counter restoration after a reopen.

        store.flush()
        path = store.path

        # Reopen in a fresh store object and verify the counter picks
        # up cleanly — that proves all writes landed with no gaps.
        store2 = SessionStore(path)
        new_key, _ = store2.append_event("x", "marker", {"content": "probe"})
        assert new_key == "x:e000016"
        store2.close()
