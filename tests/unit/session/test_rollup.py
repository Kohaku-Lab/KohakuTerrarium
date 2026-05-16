"""Unit tests for :mod:`kohakuterrarium.session.rollup`."""

from kohakuvault import KVault

from kohakuterrarium.session.rollup import (
    get_turn_rollup,
    list_turn_rollups,
    save_turn_rollup,
    turn_rollup_key,
)


def _make_table(path) -> KVault:
    t = KVault(str(path), table="rollups")
    t.enable_auto_pack()
    return t


class TestTurnRollupKey:
    def test_format(self):
        assert turn_rollup_key("alpha", 0) == "alpha:turn:000000"
        assert turn_rollup_key("alpha", 7) == "alpha:turn:000007"
        assert turn_rollup_key("alpha", 123456) == "alpha:turn:123456"

    def test_zero_padded_six(self):
        # Specifically padded to six digits so sorted scan works.
        k = turn_rollup_key("a", 12)
        assert k.split(":")[-1] == "000012"

    def test_overflow_beyond_six_digits(self):
        # The pad is a minimum width — does not truncate.
        k = turn_rollup_key("a", 1234567)
        assert k.split(":")[-1] == "1234567"


class TestSaveTurnRollup:
    def test_round_trip_basic(self, tmp_path):
        t = _make_table(tmp_path / "r.kohakutr")
        try:
            save_turn_rollup(t, "alpha", 0, {"tokens_in": 1, "tokens_out": 2})
            got = get_turn_rollup(t, "alpha", 0)
            assert got["tokens_in"] == 1
            assert got["tokens_out"] == 2
            assert got["agent"] == "alpha"
            assert got["turn_index"] == 0
            # cost_usd auto-injected with default None.
            assert got["cost_usd"] is None
        finally:
            t.close()

    def test_existing_keys_not_overwritten_by_defaults(self, tmp_path):
        t = _make_table(tmp_path / "r.kohakutr")
        try:
            save_turn_rollup(
                t,
                "alpha",
                3,
                {"agent": "ignored", "turn_index": 999, "cost_usd": 1.25},
            )
            got = get_turn_rollup(t, "alpha", 3)
            # Caller-provided values win over setdefault().
            assert got["agent"] == "ignored"
            assert got["turn_index"] == 999
            assert got["cost_usd"] == 1.25
        finally:
            t.close()

    def test_does_not_mutate_input(self, tmp_path):
        t = _make_table(tmp_path / "r.kohakutr")
        try:
            payload = {"tokens_in": 5}
            save_turn_rollup(t, "alpha", 0, payload)
            # Input dict not mutated.
            assert payload == {"tokens_in": 5}
        finally:
            t.close()


class TestGetTurnRollup:
    def test_missing_returns_none(self, tmp_path):
        t = _make_table(tmp_path / "r.kohakutr")
        try:
            assert get_turn_rollup(t, "alpha", 0) is None
        finally:
            t.close()

    def test_non_dict_value_returns_none(self, tmp_path):
        t = _make_table(tmp_path / "r.kohakutr")
        try:
            t[turn_rollup_key("alpha", 0)] = ["not", "a", "dict"]
            assert get_turn_rollup(t, "alpha", 0) is None
        finally:
            t.close()


class TestListTurnRollups:
    def test_ordered_by_turn_index(self, tmp_path):
        t = _make_table(tmp_path / "r.kohakutr")
        try:
            for i in [2, 0, 5, 1]:
                save_turn_rollup(t, "alpha", i, {"tokens_in": i})
            rows = list_turn_rollups(t, "alpha")
            assert [r["turn_index"] for r in rows] == [0, 1, 2, 5]
        finally:
            t.close()

    def test_filters_by_agent_prefix(self, tmp_path):
        t = _make_table(tmp_path / "r.kohakutr")
        try:
            save_turn_rollup(t, "alpha", 0, {"x": 1})
            save_turn_rollup(t, "beta", 0, {"x": 2})
            alpha = list_turn_rollups(t, "alpha")
            beta = list_turn_rollups(t, "beta")
            assert len(alpha) == 1 and alpha[0]["x"] == 1
            assert len(beta) == 1 and beta[0]["x"] == 2
        finally:
            t.close()

    def test_empty_table(self, tmp_path):
        t = _make_table(tmp_path / "r.kohakutr")
        try:
            assert list_turn_rollups(t, "alpha") == []
        finally:
            t.close()

    def test_unreadable_row_is_skipped_not_fatal(self, tmp_path):
        # If reading one rollup row raises, list_turn_rollups logs and
        # skips it — the surrounding good rows still come back.
        t = _make_table(tmp_path / "r.kohakutr")
        try:
            save_turn_rollup(t, "alpha", 0, {"tokens_in": 0})
            save_turn_rollup(t, "alpha", 1, {"tokens_in": 1})
            save_turn_rollup(t, "alpha", 2, {"tokens_in": 2})

            bad_key = turn_rollup_key("alpha", 1)

            class _FlakyTable:
                """Wraps the real table; one specific key raises on read."""

                def __init__(self, inner):
                    self._inner = inner

                def keys(self, **kw):
                    return self._inner.keys(**kw)

                def __getitem__(self, key):
                    k = key.decode() if isinstance(key, bytes) else key
                    if k == bad_key:
                        raise RuntimeError("corrupt row")
                    return self._inner[key]

            rows = list_turn_rollups(_FlakyTable(t), "alpha")
            # turn 1 was unreadable → skipped; 0 and 2 survive in order.
            assert [r["turn_index"] for r in rows] == [0, 2]
        finally:
            t.close()
