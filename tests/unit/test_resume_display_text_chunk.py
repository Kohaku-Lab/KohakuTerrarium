"""Resume display surfaces must handle Wave C ``text_chunk`` events
and skip events on superseded branches (regen / edit+rerun).

Six readers were affected by the v2 changes — the four resume-time
event-stream readers (Rich CLI scrollback, TUI replay, plain-stdout
preview, terrarium CLI replay) plus the FTS indexer in
``session/memory.py`` and the frontend's ``_replayEvents``. These
tests pin the contract for the Python-side surfaces.
"""

from kohakuterrarium.builtins.outputs.stdout import _group_resume_events
from kohakuterrarium.builtins.tui.output import _group_into_turns
from kohakuterrarium.session.memory import _extract_blocks
from kohakuterrarium.terrarium.cli_output import _group_resume_events as _terra_group

# ---- TUI ------------------------------------------------------------


class TestTUIGroupIntoTurns:
    def test_renders_text_chunk_events(self):
        events = [
            {
                "type": "user_input",
                "content": "hi",
                "event_id": 1,
                "turn_index": 1,
                "branch_id": 1,
            },
            {
                "type": "processing_start",
                "event_id": 2,
                "turn_index": 1,
                "branch_id": 1,
            },
            {
                "type": "text_chunk",
                "content": "Hel",
                "chunk_seq": 0,
                "event_id": 3,
                "turn_index": 1,
                "branch_id": 1,
            },
            {
                "type": "text_chunk",
                "content": "lo!",
                "chunk_seq": 1,
                "event_id": 4,
                "turn_index": 1,
                "branch_id": 1,
            },
            {
                "type": "processing_end",
                "event_id": 5,
                "turn_index": 1,
                "branch_id": 1,
            },
        ]
        turns = _group_into_turns(events)
        assert len(turns) == 1
        text_steps = [s for s in turns[0]["steps"] if s[0] == "text"]
        assert len(text_steps) == 1
        assert text_steps[0][1] == "Hello!"

    def test_drops_old_branch_events(self):
        # Each branch is self-contained: it carries its own user_input.
        events = [
            {
                "type": "user_input",
                "content": "hi",
                "event_id": 1,
                "turn_index": 1,
                "branch_id": 1,
            },
            {
                "type": "processing_start",
                "event_id": 2,
                "turn_index": 1,
                "branch_id": 1,
            },
            {
                "type": "text_chunk",
                "content": "OLD",
                "chunk_seq": 0,
                "event_id": 3,
                "turn_index": 1,
                "branch_id": 1,
            },
            {
                "type": "processing_end",
                "event_id": 4,
                "turn_index": 1,
                "branch_id": 1,
            },
            # regen — branch 2 with its own user_input (mirrored content)
            {
                "type": "user_input",
                "content": "hi",
                "event_id": 5,
                "turn_index": 1,
                "branch_id": 2,
            },
            {
                "type": "processing_start",
                "event_id": 6,
                "turn_index": 1,
                "branch_id": 2,
            },
            {
                "type": "text_chunk",
                "content": "NEW",
                "chunk_seq": 0,
                "event_id": 7,
                "turn_index": 1,
                "branch_id": 2,
            },
            {
                "type": "processing_end",
                "event_id": 8,
                "turn_index": 1,
                "branch_id": 2,
            },
        ]
        turns = _group_into_turns(events)
        all_text = "".join(
            s[1] for turn in turns for s in turn["steps"] if s[0] == "text"
        )
        assert "OLD" not in all_text
        assert all_text == "NEW"

    def test_legacy_text_events_still_work(self):
        events = [
            {"type": "user_input", "content": "hi", "event_id": 1},
            {"type": "text", "content": "legacy text", "event_id": 2},
        ]
        turns = _group_into_turns(events)
        assert len(turns) == 1
        text_steps = [s for s in turns[0]["steps"] if s[0] == "text"]
        assert text_steps == [("text", "legacy text")]


# ---- stdout (plain) -------------------------------------------------


class TestStdoutGroupResume:
    def test_renders_text_chunk_events(self):
        events = [
            {
                "type": "user_input",
                "content": "hi",
                "event_id": 1,
                "turn_index": 1,
                "branch_id": 1,
            },
            {
                "type": "text_chunk",
                "content": "abc",
                "chunk_seq": 0,
                "event_id": 2,
                "turn_index": 1,
                "branch_id": 1,
            },
            {
                "type": "text_chunk",
                "content": "def",
                "chunk_seq": 1,
                "event_id": 3,
                "turn_index": 1,
                "branch_id": 1,
            },
        ]
        turns = _group_resume_events(events)
        assert len(turns) == 1
        assert turns[0]["text"] == "abcdef"

    def test_drops_old_branch_events(self):
        events = [
            {
                "type": "user_input",
                "content": "hi",
                "event_id": 1,
                "turn_index": 1,
                "branch_id": 1,
            },
            {
                "type": "text_chunk",
                "content": "OLD",
                "chunk_seq": 0,
                "event_id": 2,
                "turn_index": 1,
                "branch_id": 1,
            },
            {
                "type": "text_chunk",
                "content": "NEW",
                "chunk_seq": 0,
                "event_id": 3,
                "turn_index": 1,
                "branch_id": 2,
            },
        ]
        turns = _group_resume_events(events)
        assert len(turns) == 1
        assert turns[0]["text"] == "NEW"


# ---- terrarium CLI --------------------------------------------------


class TestTerrariumGroupResume:
    def test_renders_text_chunk_events(self):
        events = [
            {
                "type": "user_input",
                "content": "q",
                "event_id": 1,
                "turn_index": 1,
                "branch_id": 1,
            },
            {
                "type": "text_chunk",
                "content": "ans",
                "chunk_seq": 0,
                "event_id": 2,
                "turn_index": 1,
                "branch_id": 1,
            },
        ]
        turns = _terra_group(events)
        assert turns and turns[0]["text"] == "ans"

    def test_drops_old_branch_events(self):
        events = [
            {
                "type": "user_input",
                "content": "q",
                "event_id": 1,
                "turn_index": 1,
                "branch_id": 1,
            },
            {
                "type": "text_chunk",
                "content": "old",
                "chunk_seq": 0,
                "event_id": 2,
                "turn_index": 1,
                "branch_id": 1,
            },
            {
                "type": "text_chunk",
                "content": "new",
                "chunk_seq": 0,
                "event_id": 3,
                "turn_index": 1,
                "branch_id": 2,
            },
        ]
        turns = _terra_group(events)
        assert turns and turns[0]["text"] == "new"


# ---- session/memory FTS indexing -----------------------------------


class TestMemoryIndexing:
    def test_indexes_text_chunk_events(self):
        events = [
            {
                "type": "user_input",
                "content": "hi",
                "ts": 0,
                "event_id": 1,
                "turn_index": 1,
                "branch_id": 1,
            },
            {
                "type": "text_chunk",
                "content": "indexable streaming reply",
                "chunk_seq": 0,
                "ts": 1,
                "event_id": 2,
                "turn_index": 1,
                "branch_id": 1,
            },
        ]
        blocks = _extract_blocks("alice", events)
        types = [b.block_type for b in blocks]
        assert "user" in types
        assert "text" in types
        text_block = next(b for b in blocks if b.block_type == "text")
        assert "indexable" in text_block.content

    def test_skips_old_branch_text_chunks(self):
        events = [
            {
                "type": "user_input",
                "content": "hi",
                "ts": 0,
                "event_id": 1,
                "turn_index": 1,
                "branch_id": 1,
            },
            {
                "type": "text_chunk",
                "content": "OLD assistant reply",
                "chunk_seq": 0,
                "ts": 1,
                "event_id": 2,
                "turn_index": 1,
                "branch_id": 1,
            },
            # branch 2: own user_input + assistant
            {
                "type": "user_input",
                "content": "hi",
                "ts": 2,
                "event_id": 3,
                "turn_index": 1,
                "branch_id": 2,
            },
            {
                "type": "text_chunk",
                "content": "NEW assistant reply",
                "chunk_seq": 0,
                "ts": 3,
                "event_id": 4,
                "turn_index": 1,
                "branch_id": 2,
            },
        ]
        blocks = _extract_blocks("alice", events)
        text_blocks = [b for b in blocks if b.block_type == "text"]
        assert len(text_blocks) == 1
        assert "NEW" in text_blocks[0].content
        assert "OLD" not in text_blocks[0].content
