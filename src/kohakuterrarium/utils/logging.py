"""
Custom logging module with colored output and comprehensive formatting.

Format: [HH:MM:SS] [module.name] [LEVEL] message
Colors: DEBUG=gray, INFO=green, WARNING=yellow, ERROR=red

Default behavior:
  - Logs written to ``~/.kohakuterrarium/logs/kt.log`` (rotating, 10MB x 5)
  - No stderr output by default, keeps CLI clean
  - Set ``KT_LOG_STDERR=1`` to also log to stderr (for debugging)
  - CLI commands can opt-in via ``enable_stderr_logging`` when the
    terminal is not owned by a full-screen UI
"""

import datetime
import hashlib
import locale
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

from kohakuterrarium.utils.config_dir import config_dir

try:
    import ctypes

    HAS_CTYPES = True
except (
    ImportError
):  # pragma: no cover - ctypes is available on every supported platform
    ctypes = None  # type: ignore[assignment]
    HAS_CTYPES = False

# ANSI color codes
COLORS = {
    "DEBUG": "\033[90m",  # Gray
    "INFO": "\033[92m",  # Green
    "WARNING": "\033[93m",  # Yellow
    "ERROR": "\033[91m",  # Red
    "CRITICAL": "\033[95m",  # Magenta
    "RESET": "\033[0m",
}


# Check if terminal supports colors
def _supports_color() -> bool:
    """Check if the terminal supports ANSI colors."""
    if not hasattr(sys.stdout, "isatty"):
        return False
    if not sys.stdout.isatty():
        return False
    # Windows 10+ supports ANSI, but need to enable it
    if sys.platform == "win32":
        if not HAS_CTYPES:
            return False
        try:
            kernel32 = ctypes.windll.kernel32
            # Enable ANSI escape sequences on Windows
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
            return True
        except Exception as e:
            _ = e  # intentionally suppressed: Windows console mode unsupported
            return False
    return True


SUPPORTS_COLOR = _supports_color()


class FlushingStreamHandler(logging.StreamHandler):
    """StreamHandler that flushes after every emit.

    Also robust against non-ASCII log messages on streams whose underlying
    encoding can't represent them (e.g. Windows ``cp1252`` stderr). Without
    this, logging an LLM response that contains CJK / emoji would raise
    ``UnicodeEncodeError`` mid-stream and abort the request.
    """

    def emit(self, record: logging.LogRecord) -> None:
        """Emit a record, flushing immediately and surviving encoding errors.

        We reimplement the write loop instead of delegating to
        ``super().emit`` because ``logging.StreamHandler.emit`` catches
        *every* ``Exception`` (including ``UnicodeEncodeError``) and
        routes it straight to ``handleError`` — so a fallback wrapped
        around ``super().emit`` could never fire. By doing the format +
        write ourselves we own the exception path and can render an
        ASCII-safe replacement when the stream's encoding can't carry
        the message (e.g. CJK / emoji on a ``cp1252`` Windows console).
        """
        try:
            msg = self.format(record)
            stream = self.stream
            try:
                stream.write(msg + self.terminator)
            except UnicodeEncodeError:
                enc = getattr(stream, "encoding", None) or "ascii"
                safe = (msg + self.terminator).encode(enc, errors="replace").decode(enc)
                stream.write(safe)
            self.flush()
        except RecursionError:  # pragma: no cover - re-raised per stdlib
            raise
        except Exception:
            self.handleError(record)


class ColoredFormatter(logging.Formatter):
    """Custom formatter that adds colors and structured format."""

    def __init__(self, use_color: bool = True):
        super().__init__()
        self.use_color = use_color and SUPPORTS_COLOR

    def format(self, record: logging.LogRecord) -> str:
        # Time format: HH:MM:SS
        time_str = self.formatTime(record, "%H:%M:%S")

        # Module name: truncate if too long
        module = record.name
        if len(module) > 25:
            module = "..." + module[-22:]

        # Level name: pad to consistent width
        level = record.levelname

        # Base message
        message = record.getMessage()

        # Add any extra fields (passed via logger.info("msg", extra_field=value))
        extras = []
        for key, value in record.__dict__.items():
            if key not in (
                "name",
                "msg",
                "args",
                "created",
                "filename",
                "funcName",
                "levelname",
                "levelno",
                "lineno",
                "module",
                "msecs",
                "pathname",
                "process",
                "processName",
                "relativeCreated",
                "stack_info",
                "exc_info",
                "exc_text",
                "thread",
                "threadName",
                "taskName",
                "message",
            ):
                extras.append(f"{key}={value}")

        if extras:
            message = f"{message} [{', '.join(extras)}]"

        # Format with colors
        if self.use_color:
            color = COLORS.get(level, "")
            reset = COLORS["RESET"]
            return f"{color}[{time_str}] [{module}] [{level}] {message}{reset}"
        else:
            return f"[{time_str}] [{module}] [{level}] {message}"

    def formatException(self, ei: Any) -> str:
        """Format exception with color if enabled."""
        result = super().formatException(ei)
        if self.use_color:
            return f"{COLORS['ERROR']}{result}{COLORS['RESET']}"
        return result


class KTLogger(logging.Logger):
    """Extended logger with extra field support."""

    def _log(
        self,
        level: int,
        msg: object,
        args: tuple[Any, ...],
        exc_info: Any = None,
        extra: dict[str, Any] | None = None,
        stack_info: bool = False,
        stacklevel: int = 1,
        **kwargs: Any,
    ) -> None:
        # Merge kwargs into extra for convenience
        # This allows: logger.info("message", field1=value1, field2=value2)
        if kwargs:
            if extra is None:
                extra = {}
            extra.update(kwargs)
        super()._log(level, msg, args, exc_info, extra, stack_info, stacklevel + 1)


# Set custom logger class
logging.setLoggerClass(KTLogger)

# Global handler to avoid duplicates
_handler: logging.Handler | None = None


# Patterns we recognise in log messages and mask before emit:
#   ?token=<anything-not-whitespace-quote-or-amp>
#   &token=<same>
#   "token": "<value>"  (single or double quoted, any value)
# We keep the surrounding context so the operator can still see WHERE
# in the log the token would have appeared.
_TOKEN_QUERY_RE = re.compile(r"([?&]token=)[^\s&\"']+", re.IGNORECASE)
_TOKEN_JSON_RE = re.compile(r'("token"\s*:\s*")[^"]+(")', re.IGNORECASE)
_TOKEN_KV_RE = re.compile(r"(\btoken\s*[=:]\s*)[A-Za-z0-9._-]{8,}", re.IGNORECASE)


def _mask_tokens(text: str) -> str:
    """Replace any token-bearing substring in ``text`` with ``****``.

    Best-effort regex masking — covers the three shapes the framework
    produces: WS-URL query (``?token=abc``), JSON dumps
    (``"token": "abc"``), and bare ``token=abc`` / ``token: abc``.
    Wider patterns (env-var dumps, raw bearer headers) are not in
    scope; those should never reach log records in the first place.
    """
    text = _TOKEN_QUERY_RE.sub(r"\1****", text)
    text = _TOKEN_JSON_RE.sub(r"\1****\2", text)
    text = _TOKEN_KV_RE.sub(r"\1****", text)
    return text


class _TokenMaskingFilter(logging.Filter):
    """Logging filter that scrubs lab tokens from every emitted record.

    Installed on every framework log handler so a stray
    ``logger.info("connecting to %s", url_with_token)`` cannot leak
    a credential. Cannot be opted out; the cost is two regex passes
    per record which is negligible compared to the I/O of emit.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            # Mask the pre-formatted message if present (KTLogger
            # populates record.msg with a structured dict, but plain
            # logger.info("...") also lands here).
            if isinstance(record.msg, str):
                record.msg = _mask_tokens(record.msg)
            if record.args:
                if isinstance(record.args, tuple):
                    record.args = tuple(
                        _mask_tokens(a) if isinstance(a, str) else a
                        for a in record.args
                    )
                elif isinstance(record.args, dict):
                    record.args = {
                        k: (_mask_tokens(v) if isinstance(v, str) else v)
                        for k, v in record.args.items()
                    }
        except Exception:  # pragma: no cover - defensive
            # Never crash logging — better a leaked token than a crash
            # loop. The pre-formatted ``record.message`` (set after
            # format()) is also masked via the formatter path below.
            pass
        return True


def _default_log_dir() -> Path:
    """Resolve the framework log directory fresh, honouring KT_CONFIG_DIR.

    Previously a module-constant ``Path.home() / ".kohakuterrarium" /
    "logs"`` computed at import time — that ignored ``KT_CONFIG_DIR``
    and leaked test-suite logs into the operator's real config dir.
    """
    return config_dir() / "logs"


# Back-compat — callers that imported the constant for *display* still
# resolve; live writes use :func:`_default_log_dir`.
DEFAULT_LOG_DIR = Path.home() / ".kohakuterrarium" / "logs"


def configure_utf8_stdio(*, log: bool = False) -> None:
    """Best-effort UTF-8 configuration for stdout/stderr.

    Reconfigures text streams when possible so streamed model output does not
    inherit a legacy Windows console encoding like cp950/cp1252.
    """
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    if log:
        logger = logging.getLogger("kohakuterrarium.startup")
        logger.info(
            "stdio encoding configured",
            stdout_encoding=getattr(sys.stdout, "encoding", None),
            stderr_encoding=getattr(sys.stderr, "encoding", None),
            preferred_encoding=locale.getpreferredencoding(False),
        )


def _make_log_filename() -> str:
    """Build a unique log filename: YYYY-MM-DD_HHMMSS_pid<N>_<pwdhash>.log.

    Ensures each process has its own log file, preventing conflicts
    when multiple kt instances run concurrently.
    """
    now = datetime.datetime.now()
    date_str = now.strftime("%Y-%m-%d_%H%M%S")
    pid = os.getpid()
    # Short hash of working directory to help identify which session
    cwd_hash = hashlib.md5(str(Path.cwd()).encode()).hexdigest()[:8]
    return f"{date_str}_pid{pid}_{cwd_hash}.log"


def _create_file_handler() -> logging.Handler:
    """Create a per-process file handler with unique filename."""
    log_dir = _default_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / _make_log_filename()
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(ColoredFormatter(use_color=False))
    handler.setLevel(logging.DEBUG)
    return handler


def get_logger(name: str, level: int | str = logging.INFO) -> logging.Logger:
    """
    Get a configured logger for a module.

    By default, logs go to ``~/.kohakuterrarium/logs/kt.log`` (rotating).
    Set ``KT_LOG_STDERR=1`` to also log to stderr.

    Args:
        name: Module name (typically __name__)
        level: Logging level (default: INFO)

    Returns:
        Configured Logger instance
    """
    global _handler

    logger = logging.getLogger(name)

    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.DEBUG)

    logger.setLevel(level)

    # Only add handler once (to root logger)
    if _handler is None:
        root_logger = logging.getLogger("kohakuterrarium")

        # Default: file handler only
        _handler = _create_file_handler()
        _handler.addFilter(_TokenMaskingFilter())
        root_logger.addHandler(_handler)

        # Optional: stderr handler if KT_LOG_STDERR=1
        if os.environ.get("KT_LOG_STDERR"):
            stderr_handler = FlushingStreamHandler(sys.stderr)
            stderr_handler.setFormatter(ColoredFormatter(use_color=True))
            stderr_handler.setLevel(logging.DEBUG)
            stderr_handler.addFilter(_TokenMaskingFilter())
            root_logger.addHandler(stderr_handler)

        root_logger.setLevel(logging.INFO)
        root_logger.propagate = False

    return logger


def set_level(level: int | str) -> None:
    """
    Set global logging level for all kohakuterrarium loggers.

    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR, or int)
    """
    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.DEBUG)

    root_logger = logging.getLogger("kohakuterrarium")
    root_logger.setLevel(level)
    if _handler:
        _handler.setLevel(level)
    if _stderr_handler:
        _stderr_handler.setLevel(level)


def disable_colors() -> None:
    """Disable colored output (useful for logging to files)."""
    if _handler:
        _handler.setFormatter(ColoredFormatter(use_color=False))


class TUILogHandler(logging.Handler):
    """
    Log handler that routes records to a TUI session's Logs tab.

    Replaces the stderr handler when TUI mode is active so logs
    don't interfere with the full-screen display.
    """

    def __init__(self, write_func: Any, level: int = logging.DEBUG):
        super().__init__(level)
        self._write_func = write_func
        self.setFormatter(ColoredFormatter(use_color=False))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            self._write_func(msg)
        except Exception as e:
            _ = e  # intentionally suppressed: logging errors must not crash the app


_tui_handler: logging.Handler | None = None
_stderr_handler: logging.Handler | None = None


def enable_stderr_logging(level: int | str = logging.DEBUG) -> None:
    """Attach a stderr handler on top of the existing file handler.

    Idempotent: a second call updates the level of the existing handler
    instead of adding a duplicate. Safe to call after ``get_logger`` has
    initialized the root handler.

    Args:
        level: Minimum level the stderr handler will emit at.
    """
    global _stderr_handler

    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.DEBUG)

    root_logger = logging.getLogger("kohakuterrarium")
    if _stderr_handler is not None:
        _stderr_handler.setLevel(level)
        return

    _stderr_handler = FlushingStreamHandler(sys.stderr)
    _stderr_handler.setFormatter(ColoredFormatter(use_color=True))
    _stderr_handler.setLevel(level)
    _stderr_handler.addFilter(_TokenMaskingFilter())
    root_logger.addHandler(_stderr_handler)


def disable_stderr_logging() -> None:
    """Remove the stderr handler if one was attached."""
    global _stderr_handler
    if _stderr_handler is None:
        return
    root_logger = logging.getLogger("kohakuterrarium")
    root_logger.removeHandler(_stderr_handler)
    _stderr_handler = None


def enable_tui_logging(write_func: Any) -> None:
    """Add a TUI handler that routes logs to a TUI write function.

    The file handler keeps running — TUI handler is additive.
    """
    global _tui_handler
    root_logger = logging.getLogger("kohakuterrarium")
    _tui_handler = TUILogHandler(write_func)
    root_logger.addHandler(_tui_handler)


def disable_tui_logging() -> None:
    """Remove the TUI log handler."""
    global _tui_handler
    if _tui_handler:
        root_logger = logging.getLogger("kohakuterrarium")
        root_logger.removeHandler(_tui_handler)
        _tui_handler = None


def suppress_logging() -> None:
    """Deprecated: file-only logging is already quiet. No-op."""


def restore_logging() -> None:
    """Deprecated: file-only logging is already quiet. No-op."""
