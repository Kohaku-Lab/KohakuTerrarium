"""Unit tests for session inspection summaries and reasoning helpers."""

import importlib.util
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

import pytest
from kohakuvault import KVault

from kohakuterrarium.session.occupancy.gate import GATE, ResourceReleaseError, try_fast
from kohakuterrarium.session.store import SessionStore

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "scripts" / "inspect_session.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("inspect_session", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_inspect_session = _load_script()


def _business_rows(path):
    with closing(
        sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    ) as db:
        return {
            table: db.execute(
                f'SELECT key, value FROM "{table}" ORDER BY key'
            ).fetchall()
            for table in (
                "meta",
                "state",
                "events",
                "channels",
                "subagents",
                "jobs",
                "conversation",
                "turn_rollup",
            )
        }


class TestBoundedText:
    def test_short_text_unchanged(self):
        assert _inspect_session._bounded_text("short", 10) == "short"

    def test_long_text_gets_marker(self):
        out = _inspect_session._bounded_text("abcdef", 3)
        assert out.startswith("abc")
        assert "3 more chars" in out


class TestReasoningEntries:
    def test_flat_fields(self):
        out = _inspect_session._reasoning_entries(
            {
                "role": "assistant",
                "content": "answer",
                "reasoning_content": "private",
                "reasoning": "plain",
            }
        )
        assert out == [
            ("reasoning_content", "private"),
            ("reasoning", "plain"),
        ]

    def test_reasoning_details_with_signature(self):
        out = _inspect_session._reasoning_entries(
            {
                "role": "assistant",
                "reasoning_details": [
                    {"type": "reasoning.text", "text": "think", "signature": "sig"}
                ],
            }
        )
        assert out == [
            ("reasoning_details[0]:reasoning.text", "think\n[signature: sig]")
        ]

    def test_anthropic_thinking_blocks(self):
        out = _inspect_session._reasoning_entries(
            {
                "role": "assistant",
                "_kt_anthropic_content": [
                    {"type": "text", "text": "answer"},
                    {"type": "thinking", "thinking": "hmm", "signature": "sig2"},
                ],
            }
        )
        assert out == [("anthropic:thinking[1]", "hmm\n[signature: sig2]")]

    def test_ordered_segments_preferred_over_flat_fields(self):
        out = _inspect_session._reasoning_entries(
            {
                "role": "assistant",
                "reasoning_content": "flat",
                "_kt_assistant_segments": [
                    {
                        "type": "reasoning",
                        "source": "reasoning_content",
                        "text": "think 1",
                    },
                    {"type": "text", "text": "answer 1"},
                    {"type": "tool_call_ref", "call_id": "call_1"},
                ],
            }
        )
        assert out == [
            ("segments[0] reasoning:reasoning_content", "think 1"),
            ("segments[1] text", "answer 1"),
            ("segments[2] tool_call_ref", "call_1"),
        ]

    def test_non_assistant_ignored_by_print_path(self):
        assert _inspect_session._reasoning_entries({"role": "user"}) == []


@pytest.fixture
def summary_store(tmp_path):
    store = SessionStore(tmp_path / "summary.kohakutr")
    store.init_meta("summary", "agent", "config", "", ["a", "ab", "a:worker", "empty"])
    try:
        yield store
    finally:
        store.close(update_status=False)


def forbid_history_values(monkeypatch, store):
    tables = (store.events, store.channels, store.subagents)
    vault_type = type(store.events)

    def guard(original):
        def read(table, *args, **kwargs):
            if any(table is target for target in tables):
                pytest.fail("summary must not read history payloads")
            return original(table, *args, **kwargs)

        return read

    monkeypatch.setattr(vault_type, "__getitem__", guard(vault_type.__getitem__))
    monkeypatch.setattr(vault_type, "get", guard(vault_type.get))


@pytest.fixture
def closed_inspection_source(tmp_path):
    path = tmp_path / "inspect.kohakutr"
    store = SessionStore(path)
    try:
        store.init_meta("inspect", "agent", "config", "", ["a"])
        store.append_event("a", "text", {"content": "inspectionneedle"})
        store.save_channel_message("tasks", {"sender": "a", "content": "task detail"})
        store.save_conversation("a", [{"role": "assistant", "content": "answer"}])
    finally:
        store.close(update_status=False)
    # Legacy files carry no event counters.
    with closing(KVault(str(path), table="state")) as state:
        state.delete("counters:max_event_id")
        state.delete("counters:exact_event_id")
    with closing(KVault(str(path), table="meta")) as meta:
        meta.enable_auto_pack()
        meta["status"] = "completed"
        meta["last_active"] = "original-recency"
    return path


@pytest.mark.parametrize("failure", [None, RuntimeError, KeyboardInterrupt])
def test_main_legacy_counter_absence_and_failure_cleanup(
    closed_inspection_source, monkeypatch, capsys, failure
):
    path = closed_inspection_source
    before = _business_rows(path)
    monkeypatch.setattr(
        _inspect_session.sys,
        "argv",
        [str(SCRIPT_PATH), str(path), "--all", "--search", "inspectionneedle"],
    )
    original = _inspect_session.print_summary

    def observed(store):
        assert try_fast(path, eligible=True).reason == "legacy_enrolled"
        original(store)
        if failure:
            raise failure("output interrupted")

    monkeypatch.setattr(_inspect_session, "print_summary", observed)
    if failure:
        with pytest.raises(failure, match="output interrupted"):
            _inspect_session.main()
    else:
        _inspect_session.main()
        output = capsys.readouterr().out
        assert "TEXT: inspectionneedle" in output
        assert "[a] task detail" in output
        assert "[assistant] answer" in output
        assert "Search: 'inspectionneedle' (1 results)" in output
    assert not GATE.snapshot(path).registered
    assert _business_rows(path) == before
    with closing(SessionStore.open_readonly(path)) as check:
        assert check.state.get("counters:max_event_id") is None
        assert check.meta["status"] == "completed"
        assert check.meta["last_active"] == "original-recency"


def test_main_waits_for_new_then_releases_its_own_resources(
    closed_inspection_source, monkeypatch
):
    path = closed_inspection_source
    before = _business_rows(path)
    monkeypatch.setattr(_inspect_session.sys, "argv", [str(SCRIPT_PATH), str(path)])
    entered = threading.Event()
    original = _inspect_session.print_summary

    def observed(store):
        entered.set()
        assert try_fast(path, eligible=True).reason == "legacy_enrolled"
        original(store)

    monkeypatch.setattr(_inspect_session, "print_summary", observed)
    lease = try_fast(path, eligible=True).lease
    assert lease is not None
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_inspect_session.main)
        try:
            deadline = time.monotonic() + 5
            while (
                not GATE.snapshot(path).legacy_pending and time.monotonic() < deadline
            ):
                time.sleep(0.005)
            assert GATE.snapshot(path).legacy_pending
            assert not entered.is_set() and not future.done()
        finally:
            lease.finish(source_released=True)
        future.result(timeout=10)
    assert entered.is_set() and not GATE.snapshot(path).registered
    assert _business_rows(path) == before


def test_main_refuses_uncertain_source_and_missing_path(
    closed_inspection_source, tmp_path, monkeypatch
):
    path = closed_inspection_source
    before = _business_rows(path)
    lease = try_fast(path, eligible=True).lease
    assert lease is not None
    lease.finish(source_released=False)
    monkeypatch.setattr(_inspect_session.sys, "argv", [str(SCRIPT_PATH), str(path)])
    with pytest.raises(ResourceReleaseError):
        _inspect_session.main()
    assert GATE.snapshot(path).new_uncertain
    assert _business_rows(path) == before
    missing = tmp_path / "missing.kohakutr"
    monkeypatch.setattr(_inspect_session.sys, "argv", [str(SCRIPT_PATH), str(missing)])
    with pytest.raises(SystemExit) as exc:
        _inspect_session.main()
    assert exc.value.code == 1 and not missing.exists()


class TestStoredSummaryCounts:
    def test_counts_keys_without_reading_values(
        self, summary_store, monkeypatch, capsys
    ):
        store = summary_store
        for key in (
            "a:e000000",
            "a:e000009",
            "a:e1000000",
            "ab:e000000",
            "a:worker:e000002",
        ):
            store.events[key] = b"unvalidated payload"
        for key in ("a:m000000", "a:m1000000", "ab:m000003", "a:worker:m000000"):
            store.channels[key] = b"unvalidated payload"
        for key in (
            "a:worker:explore:0:meta",
            "a:worker:explore:7:meta",
            "ab:plan:3:meta",
        ):
            store.subagents[key] = b"unvalidated payload"
        store.subagents["a:worker:explore:0:conversation"] = "not a run"
        forbid_history_values(monkeypatch, store)
        _inspect_session.print_summary(store)
        lines = capsys.readouterr().out.splitlines()
        assert lines[0:4] == [
            "Session: summary",
            "Type: agent",
            "Config: config",
            "Status: running",
        ]
        assert "Agents: ['a', 'ab', 'a:worker', 'empty']" in lines
        assert [line for line in lines if line.endswith(" events")] == [
            "  a: 3 events",
            "  ab: 1 events",
            "  a:worker: 1 events",
            "  empty: 0 events",
        ]
        assert "Channels: 3 (4 messages)" in lines
        assert "Sub-agent runs: 3" in lines
        assert "Counts describe stored records; payloads are not validated." in lines

    def test_invalid_record_keys_not_counted(self, summary_store, monkeypatch, capsys):
        store = summary_store
        for key in ("a:e", "a:e-1", "a:e12x", "a:e１２", "a:e000001:extra", "a:other"):
            store.events[key] = {}
        for key in ("a:m", "a:m-1", "a:m12x", "a:m１２", "a:m000001:extra", "a:other"):
            store.channels[key] = {}
        for key in (
            "a:meta",
            "a:worker:meta",
            "a:worker:-1:meta",
            "a:worker:１２:meta",
            "a:worker:0:conversation",
            "a:worker:0:meta:extra",
        ):
            store.subagents[key] = {}
        forbid_history_values(monkeypatch, store)
        _inspect_session.print_summary(store)
        out = capsys.readouterr().out
        assert "  a: 0 events" in out
        assert "Channels: 0 (0 messages)" in out
        assert "Sub-agent runs: 0" in out

    def test_counts_are_not_truncated_at_default_key_limit(
        self, summary_store, monkeypatch, capsys
    ):
        store = summary_store
        store.channels.enable_cache(flush_interval=60)
        store.subagents.enable_cache(flush_interval=60)
        for index in range(10005):
            store.events[f"a:e{index:06d}"] = {}
            store.channels[f"c{index}:m000000"] = {}
            store.subagents[f"a:explore:{index}:meta"] = {}
        store.flush()
        store.channels.flush_cache()
        store.subagents.flush_cache()
        forbid_history_values(monkeypatch, store)
        _inspect_session.print_summary(store)
        out = capsys.readouterr().out
        assert "  a: 10005 events" in out
        assert "Channels: 10005 (10005 messages)" in out
        assert "Sub-agent runs: 10005" in out

    def test_empty_tables_and_new_cached_events(
        self, summary_store, monkeypatch, capsys
    ):
        store = summary_store
        forbid_history_values(monkeypatch, store)
        _inspect_session.print_summary(store)
        first = capsys.readouterr().out
        assert "  a: 0 events" in first
        assert "Channels: 0 (0 messages)" in first
        assert "Sub-agent runs: 0" in first
        store.events["a:e000004"] = {}
        _inspect_session.print_summary(store)
        second = capsys.readouterr().out
        assert "  a: 1 events" in second

    @pytest.mark.parametrize("encode", [False, True])
    def test_key_parsing_handles_markers_in_names(self, encode):
        keys = [
            "a:e000000",
            "a:echild:e000001",
            "ab:e000005",
            "a:worker:e1000001",
            "a:echild:e000002",
        ]
        if encode:
            keys = [key.encode() for key in keys]
        assert _inspect_session._sequence_counts(keys, ":e") == {
            "a": 1,
            "a:echild": 2,
            "ab": 1,
            "a:worker": 1,
        }
        runs = [
            "a:explore:role:2:meta",
            "a:explore:role:2:conversation",
            "a:explore:1000000:meta",
        ]
        if encode:
            runs = [key.encode() for key in runs]
        assert _inspect_session._subagent_run_count(runs) == 2

    def test_undecodable_keys_are_excluded(self, summary_store, monkeypatch, capsys):
        store = summary_store
        for table in (store.events, store.channels, store.subagents):
            table[b"\xff"] = {}
        store.events["a:e000000"] = {}
        store.channels["tasks:m000000"] = {}
        store.subagents["a:explore:0:meta"] = {}
        forbid_history_values(monkeypatch, store)
        _inspect_session.print_summary(store)
        out = capsys.readouterr().out
        assert "  a: 1 events" in out
        assert "Channels: 1 (1 messages)" in out
        assert "Sub-agent runs: 1" in out

    def test_main_summary_preserves_business_state(
        self, summary_store, monkeypatch, capsys
    ):
        store = summary_store
        path = store.path
        store.append_event("a", "text", {"content": "summary only"})
        store.meta["status"] = "completed"
        store.close(update_status=False)
        opened = []

        before = _business_rows(path)

        class ObservedStore(SessionStore):
            def __init__(self, path, **kwargs):
                super().__init__(path, **kwargs)
                opened.append(self)
                forbid_history_values(monkeypatch, self)

        monkeypatch.setattr(_inspect_session, "SessionStore", ObservedStore)
        monkeypatch.setattr(_inspect_session.sys, "argv", [str(SCRIPT_PATH), path])
        _inspect_session.main()
        out = capsys.readouterr().out
        assert "Status: completed" in out
        assert "  a: 1 events" in out
        assert "TEXT: summary only" not in out
        assert "Use --events" in out
        assert len(opened) == 1 and opened[0]._closed
        assert _business_rows(path) == before
        assert not GATE.snapshot(path).registered

    def test_explicit_detail_readers_still_show_payloads(self, summary_store, capsys):
        store = summary_store
        store.append_event("a", "text", {"content": "event detail"})
        store.save_channel_message(
            "team:tasks", {"sender": "a", "content": "channel detail"}
        )
        store.save_subagent(
            "a",
            "explore",
            0,
            {"task": "subagent detail", "turns": 2, "tools_used": [], "success": True},
            "[]",
        )
        _inspect_session.print_events(store, "a")
        _inspect_session.print_channels(store)
        _inspect_session.print_subagents(store)
        out = capsys.readouterr().out
        assert "TEXT: event detail" in out
        assert "team:tasks (1 messages):" in out
        assert "[a] channel detail" in out
        assert "task=subagent detail" in out
        assert "(conversation saved)" in out
