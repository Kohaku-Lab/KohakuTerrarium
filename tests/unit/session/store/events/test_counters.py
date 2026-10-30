"""Unit tests for :mod:`kohakuterrarium.session.store.events.counters`."""

from kohakuvault import KVault

from kohakuterrarium.session.store.events.counters import (
    EXACT_EVENT_ID_KEY,
    MAX_EVENT_ID_KEY,
    EventIdCounters,
    _decode_key,
    persist_event_counter,
    reserve_event_ids,
    restore_event_counters,
    restore_subagent_counters,
    restore_suffix_counters,
    scan_max_event_id,
)


def _make_table(path, name) -> KVault:
    t = KVault(str(path), table=name)
    t.enable_auto_pack()
    return t


class TestDecodeKey:
    def test_bytes_decoded(self):
        assert _decode_key(b"hello") == "hello"

    def test_str_unchanged(self):
        assert _decode_key("hello") == "hello"

    def test_invalid_bytes_replaced(self):
        out = _decode_key(b"\xff\xfe")
        # Doesn't raise; each undecodable byte becomes U+FFFD.
        assert out == "��"


class TestRestoreEventSeq:
    def test_populates_seq_from_keys_without_state(self, tmp_path):
        t = _make_table(tmp_path / "ev.kohakutr", "events")
        try:
            t["alpha:e000000"] = {"event_id": 1}
            t["alpha:e000001"] = {"event_id": 2}
            t["beta:e000003"] = {"event_id": 5}
            t["alpha:enotanumber"] = {"event_id": 7}
            seq: dict[str, int] = {}
            assert restore_event_counters(t, seq) == EventIdCounters(None, 0)
            assert seq == {"alpha": 2, "beta": 4}
        finally:
            t.close()


class TestScanMaxEventId:
    def test_takes_largest_int_id(self, tmp_path):
        t = _make_table(tmp_path / "ev.kohakutr", "events")
        try:
            assert scan_max_event_id(t) == 0
            t["alpha:e000000"] = {"event_id": 100}
            t["alpha:e000001"] = {"event_id": 50}
            t["alpha:e000002"] = {"event_id": 200}
            t["alpha:e000003"] = {"data": "no id"}
            t["alpha:e000004"] = ["not", "a", "dict"]
            t["alpha:e000005"] = {"event_id": "300"}
            assert scan_max_event_id(t) == 200
        finally:
            t.close()


class TestPersistedCounters:
    def test_clean_close_is_trusted_without_reading_values(self, tmp_path):
        events = _make_table(tmp_path / "ev.kohakutr", "events")
        state = _make_table(tmp_path / "ev.kohakutr", "state")
        try:
            events["alpha:e000000"] = ["corrupt"]
            assert reserve_event_ids(state, 264)
            assert persist_event_counter(state, 200, reserved=264)
            assert state.get_many([MAX_EVENT_ID_KEY, EXACT_EVENT_ID_KEY]) == [200, 200]
            seq: dict[str, int] = {}
            assert restore_event_counters(events, seq, state=state) == EventIdCounters(
                200, 200
            )
            assert seq["alpha"] == 1
        finally:
            events.close()
            state.close()

    def test_reservation_only_raises_the_watermark(self, tmp_path):
        state = _make_table(tmp_path / "ev.kohakutr", "state")
        try:
            assert reserve_event_ids(state, 64)
            assert reserve_event_ids(state, 10)
            assert state[MAX_EVENT_ID_KEY] == 64
            assert reserve_event_ids(state, 128)
            assert state[MAX_EVENT_ID_KEY] == 128
        finally:
            state.close()

    def test_crash_after_reservation_needs_a_scan_and_keeps_the_floor(self, tmp_path):
        events = _make_table(tmp_path / "ev.kohakutr", "events")
        state = _make_table(tmp_path / "ev.kohakutr", "state")
        try:
            persist_event_counter(state, 5, reserved=0)
            reserve_event_ids(state, 70)
            assert restore_event_counters(events, {}, state=state) == EventIdCounters(
                None, 70
            )
        finally:
            events.close()
            state.close()

    def test_close_under_a_foreign_watermark_writes_only_the_marker(self, tmp_path):
        state = _make_table(tmp_path / "ev.kohakutr", "state")
        events = _make_table(tmp_path / "ev.kohakutr", "events")
        try:
            # Another writer reserved through 64; this one reserved nothing.
            reserve_event_ids(state, 64)
            assert persist_event_counter(state, 3, reserved=0)
            assert state.get_many([MAX_EVENT_ID_KEY, EXACT_EVENT_ID_KEY]) == [64, 3]
            assert restore_event_counters(events, {}, state=state) == EventIdCounters(
                None, 64
            )
        finally:
            state.close()
            events.close()

    def test_legacy_counter_is_a_floor_until_a_clean_close(self, tmp_path):
        state = _make_table(tmp_path / "ev.kohakutr", "state")
        events = _make_table(tmp_path / "ev.kohakutr", "events")
        try:
            state[MAX_EVENT_ID_KEY] = 42
            assert restore_event_counters(events, {}, state=state) == EventIdCounters(
                None, 42
            )
            assert persist_event_counter(state, 42, reserved=0)
            assert restore_event_counters(events, {}, state=state) == EventIdCounters(
                42, 42
            )
        finally:
            state.close()
            events.close()

    def test_counter_failures_are_reported(self, tmp_path, caplog):
        class Broken:
            def put_many(self, rows):
                raise OSError("disk gone")

            def __setitem__(self, key, value):
                raise OSError("disk gone")

            def get(self, key):
                raise OSError("disk gone")

        assert persist_event_counter(Broken(), 3, reserved=0) is False
        assert reserve_event_ids(Broken(), 3) is False
        assert "Failed to persist event counter" in caplog.text
        assert "Failed to reserve event ids" in caplog.text
        events = _make_table(tmp_path / "ev.kohakutr", "events")
        try:
            assert restore_event_counters(events, {}, state=Broken()) == (
                EventIdCounters(None, 0)
            )
            assert "Failed to read persisted event counter" in caplog.text
        finally:
            events.close()


class TestRestoreSuffixCounters:
    def test_basic(self, tmp_path):
        t = _make_table(tmp_path / "ch.kohakutr", "channels")
        try:
            t["mychan:m000000"] = {"x": 1}
            t["mychan:m000002"] = {"x": 2}
            t["other:m000000"] = {"x": 3}
            counter: dict[str, int] = {}
            restore_suffix_counters(t, ":m", counter)
            assert counter["mychan"] == 3  # last seq 2 + 1
            assert counter["other"] == 1
        finally:
            t.close()

    def test_bad_seq_skipped(self, tmp_path):
        t = _make_table(tmp_path / "ch.kohakutr", "channels")
        try:
            t["mychan:mNOTNUM"] = {"x": 1}
            counter: dict[str, int] = {}
            restore_suffix_counters(t, ":m", counter)
            assert "mychan" not in counter
        finally:
            t.close()


class TestRestoreSubagentCounters:
    def test_basic(self, tmp_path):
        t = _make_table(tmp_path / "sa.kohakutr", "subagents")
        try:
            t["parent:critic:0:meta"] = {"x": 1}
            t["parent:critic:1:meta"] = {"x": 2}
            t["parent:critic:5:meta"] = {"x": 3}
            t["other:plan:0:meta"] = {"x": 4}
            runs: dict[str, int] = {}
            restore_subagent_counters(t, runs)
            assert runs["parent:critic"] == 6  # 5+1
            assert runs["other:plan"] == 1
        finally:
            t.close()

    def test_ignores_non_meta_keys(self, tmp_path):
        t = _make_table(tmp_path / "sa.kohakutr", "subagents")
        try:
            t["parent:critic:0:data"] = {"x": 1}
            runs: dict[str, int] = {}
            restore_subagent_counters(t, runs)
            assert runs == {}
        finally:
            t.close()

    def test_bad_run_skipped(self, tmp_path):
        t = _make_table(tmp_path / "sa.kohakutr", "subagents")
        try:
            t["parent:critic:not_an_int:meta"] = {"x": 1}
            runs: dict[str, int] = {}
            restore_subagent_counters(t, runs)
            assert runs == {}
        finally:
            t.close()
