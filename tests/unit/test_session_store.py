"""Tests for SessionStore - persistent session storage."""

import time

import pytest

from kohakuterrarium.session.store import SessionStore


@pytest.fixture
def store(tmp_path):
    """Create a fresh SessionStore in a temp directory."""
    s = SessionStore(tmp_path / "test_session.kohakutr")
    yield s
    s.close()


@pytest.fixture
def populated_store(store):
    """Store with some pre-populated data for read tests."""
    store.init_meta(
        session_id="sess_test123",
        config_type="terrarium",
        config_path="/path/to/swe_team",
        pwd="/home/user/project",
        agents=["root", "swe", "reviewer"],
        config_snapshot={"name": "swe_team", "creatures": []},
        terrarium_name="swe_team",
        terrarium_channels=[
            {"name": "tasks", "type": "queue", "description": "Task assignments"},
            {"name": "team_chat", "type": "broadcast", "description": "Team chat"},
        ],
        terrarium_creatures=[
            {"name": "swe", "listen": ["tasks"], "send": ["review"]},
            {"name": "reviewer", "listen": ["review"], "send": ["results"]},
        ],
    )

    # Root agent events
    store.append_event("root", "user_input", {"content": "Fix the auth bug"})
    store.append_event("root", "processing_start", {})
    store.append_event("root", "text", {"content": "I'll dispatch to SWE."})
    store.append_event(
        "root",
        "tool_call",
        {"name": "terrarium_send", "call_id": "tc_001", "args": {"channel": "tasks"}},
    )
    store.append_event(
        "root",
        "tool_result",
        {"name": "terrarium_send", "call_id": "tc_001", "output": "OK", "exit_code": 0},
    )
    store.append_event(
        "root",
        "token_usage",
        {"prompt_tokens": 2500, "completion_tokens": 180, "total_tokens": 2680},
    )
    store.append_event("root", "processing_end", {})

    # SWE events
    store.append_event(
        "swe",
        "trigger_fired",
        {"channel": "tasks", "sender": "root", "content": "Fix the auth bug"},
    )
    store.append_event("swe", "processing_start", {})
    store.append_event("swe", "text", {"content": "Analyzing auth module."})
    store.append_event(
        "swe",
        "tool_call",
        {"name": "bash", "call_id": "tc_002", "args": {"command": "ls src/auth/"}},
    )
    store.append_event(
        "swe",
        "tool_result",
        {
            "name": "bash",
            "call_id": "tc_002",
            "output": "middleware.py\nmodels.py",
            "exit_code": 0,
        },
    )
    store.append_event("swe", "processing_end", {})

    # Channel messages
    store.save_channel_message(
        "tasks",
        {"sender": "root", "content": "Fix the auth bug", "msg_id": "m001"},
    )
    store.save_channel_message(
        "team_chat",
        {"sender": "swe", "content": "Starting auth fix work", "msg_id": "m002"},
    )

    # Conversation snapshots (raw message lists)
    store.save_conversation("root", [{"role": "user", "content": "Fix auth"}])
    store.save_conversation("swe", [{"role": "user", "content": "triggered"}])

    # Scratchpad
    store.save_state("root", scratchpad={"plan": "dispatch to swe"}, turn_count=1)
    store.save_state(
        "swe",
        scratchpad={"bug": "line 42"},
        turn_count=1,
        token_usage={"prompt": 8000, "completion": 500, "total": 8500},
    )

    # Sub-agent
    store.save_subagent(
        parent="swe",
        name="explore",
        run=0,
        meta={
            "task": "Find token validation",
            "turns": 3,
            "tools_used": ["grep", "read"],
            "success": True,
            "duration": 5.5,
        },
        conv_json='{"messages": [{"role": "system", "content": "You are explore"}]}',
    )

    # Job
    store.save_job(
        "tool_bash_tc_002",
        {
            "agent": "swe",
            "job_type": "tool",
            "type_name": "bash",
            "state": "done",
            "exit_code": 0,
            "output_preview": "middleware.py\nmodels.py",
        },
    )

    return store


# ─── Meta Tests ──────────────────────────────────────────────────


class TestMeta:
    def test_init_and_load(self, store):
        store.init_meta(
            session_id="sess_abc",
            config_type="agent",
            config_path="/path/to/swe",
            pwd="/home/user",
            agents=["swe_agent"],
        )
        meta = store.load_meta()
        assert meta["session_id"] == "sess_abc"
        assert meta["config_type"] == "agent"
        assert meta["config_path"] == "/path/to/swe"
        assert meta["pwd"] == "/home/user"
        assert meta["agents"] == ["swe_agent"]
        assert meta["status"] == "running"
        assert meta["format_version"] == 2
        assert "created_at" in meta
        assert "hostname" in meta

    def test_terrarium_meta(self, populated_store):
        meta = populated_store.load_meta()
        assert meta["config_type"] == "terrarium"
        assert meta["terrarium_name"] == "swe_team"
        assert len(meta["terrarium_channels"]) == 2
        assert len(meta["terrarium_creatures"]) == 2
        assert meta["agents"] == ["root", "swe", "reviewer"]

    def test_update_status(self, store):
        store.init_meta(
            session_id="s1",
            config_type="agent",
            config_path="/p",
            pwd="/w",
            agents=["a"],
        )
        store.update_status("paused")
        assert store.meta["status"] == "paused"

    def test_touch_updates_timestamp(self, store):
        store.init_meta(
            session_id="s1",
            config_type="agent",
            config_path="/p",
            pwd="/w",
            agents=["a"],
        )
        t1 = store.meta["last_active"]
        time.sleep(0.01)
        store.touch()
        t2 = store.meta["last_active"]
        assert t2 >= t1


# ─── Event Tests ─────────────────────────────────────────────────


class TestEvents:
    def test_append_and_read(self, store):
        key1, eid1 = store.append_event("root", "user_input", {"content": "hello"})
        key2, eid2 = store.append_event("root", "text", {"content": "world"})
        assert key1 == "root:e000000"
        assert key2 == "root:e000001"
        # Wave B: event_id is a global monotonic integer.
        assert eid1 == 1
        assert eid2 == 2

        events = store.get_events("root")
        assert len(events) == 2
        assert events[0]["type"] == "user_input"
        assert events[0]["content"] == "hello"
        assert events[1]["type"] == "text"
        assert events[1]["content"] == "world"

    def test_multi_agent_events(self, store):
        store.append_event("root", "user_input", {"content": "task"})
        store.append_event("swe", "trigger_fired", {"channel": "tasks"})
        store.append_event("root", "text", {"content": "dispatched"})
        store.append_event("swe", "text", {"content": "working"})

        root_events = store.get_events("root")
        swe_events = store.get_events("swe")
        assert len(root_events) == 2
        assert len(swe_events) == 2
        assert root_events[0]["content"] == "task"
        assert swe_events[0]["channel"] == "tasks"

    def test_event_has_timestamp(self, store):
        store.append_event("root", "text", {"content": "test"})
        events = store.get_events("root")
        assert "ts" in events[0]
        assert events[0]["ts"] > 0

    def test_event_has_type(self, store):
        store.append_event("root", "tool_call", {"name": "bash"})
        events = store.get_events("root")
        assert events[0]["type"] == "tool_call"
        assert events[0]["name"] == "bash"

    def test_get_all_events_sorted_by_time(self, populated_store):
        all_events = populated_store.get_all_events()
        assert len(all_events) > 5
        timestamps = [evt["ts"] for _, evt in all_events]
        assert timestamps == sorted(timestamps)

    def test_populated_event_counts(self, populated_store):
        root_events = populated_store.get_events("root")
        swe_events = populated_store.get_events("swe")
        assert (
            len(root_events) == 7
        )  # user_input, processing_start, text, tool_call, tool_result, token_usage, processing_end
        assert (
            len(swe_events) == 6
        )  # trigger, processing_start, text, tool_call, tool_result, processing_end


# ─── Conversation Tests ──────────────────────────────────────────


class TestConversation:
    def test_save_and_load(self, store):
        store.save_conversation("root", [{"role": "user", "content": "hi"}])
        result = store.load_conversation("root")
        assert isinstance(result, list)
        assert result[0]["content"] == "hi"

    def test_overwrite(self, store):
        store.save_conversation("root", [{"role": "user", "content": "v1"}])
        store.save_conversation("root", [{"role": "user", "content": "v2"}])
        result = store.load_conversation("root")
        assert result[0]["content"] == "v2"

    def test_load_missing(self, store):
        assert store.load_conversation("nonexistent") is None

    def test_multi_agent(self, populated_store):
        root = populated_store.load_conversation("root")
        swe = populated_store.load_conversation("swe")
        assert root is not None
        assert swe is not None
        assert root[0]["content"] == "Fix auth"
        assert swe[0]["content"] == "triggered"


# ─── State Tests ─────────────────────────────────────────────────


class TestState:
    def test_scratchpad(self, store):
        store.save_state("swe", scratchpad={"key": "value", "plan": "do stuff"})
        pad = store.load_scratchpad("swe")
        assert pad["key"] == "value"
        assert pad["plan"] == "do stuff"

    def test_turn_count(self, store):
        store.save_state("root", turn_count=5)
        assert store.load_turn_count("root") == 5

    def test_token_usage(self, store):
        store.save_state(
            "swe", token_usage={"prompt": 1000, "completion": 200, "total": 1200}
        )
        usage = store.load_token_usage("swe")
        assert usage["prompt"] == 1000
        assert usage["total"] == 1200

    def test_missing_state(self, store):
        assert store.load_scratchpad("nonexistent") == {}
        assert store.load_turn_count("nonexistent") == 0
        assert store.load_token_usage("nonexistent") == {}


# ─── Channel Tests ───────────────────────────────────────────────


class TestChannels:
    def test_append_and_read(self, store):
        store.save_channel_message("tasks", {"sender": "root", "content": "do thing"})
        store.save_channel_message(
            "tasks", {"sender": "root", "content": "another thing"}
        )
        msgs = store.get_channel_messages("tasks")
        assert len(msgs) == 2
        assert msgs[0]["content"] == "do thing"
        assert msgs[1]["content"] == "another thing"

    def test_multi_channel(self, populated_store):
        tasks = populated_store.get_channel_messages("tasks")
        chat = populated_store.get_channel_messages("team_chat")
        assert len(tasks) == 1
        assert len(chat) == 1
        assert tasks[0]["sender"] == "root"
        assert chat[0]["sender"] == "swe"

    def test_empty_channel(self, store):
        assert store.get_channel_messages("nonexistent") == []


# ─── Sub-Agent Tests ─────────────────────────────────────────────


class TestSubAgents:
    def test_save_and_load_meta(self, store):
        store.save_subagent(
            "swe",
            "explore",
            0,
            meta={"task": "find files", "turns": 2, "success": True},
        )
        meta = store.load_subagent_meta("swe", "explore", 0)
        assert meta is not None
        assert meta["task"] == "find files"
        assert meta["turns"] == 2

    def test_save_and_load_conversation(self, store):
        store.save_subagent(
            "swe",
            "explore",
            0,
            meta={"task": "test"},
            conv_json='[{"role": "user", "content": "find"}]',
        )
        conv = store.load_subagent_conversation("swe", "explore", 0)
        assert conv is not None
        assert "find" in conv

    def test_missing_subagent(self, store):
        assert store.load_subagent_meta("x", "y", 0) is None
        assert store.load_subagent_conversation("x", "y", 0) is None

    def test_run_counter(self, store):
        r0 = store.next_subagent_run("swe", "explore")
        r1 = store.next_subagent_run("swe", "explore")
        r2 = store.next_subagent_run("swe", "worker")
        assert r0 == 0
        assert r1 == 1
        assert r2 == 0


# ─── Job Tests ───────────────────────────────────────────────────


class TestJobs:
    def test_save_and_load(self, store):
        store.save_job("tool_bash_123", {"state": "done", "exit_code": 0})
        job = store.load_job("tool_bash_123")
        assert job is not None
        assert job["state"] == "done"

    def test_missing_job(self, store):
        assert store.load_job("nonexistent") is None


# ─── FTS Search Tests ────────────────────────────────────────────


class TestSearch:
    def test_events_indexed_in_fts(self, populated_store):
        results = populated_store.search("auth bug")
        assert len(results) > 0
        # Should find the user input and/or trigger content
        event_keys = [
            r["meta"]["event_key"] for r in results if "event_key" in r["meta"]
        ]
        assert len(event_keys) > 0

    def test_channel_messages_indexed(self, populated_store):
        results = populated_store.search("auth fix work")
        assert len(results) > 0

    def test_empty_search(self, store):
        results = store.search("nonexistent query xyz")
        assert results == []


# ─── Counter Restoration Tests ───────────────────────────────────


class TestCounterRestoration:
    def test_event_counter_survives_reopen(self, tmp_path):
        path = tmp_path / "counter_test.kohakutr"

        # Write some events
        s1 = SessionStore(path)
        s1.append_event("root", "text", {"content": "first"})
        s1.append_event("root", "text", {"content": "second"})
        s1.append_event("swe", "text", {"content": "third"})
        s1.close()

        # Reopen and verify counters restored
        s2 = SessionStore(path)
        key, _ = s2.append_event("root", "text", {"content": "fourth"})
        assert key == "root:e000002"  # Continues from 2, not 0

        swe_key, _ = s2.append_event("swe", "text", {"content": "fifth"})
        assert swe_key == "swe:e000001"  # Continues from 1
        s2.close()

    def test_channel_counter_survives_reopen(self, tmp_path):
        path = tmp_path / "ch_counter_test.kohakutr"

        s1 = SessionStore(path)
        s1.save_channel_message("tasks", {"content": "msg1"})
        s1.save_channel_message("tasks", {"content": "msg2"})
        s1.close()

        s2 = SessionStore(path)
        key = s2.save_channel_message("tasks", {"content": "msg3"})
        assert key == "tasks:m000002"
        s2.close()

    def test_subagent_counter_survives_reopen(self, tmp_path):
        path = tmp_path / "sa_counter_test.kohakutr"

        s1 = SessionStore(path)
        s1.save_subagent("swe", "explore", 0, meta={"task": "run0"})
        s1.save_subagent("swe", "explore", 1, meta={"task": "run1"})
        s1.close()

        s2 = SessionStore(path)
        run = s2.next_subagent_run("swe", "explore")
        assert run == 2  # Continues from 2
        s2.close()


# ─── Lifecycle Tests ─────────────────────────────────────────────


class TestLifecycle:
    def test_close_sets_paused(self, tmp_path):
        path = tmp_path / "lifecycle.kohakutr"
        s = SessionStore(path)
        s.init_meta(
            session_id="s1",
            config_type="agent",
            config_path="/p",
            pwd="/w",
            agents=["a"],
        )
        s.close()

        # Reopen and check status
        s2 = SessionStore(path)
        meta = s2.load_meta()
        assert meta["status"] == "paused"
        s2.close()

    def test_repr(self, store):
        assert "SessionStore" in repr(store)
        assert "test_session.kohakutr" in repr(store)

    def test_flush(self, store):
        store.append_event("root", "text", {"content": "cached"})
        store.flush()
        # Should not raise, data should be on disk
        events = store.get_events("root")
        assert len(events) == 1
