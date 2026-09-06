"""Unit tests for :mod:`kohakuterrarium.utils.fd_limit`."""

import pytest

import kohakuterrarium.utils.fd_limit as fd_limit


class FakeResource:
    """``resource`` stand-in whose kernel rejects soft limits above ``accepts``."""

    RLIMIT_NOFILE = 7
    RLIM_INFINITY = -1

    def __init__(self, soft: int, hard: int, *, accepts: int):
        self.soft = soft
        self.hard = hard
        self.accepts = accepts
        self.calls: list[tuple[int, int]] = []

    def getrlimit(self, which):
        assert which == self.RLIMIT_NOFILE
        return (self.soft, self.hard)

    def setrlimit(self, which, limits):
        assert which == self.RLIMIT_NOFILE
        self.calls.append(tuple(limits))
        if limits[0] > self.accepts:
            raise ValueError("not allowed to raise maximum limit")
        self.soft, self.hard = limits


class TestRaiseFdLimit:
    def test_unsupported_platform_is_noop(self, monkeypatch):
        monkeypatch.setattr(fd_limit, "resource", None)
        assert fd_limit.raise_fd_limit() is None

    def test_macos_default_lands_on_open_max(self, monkeypatch):
        fake = FakeResource(256, -1, accepts=10240)
        monkeypatch.setattr(fd_limit, "resource", fake)
        assert fd_limit.raise_fd_limit() == 10240
        assert fake.calls == [(65536, -1), (10240, -1)]
        assert fake.soft == 10240

    def test_finite_hard_limit_caps_the_target(self, monkeypatch):
        fake = FakeResource(1024, 4096, accepts=4096)
        monkeypatch.setattr(fd_limit, "resource", fake)
        assert fd_limit.raise_fd_limit() == 4096
        assert fake.calls == [(4096, 4096)]

    def test_already_at_target_makes_no_call(self, monkeypatch):
        fake = FakeResource(65536, -1, accepts=65536)
        monkeypatch.setattr(fd_limit, "resource", fake)
        assert fd_limit.raise_fd_limit() == 65536
        assert fake.calls == []

    def test_unlimited_soft_limit_is_left_alone(self, monkeypatch):
        fake = FakeResource(-1, -1, accepts=0)
        monkeypatch.setattr(fd_limit, "resource", fake)
        assert fd_limit.raise_fd_limit() is None
        assert fake.calls == []

    def test_every_candidate_rejected_keeps_current_limit(self, monkeypatch):
        fake = FakeResource(256, -1, accepts=0)
        monkeypatch.setattr(fd_limit, "resource", fake)
        assert fd_limit.raise_fd_limit() == 256
        assert fake.soft == 256
        assert [c[0] for c in fake.calls] == [65536, 10240, 4096, 1024]

    def test_fallbacks_below_current_soft_are_skipped(self, monkeypatch):
        fake = FakeResource(8192, -1, accepts=0)
        monkeypatch.setattr(fd_limit, "resource", fake)
        assert fd_limit.raise_fd_limit() == 8192
        assert [c[0] for c in fake.calls] == [65536, 10240]

    def test_custom_target(self, monkeypatch):
        fake = FakeResource(256, -1, accepts=100000)
        monkeypatch.setattr(fd_limit, "resource", fake)
        assert fd_limit.raise_fd_limit(2048) == 2048
        assert fake.calls == [(2048, -1)]

    def test_log_reports_resulting_limits(self, monkeypatch):
        fake = FakeResource(256, -1, accepts=10240)
        monkeypatch.setattr(fd_limit, "resource", fake)
        records: list[dict] = []
        monkeypatch.setattr(
            fd_limit.logger, "info", lambda *args, **kwargs: records.append(kwargs)
        )
        fd_limit.raise_fd_limit(log=True)
        assert records == [{"soft": 10240, "hard": "unlimited"}]

    def test_silent_by_default(self, monkeypatch):
        fake = FakeResource(256, -1, accepts=10240)
        monkeypatch.setattr(fd_limit, "resource", fake)
        records: list[dict] = []
        monkeypatch.setattr(
            fd_limit.logger, "info", lambda *args, **kwargs: records.append(kwargs)
        )
        fd_limit.raise_fd_limit()
        assert records == []

    @pytest.mark.skipif(fd_limit.resource is None, reason="RLIMIT_NOFILE is POSIX-only")
    def test_real_platform_never_lowers_the_limit(self):
        res = fd_limit.resource
        before = res.getrlimit(res.RLIMIT_NOFILE)
        try:
            result = fd_limit.raise_fd_limit()
            after = res.getrlimit(res.RLIMIT_NOFILE)
            if before[0] == res.RLIM_INFINITY:
                assert result is None
            else:
                assert result == after[0]
                assert after[0] >= before[0]
        finally:
            res.setrlimit(res.RLIMIT_NOFILE, before)


class TestSoftFdLimit:
    def test_unsupported_platform(self, monkeypatch):
        monkeypatch.setattr(fd_limit, "resource", None)
        assert fd_limit.soft_fd_limit() is None

    def test_unlimited_reads_as_none(self, monkeypatch):
        monkeypatch.setattr(fd_limit, "resource", FakeResource(-1, -1, accepts=0))
        assert fd_limit.soft_fd_limit() is None

    def test_finite_value(self, monkeypatch):
        monkeypatch.setattr(fd_limit, "resource", FakeResource(256, -1, accepts=0))
        assert fd_limit.soft_fd_limit() == 256
