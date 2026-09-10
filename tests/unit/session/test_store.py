"""Unit tests for :mod:`kohakuterrarium.session.store`."""

import gc
import os
import threading
from pathlib import Path

import pytest

import kohakuterrarium.session.store as store_mod
from kohakuterrarium.errors import SessionLockedError
from kohakuterrarium.session.store import SessionStore, iter_kv_keys

try:
    import resource
except ImportError:
    resource = None

# ── helpers ───────────────────────────────────────────────────────


def _store(tmp_path, name="s.kohakutr") -> SessionStore:
    return SessionStore(str(tmp_path / name))


def test_get_events_since_event_id_filters(tmp_path):
    path = tmp_path / "since.kohakutr"
    store = SessionStore(str(path))
    try:
        store.append_event("a", "user_input", {"content": "1"})
        store.append_event("a", "text", {"content": "2"})
        store.append_event("a", "text", {"content": "3"})
        all_events = store.get_events("a")
        cursor = all_events[1]["event_id"]
        tail = store.get_events("a", since_event_id=cursor)
        assert [e["content"] for e in tail] == ["3"]
        assert store.get_events("a", since_event_id=10**9) == []
    finally:
        store.close()


def test_max_event_id_never_underreports_mirrored_ids(tmp_path):
    path = tmp_path / "mirror.kohakutr"
    store = SessionStore(str(path))
    try:
        store.append_event("a", "text", {"content": "local"})
        # A mirrored event carries its source id (larger than any local id).
        store.append_event("a", "text", {"content": "mirrored", "event_id": 500})
        # A later local event gets id 2 — at the last seq position — while a
        # mirrored event earlier in the log holds 500.
        store.append_event("a", "text", {"content": "local2"})
        assert store.max_event_id("a") >= 500
    finally:
        store.close()


def test_max_event_id_matches_full_scan(tmp_path):
    path = tmp_path / "maxid.kohakutr"
    store = SessionStore(str(path))
    try:
        assert store.max_event_id("a") == 0
        for i in range(5):
            store.append_event("a", "text", {"content": f"c{i}"})
        expected = max(e["event_id"] for e in store.get_events("a"))
        assert store.max_event_id("a") == expected
        # event_id is session-global; the agent arg is API symmetry only.
        store.append_event("b", "text", {"content": "x"})
        assert store.max_event_id("b") > expected
    finally:
        store.close()


def test_max_event_id_survives_legacy_missing_counter_slot(tmp_path):
    # Old session files carry no counters:max_event_id. The reopen
    # fallback must full-scan event VALUES and report the true max — a
    # silent 0 would let the resume fast path skip a stale tail.
    from kohakuvault import KVault

    path = tmp_path / "legacy.kohakutr"
    store = SessionStore(str(path))
    store.append_event("a", "text", {"content": "one"})
    store.append_event("a", "text", {"content": "two"})
    store.close()
    state = KVault(str(path), table="state")
    try:
        state.delete("counters:max_event_id")
    finally:
        state.close()

    reopened = SessionStore(str(path))
    try:
        expected = max(e["event_id"] for e in reopened.get_events("a"))
        assert expected == 2
        assert reopened.max_event_id("a") == 2
    finally:
        reopened.close()


def test_append_persists_event_counter_for_reopen(tmp_path):
    path = tmp_path / "sess.kohakutr"
    store = SessionStore(str(path))
    store.append_event("alpha", "user_input", {"content": "one"})
    store.append_event("alpha", "text", {"content": "two"})
    store.close()

    # The persisted counter must have been written during appends/close.
    from kohakuvault import KVault

    state = KVault(str(path), table="state")
    try:
        assert state.get("counters:max_event_id") == 2
    finally:
        state.close()

    reopened = SessionStore(str(path))
    try:
        assert reopened._global_event_id == 2
        _, eid = reopened.append_event("alpha", "text", {"content": "three"})
        assert eid == 3
    finally:
        reopened.close()


# ── construction ──────────────────────────────────────────────────


class TestSessionStoreConstruction:
    def test_construct_basic(self, tmp_path):
        s = _store(tmp_path)
        try:
            assert s.path.endswith("s.kohakutr")
            # All tables open and actually writable + readable.
            s.meta["probe"] = "m"
            s.state["probe"] = "s"
            assert s.meta["probe"] == "m"
            assert s.state["probe"] == "s"
            # events table accepts an append and reads it back.
            _, eid = s.append_event("a", "x", {})
            assert eid == 1
        finally:
            s.close()

    def test_session_id_from_filename(self, tmp_path):
        s = _store(tmp_path, "alice.kohakutr")
        try:
            assert s.session_id == "alice"
        finally:
            s.close()

    def test_session_id_from_meta_wins(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.meta["session_id"] = "stored-id"
            assert s.session_id == "stored-id"
        finally:
            s.close()

    def test_repr(self, tmp_path):
        s = _store(tmp_path)
        try:
            assert "SessionStore" in repr(s)
            # On Windows the repr backslash-escapes — compare via basename.
            assert "s.kohakutr" in repr(s)
        finally:
            s.close()

    def test_custom_flush_thresholds(self, tmp_path):
        s = SessionStore(
            str(tmp_path / "x.kohakutr"),
            flush_every_n_events=10,
            flush_every_n_seconds=5.0,
        )
        try:
            assert s._flush_every_n_events == 10
            assert s._flush_every_n_seconds == 5.0
        finally:
            s.close()


# ── append_event / read paths ────────────────────────────────────


class TestAppendEvent:
    def test_basic(self, tmp_path):
        s = _store(tmp_path)
        try:
            key, eid = s.append_event("alice", "user_message", {"content": "hi"})
            assert key.startswith("alice:e")
            assert eid == 1
            events = s.get_events("alice")
            assert len(events) == 1
            assert events[0]["type"] == "user_message"
            assert events[0]["event_id"] == 1
            # ts auto-stamped.
            assert "ts" in events[0]
        finally:
            s.close()

    def test_event_ids_monotonic(self, tmp_path):
        s = _store(tmp_path)
        try:
            _, e1 = s.append_event("a", "x", {})
            _, e2 = s.append_event("a", "x", {})
            _, e3 = s.append_event("b", "x", {})
            assert (e1, e2, e3) == (1, 2, 3)
        finally:
            s.close()

    def test_branch_metadata_recorded(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.append_event(
                "alice",
                "user_message",
                {"content": "hi"},
                turn_index=2,
                branch_id=1,
                parent_branch_path=[(0, 1), (1, 1)],
            )
            evt = s.get_events("alice")[0]
            assert evt["turn_index"] == 2
            assert evt["branch_id"] == 1
            assert evt["parent_branch_path"] == [[0, 1], [1, 1]]
        finally:
            s.close()

    def test_spawned_in_turn_defaults_to_turn_index(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.append_event("a", "x", {}, turn_index=3)
            evt = s.get_events("a")[0]
            assert evt["spawned_in_turn"] == 3
        finally:
            s.close()

    def test_explicit_spawned_in_turn_kept(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.append_event("a", "x", {}, turn_index=3, spawned_in_turn=99)
            evt = s.get_events("a")[0]
            assert evt["spawned_in_turn"] == 99
        finally:
            s.close()

    def test_counters_survive_reopen(self, tmp_path):
        path = tmp_path / "x.kohakutr"
        s = SessionStore(str(path))
        try:
            s.append_event("a", "x", {})
            s.append_event("a", "x", {})
            s.flush()
        finally:
            s.close()
        s2 = SessionStore(str(path))
        try:
            # The next event picks up from seq 2 (not 0).
            key, eid = s2.append_event("a", "x", {})
            assert key.endswith("e000002")
            # Global event id continues from 2.
            assert eid == 3
        finally:
            s2.close()


# ── subscribe / unsubscribe ───────────────────────────────────────


class TestSubscribe:
    def test_subscriber_fires_on_append(self, tmp_path):
        s = _store(tmp_path)
        try:
            received = []
            s.subscribe(lambda k, d: received.append((k, d.get("type"))))
            s.append_event("a", "x", {})
            assert len(received) == 1
            assert received[0][1] == "x"
        finally:
            s.close()

    def test_idempotent_subscribe(self, tmp_path):
        s = _store(tmp_path)
        try:
            cb_calls = []

            def cb(k, d):
                cb_calls.append(k)

            s.subscribe(cb)
            s.subscribe(cb)
            s.append_event("a", "x", {})
            # Only one invocation despite double-subscribe.
            assert len(cb_calls) == 1
        finally:
            s.close()

    def test_unsubscribe(self, tmp_path):
        s = _store(tmp_path)
        try:
            received = []

            def cb(k, d):
                received.append(k)

            s.subscribe(cb)
            s.unsubscribe(cb)
            s.append_event("a", "x", {})
            assert received == []
        finally:
            s.close()

    def test_unsubscribe_unknown_is_safe(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.unsubscribe(lambda k, d: None)  # never subscribed
        finally:
            s.close()

    def test_failing_subscriber_doesnt_block_append(self, tmp_path):
        s = _store(tmp_path)
        try:

            def boom(k, d):
                raise RuntimeError("subscriber boom")

            s.subscribe(boom)
            # Append still succeeds.
            key, eid = s.append_event("a", "x", {})
            assert eid == 1
        finally:
            s.close()


# ── conversation snapshots ───────────────────────────────────────


class TestConversation:
    def test_save_load_list(self, tmp_path):
        s = _store(tmp_path)
        try:
            msgs = [{"role": "user", "content": "hi"}]
            s.save_conversation("a", msgs)
            assert s.load_conversation("a") == msgs
        finally:
            s.close()

    def test_load_missing_returns_none(self, tmp_path):
        s = _store(tmp_path)
        try:
            assert s.load_conversation("missing") is None
        finally:
            s.close()

    def test_legacy_json_string_decoded(self, tmp_path):
        import json

        s = _store(tmp_path)
        try:
            s.conversation["a"] = json.dumps(
                {"messages": [{"role": "user", "content": "x"}]}
            )
            out = s.load_conversation("a")
            assert out == [{"role": "user", "content": "x"}]
        finally:
            s.close()


# ── per-agent state ──────────────────────────────────────────────


class TestState:
    def test_save_load_scratchpad(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.save_state("a", scratchpad={"notes": "todo"})
            assert s.load_scratchpad("a") == {"notes": "todo"}
        finally:
            s.close()

    def test_load_scratchpad_missing(self, tmp_path):
        s = _store(tmp_path)
        try:
            assert s.load_scratchpad("missing") == {}
        finally:
            s.close()

    def test_turn_count(self, tmp_path):
        s = _store(tmp_path)
        try:
            assert s.load_turn_count("a") == 0
            s.save_state("a", turn_count=7)
            assert s.load_turn_count("a") == 7
        finally:
            s.close()

    def test_token_usage(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.save_state("a", token_usage={"prompt": 100, "completion": 50})
            assert s.load_token_usage("a") == {"prompt": 100, "completion": 50}
        finally:
            s.close()

    def test_load_token_usage_missing(self, tmp_path):
        s = _store(tmp_path)
        try:
            assert s.load_token_usage("missing") == {}
        finally:
            s.close()

    def test_triggers(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.save_state("a", triggers=[{"name": "t1"}])
            assert s.load_triggers("a") == [{"name": "t1"}]
        finally:
            s.close()

    def test_load_triggers_missing(self, tmp_path):
        s = _store(tmp_path)
        try:
            assert s.load_triggers("missing") == []
        finally:
            s.close()


# ── channels ──────────────────────────────────────────────────────


class TestChannels:
    def test_save_get(self, tmp_path):
        s = _store(tmp_path)
        try:
            k = s.save_channel_message("chan-1", {"content": "hello world"})
            assert k.startswith("chan-1:m")
            msgs = s.get_channel_messages("chan-1")
            assert len(msgs) == 1
            assert msgs[0]["content"] == "hello world"
        finally:
            s.close()

    def test_ordering(self, tmp_path):
        s = _store(tmp_path)
        try:
            for i in range(3):
                s.save_channel_message("ch", {"content": f"m{i}"})
            msgs = s.get_channel_messages("ch")
            assert [m["content"] for m in msgs] == ["m0", "m1", "m2"]
        finally:
            s.close()


# ── subagents ─────────────────────────────────────────────────────


class TestSubagents:
    def test_next_run_increments(self, tmp_path):
        s = _store(tmp_path)
        try:
            assert s.next_subagent_run("parent", "critic") == 0
            assert s.next_subagent_run("parent", "critic") == 1
            assert s.next_subagent_run("parent", "plan") == 0
        finally:
            s.close()

    def test_save_load_meta(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.save_subagent("p", "explore", 0, {"task": "find x"})
            assert s.load_subagent_meta("p", "explore", 0)["task"] == "find x"
        finally:
            s.close()

    def test_load_missing_subagent_meta(self, tmp_path):
        s = _store(tmp_path)
        try:
            assert s.load_subagent_meta("p", "nope", 0) is None
        finally:
            s.close()

    def test_save_load_conversation(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.save_subagent("p", "x", 0, {"task": "t"}, conv_json='[{"role":"user"}]')
            out = s.load_subagent_conversation("p", "x", 0)
            assert out == '[{"role":"user"}]'
        finally:
            s.close()

    def test_load_missing_conversation(self, tmp_path):
        s = _store(tmp_path)
        try:
            assert s.load_subagent_conversation("p", "x", 99) is None
        finally:
            s.close()

    def test_lists_runs_and_finds_exact_job_id(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.save_subagent(
                "p",
                "explore",
                0,
                {"job_id": "agent_explore_11111111", "task": "first"},
            )
            s.save_subagent(
                "p",
                "explore",
                1,
                {"job_id": "agent_explore_22222222", "task": "second"},
            )
            s.save_subagent("other", "explore", 0, {"job_id": "other-job"})

            runs = s.list_subagent_runs(parent="p", name="explore")
            assert [(row["run"], row["job_id"]) for row in runs] == [
                (0, "agent_explore_11111111"),
                (1, "agent_explore_22222222"),
            ]
            found = s.find_subagent_run("agent_explore_11111111", parent="p")
            assert found is not None
            assert found["parent"] == "p"
            assert found["name"] == "explore"
            assert found["run"] == 0
            assert found["job_id"] == "agent_explore_11111111"
            assert found["meta"]["task"] == "first"
            assert s.find_subagent_run("missing", parent="p") is None
        finally:
            s.close()


# ── jobs ──────────────────────────────────────────────────────────


class TestJobs:
    def test_save_load(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.save_job("j1", {"name": "bash"})
            assert s.load_job("j1")["name"] == "bash"
        finally:
            s.close()

    def test_load_missing(self, tmp_path):
        s = _store(tmp_path)
        try:
            assert s.load_job("missing") is None
        finally:
            s.close()


# ── meta / lifecycle ──────────────────────────────────────────────


class TestMeta:
    def test_init_meta(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.init_meta(
                "sess-1",
                "agent",
                "/path/to/cfg",
                "/cwd",
                ["alice"],
                config_snapshot={"x": 1},
            )
            meta = s.load_meta()
            assert meta["session_id"] == "sess-1"
            assert meta["config_type"] == "agent"
            assert meta["config_path"] == "/path/to/cfg"
            assert meta["pwd"] == "/cwd"
            assert "alice" in meta["agents"]
            assert meta["status"] == "running"
            assert bool(meta["conversation_open"]) is True
            assert isinstance(meta["conversation_id"], str)
            assert meta["conversation_id"]
            assert "hostname" in meta
            assert "python_version" in meta
            s.set_conversation_open(False)
            assert bool(s.load_meta()["conversation_open"]) is False
        finally:
            s.close()

    def test_conversation_id_is_immutable_across_reinitialization(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.init_meta("runtime-1", "agent", "/p", "/w", ["a"])
            conversation_id = s.load_meta()["conversation_id"]
            s.init_meta("runtime-2", "agent", "/p", "/w", ["a"])
            assert s.load_meta()["conversation_id"] == conversation_id
        finally:
            s.close()

    def test_marked_legacy_session_gets_deterministic_persisted_identity(
        self, tmp_path
    ):
        path = tmp_path / "legacy.kohakutr"
        s = SessionStore(path)
        try:
            s.meta["session_id"] = "old-runtime"
            s.meta["conversation_open"] = True
            first = s.ensure_conversation_id()
            assert first == s.ensure_conversation_id()
            assert s.load_meta()["conversation_id"] == first
        finally:
            s.close(update_status=False)

        reopened = SessionStore(path)
        try:
            assert reopened.ensure_conversation_id() == first
        finally:
            reopened.close(update_status=False)

    def test_unmarked_legacy_session_is_not_backfilled(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.meta["session_id"] = "legacy"
            assert s.ensure_conversation_id() is None
            assert "conversation_id" not in s.meta
        finally:
            s.close(update_status=False)

    def test_update_status(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.init_meta("sess", "agent", "/p", "/w", ["a"])
            s.update_status("completed")
            assert s.load_meta()["status"] == "completed"
        finally:
            s.close()

    def test_touch_updates_last_active(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.init_meta("sess", "agent", "/p", "/w", ["a"])
            first = s.load_meta()["last_active"]
            import time as _t

            _t.sleep(0.01)
            s.touch()
            second = s.load_meta()["last_active"]
            assert second >= first
        finally:
            s.close()

    def test_set_viewer_default_agent(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.set_viewer_default_agent("host:attached:rev:0")
            assert s.meta["viewer_default_agent"] == "host:attached:rev:0"
        finally:
            s.close()

    def test_set_viewer_default_agent_rejects_blank(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.set_viewer_default_agent("")
            assert "viewer_default_agent" not in s.meta
        finally:
            s.close()

    def test_load_meta_discovers_unrecorded_agents(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.init_meta("sess", "agent", "/p", "/w", ["alice"])
            # Append an event under a different agent name (hot-plug).
            s.append_event("bob", "user_message", {"content": "x"})
            s.flush()
            meta = s.load_meta()
            assert "alice" in meta["agents"]
            assert "bob" in meta["agents"]
        finally:
            s.close()


class TestDiscoverAgentsFromEvents:
    def test_empty(self, tmp_path):
        s = _store(tmp_path)
        try:
            assert s.discover_agents_from_events() == []
        finally:
            s.close()

    def test_excludes_terrarium(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.append_event("terrarium", "x", {})
            s.append_event("alice", "x", {})
            s.flush()
            assert s.discover_agents_from_events() == ["alice"]
        finally:
            s.close()

    def test_excludes_attached_namespaces(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.append_event("alice", "x", {})
            s.append_event("alice:attached:rev:0", "x", {})
            s.flush()
            agents = s.discover_agents_from_events()
            assert "alice" in agents
            assert "alice:attached:rev:0" not in agents
        finally:
            s.close()


class TestDiscoverAttachedAgents:
    def test_empty(self, tmp_path):
        s = _store(tmp_path)
        try:
            assert s.discover_attached_agents() == []
        finally:
            s.close()

    def test_parses_namespace(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.append_event("host:attached:reviewer:3", "x", {})
            s.flush()
            entries = s.discover_attached_agents()
            assert len(entries) == 1
            assert entries[0]["host"] == "host"
            assert entries[0]["role"] == "reviewer"
            assert entries[0]["attach_seq"] == 3

        finally:
            s.close()

    def test_invalid_seq_skipped(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.append_event("host:attached:reviewer:notanint", "x", {})
            s.flush()
            assert s.discover_attached_agents() == []
        finally:
            s.close()


# ── search ────────────────────────────────────────────────────────


class TestSearch:
    def test_search_finds_text(self, tmp_path):
        s = _store(tmp_path)
        try:
            _, eid = s.append_event(
                "a",
                "user_message",
                {"content": "the quick brown fox jumps over the lazy dog"},
            )
            s.flush()
            hits = s.search("quick", k=5)
            # The indexed event is found; result carries score + meta.
            assert len(hits) == 1
            assert hits[0]["meta"]["agent"] == "a"
            assert hits[0]["meta"]["type"] == "user_message"
            assert hits[0]["meta"]["event_id"] == eid
            assert isinstance(hits[0]["score"], (int, float))
        finally:
            s.close()

    def test_search_no_match_returns_empty(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.append_event("a", "user_message", {"content": "the quick brown fox"})
            s.flush()
            # A term absent from every indexed document yields nothing.
            assert s.search("nonexistentterm", k=5) == []
        finally:
            s.close()


# ── flush / close ─────────────────────────────────────────────────


class TestFlushClose:
    def test_close_idempotent_status(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.init_meta("x", "agent", "/p", "/w", ["a"])
        finally:
            s.close()
        # Reopen and check status updated to paused.
        s2 = SessionStore(s._path)
        try:
            meta = s2.load_meta()
            assert meta["status"] == "paused"
            assert bool(meta["conversation_open"]) is True
        finally:
            s2.close()

    def test_close_no_status_update(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.init_meta("x", "agent", "/p", "/w", ["a"])
        finally:
            s.close(update_status=False)
        s2 = SessionStore(s._path)
        try:
            # Status stayed at running.
            assert s2.load_meta()["status"] == "running"
        finally:
            s2.close()

    def test_open_readonly_never_mutates_meta(self, tmp_path):
        # E8: listing / preview consumers used plain opens whose close
        # bumped last_active + flipped status — corrupting the recency
        # ordering ``kt resume`` sorts by.
        s = _store(tmp_path)
        try:
            s.init_meta("x", "agent", "/p", "/w", ["a"])
        finally:
            s.close(update_status=False)
        before = SessionStore.open_readonly(s._path)
        meta_before = dict(before.load_meta())
        # Even an EXPLICIT update_status=True is ignored on a
        # read-only store.
        before.close(update_status=True)
        after = SessionStore.open_readonly(s._path)
        try:
            meta_after = dict(after.load_meta())
            assert meta_after["status"] == meta_before["status"] == "running"
            assert meta_after["last_active"] == meta_before["last_active"]
        finally:
            after.close()

    def test_double_close_is_noop(self, tmp_path):
        s = _store(tmp_path)
        s.init_meta("x", "agent", "/p", "/w", ["a"])
        s.close()
        # Second close (engine shutdown after caller close) — no error.
        s.close()

    def test_flush_resets_counter(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.append_event("a", "x", {})
            s.flush()
            assert s._unflushed_event_count == 0
        finally:
            s.close()

    def test_close_releases_the_file_handle(self, tmp_path):
        """Regression guard for B-e2e-1: after ``close()`` the
        ``.kohakutr`` file must be deletable — every native SQLite
        handle (the 8 KVault tables AND the FTS TextVault) is released,
        not left dangling until GC. Before the fix, ``KVault.close()``
        never dropped its native ``_inner`` and ``TextVault`` had no
        ``close()`` at all, so on Windows the file stayed locked and a
        delete of a just-closed session failed with WinError 32."""
        s = _store(tmp_path)
        s.init_meta("x", "agent", "/p", "/w", ["a"])
        # Write to the FTS table too — the TextVault handle was the leak
        # the KVault-only close path missed.
        s.append_event("a", "user_input", {"content": "searchable token"})
        s.flush()
        path = Path(s._path)
        s.close()
        # The file (and any WAL/SHM sidecars) must be unlinkable now.
        for p in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
            if p.exists():
                p.unlink()  # raises PermissionError if a handle lingers
        assert not path.exists()


# ── get_all_events ────────────────────────────────────────────────


class TestGetAllEvents:
    def test_orders_by_ts(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.append_event("a", "x", {"ts": 200.0})
            s.append_event("b", "x", {"ts": 100.0})
            s.append_event("c", "x", {"ts": 150.0})
            ts_order = [evt["ts"] for _, evt in s.get_all_events()]
            assert ts_order == sorted(ts_order)
        finally:
            s.close()


# ── artifacts ─────────────────────────────────────────────────────


class TestArtifacts:
    def test_write_artifact(self, tmp_path):
        s = _store(tmp_path)
        try:
            p = s.write_artifact("a.png", b"PNG-RAW")
            assert p.read_bytes() == b"PNG-RAW"
            # Lives under the artifacts_dir sibling.
            assert s.artifacts_dir == p.parent
        finally:
            s.close()


# ── rollup wrappers ───────────────────────────────────────────────


class TestRollupAPI:
    def test_save_get_list(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.save_turn_rollup("a", 0, {"tokens_in": 5})
            assert s.get_turn_rollup("a", 0)["tokens_in"] == 5
            assert len(s.list_turn_rollups("a")) == 1
        finally:
            s.close()


# ── iter_kv_keys helper ───────────────────────────────────────────


class TestIterKvKeys:
    def test_iter_no_prefix(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.append_event("a", "x", {})
            s.append_event("b", "x", {})
            s.flush()
            keys = list(iter_kv_keys(s.events))
            assert len(keys) == 2
        finally:
            s.close()

    def test_iter_with_prefix(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.append_event("a", "x", {})
            s.append_event("b", "x", {})
            s.flush()
            keys = list(iter_kv_keys(s.events, prefix=b"a:e"))
            assert len(keys) == 1
        finally:
            s.close()


# -- load_conversation legacy + missing shapes --------------------


class TestLoadConversationShapes:
    def test_legacy_json_list_string(self, tmp_path):
        # A pre-msgpack session stored the conversation as a raw JSON
        # *list* string -- load_conversation must still decode it.
        import json

        s = _store(tmp_path)
        try:
            s.conversation["alice"] = json.dumps(
                [{"role": "user", "content": "legacy"}]
            )
            s.flush()
            out = s.load_conversation("alice")
            assert out == [{"role": "user", "content": "legacy"}]
        finally:
            s.close()

    def test_legacy_json_dict_with_messages(self, tmp_path):
        import json

        s = _store(tmp_path)
        try:
            s.conversation["bob"] = json.dumps(
                {"messages": [{"role": "user", "content": "wrapped"}]}
            )
            s.flush()
            out = s.load_conversation("bob")
            assert out == [{"role": "user", "content": "wrapped"}]
        finally:
            s.close()

    def test_unknown_shape_returns_none(self, tmp_path):
        import json

        s = _store(tmp_path)
        try:
            # A JSON int -- neither a list nor a {messages:...} dict.
            s.conversation["carol"] = json.dumps(42)
            s.flush()
            assert s.load_conversation("carol") is None
        finally:
            s.close()

    def test_missing_returns_none(self, tmp_path):
        s = _store(tmp_path)
        try:
            assert s.load_conversation("never-saved") is None
        finally:
            s.close()


# -- init_meta terrarium fields -----------------------------------


class TestInitMetaTerrariumFields:
    def test_terrarium_fields_persisted(self, tmp_path):
        # When init_meta is given terrarium name / channels / creatures
        # they all land in meta and read back.
        s = _store(tmp_path)
        try:
            s.init_meta(
                "sess",
                "terrarium",
                "/cfg",
                "/wd",
                ["alice", "bob"],
                terrarium_name="swarm",
                terrarium_channels=[{"name": "chat"}],
                terrarium_creatures=[{"name": "alice"}, {"name": "bob"}],
            )
            meta = s.load_meta()
            assert meta["terrarium_name"] == "swarm"
            assert meta["terrarium_channels"] == [{"name": "chat"}]
            assert meta["terrarium_creatures"] == [
                {"name": "alice"},
                {"name": "bob"},
            ]
        finally:
            s.close()


# -- discover_attached_agents key filtering -----------------------


class TestDiscoverAttachedAgents:
    def test_well_formed_attached_namespace_discovered(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.append_event("host:attached:reviewer:0", "x", {})
            s.flush()
            found = s.discover_attached_agents()
            namespaces = {e["namespace"] for e in found}
            assert "host:attached:reviewer:0" in namespaces
            entry = next(
                e for e in found if e["namespace"] == "host:attached:reviewer:0"
            )
            assert entry["host"] == "host"
            assert entry["role"] == "reviewer"
        finally:
            s.close()

    def test_malformed_attached_namespaces_skipped(self, tmp_path):
        s = _store(tmp_path)
        try:
            # ``:attached:`` present but missing the trailing role:seq.
            s.append_event("host:attached:incomplete", "x", {})
            s.flush()
            found = s.discover_attached_agents()
            # The malformed namespace is not reported.
            namespaces = {e["namespace"] for e in found}
            assert "host:attached:incomplete" not in namespaces
        finally:
            s.close()

    def test_duplicate_attached_namespace_deduped(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.append_event("host:attached:rev:0", "e", {})
            s.append_event("host:attached:rev:0", "e", {})
            s.flush()
            found = s.discover_attached_agents()
            matches = [e for e in found if e["namespace"] == "host:attached:rev:0"]
            assert len(matches) == 1
        finally:
            s.close()


# -- store.token_usage / token_usage_all_loops wrappers -----------


class TestTokenUsageWrappers:
    def test_token_usage_wrapper_delegates(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.save_state("alice", token_usage={"prompt_tokens": 7})
            out = s.token_usage("alice")
            assert out["agent"] == "alice"
            assert out["prompt_tokens"] == 7
        finally:
            s.close()

    def test_token_usage_all_loops_wrapper_delegates(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.init_meta("sess", "agent", "/p", "/w", ["alice"])
            s.save_state("alice", token_usage={"prompt_tokens": 3})
            loops = s.token_usage_all_loops()
            assert any(name == "alice" for name, _ in loops)
        finally:
            s.close()


# -- save_state optional fields -----------------------------------


class TestSaveStateOptionalFields:
    def test_triggers_and_compact_count_persist(self, tmp_path):
        # save_state with triggers + compact_count writes both to state.
        s = _store(tmp_path)
        try:
            s.save_state(
                "alice",
                triggers=[{"name": "t1"}],
                compact_count=3,
            )
            s.flush()
            assert s.state["alice:triggers"] == [{"name": "t1"}]
            assert s.state["alice:compact_count"] == 3
        finally:
            s.close()


# -- get_resumable_events + fork wrappers -------------------------


class TestResumableAndFork:
    def test_get_resumable_events_synthesizes_interrupted_result(self, tmp_path):
        # An unfinished tool_call (no matching tool_result) is turned
        # into a synthetic interrupted tool_result on resume.
        s = _store(tmp_path)
        try:
            s.append_event("alice", "tool_call", {"call_id": "c1", "name": "bash"})
            s.flush()
            events = s.get_resumable_events("alice")
            synth = [e for e in events if e.get("_synthetic_resume")]
            assert len(synth) == 1
            assert synth[0]["call_id"] == "c1"
            assert synth[0]["interrupted"] is True
        finally:
            s.close()

    def test_fork_creates_independent_copy(self, tmp_path):
        # fork() copies the store to a new path; the fork reads back the
        # parent's data and writes don't bleed back.
        src = _store(tmp_path, "parent.kohakutr")
        try:
            src.init_meta("sess", "agent", "/p", "/w", ["alice"])
            _, eid = src.append_event("alice", "user_message", {"content": "shared"})
            src.flush()
            fork_path = tmp_path / "child.kohakutr"
            parent_conversation_id = src.meta["conversation_id"]
            forked = src.fork(str(fork_path), at_event_id=eid)
            try:
                assert forked.meta["conversation_id"] != parent_conversation_id
                # The fork sees the parent's event.
                fevents = forked.get_events("alice")
                assert any(e.get("content") == "shared" for e in fevents)
                # A write to the fork does not appear in the parent.
                forked.append_event("alice", "user_message", {"content": "fork-only"})
                forked.flush()
                parent_contents = [e.get("content") for e in src.get_events("alice")]
                assert "fork-only" not in parent_contents
            finally:
                forked.close()
        finally:
            src.close()


# -- defensive KVault-read failure branches -----------------------


class _FlakyTable:
    """Wraps a real KVault table; reading any key raises."""

    def __init__(self, inner):
        self._inner = inner

    def keys(self, **kw):
        return self._inner.keys(**kw)

    def __getitem__(self, key):
        raise RuntimeError("kv read exploded")

    def __setitem__(self, key, value):
        self._inner[key] = value

    def __contains__(self, key):
        return key in self._inner

    def flush_cache(self):
        return self._inner.flush_cache()


class TestDefensiveReadFailures:
    def test_get_events_skips_unreadable_rows(self, tmp_path):
        # If reading an event row raises, get_events logs + skips it
        # rather than propagating — the call returns (possibly empty).
        s = _store(tmp_path)
        try:
            s.append_event("alice", "user_message", {"content": "x"})
            s.flush()
            s.events = _FlakyTable(s.events)
            # Must not raise; the unreadable row is simply dropped.
            assert s.get_events("alice") == []
        finally:
            s._closed = True

    def test_get_channel_messages_skips_unreadable_rows(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.save_channel_message("chat", {"sender": "a", "content": "hi"})
            s.flush()
            s.channels = _FlakyTable(s.channels)
            assert s.get_channel_messages("chat") == []
        finally:
            s._closed = True

    def test_load_meta_skips_unreadable_keys(self, tmp_path):
        s = _store(tmp_path)
        try:
            s.init_meta("sess", "agent", "/p", "/w", ["alice"])
            s.flush()
            s.meta = _FlakyTable(s.meta)
            # Every meta key read raises → load_meta returns an empty-ish
            # dict instead of crashing.
            out = s.load_meta()
            assert isinstance(out, dict)
        finally:
            s._closed = True


class TestWriterLock:
    """``writer_lock=True`` refuses a second concurrent writer (detect +
    error) while leaving read-only / viewer opens unaffected."""

    def test_second_writer_refused(self, tmp_path):
        p = tmp_path / "s.kohakutr"
        w1 = SessionStore(p, writer_lock=True)
        try:
            with pytest.raises(SessionLockedError) as exc:
                SessionStore(p, writer_lock=True)
            assert exc.value.holder_pid is not None
        finally:
            w1.close()

    def test_readonly_open_coexists_with_writer(self, tmp_path):
        p = tmp_path / "s.kohakutr"
        w = SessionStore(p, writer_lock=True)
        try:
            # open_readonly never takes the lock — viewing a running
            # session must not raise.
            r = SessionStore.open_readonly(p)
            r.close()
        finally:
            w.close()

    def test_plain_open_coexists_with_writer(self, tmp_path):
        # The viewer / index-reconciler path uses the default
        # (writer_lock=False) constructor and must not contend.
        p = tmp_path / "s.kohakutr"
        w = SessionStore(p, writer_lock=True)
        try:
            v = SessionStore(p)
            v.close()
        finally:
            w.close()

    def test_lock_released_on_close(self, tmp_path):
        p = tmp_path / "s.kohakutr"
        w1 = SessionStore(p, writer_lock=True)
        w1.close()
        # Lock is free again -> a new writer can take it. (The .lock
        # sidecar is intentionally left on disk; see FileLock.release.)
        w2 = SessionStore(p, writer_lock=True)
        w2.close()

    def test_no_lock_without_flag(self, tmp_path):
        # Two default opens never contend (back-compat for the many
        # transient read/peek opens across the codebase).
        p = tmp_path / "s.kohakutr"
        a = SessionStore(p)
        b = SessionStore(p)
        a.close()
        b.close()

    def test_lock_released_if_construction_fails(self, tmp_path, monkeypatch):
        # If table-opening raises AFTER the lock is acquired, __init__ must
        # release it — otherwise an in-process retry is wedged by a leaked
        # lock (the "OS frees on exit" net doesn't help a live process).
        p = tmp_path / "s.kohakutr"

        def _boom(self):
            raise RuntimeError("table open failed")

        monkeypatch.setattr(SessionStore, "_open_tables", _boom)
        with pytest.raises(RuntimeError):
            SessionStore(p, writer_lock=True)
        monkeypatch.undo()
        # Lock is free -> a fresh writer-locked open succeeds (no leak).
        w = SessionStore(p, writer_lock=True)
        w.close()


# ── Partial-open cleanup ─────────────────────────────────────────


class TestPartialOpenCleanup:
    """A construction failure releases every table opened before it."""

    def test_tables_opened_before_failure_are_closed(self, tmp_path, monkeypatch):
        opened: list = []
        real_kvault = store_mod.KVault

        class RecordingKVault(real_kvault):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                opened.append(self)

        def _boom(*args, **kwargs):
            raise RuntimeError("fts open failed")

        monkeypatch.setattr(store_mod, "KVault", RecordingKVault)
        monkeypatch.setattr(store_mod, "TextVault", _boom)
        threads_before = threading.active_count()
        with pytest.raises(RuntimeError):
            SessionStore(tmp_path / "s.kohakutr")
        assert len(opened) == 8
        assert all(table._closed for table in opened)
        assert not any(hasattr(table, "_inner") for table in opened)
        assert threading.active_count() == threads_before

    def test_counter_restore_failure_closes_every_table(self, tmp_path, monkeypatch):
        opened: list = []
        real_kvault = store_mod.KVault

        class RecordingKVault(real_kvault):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                opened.append(self)

        def _boom(self):
            raise RuntimeError("counter restore failed")

        monkeypatch.setattr(store_mod, "KVault", RecordingKVault)
        monkeypatch.setattr(SessionStore, "_restore_counters", _boom)
        threads_before = threading.active_count()
        with pytest.raises(RuntimeError):
            SessionStore(tmp_path / "s.kohakutr")
        assert len(opened) == 8
        assert all(table._closed for table in opened)
        assert threading.active_count() == threads_before

    @pytest.mark.skipif(resource is None, reason="RLIMIT_NOFILE is POSIX-only")
    def test_failed_open_leaves_no_descriptors_behind(self, tmp_path):
        path = tmp_path / "s.kohakutr"
        SessionStore(path).close()

        def open_fds() -> int:
            return len(os.listdir("/dev/fd"))

        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        burned: list[int] = []
        limit = open_fds() + 40
        resource.setrlimit(resource.RLIMIT_NOFILE, (limit, hard))
        try:
            gc.collect()
            while open_fds() < limit - 12:
                burned.append(os.open(os.devnull, os.O_RDONLY))
            fds_before = open_fds()
            threads_before = threading.active_count()
            with pytest.raises(Exception):
                SessionStore(path)
            gc.collect()
            assert open_fds() == fds_before
            assert threading.active_count() == threads_before
        finally:
            for fd in burned:
                os.close(fd)
            resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))


# ── Companion closers (generic sidecar hook; no Drive knowledge) ───


class TestCompanionCloser:
    def test_registered_closer_runs_once_at_close(self, tmp_path):
        s = _store(tmp_path)
        calls = []
        s.register_companion_closer(lambda: calls.append("x"))
        s.close()
        assert calls == ["x"]

    def test_closers_run_lifo(self, tmp_path):
        s = _store(tmp_path)
        calls = []
        s.register_companion_closer(lambda: calls.append("a"))
        s.register_companion_closer(lambda: calls.append("b"))
        s.close()
        # last registered closes first (LIFO)
        assert calls == ["b", "a"]

    def test_register_is_idempotent(self, tmp_path):
        s = _store(tmp_path)
        calls = []

        def closer():
            calls.append("x")

        s.register_companion_closer(closer)
        s.register_companion_closer(closer)  # re-register is a no-op
        s.close()
        assert calls == ["x"]

    def test_failing_closer_does_not_strand_the_store(self, tmp_path):
        # One bad closer is logged, never fatal: tables still close and the
        # writer lock still releases (a fresh writer-locked open succeeds).
        path = tmp_path / "s.kohakutr"
        s = SessionStore(path, writer_lock=True)

        def boom():
            raise RuntimeError("closer boom")

        s.register_companion_closer(boom)
        s.close()
        w = SessionStore(path, writer_lock=True)
        w.close()

    def test_no_closers_is_noop(self, tmp_path):
        s = _store(tmp_path)
        s.close()  # empty companion list closes cleanly
