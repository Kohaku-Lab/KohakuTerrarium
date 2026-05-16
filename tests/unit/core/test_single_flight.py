"""Unit tests for :mod:`kohakuterrarium.core.single_flight`."""

import threading

from kohakuterrarium.core.single_flight import SingleFlightDispatch, SingleFlightLease


class TestSingleFlightLease:
    def test_frozen_dataclass(self):
        lease = SingleFlightLease(token=1)
        try:
            lease.token = 2
        except Exception:
            return  # frozen — assignment raises, OK
        raise AssertionError("lease should be frozen")


class TestSingleFlightDispatch:
    def test_initial_state_idle(self):
        d = SingleFlightDispatch()
        assert d.is_running is False

    def test_acquire_returns_lease_when_idle(self):
        d = SingleFlightDispatch()
        lease = d.try_acquire()
        assert lease is not None
        assert isinstance(lease, SingleFlightLease)
        assert d.is_running is True

    def test_second_acquire_returns_none(self):
        d = SingleFlightDispatch()
        first = d.try_acquire()
        second = d.try_acquire()
        assert first is not None
        assert second is None

    def test_release_with_matching_lease(self):
        d = SingleFlightDispatch()
        lease = d.try_acquire()
        assert d.release(lease) is True
        assert d.is_running is False

    def test_release_with_stale_lease_rejected(self):
        d = SingleFlightDispatch()
        lease_a = d.try_acquire()
        d.release(lease_a)
        lease_b = d.try_acquire()
        # Releasing with the OLD lease must not unlock the new flight.
        assert d.release(lease_a) is False
        assert d.is_running is True
        assert d.release(lease_b) is True

    def test_release_with_none_lease_succeeds(self):
        d = SingleFlightDispatch()
        d.try_acquire()
        # ``None`` lease => "I don't care which one" → succeeds.
        assert d.release(None) is True
        assert d.is_running is False

    def test_release_when_idle_returns_false(self):
        d = SingleFlightDispatch()
        assert d.release(None) is False
        assert d.release(SingleFlightLease(token=1)) is False

    def test_force_release_unconditionally(self):
        d = SingleFlightDispatch()
        d.try_acquire()
        assert d.force_release() is True
        assert d.is_running is False

    def test_force_release_when_idle_returns_false(self):
        d = SingleFlightDispatch()
        assert d.force_release() is False

    def test_tokens_increment(self):
        d = SingleFlightDispatch()
        a = d.try_acquire()
        d.release(a)
        b = d.try_acquire()
        # Tokens are monotonic — second token > first.
        assert b.token > a.token

    def test_thread_safe_only_one_winner(self):
        # 32 threads race to acquire; exactly ONE wins.
        d = SingleFlightDispatch()
        winners: list[SingleFlightLease | None] = []
        ready = threading.Event()

        def worker():
            ready.wait()
            winners.append(d.try_acquire())

        threads = [threading.Thread(target=worker) for _ in range(32)]
        for t in threads:
            t.start()
        ready.set()
        for t in threads:
            t.join()
        leases = [w for w in winners if w is not None]
        assert len(leases) == 1
