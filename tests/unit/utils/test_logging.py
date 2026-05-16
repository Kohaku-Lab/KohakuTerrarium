"""Unit tests for :mod:`kohakuterrarium.utils.logging`.

Every public function and class is exercised against the real logging
machinery, with the module-level globals (``_handler``,
``_stderr_handler``, etc.) snapshotted around each test so we don't
leak handlers between cases.
"""

import io
import logging
import os
import sys

import pytest

import kohakuterrarium.utils.logging as ktlog
from kohakuterrarium.utils.logging import (
    COLORS,
    ColoredFormatter,
    FlushingStreamHandler,
    KTLogger,
    TUILogHandler,
    _make_log_filename,
    _supports_color,
    configure_utf8_stdio,
    disable_colors,
    disable_stderr_logging,
    disable_tui_logging,
    enable_stderr_logging,
    enable_tui_logging,
    get_logger,
    restore_logging,
    set_level,
    suppress_logging,
)

# ── isolation ────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_logging_state(monkeypatch, tmp_path):
    """Snapshot every module-global + the kohakuterrarium root handlers.

    Without this, tests that call ``get_logger`` or
    ``enable_stderr_logging`` poison each other.
    """
    monkeypatch.setattr(ktlog, "DEFAULT_LOG_DIR", tmp_path / "logs")
    saved_handler = ktlog._handler
    saved_stderr = ktlog._stderr_handler
    saved_tui = ktlog._tui_handler
    monkeypatch.setattr(ktlog, "_handler", None)
    monkeypatch.setattr(ktlog, "_stderr_handler", None)
    monkeypatch.setattr(ktlog, "_tui_handler", None)
    root = logging.getLogger("kohakuterrarium")
    saved_handlers = list(root.handlers)
    saved_level = root.level
    saved_propagate = root.propagate
    root.handlers = []
    try:
        yield
    finally:
        root.handlers = saved_handlers
        root.level = saved_level
        root.propagate = saved_propagate
        ktlog._handler = saved_handler
        ktlog._stderr_handler = saved_stderr
        ktlog._tui_handler = saved_tui


# ── _supports_color + Colors ────────────────────────────────────────


class TestSupportsColor:
    def test_no_isatty_returns_false(self, monkeypatch):
        class _NoIsatty:
            pass

        monkeypatch.setattr(sys, "stdout", _NoIsatty())
        assert _supports_color() is False

    def test_non_tty_returns_false(self, monkeypatch):
        class _NotATty:
            def isatty(self):
                return False

        monkeypatch.setattr(sys, "stdout", _NotATty())
        assert _supports_color() is False

    def test_unix_tty_returns_true(self, monkeypatch):
        class _Tty:
            def isatty(self):
                return True

        monkeypatch.setattr(sys, "stdout", _Tty())
        monkeypatch.setattr(sys, "platform", "linux")
        assert _supports_color() is True

    def test_windows_without_ctypes_returns_false(self, monkeypatch):
        class _Tty:
            def isatty(self):
                return True

        monkeypatch.setattr(sys, "stdout", _Tty())
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(ktlog, "HAS_CTYPES", False)
        assert _supports_color() is False

    def test_windows_with_working_ctypes_returns_true(self, monkeypatch):
        class _Tty:
            def isatty(self):
                return True

        class _FakeKernel32:
            def GetStdHandle(self, n):
                return 0xDEADBEEF

            def SetConsoleMode(self, handle, mode):
                # Real call sets ANSI mode bit 0x4 in addition to existing
                # bits.  Return 1 (success).
                return 1

        class _FakeWindll:
            kernel32 = _FakeKernel32()

        class _FakeCtypes:
            windll = _FakeWindll()

        monkeypatch.setattr(sys, "stdout", _Tty())
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(ktlog, "HAS_CTYPES", True)
        monkeypatch.setattr(ktlog, "ctypes", _FakeCtypes())
        assert _supports_color() is True

    def test_windows_with_failing_ctypes_returns_false(self, monkeypatch):
        class _Tty:
            def isatty(self):
                return True

        class _Boom:
            @property
            def windll(self):
                raise OSError("not really windows")

        monkeypatch.setattr(sys, "stdout", _Tty())
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(ktlog, "HAS_CTYPES", True)
        monkeypatch.setattr(ktlog, "ctypes", _Boom())
        assert _supports_color() is False


# ── ColoredFormatter ─────────────────────────────────────────────────


class TestColoredFormatter:
    def test_basic_format_without_color(self):
        fmt = ColoredFormatter(use_color=False)
        record = logging.LogRecord(
            name="kohakuterrarium.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="hello",
            args=(),
            exc_info=None,
        )
        out = fmt.format(record)
        assert "hello" in out
        assert "[INFO]" in out
        # ``[HH:MM:SS]`` shape.
        assert out.startswith("[")
        # No ANSI escape codes when color disabled.
        assert "\033[" not in out

    def test_truncates_long_module_name(self):
        fmt = ColoredFormatter(use_color=False)
        record = logging.LogRecord(
            name="kohakuterrarium.very.deep.nested.submodule.deeper.path",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="m",
            args=(),
            exc_info=None,
        )
        out = fmt.format(record)
        # Long module names get the ``...`` prefix.
        assert "..." in out

    def test_extras_appended_in_brackets(self):
        fmt = ColoredFormatter(use_color=False)
        rec = logging.LogRecord("k.t", logging.INFO, "p", 1, "m", (), None)
        # Inject extras as attributes (the real KTLogger merges kwargs).
        rec.extra_key = "extra_value"
        out = fmt.format(rec)
        assert "extra_key=extra_value" in out

    def test_skips_reserved_logrecord_attrs(self):
        fmt = ColoredFormatter(use_color=False)
        rec = logging.LogRecord("k.t", logging.INFO, "p", 1, "m", (), None)
        # ``msecs`` is a reserved attribute that must NOT show up as extras.
        out = fmt.format(rec)
        assert "msecs=" not in out
        assert "pathname=" not in out

    def test_format_exception_without_color(self):
        fmt = ColoredFormatter(use_color=False)
        try:
            raise ValueError("boom")
        except ValueError:
            ei = sys.exc_info()
        out = fmt.formatException(ei)
        assert "ValueError" in out
        assert "\033[" not in out

    def test_color_format_wraps_with_ansi(self, monkeypatch):
        # Force SUPPORTS_COLOR=True so the use_color branch fires.
        monkeypatch.setattr(ktlog, "SUPPORTS_COLOR", True)
        fmt = ColoredFormatter(use_color=True)
        record = logging.LogRecord("k.t", logging.INFO, "p", 1, "hi", (), None)
        out = fmt.format(record)
        assert out.startswith(COLORS["INFO"])
        assert out.endswith(COLORS["RESET"])
        assert "hi" in out

    def test_color_format_exception_wraps_with_ansi(self, monkeypatch):
        monkeypatch.setattr(ktlog, "SUPPORTS_COLOR", True)
        fmt = ColoredFormatter(use_color=True)
        try:
            raise ValueError("boom")
        except ValueError:
            ei = sys.exc_info()
        out = fmt.formatException(ei)
        assert out.startswith(COLORS["ERROR"])
        assert out.endswith(COLORS["RESET"])
        assert "ValueError" in out


# ── FlushingStreamHandler ────────────────────────────────────────────


class TestFlushingStreamHandler:
    def test_emit_writes_and_flushes(self):
        stream = io.StringIO()
        h = FlushingStreamHandler(stream)
        h.setFormatter(ColoredFormatter(use_color=False))
        rec = logging.LogRecord("k.t", logging.INFO, "p", 1, "hello", (), None)
        h.emit(rec)
        assert "hello" in stream.getvalue()

    def test_unicode_encode_error_falls_back(self):
        # Regression test for B-log-1 (fixed): emit() now reimplements
        # the write loop instead of delegating to super().emit (which
        # swallows UnicodeEncodeError into handleError), so the
        # ASCII-replace fallback actually runs.
        # A stream whose encoding is ASCII; writing non-ASCII triggers
        # UnicodeEncodeError, which the handler must catch + replace.
        class _AsciiStream:
            encoding = "ascii"

            def __init__(self):
                self.written: list[str] = []

            def write(self, s):
                if any(ord(c) > 127 for c in s):
                    raise UnicodeEncodeError("ascii", s, 0, 1, "ASCII only")
                self.written.append(s)

            def flush(self):
                pass

        stream = _AsciiStream()
        h = FlushingStreamHandler(stream)
        h.setFormatter(ColoredFormatter(use_color=False))
        rec = logging.LogRecord("k.t", logging.INFO, "p", 1, "日本語", (), None)
        # Must NOT raise AND must write a replaced message.
        h.emit(rec)
        joined = "".join(stream.written)
        assert "?" in joined


# ── KTLogger ─────────────────────────────────────────────────────────


class TestKTLogger:
    def test_kwargs_merged_into_extra(self):
        # Use the real logger class — install a capturing handler.
        captured: list[logging.LogRecord] = []

        class _Cap(logging.Handler):
            def emit(self, record):
                captured.append(record)

        log = get_logger("kohakuterrarium.kt_test")
        cap = _Cap(level=logging.DEBUG)
        log.addHandler(cap)
        log.info("hi", custom_field="x", number=42)
        assert len(captured) == 1
        assert captured[0].custom_field == "x"
        assert captured[0].number == 42

    def test_logger_is_KTLogger_instance(self):
        log = get_logger("kohakuterrarium.kt_test2")
        assert isinstance(log, KTLogger)


# ── _make_log_filename ───────────────────────────────────────────────


class TestMakeLogFilename:
    def test_includes_pid_and_date(self):
        name = _make_log_filename()
        assert f"pid{os.getpid()}" in name
        assert name.endswith(".log")
        # ``YYYY-MM-DD_HHMMSS`` prefix.
        assert name[4] == "-" and name[7] == "-"


# ── get_logger ───────────────────────────────────────────────────────


class TestGetLogger:
    def test_first_call_initialises_root_with_file_handler(self):
        log = get_logger("kohakuterrarium.gl_a")
        root = logging.getLogger("kohakuterrarium")
        assert len(root.handlers) >= 1
        # ``_handler`` is now non-None.
        assert ktlog._handler is not None
        # propagate is disabled to keep kohakuterrarium logs off the
        # root Python logger.
        assert root.propagate is False
        assert log.level == logging.INFO

    def test_repeated_call_does_not_add_handler(self):
        get_logger("kohakuterrarium.gl_b")
        before = list(logging.getLogger("kohakuterrarium").handlers)
        get_logger("kohakuterrarium.gl_c")
        after = list(logging.getLogger("kohakuterrarium").handlers)
        assert after == before

    def test_string_level_normalised(self):
        log = get_logger("kohakuterrarium.gl_d", level="DEBUG")
        assert log.level == logging.DEBUG

    def test_invalid_string_level_falls_through(self):
        log = get_logger("kohakuterrarium.gl_e", level="NOT_REAL")
        # Falls back to DEBUG per the implementation.
        assert log.level == logging.DEBUG

    def test_env_var_attaches_stderr_handler(self, monkeypatch):
        monkeypatch.setenv("KT_LOG_STDERR", "1")
        get_logger("kohakuterrarium.gl_env")
        root = logging.getLogger("kohakuterrarium")
        # One of the handlers must be a FlushingStreamHandler.
        assert any(isinstance(h, FlushingStreamHandler) for h in root.handlers)

    def test_no_env_var_no_stderr_handler(self, monkeypatch):
        monkeypatch.delenv("KT_LOG_STDERR", raising=False)
        get_logger("kohakuterrarium.gl_no_env")
        root = logging.getLogger("kohakuterrarium")
        assert not any(isinstance(h, FlushingStreamHandler) for h in root.handlers)


# ── set_level ────────────────────────────────────────────────────────


class TestSetLevel:
    def test_updates_root_logger_level(self):
        get_logger("kohakuterrarium.sl_a")
        set_level("WARNING")
        root = logging.getLogger("kohakuterrarium")
        assert root.level == logging.WARNING

    def test_updates_file_handler_level(self):
        get_logger("kohakuterrarium.sl_b")
        set_level("ERROR")
        assert ktlog._handler is not None
        assert ktlog._handler.level == logging.ERROR

    def test_updates_stderr_handler_level(self):
        get_logger("kohakuterrarium.sl_c")
        enable_stderr_logging("DEBUG")
        set_level("WARNING")
        assert ktlog._stderr_handler is not None
        assert ktlog._stderr_handler.level == logging.WARNING

    def test_int_level_accepted(self):
        get_logger("kohakuterrarium.sl_d")
        set_level(logging.CRITICAL)
        root = logging.getLogger("kohakuterrarium")
        assert root.level == logging.CRITICAL


# ── enable / disable stderr ──────────────────────────────────────────


class TestEnableDisableStderrLogging:
    def test_first_call_attaches(self):
        enable_stderr_logging("INFO")
        root = logging.getLogger("kohakuterrarium")
        assert any(isinstance(h, FlushingStreamHandler) for h in root.handlers)
        assert ktlog._stderr_handler is not None
        assert ktlog._stderr_handler.level == logging.INFO

    def test_second_call_idempotent_only_updates_level(self):
        enable_stderr_logging("INFO")
        before = list(logging.getLogger("kohakuterrarium").handlers)
        enable_stderr_logging("WARNING")
        after = list(logging.getLogger("kohakuterrarium").handlers)
        assert after == before
        assert ktlog._stderr_handler.level == logging.WARNING

    def test_disable_removes_handler(self):
        enable_stderr_logging("INFO")
        disable_stderr_logging()
        root = logging.getLogger("kohakuterrarium")
        assert not any(isinstance(h, FlushingStreamHandler) for h in root.handlers)
        assert ktlog._stderr_handler is None

    def test_disable_when_not_enabled_is_noop(self):
        # Nothing to remove — must not raise.
        disable_stderr_logging()
        assert ktlog._stderr_handler is None


# ── disable_colors ───────────────────────────────────────────────────


class TestDisableColors:
    def test_no_handler_yet_is_noop(self):
        # No file handler yet — disable_colors must not crash.
        disable_colors()
        # Still no handler.
        assert ktlog._handler is None

    def test_replaces_formatter_on_existing_handler(self):
        get_logger("kohakuterrarium.dc_a")
        disable_colors()
        assert ktlog._handler is not None
        # The formatter is now a non-color ColoredFormatter.
        fmt = ktlog._handler.formatter
        assert isinstance(fmt, ColoredFormatter)
        assert fmt.use_color is False


# ── TUI handler ──────────────────────────────────────────────────────


class TestTUILogHandler:
    def test_emit_calls_write_func(self):
        captured: list[str] = []
        h = TUILogHandler(captured.append, level=logging.DEBUG)
        rec = logging.LogRecord("k.t", logging.INFO, "p", 1, "tui-msg", (), None)
        h.emit(rec)
        assert len(captured) == 1
        assert "tui-msg" in captured[0]

    def test_write_func_exception_is_swallowed(self):
        def _boom(_):
            raise RuntimeError("write failed")

        h = TUILogHandler(_boom, level=logging.DEBUG)
        rec = logging.LogRecord("k.t", logging.INFO, "p", 1, "x", (), None)
        # Must not raise.
        h.emit(rec)


class TestEnableDisableTUILogging:
    def test_enable_then_disable(self):
        captured: list[str] = []
        enable_tui_logging(captured.append)
        root = logging.getLogger("kohakuterrarium")
        assert any(isinstance(h, TUILogHandler) for h in root.handlers)
        disable_tui_logging()
        assert not any(isinstance(h, TUILogHandler) for h in root.handlers)
        assert ktlog._tui_handler is None

    def test_disable_when_not_enabled_is_noop(self):
        disable_tui_logging()
        assert ktlog._tui_handler is None


# ── configure_utf8_stdio ─────────────────────────────────────────────


class TestConfigureUtf8Stdio:
    def test_reconfigure_called_when_available(self, monkeypatch):
        calls: list[tuple[str, dict]] = []

        class _Stream:
            def reconfigure(self, **kwargs):
                calls.append(("reconfigure", kwargs))

        monkeypatch.setattr(sys, "stdout", _Stream())
        monkeypatch.setattr(sys, "stderr", _Stream())
        configure_utf8_stdio()
        assert len(calls) == 2
        for _, kw in calls:
            assert kw == {"encoding": "utf-8", "errors": "replace"}

    def test_no_reconfigure_attribute_silently_ignored(self, monkeypatch):
        class _Stream:
            pass

        monkeypatch.setattr(sys, "stdout", _Stream())
        monkeypatch.setattr(sys, "stderr", _Stream())
        # Must not raise.
        configure_utf8_stdio()

    def test_none_stream_silently_skipped(self, monkeypatch):
        # Some embedded runners (briefcase / windowed Windows builds)
        # have ``sys.stderr is None``.  configure_utf8_stdio must not
        # crash trying to call ``reconfigure`` on None.
        monkeypatch.setattr(sys, "stdout", None)
        monkeypatch.setattr(sys, "stderr", None)
        configure_utf8_stdio()

    def test_reconfigure_exception_swallowed(self, monkeypatch):
        class _Stream:
            def reconfigure(self, **_kw):
                raise OSError("locked")

        monkeypatch.setattr(sys, "stdout", _Stream())
        monkeypatch.setattr(sys, "stderr", _Stream())
        configure_utf8_stdio()  # must not raise

    def test_log_flag_emits_startup_record(self):
        # When ``log=True`` and reconfigure didn't crash, an INFO record
        # is logged under ``kohakuterrarium.startup``.
        # Can't use caplog: get_logger sets propagate=False on the
        # kohakuterrarium root, so records don't reach pytest's root
        # logger.  Install a capturing handler directly.
        captured: list[logging.LogRecord] = []

        class _Cap(logging.Handler):
            def emit(self, record):
                captured.append(record)

        log = logging.getLogger("kohakuterrarium.startup")
        cap = _Cap(level=logging.DEBUG)
        log.addHandler(cap)
        log.setLevel(logging.INFO)
        try:
            configure_utf8_stdio(log=True)
        finally:
            log.removeHandler(cap)
        msgs = [r.getMessage() for r in captured]
        assert any("stdio encoding configured" in m for m in msgs)


# ── suppress / restore logging (deprecated no-ops) ───────────────────


class TestSuppressRestoreLogging:
    def test_suppress_is_noop(self):
        suppress_logging()  # no-op; doesn't crash.

    def test_restore_is_noop(self):
        restore_logging()  # no-op; doesn't crash.


# ── color palette sanity ─────────────────────────────────────────────


class TestColors:
    def test_colors_dict_has_every_level(self):
        for lvl in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL", "RESET"):
            assert lvl in COLORS
            assert COLORS[lvl].startswith("\033[")
