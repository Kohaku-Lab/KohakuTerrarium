"""Everything that would print over the UI goes to the Logs tab instead.

While ktui runs, ``sys.stdout`` / ``sys.stderr`` (``print``, plugins writing to stderr),
Python warnings and log records are kept as lines here: KT's own loggers from INFO, every
other logger (plugins, libraries; Python's last-resort handler would print those) from
WARNING. Writes made by native code straight to the file descriptors are not caught.
"""

import io
import logging
import sys
import threading
from collections import deque
from typing import Any, Callable

LOG_LINES = 300


class _Short(logging.Formatter):
    """``HH:MM:SS L module: message``, the logger's last name part only (a narrow pane)."""

    def format(self, record: logging.LogRecord) -> str:
        record.short = record.name.rsplit(".", 1)[-1]
        return super().format(record)


FORMAT = _Short("%(asctime)s %(levelname).1s %(short)s: %(message)s", "%H:%M:%S")


class LogTail(logging.Handler):
    """The newest lines, and how many errors arrived since the page was last looked at (the
    badge: warnings are kept but not counted, a session can log them by the dozen)."""

    def __init__(self, on_line: Callable[[], None] = lambda: None):
        super().__init__(logging.INFO)
        self.lines: deque[str] = deque(maxlen=LOG_LINES)
        # records come from any thread; the UI copies the lines under the same lock
        self.guard = threading.Lock()
        self.on_line = on_line
        self.unseen = 0
        # lines added so far (a reader can tell whether anything new came)
        self.count = 0
        self.setFormatter(FORMAT)
        self._saved: tuple[Any, Any] | None = None
        self._root: logging.Handler | None = None
        self._had_warnings = False

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.add(self.format(record), record.levelno >= logging.ERROR)
        except Exception:  # never raise out of logging
            pass

    def add(self, line: str, alarm: bool = False) -> None:
        with self.guard:
            self.lines.extend(line.splitlines() or [""])
            self.count += 1
            if alarm:
                self.unseen += 1
        self.on_line()

    def snapshot(self, n: int) -> list[str]:
        with self.guard:
            return list(self.lines)[-n:]

    # -- capture --------------------------------------------------------

    def capture(self) -> None:
        """Takes stdio, warnings and the loggers until :meth:`release`."""
        if self._saved is not None:
            return
        self._saved = (sys.stdout, sys.stderr)
        sys.stdout = _Stream(self, "out", sys.stdout)
        sys.stderr = _Stream(self, "err", sys.stderr)
        # left as found on release (another part of the program may have turned it on)
        self._had_warnings = getattr(logging, "_warnings_showwarning", None) is not None
        logging.captureWarnings(True)
        logging.getLogger("kohakuterrarium").addHandler(self)
        self._root = _Root(self)
        logging.getLogger().addHandler(self._root)

    def release(self) -> None:
        if self._saved is None:
            return
        for stream in (sys.stdout, sys.stderr):
            if isinstance(stream, _Stream):
                stream.flush()
        sys.stdout, sys.stderr = self._saved
        self._saved = None
        if not self._had_warnings:
            logging.captureWarnings(False)
        logging.getLogger("kohakuterrarium").removeHandler(self)
        if self._root is not None:
            logging.getLogger().removeHandler(self._root)


class _Root(logging.Handler):
    """Other loggers (they reach the root): warnings and worse, into the same tail."""

    def __init__(self, tail: LogTail):
        super().__init__(logging.WARNING)
        self.tail = tail
        self.setFormatter(FORMAT)

    def emit(self, record: logging.LogRecord) -> None:
        # KT's own records, and packages' records KT's logger already handed to the tail
        if record.name.startswith("kohakuterrarium") or getattr(
            record, "kt_forwarded", False
        ):
            return
        try:
            self.tail.add(self.format(record), record.levelno >= logging.ERROR)
        except Exception:
            pass


class _Stream(io.TextIOBase):
    """A stdout / stderr stand-in: complete lines go to the tail (stderr counts as a warning)."""

    def __init__(self, tail: LogTail, name: str, real: Any):
        self.tail = tail
        self.name = name
        # the stream it stands in for: code that needs a real descriptor gets that one
        self.real = real
        self._partial = ""
        self._lock = threading.Lock()

    @property
    def encoding(self) -> str:
        return "utf-8"

    def writable(self) -> bool:
        return True

    def isatty(self) -> bool:
        return False

    def fileno(self) -> int:
        return self.real.fileno()

    @property
    def buffer(self) -> Any:
        return self.real.buffer

    def write(self, s: str) -> int:
        if not s:
            return 0
        with self._lock:
            *lines, self._partial = (self._partial + s).split("\n")
        for line in lines:
            if line.strip():
                self.tail.add(f"[{self.name}] {line}", self.name == "err")
        return len(s)

    def flush(self) -> None:
        with self._lock:
            rest, self._partial = self._partial, ""
        if rest.strip():
            self.tail.add(f"[{self.name}] {rest}", self.name == "err")
