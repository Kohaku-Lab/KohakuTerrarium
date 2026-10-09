"""Unit tests for :mod:`kohakuterrarium.session.run_state`."""

import json
import subprocess
import sys
import textwrap
import time

import pytest

import kohakuterrarium.session.run_state as rs
from kohakuterrarium.session.output import SessionOutput
from kohakuterrarium.session.store import SessionStore


@pytest.fixture
def store(tmp_path):
    s = SessionStore(str(tmp_path / "s.kohakutr"))
    yield s
    rs.thaw(s)
    s.close(update_status=False)


class _Agent:
    def __init__(self, turn=3):
        self.controller = None
        self.session = None
        self._turn_index = turn
        self._branch_id = 1
        self._parent_branch_path = []


def test_write_run_records_each_state_and_keeps_the_turn(store):
    started = rs.write_run(store, "alice", rs.STARTED, turn_id=7, now=100.0)
    assert started == {
        "state": "started",
        "turn_id": 7,
        "started_at": 100.0,
        "last_beat": 100.0,
        "ended_at": None,
        "boot_id": rs.BOOT_ID,
    }
    rs.write_run(store, "alice", rs.ACTIVE, now=105.0)
    idle = rs.write_run(store, "alice", rs.IDLE, now=110.0)
    assert idle["turn_id"] == 7 and idle["started_at"] == 100.0
    assert (idle["last_beat"], idle["ended_at"]) == (110.0, 110.0)
    assert rs.read_run(store, "alice") == idle
    assert rs.read_runs(store, ["alice", "bob"]) == {"alice": idle, "bob": None}


def test_classify_reads_a_record_left_by_an_earlier_boot():
    old = {"state": rs.ACTIVE, "boot_id": "previous"}
    assert rs.classify(old) == "interrupted"
    assert rs.classify({"state": rs.STARTED, "boot_id": "previous"}) == "interrupted"
    assert rs.classify({"state": rs.ACTIVE, "boot_id": rs.BOOT_ID}) == rs.IDLE
    assert rs.classify({"state": rs.IDLE, "boot_id": "previous"}) == rs.IDLE
    assert rs.classify({"state": rs.STOPPED, "boot_id": "previous"}) == rs.STOPPED
    assert rs.classify(None) == rs.IDLE


def test_lifecycle_tells_user_stop_shutdown_and_crash_apart(store):
    assert rs.stop_reason({}) is None
    rs.set_lifecycle(store, live=True)
    assert rs.stop_reason(rs.read_lifecycle(store)) is None
    assert rs.stop_reason(rs.read_lifecycle(store), boot_id="later") == rs.STOP_CRASH
    rs.set_lifecycle(store, live=True, stop_reason=rs.STOP_SHUTDOWN)
    assert rs.stop_reason(rs.read_lifecycle(store), boot_id="later") == rs.STOP_SHUTDOWN
    rs.set_lifecycle(store, live=False, stop_reason=rs.STOP_USER)
    assert rs.stop_reason(rs.read_lifecycle(store), boot_id="later") == rs.STOP_USER
    assert rs.stop_reason({"live": False}) == rs.STOP_USER


def test_mark_shutdown_records_the_reason_and_freezes_the_records(store):
    rs.set_lifecycle(store, live=True)
    rs.write_run(store, "alice", rs.ACTIVE, now=1.0)
    rs.mark_shutdown(store)
    assert rs.read_lifecycle(store)["stop_reason"] == rs.STOP_SHUTDOWN
    assert rs.write_run(store, "alice", rs.STOPPED) is None
    assert rs.read_run(store, "alice")["state"] == rs.ACTIVE
    rs.thaw(store)
    assert rs.write_run(store, "alice", rs.IDLE)["state"] == rs.IDLE


def test_mark_shutdown_leaves_a_session_that_is_not_live_alone(store):
    rs.set_lifecycle(store, live=False, stop_reason=rs.STOP_USER)
    rs.mark_shutdown(store)
    assert rs.read_lifecycle(store)["stop_reason"] == rs.STOP_USER
    assert rs.is_frozen(store)


def test_tracker_beats_at_most_every_interval_and_only_inside_a_turn(
    store, monkeypatch
):
    clock = [1000.0]
    monkeypatch.setattr(rs.time, "monotonic", lambda: clock[0])
    writes = []
    tracker = rs.RunTracker(
        store, "alice", lambda fn, *a, **k: writes.append(a[2]) or fn(*a, **k)
    )
    tracker.touch()
    assert writes == []
    tracker.turn_started(turn_id=1)
    clock[0] += rs.BEAT_INTERVAL - 0.1
    tracker.touch()
    clock[0] += 0.2
    tracker.touch()
    tracker.touch()
    tracker.turn_ended()
    clock[0] += 60
    tracker.touch()
    tracker.stopped()
    assert writes == [rs.STARTED, rs.ACTIVE, rs.IDLE, rs.STOPPED]
    assert rs.read_run(store, "alice")["state"] == rs.STOPPED


async def test_session_output_brackets_turns_and_stops(store):
    out = SessionOutput("alice", store, _Agent(turn=3))
    await out.on_processing_start()
    await out.drain()
    assert rs.read_run(store, "alice")["state"] == rs.STARTED
    assert rs.read_run(store, "alice")["turn_id"] == 3
    await out.on_processing_end()
    await out.drain()
    assert rs.read_run(store, "alice")["state"] == rs.IDLE
    await out.stop()
    assert rs.read_run(store, "alice")["state"] == rs.STOPPED


async def test_session_output_keeps_the_shutdown_record_through_teardown(store):
    out = SessionOutput("alice", store, _Agent())
    await out.on_processing_start()
    await out.drain()
    rs.mark_shutdown(store)
    await out.on_processing_end()
    await out.stop()
    assert rs.read_run(store, "alice")["state"] == rs.STARTED


_CHILD = textwrap.dedent("""
    import sys, time
    import kohakuterrarium.session.run_state as rs
    from kohakuterrarium.session.store import SessionStore
    store = SessionStore(sys.argv[1])
    seq = 0
    while True:
        seq += 1
        store.append_event("alice", "text_chunk", {"content": "x" * 200})
        rs.write_run(store, "alice", rs.STARTED, turn_id=seq)
        sys.stdout.write(f"{seq}\\n")
        sys.stdout.flush()
    """)


@pytest.mark.parametrize("kill_after", [0.6, 1.2])
def test_a_hard_killed_writer_never_loses_an_acknowledged_record(tmp_path, kill_after):
    path = str(tmp_path / "kill.kohakutr")
    proc = subprocess.Popen(
        [sys.executable, "-c", _CHILD, path], stdout=subprocess.PIPE, text=True
    )
    acked = 0
    deadline = time.monotonic() + kill_after
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if line.strip().isdigit():
            acked = int(line)
    proc.kill()
    proc.wait()
    for line in proc.stdout:
        if line.strip().isdigit():
            acked = int(line)
    assert acked > 0
    store = SessionStore(path)
    try:
        record = rs.read_run(store, "alice")
        assert record["turn_id"] >= acked, json.dumps(record)
        assert record["state"] == rs.STARTED
    finally:
        store.close(update_status=False)
