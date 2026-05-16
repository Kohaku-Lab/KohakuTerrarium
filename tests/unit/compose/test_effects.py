"""Unit tests for :mod:`kohakuterrarium.compose.effects`."""

from kohakuterrarium.compose.effects import Effects, _add, _max, _mul


class TestEffectsDataclass:
    def test_default_fields(self):
        e = Effects()
        assert e.cost is None
        assert e.latency is None
        assert e.reliability is None

    def test_explicit_values(self):
        e = Effects(cost=1.0, latency=2.0, reliability=0.9)
        assert e.cost == 1.0
        assert e.latency == 2.0
        assert e.reliability == 0.9


class TestSequentialCompose:
    def test_adds_cost_and_latency_mul_reliability(self):
        a = Effects(cost=1.0, latency=2.0, reliability=0.9)
        b = Effects(cost=3.0, latency=4.0, reliability=0.8)
        out = a.sequential(b)
        assert out.cost == 4.0
        assert out.latency == 6.0
        # 0.9 * 0.8
        assert abs(out.reliability - 0.72) < 1e-9

    def test_none_propagates(self):
        a = Effects(cost=None, latency=2.0, reliability=0.9)
        b = Effects(cost=3.0, latency=None, reliability=None)
        out = a.sequential(b)
        assert out.cost is None
        assert out.latency is None
        assert out.reliability is None


class TestParallelCompose:
    def test_adds_cost_max_latency_mul_reliability(self):
        a = Effects(cost=1.0, latency=2.0, reliability=0.9)
        b = Effects(cost=3.0, latency=4.0, reliability=0.8)
        out = a.parallel(b)
        assert out.cost == 4.0
        # max(2, 4)
        assert out.latency == 4.0
        assert abs(out.reliability - 0.72) < 1e-9

    def test_none_propagates(self):
        a = Effects(cost=None, latency=2.0)
        b = Effects(cost=3.0, latency=4.0)
        out = a.parallel(b)
        assert out.cost is None


class TestHelpers:
    def test_add_both_none(self):
        assert _add(None, 1.0) is None
        assert _add(1.0, None) is None
        assert _add(1.0, 2.0) == 3.0

    def test_max_both_none(self):
        assert _max(None, 1.0) is None
        assert _max(1.0, None) is None
        assert _max(3.0, 2.0) == 3.0

    def test_mul_both_none(self):
        assert _mul(None, 1.0) is None
        assert _mul(1.0, None) is None
        assert _mul(2.0, 3.0) == 6.0
