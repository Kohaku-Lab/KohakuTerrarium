"""Unit tests for :mod:`kohakuterrarium.session.migrations.v1_to_v2`."""

import pytest

from kohakuterrarium.session.migrations.v1_to_v2 import (
    _backfill_assistant_tool_call_content,
    _coerce_args,
    _copy_artifacts,
    _copy_kvault_table,
    _copy_meta_fields,
    _copy_state,
    _flush_pending_tool_calls,
    _highest_synthetic_event_id,
    _iter_agents,
    _synth_events_from_message,
    _synth_events_from_snapshot,
    _translate_v1_events,
    migrate,
)
from kohakuterrarium.session.store import SessionStore

# ── _coerce_args ──────────────────────────────────────────────────


class TestCoerceArgs:
    def test_string_passthrough(self):
        assert _coerce_args('{"x":1}') == '{"x":1}'

    def test_none_becomes_empty_object(self):
        assert _coerce_args(None) == "{}"

    def test_dict_serialised(self):
        assert _coerce_args({"a": 1}) == '{"a": 1}'

    def test_non_serialisable_falls_back(self):
        class _Junk:
            pass

        assert _coerce_args(_Junk()) == "{}"


# ── _flush_pending_tool_calls ────────────────────────────────────


class TestFlushPendingToolCalls:
    def test_empty_returns_none(self):
        assert _flush_pending_tool_calls([]) is None

    def test_drains_pending(self):
        pending = [
            {"call_id": "c1", "name": "bash", "args": {"cmd": "ls"}},
            {"id": "c2", "name": "echo", "args": '{"msg":"hi"}'},
        ]
        out = _flush_pending_tool_calls(pending)
        assert out[0] == "assistant_tool_calls"
        tc = out[1]["tool_calls"]
        assert len(tc) == 2
        assert tc[0]["id"] == "c1"
        assert tc[1]["function"]["arguments"] == '{"msg":"hi"}'
        # Pending list mutated to empty.
        assert pending == []


# ── _translate_v1_events ─────────────────────────────────────────


class TestTranslateV1Events:
    def test_empty(self):
        assert _translate_v1_events([]) == []

    def test_user_input_opens_turn_and_emits_user_message(self):
        out = _translate_v1_events([{"type": "user_input", "content": "hi"}])
        types = [e[0] for e in out]
        assert "user_input" in types
        assert "user_message" in types
        # Turn index advanced to 1.
        assert all(ti == 1 for _, _, ti in out)

    def test_text_chunk_carries_seq(self):
        out = _translate_v1_events(
            [
                {"type": "user_input", "content": "q"},
                {"type": "text", "content": "first"},
                {"type": "text", "content": "second"},
            ]
        )
        text_events = [e for e in out if e[0] == "text_chunk"]
        assert len(text_events) == 2

    def test_tool_call_buffered_then_flushed(self):
        out = _translate_v1_events(
            [
                {"type": "user_input", "content": "q"},
                {
                    "type": "tool_call",
                    "call_id": "c1",
                    "name": "bash",
                    "args": {"cmd": "ls"},
                },
                {
                    "type": "tool_result",
                    "call_id": "c1",
                    "name": "bash",
                    "output": "x",
                },
            ]
        )
        types = [e[0] for e in out]
        # tool_call buffered → assistant_tool_calls flushed before tool_result.
        assert "assistant_tool_calls" in types
        assert "tool_result" in types
        assert types.index("assistant_tool_calls") < types.index("tool_result")

    def test_pending_flushed_on_next_user_input(self):
        out = _translate_v1_events(
            [
                {"type": "user_input", "content": "q1"},
                {"type": "tool_call", "call_id": "c1", "name": "bash"},
                # Second user input flushes the pending tool_call.
                {"type": "user_input", "content": "q2"},
            ]
        )
        types = [e[0] for e in out]
        assert "assistant_tool_calls" in types

    def test_pending_flushed_at_end(self):
        # Pending tool_call with no flushing event before end.
        out = _translate_v1_events(
            [
                {"type": "user_input", "content": "q"},
                {"type": "tool_call", "call_id": "c1", "name": "bash"},
            ]
        )
        types = [e[0] for e in out]
        assert "assistant_tool_calls" in types

    def test_compact_complete_emits_replace(self):
        out = _translate_v1_events(
            [
                {
                    "type": "compact_complete",
                    "summary": "compacted summary",
                    "replaced_from_event_id": 1,
                    "replaced_to_event_id": 5,
                }
            ]
        )
        types = [e[0] for e in out]
        assert "compact_replace" in types
        assert "compact_complete" in types

    def test_observability_passthrough(self):
        out = _translate_v1_events(
            [
                {"type": "trigger_fired", "channel": "c"},
                {"type": "token_usage", "tokens_in": 10},
                {"type": "processing_start"},
            ]
        )
        types = [e[0] for e in out]
        assert "trigger_fired" in types
        assert "token_usage" in types
        assert "processing_start" in types

    def test_pending_tool_call_flushed_before_text(self):
        # A buffered tool_call followed by a text event flushes the
        # tool_call (as assistant_tool_calls) FIRST so the text lands
        # after it.
        out = _translate_v1_events(
            [
                {"type": "tool_call", "name": "bash", "call_id": "c1"},
                {"type": "text", "content": "after the call"},
            ]
        )
        types = [e[0] for e in out]
        assert types.index("assistant_tool_calls") < types.index("text_chunk")

    def test_pending_tool_call_flushed_before_compact(self):
        # A buffered tool_call followed by compact_complete flushes the
        # tool_call before the compact_replace.
        out = _translate_v1_events(
            [
                {"type": "tool_call", "name": "bash", "call_id": "c1"},
                {"type": "compact_complete", "summary": "s"},
            ]
        )
        types = [e[0] for e in out]
        assert types.index("assistant_tool_calls") < types.index("compact_replace")

    def test_subagent_call_passthrough(self):
        # subagent_call is not state-bearing but is kept verbatim so the
        # frontend can still render it.
        out = _translate_v1_events(
            [{"type": "subagent_call", "name": "explore", "job_id": "j1"}]
        )
        assert out[0][0] == "subagent_call"
        assert out[0][1]["name"] == "explore"

    def test_context_cleared_passthrough(self):
        out = _translate_v1_events([{"type": "context_cleared"}])
        assert out[0][0] == "context_cleared"

    def test_subagent_result_passthrough(self):
        out = _translate_v1_events(
            [{"type": "subagent_result", "name": "explore", "output": "done"}]
        )
        assert out[0][0] == "subagent_result"
        assert out[0][1]["output"] == "done"

    def test_compact_start_passthrough(self):
        out = _translate_v1_events([{"type": "compact_start", "round": 2}])
        assert out[0][0] == "compact_start"
        assert out[0][1]["round"] == 2

    def test_unknown_event_passthrough(self):
        out = _translate_v1_events([{"type": "wat"}])
        assert out[0][0] == "wat"

    def test_skips_no_type(self):
        out = _translate_v1_events([{"content": "no type"}])
        assert out == []


# ── _synth_events_from_message ───────────────────────────────────


class TestSynthEventsFromMessage:
    def test_system(self):
        out = _synth_events_from_message({"role": "system", "content": "be x"})
        assert out == [("system_prompt_set", {"content": "be x"})]

    def test_user(self):
        out = _synth_events_from_message({"role": "user", "content": "hi"})
        assert out == [("user_message", {"content": "hi"})]

    def test_assistant_text_only(self):
        out = _synth_events_from_message({"role": "assistant", "content": "reply"})
        assert out[0] == ("text_chunk", {"content": "reply", "chunk_seq": 0})

    def test_assistant_with_tool_calls(self):
        out = _synth_events_from_message(
            {
                "role": "assistant",
                "content": "calling",
                "tool_calls": [{"id": "c1"}],
            }
        )
        types = [t for t, _ in out]
        assert "text_chunk" in types
        assert "assistant_tool_calls" in types

    def test_assistant_empty_emits_placeholder(self):
        out = _synth_events_from_message({"role": "assistant", "content": ""})
        # Falls through to the empty-chunk placeholder.
        assert out == [("text_chunk", {"content": "", "chunk_seq": 0})]

    def test_tool(self):
        out = _synth_events_from_message(
            {
                "role": "tool",
                "name": "bash",
                "tool_call_id": "c1",
                "content": "out",
            }
        )
        assert out[0][0] == "tool_result"
        assert out[0][1]["name"] == "bash"
        assert out[0][1]["call_id"] == "c1"

    def test_unknown_role_returns_empty(self):
        assert _synth_events_from_message({"role": "weird"}) == []


# ── _synth_events_from_snapshot ──────────────────────────────────


class TestSynthEventsFromSnapshot:
    def test_empty(self):
        assert _synth_events_from_snapshot([]) == []

    def test_user_advances_turn(self):
        out = _synth_events_from_snapshot(
            [
                {"role": "user", "content": "q1"},
                {"role": "assistant", "content": "a1"},
                {"role": "user", "content": "q2"},
            ]
        )
        # First user -> turn 1; assistant stays in turn 1; second user -> 2.
        turn_indices = [t for _, _, t in out]
        assert turn_indices == [1, 1, 2]


# ── _backfill_assistant_tool_call_content ────────────────────────


class TestBackfillContent:
    def test_no_assistant_tool_calls_unchanged(self):
        triples = [("text_chunk", {"content": "hi"}, 1)]
        out = _backfill_assistant_tool_call_content(triples)
        assert out == triples

    def test_fills_empty_content(self):
        triples = [
            ("text_chunk", {"content": "hello "}, 1),
            ("text_chunk", {"content": "world"}, 1),
            ("assistant_tool_calls", {"tool_calls": [], "content": ""}, 1),
        ]
        out = _backfill_assistant_tool_call_content(triples)
        atc = next(t for t in out if t[0] == "assistant_tool_calls")
        assert atc[1]["content"] == "hello world"

    def test_doesnt_clobber_existing_content(self):
        triples = [
            ("text_chunk", {"content": "ignored"}, 1),
            ("assistant_tool_calls", {"tool_calls": [], "content": "kept"}, 1),
        ]
        out = _backfill_assistant_tool_call_content(triples)
        atc = next(t for t in out if t[0] == "assistant_tool_calls")
        assert atc[1]["content"] == "kept"

    def test_per_turn_scope(self):
        # Text in turn 1 doesn't leak into tool_calls in turn 2.
        triples = [
            ("text_chunk", {"content": "turn1"}, 1),
            ("assistant_tool_calls", {"tool_calls": [], "content": ""}, 2),
        ]
        out = _backfill_assistant_tool_call_content(triples)
        atc = next(t for t in out if t[0] == "assistant_tool_calls")
        assert atc[1]["content"] == ""


# ── _iter_agents ─────────────────────────────────────────────────


class TestIterAgents:
    def test_combines_meta_and_discovered(self, tmp_path):
        s = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            s.append_event("bob", "x", {})
            s.flush()
            agents = _iter_agents(s, {"agents": ["alice"]})
            assert "alice" in agents
            assert "bob" in agents
        finally:
            s.close()

    def test_no_dup(self, tmp_path):
        s = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            s.append_event("alice", "x", {})
            s.flush()
            agents = _iter_agents(s, {"agents": ["alice"]})
            assert agents.count("alice") == 1
        finally:
            s.close()


# ── _highest_synthetic_event_id ──────────────────────────────────


class TestHighestSyntheticEventId:
    def test_no_events_returns_zero(self, tmp_path):
        s = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            assert _highest_synthetic_event_id(s, "alice") == 0
        finally:
            s.close()

    def test_picks_largest_event_id(self, tmp_path):
        s = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            s.append_event("alice", "x", {})
            s.append_event("alice", "x", {})
            s.append_event("alice", "x", {})
            s.flush()
            assert _highest_synthetic_event_id(s, "alice") == 3
        finally:
            s.close()


# ── migrate (end-to-end) ─────────────────────────────────────────


class TestMigrateE2E:
    def test_missing_source_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            migrate(str(tmp_path / "nope.kohakutr"), str(tmp_path / "dst"))

    def test_target_exists_raises(self, tmp_path):
        src = tmp_path / "src.kohakutr"
        dst = tmp_path / "dst.kohakutr.v2"
        src.write_bytes(b"")
        dst.write_bytes(b"")
        with pytest.raises(FileExistsError):
            migrate(str(src), str(dst))

    def test_migrates_event_log(self, tmp_path):
        src_path = tmp_path / "src.kohakutr"
        s = SessionStore(str(src_path))
        try:
            s.meta["format_version"] = 1
            s.init_meta("sess", "agent", "/p", "/w", ["alice"])
            s.append_event("alice", "user_input", {"content": "hi"})
            s.append_event("alice", "text", {"content": "reply"})
            s.flush()
        finally:
            s.close()

        dst_path = tmp_path / "src.kohakutr.v2"
        migrate(str(src_path), str(dst_path))

        # Migrated file exists and has format_version = 2.
        assert dst_path.exists()
        out = SessionStore(str(dst_path))
        try:
            meta = out.load_meta()
            assert meta["format_version"] == 2
            assert "migration" in meta["lineage"]
            evts = out.get_events("alice")
            by_type = {e["type"]: e for e in evts}
            # v1 user_input → v2 user_message, content preserved.
            assert by_type["user_message"]["content"] == "hi"
            # v1 text → v2 text_chunk, content preserved.
            assert by_type["text_chunk"]["content"] == "reply"
        finally:
            out.close()

    def test_migrates_from_snapshot_when_no_events(self, tmp_path):
        src_path = tmp_path / "src.kohakutr"
        s = SessionStore(str(src_path))
        try:
            s.meta["format_version"] = 1
            s.init_meta("sess", "agent", "/p", "/w", ["alice"])
            s.save_conversation(
                "alice",
                [
                    {"role": "system", "content": "be nice"},
                    {"role": "user", "content": "hi"},
                    {"role": "assistant", "content": "hello"},
                ],
            )
            s.flush()
        finally:
            s.close()

        dst_path = tmp_path / "src.kohakutr.v2"
        migrate(str(src_path), str(dst_path))
        out = SessionStore(str(dst_path))
        try:
            evts = out.get_events("alice")
            types = [e["type"] for e in evts]
            assert "system_prompt_set" in types
            assert "user_message" in types
            # Snapshot preserved verbatim through the migration.
            snap = out.load_conversation("alice")
            assert snap == [
                {"role": "system", "content": "be nice"},
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
            ]
        finally:
            out.close()

    def test_copies_state(self, tmp_path):
        src_path = tmp_path / "src.kohakutr"
        s = SessionStore(str(src_path))
        try:
            s.meta["format_version"] = 1
            s.init_meta("sess", "agent", "/p", "/w", ["alice"])
            s.append_event("alice", "user_input", {"content": "q"})
            s.save_state("alice", scratchpad={"k": "v"}, turn_count=2)
            s.flush()
        finally:
            s.close()
        dst_path = tmp_path / "src.kohakutr.v2"
        migrate(str(src_path), str(dst_path))
        out = SessionStore(str(dst_path))
        try:
            assert out.load_scratchpad("alice") == {"k": "v"}
            assert out.load_turn_count("alice") == 2
        finally:
            out.close()

    def test_copies_artifacts(self, tmp_path):
        src_path = tmp_path / "src.kohakutr"
        s = SessionStore(str(src_path))
        try:
            s.meta["format_version"] = 1
            s.init_meta("sess", "agent", "/p", "/w", ["alice"])
            s.append_event("alice", "user_input", {"content": "q"})
            s.write_artifact("a.png", b"DATA")
            s.flush()
        finally:
            s.close()
        dst_path = tmp_path / "src.kohakutr.v2"
        migrate(str(src_path), str(dst_path))
        # Artifact copied to the v2 artifacts dir.
        dest_art = tmp_path / "src.kohakutr.artifacts"
        assert (dest_art / "a.png").read_bytes() == b"DATA"


# -- copy-helper defensive branches -------------------------------


class _FlakyReadTable:
    """Wraps a real KVault table; reading a chosen key raises."""

    def __init__(self, inner, bad_key):
        self._inner = inner
        self._bad_key = bad_key

    def keys(self, **kw):
        return self._inner.keys(**kw)

    def __getitem__(self, key):
        k = key.decode() if isinstance(key, bytes) else key
        if k == self._bad_key:
            raise RuntimeError("read exploded")
        return self._inner[key]

    def __setitem__(self, key, value):
        self._inner[key] = value


class _FlakyWriteTable:
    """Wraps a real KVault table; writing a chosen key raises."""

    def __init__(self, inner, bad_key):
        self._inner = inner
        self._bad_key = bad_key

    def keys(self, **kw):
        return self._inner.keys(**kw)

    def __getitem__(self, key):
        return self._inner[key]

    def __setitem__(self, key, value):
        k = key.decode() if isinstance(key, bytes) else key
        if k == self._bad_key:
            raise RuntimeError("write exploded")
        self._inner[key] = value


class TestCopyHelpersDefensive:
    def test_copy_state_skips_unreadable_key(self, tmp_path):
        # A state key that raises on read is logged + skipped; the
        # readable keys still copy across.
        src = SessionStore(str(tmp_path / "src.kohakutr"))
        dst = SessionStore(str(tmp_path / "dst.kohakutr"))
        try:
            src.state["good"] = {"v": 1}
            src.state["bad"] = {"v": 2}
            src.flush()
            src.state = _FlakyReadTable(src.state, bad_key="bad")
            _copy_state(src, dst)
            assert dst.state["good"] == {"v": 1}
            # The unreadable key never made it across.
            with pytest.raises(KeyError):
                _ = dst.state["bad"]
        finally:
            src._closed = True
            dst.close()

    def test_copy_kvault_table_skips_unreadable_key(self, tmp_path):
        src = SessionStore(str(tmp_path / "src.kohakutr"))
        dst = SessionStore(str(tmp_path / "dst.kohakutr"))
        try:
            src.state["ok"] = {"v": 1}
            src.state["boom"] = {"v": 2}
            src.flush()
            flaky = _FlakyReadTable(src.state, bad_key="boom")
            _copy_kvault_table(flaky, dst.state, "state")
            assert dst.state["ok"] == {"v": 1}
        finally:
            src._closed = True
            dst.close()

    def test_copy_meta_fields_skips_unreadable_and_unwritable(self, tmp_path):
        # A meta key that fails to READ is skipped from both the
        # returned dict and the dest; one that fails to WRITE is still
        # in the returned source_meta but absent from dest.
        src = SessionStore(str(tmp_path / "src.kohakutr"))
        dst = SessionStore(str(tmp_path / "dst.kohakutr"))
        try:
            src.init_meta("sess", "agent", "/p", "/w", ["alice"])
            src.flush()
            # Read failure on the ``hostname`` key.
            src.meta = _FlakyReadTable(src.meta, bad_key="hostname")
            # Write failure on the ``pwd`` key.
            dst.meta = _FlakyWriteTable(dst.meta, bad_key="pwd")
            source_meta = _copy_meta_fields(src, dst)
            # ``hostname`` was unreadable → absent from the returned dict.
            assert "hostname" not in source_meta
            # ``config_type`` copied through fine.
            assert dst.meta["config_type"] == "agent"
            # ``pwd`` is in source_meta but the write failed → not in dst.
            assert source_meta.get("pwd") == "/w"
            with pytest.raises(KeyError):
                _ = dst.meta["pwd"]
        finally:
            src._closed = True
            dst._closed = True

    def test_copy_artifacts_copies_new_subdir_and_file(self, tmp_path):
        # When the target subdir / file does NOT already exist, both are
        # actually copied across (copytree for dirs, copy2 for files).
        from kohakuterrarium.session.artifacts import artifacts_dir_for

        source = tmp_path / "src.kohakutr"
        source.write_bytes(b"")
        art = tmp_path / "src.artifacts"
        (art / "fresh").mkdir(parents=True)
        (art / "fresh" / "n.txt").write_text("nested-data")
        (art / "top.txt").write_text("top-data")
        dest = tmp_path / "dst.kohakutr.v2"
        dest.write_bytes(b"")
        _copy_artifacts(source, dest)
        dest_art = artifacts_dir_for(dest)
        assert (dest_art / "fresh" / "n.txt").read_text() == "nested-data"
        assert (dest_art / "top.txt").read_text() == "top-data"

    def test_copy_artifacts_skips_existing_dir_and_file(self, tmp_path):
        # Both a pre-existing target subdir AND a pre-existing target
        # file are left untouched (the ``target.exists()`` skips).
        from kohakuterrarium.session.artifacts import artifacts_dir_for

        source = tmp_path / "src.kohakutr"
        source.write_bytes(b"")
        art = tmp_path / "src.artifacts"
        (art / "sub").mkdir(parents=True)
        (art / "sub" / "x.txt").write_text("parent-sub")
        (art / "loose.txt").write_text("parent-loose")
        dest = tmp_path / "dst.kohakutr.v2"
        dest.write_bytes(b"")
        dest_art = artifacts_dir_for(dest)
        # Pre-seed BOTH a same-named subdir and a same-named file.
        (dest_art / "sub").mkdir(parents=True, exist_ok=True)
        (dest_art / "sub" / "x.txt").write_text("child-sub")
        (dest_art / "loose.txt").write_text("child-loose")
        _copy_artifacts(source, dest)
        # Neither pre-existing target was clobbered.
        assert (dest_art / "sub" / "x.txt").read_text() == "child-sub"
        assert (dest_art / "loose.txt").read_text() == "child-loose"
