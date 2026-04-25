"""Wave C — ``text_chunk`` events collapse into one assistant message.

Covers the round-trip:

- SessionOutput.write_stream writes one ``text_chunk`` per call.
- The in-store event stream preserves ``chunk_seq``.
- ``replay_conversation`` collapses the run into a single assistant
  message.
"""

from types import SimpleNamespace

import pytest

from kohakuterrarium.session.history import replay_conversation
from kohakuterrarium.session.output import SessionOutput
from kohakuterrarium.session.store import SessionStore


@pytest.fixture
def store(tmp_path):
    s = SessionStore(tmp_path / "chunks.kohakutr")
    s.init_meta(
        session_id="chunks",
        config_type="agent",
        config_path="/tmp",
        pwd=str(tmp_path),
        agents=["agent"],
    )
    yield s
    s.close()


class TestTextChunkEmission:
    @pytest.mark.asyncio
    async def test_write_stream_emits_text_chunk_per_call(self, store):
        agent_stub = SimpleNamespace(controller=None, session=None)
        output = SessionOutput("agent", store, agent_stub)
        await output.on_processing_start()
        await output.write_stream("Hello ")
        await output.write_stream("world")
        await output.write_stream("!")

        chunks = [e for e in store.get_events("agent") if e["type"] == "text_chunk"]
        assert len(chunks) == 3
        assert [c["chunk_seq"] for c in chunks] == [0, 1, 2]
        assert [c["content"] for c in chunks] == ["Hello ", "world", "!"]

    @pytest.mark.asyncio
    async def test_chunk_seq_resets_each_processing_start(self, store):
        agent_stub = SimpleNamespace(controller=None, session=None)
        output = SessionOutput("agent", store, agent_stub)
        await output.on_processing_start()
        await output.write_stream("a")
        await output.on_processing_end()
        await output.on_processing_start()
        await output.write_stream("b")
        chunks = [e for e in store.get_events("agent") if e["type"] == "text_chunk"]
        # Two independent runs — chunk_seq resets to 0 each turn.
        assert chunks[0]["chunk_seq"] == 0
        assert chunks[1]["chunk_seq"] == 0


class TestTextChunkReplay:
    @pytest.mark.asyncio
    async def test_many_chunks_collapse_into_one_assistant_message(self, store):
        agent_stub = SimpleNamespace(controller=None, session=None)
        output = SessionOutput("agent", store, agent_stub)
        await output.on_processing_start()
        for token in ("The ", "quick ", "brown ", "fox"):
            await output.write_stream(token)
        await output.on_processing_end()

        events = store.get_events("agent")
        # Filter out framework housekeeping events for the assertion.
        stateful = [
            e
            for e in events
            if e["type"] in {"text_chunk", "user_message", "assistant_tool_calls"}
        ]
        msgs = replay_conversation(stateful)
        assert msgs == [{"role": "assistant", "content": "The quick brown fox"}]
