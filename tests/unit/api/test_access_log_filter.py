"""Unit tests for :class:`kohakuterrarium.api.app.PollingAccessLogFilter`."""

import logging

from kohakuterrarium.api.app import PollingAccessLogFilter, _install_access_log_filter


def _record(args):
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=args,
        exc_info=None,
    )
    return record


class TestPollingAccessLogFilter:
    def test_suppresses_active_session_polls(self):
        f = PollingAccessLogFilter()
        assert (
            f.filter(_record(("1.2.3.4:1", "GET", "/api/sessions/active", "1.1", 200)))
            is False
        )
        assert (
            f.filter(
                _record(
                    ("1.2.3.4:1", "GET", "/api/sessions/active/graph_x", "1.1", 200)
                )
            )
            is False
        )

    def test_suppresses_polls_with_query_string(self):
        f = PollingAccessLogFilter()
        assert (
            f.filter(
                _record(("1.2.3.4:1", "GET", "/api/sessions/active?x=1", "1.1", 200))
            )
            is False
        )

    def test_passes_other_requests(self):
        f = PollingAccessLogFilter()
        assert (
            f.filter(_record(("1.2.3.4:1", "POST", "/api/sessions", "1.1", 200)))
            is True
        )
        assert f.filter(_record(None)) is True
        assert f.filter(_record(("short",))) is True

    def test_never_hides_failures(self):
        f = PollingAccessLogFilter()
        assert (
            f.filter(_record(("1.2.3.4:1", "GET", "/api/sessions/active", "1.1", 500)))
            is True
        )
        assert (
            f.filter(_record(("1.2.3.4:1", "GET", "/api/sessions/active", "1.1", 404)))
            is True
        )

    def test_never_hides_other_methods(self):
        f = PollingAccessLogFilter()
        assert (
            f.filter(_record(("1.2.3.4:1", "POST", "/api/sessions/active", "1.1", 200)))
            is True
        )

    def test_never_hides_lookalike_paths(self):
        f = PollingAccessLogFilter()
        assert (
            f.filter(
                _record(("1.2.3.4:1", "GET", "/api/sessions/actively", "1.1", 200))
            )
            is True
        )

    def test_install_is_idempotent(self):
        access = logging.getLogger("uvicorn.access")
        before = [f for f in access.filters if isinstance(f, PollingAccessLogFilter)]
        try:
            _install_access_log_filter()
            _install_access_log_filter()
            installed = [
                f for f in access.filters if isinstance(f, PollingAccessLogFilter)
            ]
            assert len(installed) == 1
        finally:
            for f in list(access.filters):
                if isinstance(f, PollingAccessLogFilter) and f not in before:
                    access.removeFilter(f)
