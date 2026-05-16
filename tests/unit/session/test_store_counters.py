"""Unit tests for :mod:`kohakuterrarium.session.store_counters`."""

from kohakuvault import KVault

from kohakuterrarium.session.store_counters import (
    _decode_key,
    restore_event_counters,
    restore_subagent_counters,
    restore_suffix_counters,
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


class TestRestoreEventCounters:
    def test_populates_seq_from_keys(self, tmp_path):
        t = _make_table(tmp_path / "ev.kohakutr", "events")
        try:
            t["alpha:e000000"] = {"event_id": 1}
            t["alpha:e000001"] = {"event_id": 2}
            t["beta:e000003"] = {"event_id": 5}
            seq: dict[str, int] = {}
            max_eid = restore_event_counters(t, seq)
            # Next-seq for alpha is 2 (i.e. 1+1).
            assert seq["alpha"] == 2
            # Next-seq for beta is 4 (i.e. 3+1).
            assert seq["beta"] == 4
            assert max_eid == 5
        finally:
            t.close()

    def test_ignores_malformed_seq(self, tmp_path):
        t = _make_table(tmp_path / "ev.kohakutr", "events")
        try:
            t["alpha:enotanumber"] = {"event_id": 7}
            seq: dict[str, int] = {}
            max_eid = restore_event_counters(t, seq)
            # Bad seq is skipped; max_event_id still tracked.
            assert "alpha" not in seq
            assert max_eid == 7
        finally:
            t.close()

    def test_no_event_id_field(self, tmp_path):
        t = _make_table(tmp_path / "ev.kohakutr", "events")
        try:
            t["alpha:e000000"] = {"data": "x"}
            seq: dict[str, int] = {}
            assert restore_event_counters(t, seq) == 0
            assert seq["alpha"] == 1
        finally:
            t.close()

    def test_non_dict_event(self, tmp_path):
        t = _make_table(tmp_path / "ev.kohakutr", "events")
        try:
            t["alpha:e000000"] = ["not", "a", "dict"]
            seq: dict[str, int] = {}
            assert restore_event_counters(t, seq) == 0
        finally:
            t.close()

    def test_empty_table(self, tmp_path):
        t = _make_table(tmp_path / "ev.kohakutr", "events")
        try:
            seq: dict[str, int] = {}
            assert restore_event_counters(t, seq) == 0
            assert seq == {}
        finally:
            t.close()

    def test_max_event_id_takes_largest(self, tmp_path):
        t = _make_table(tmp_path / "ev.kohakutr", "events")
        try:
            t["alpha:e000000"] = {"event_id": 100}
            t["alpha:e000001"] = {"event_id": 50}
            t["alpha:e000002"] = {"event_id": 200}
            seq: dict[str, int] = {}
            assert restore_event_counters(t, seq) == 200
        finally:
            t.close()


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
