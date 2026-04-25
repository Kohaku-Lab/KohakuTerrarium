"""Terrarium shared SessionStore under concurrent creature writes.

Covers the pattern wired by
``terrarium/persistence.py:attach_session_store``: each creature
writes events into one shared ``SessionStore`` under a per-creature
``<agent>:e<seq>`` namespace, and channel ``on_send`` callbacks append
to ``channels`` under ``<channel>:m<seq>``. Today the store relies on
KohakuVault/SQLite serialization for atomicity.
"""

import asyncio
from types import SimpleNamespace

import pytest

from kohakuterrarium.core.channel import (
    AgentChannel,
    ChannelMessage,
    SubAgentChannel,
)
from kohakuterrarium.session.output import SessionOutput
from kohakuterrarium.session.store import SessionStore


@pytest.fixture
def shared_store(tmp_path):
    path = tmp_path / "terrarium_shared.kohakutr"
    store = SessionStore(path)
    store.init_meta(
        session_id="terrarium_shared",
        config_type="terrarium",
        config_path="/tmp/terrarium",
        pwd=str(tmp_path),
        agents=["root", "swe", "reviewer"],
        terrarium_name="swe_team",
        terrarium_channels=[
            {"name": "tasks", "type": "queue"},
            {"name": "team_chat", "type": "broadcast"},
        ],
        terrarium_creatures=[
            {"name": "swe", "listen": ["tasks"], "send": ["review"]},
            {"name": "reviewer", "listen": ["review"], "send": []},
        ],
    )
    yield store
    store.close()


def _make_output(agent_name: str, store: SessionStore) -> SessionOutput:
    agent_stub = SimpleNamespace(controller=None, session=None)
    return SessionOutput(agent_name, store, agent_stub)


def _register_channel_persistence(channel, store: SessionStore) -> None:
    """Mirror ``attach_session_store``'s on_send wiring for one channel."""

    def _cb(channel_name: str, message: ChannelMessage) -> None:
        store.save_channel_message(
            channel_name,
            {
                "sender": message.sender,
                "content": (
                    message.content
                    if isinstance(message.content, str)
                    else str(message.content)
                ),
                "msg_id": message.message_id,
            },
        )

    channel.on_send(_cb)


async def _creature_event_stream(output: SessionOutput, agent: str, count: int) -> None:
    """Simulate a creature emitting tool activity."""
    for i in range(count):
        output.on_activity_with_metadata(
            "tool_start",
            f"[work_{i}] running",
            {"job_id": f"{agent}_{i}", "args": {"step": i}},
        )
        await asyncio.sleep(0)
        output.on_activity_with_metadata(
            "tool_done",
            f"[work_{i}] {agent} completed step {i}",
            {"job_id": f"{agent}_{i}", "result": f"result from {agent} step {i}"},
        )
        await asyncio.sleep(0)


async def _channel_producer(channel: SubAgentChannel, sender: str, count: int) -> None:
    """Send ``count`` messages from ``sender`` into the channel."""
    for i in range(count):
        msg = ChannelMessage(
            sender=sender,
            content=f"message {i} from {sender}",
        )
        await channel.send(msg)
        await asyncio.sleep(0)


class TestPerCreatureEventIsolation:
    """Each creature's events stay namespaced under its own key prefix."""

    async def test_per_creature_event_keys_isolated(self, shared_store):
        swe_out = _make_output("swe", shared_store)
        rev_out = _make_output("reviewer", shared_store)

        await asyncio.gather(
            _creature_event_stream(swe_out, "swe", 6),
            _creature_event_stream(rev_out, "reviewer", 6),
        )

        swe_events = shared_store.get_events("swe")
        rev_events = shared_store.get_events("reviewer")
        assert len(swe_events) == 12  # 6 tool_call + 6 tool_result
        assert len(rev_events) == 12

        # No cross-contamination: swe's events never contain reviewer
        # job ids and vice versa.
        swe_job_ids = {e.get("call_id") for e in swe_events if "call_id" in e}
        rev_job_ids = {e.get("call_id") for e in rev_events if "call_id" in e}
        assert swe_job_ids.isdisjoint(rev_job_ids)
        assert all(jid.startswith("swe_") for jid in swe_job_ids)
        assert all(jid.startswith("reviewer_") for jid in rev_job_ids)


class TestChannelMessagePreservation:
    """Concurrent channel sends preserve every message."""

    async def test_queue_channel_messages_all_persisted(self, shared_store):
        tasks = SubAgentChannel("tasks", description="task queue")
        _register_channel_persistence(tasks, shared_store)

        await asyncio.gather(
            _channel_producer(tasks, "root", 10),
            _channel_producer(tasks, "swe", 10),
        )

        msgs = shared_store.get_channel_messages("tasks")
        assert len(msgs) == 20
        senders = {m["sender"] for m in msgs}
        assert senders == {"root", "swe"}

        # Each sender contributed exactly 10 messages.
        from_root = [m for m in msgs if m["sender"] == "root"]
        from_swe = [m for m in msgs if m["sender"] == "swe"]
        assert len(from_root) == 10
        assert len(from_swe) == 10

    async def test_broadcast_channel_messages_all_persisted(self, shared_store):
        team_chat = AgentChannel("team_chat", description="broadcast")
        # Subscribe receivers so send() has delivery targets.
        team_chat.subscribe("swe")
        team_chat.subscribe("reviewer")
        _register_channel_persistence(team_chat, shared_store)

        await asyncio.gather(
            _channel_producer(team_chat, "root", 8),
            _channel_producer(team_chat, "swe", 8),
        )

        msgs = shared_store.get_channel_messages("team_chat")
        # One row per send — subscribers don't multiply the persisted count.
        assert len(msgs) == 16


class TestFTSCoversAllCreatures:
    """FTS indexes content from every creature's stream."""

    async def test_fts_rows_for_each_creature(self, shared_store):
        swe_out = _make_output("swe", shared_store)
        rev_out = _make_output("reviewer", shared_store)

        await asyncio.gather(
            _creature_event_stream(swe_out, "swe", 5),
            _creature_event_stream(rev_out, "reviewer", 5),
        )

        shared_store.flush()
        # The tool_done metadata's ``result`` key becomes the event's
        # ``output``, which is what ``append_event`` indexes.
        swe_hits = shared_store.search("result from swe")
        rev_hits = shared_store.search("result from reviewer")
        assert swe_hits, "FTS missed swe's output"
        assert rev_hits, "FTS missed reviewer's output"
        # Each hit's metadata identifies the owning agent.
        swe_agents = {r["meta"].get("agent") for r in swe_hits}
        rev_agents = {r["meta"].get("agent") for r in rev_hits}
        assert "swe" in swe_agents
        assert "reviewer" in rev_agents


class TestConcurrentAcrossChannelsAndEvents:
    """Events + channels + multiple creatures in one gather() call."""

    async def test_all_streams_survive(self, shared_store):
        swe_out = _make_output("swe", shared_store)
        rev_out = _make_output("reviewer", shared_store)
        tasks = SubAgentChannel("tasks")
        _register_channel_persistence(tasks, shared_store)

        await asyncio.gather(
            _creature_event_stream(swe_out, "swe", 4),
            _creature_event_stream(rev_out, "reviewer", 4),
            _channel_producer(tasks, "root", 5),
            _channel_producer(tasks, "reviewer", 5),
        )

        assert len(shared_store.get_events("swe")) == 8
        assert len(shared_store.get_events("reviewer")) == 8
        assert len(shared_store.get_channel_messages("tasks")) == 10

        # Meta still valid after heavy concurrent writes.
        meta = shared_store.load_meta()
        assert meta["terrarium_name"] == "swe_team"
        assert set(meta["agents"]) == {"root", "swe", "reviewer"}
