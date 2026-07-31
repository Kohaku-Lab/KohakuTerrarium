"""
File safety guards for tool operations.

Provides three independent guards used by file-manipulating tools:

1. **Read state tracking**: Records which files the model has read,
   enforces read-before-write/edit, detects staleness via mtime.
2. **Path boundary check**: Warns or blocks access to files outside
   the agent's working directory. First attempt to a new outside
   path is blocked with a warning; retrying the same path is allowed.
3. **Binary file detection**: Prevents reading/editing binary files
   that would produce garbled output.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


# -- File Read State -------------------------------------------------------


@dataclass(slots=True)
class FileReadRecord:
    """Record of a file read by the agent."""

    path: str  # resolved absolute path
    mtime_ns: int  # os.stat st_mtime_ns at read time
    partial: bool  # True if offset/limit was used
    timestamp: float  # wall-clock time of read (time.time())


class FileReadState:
    """Tracks which files the agent has read and when.

    Used by write/edit tools to enforce read-before-write and
    detect staleness (file modified since last read).
    """

    def __init__(self) -> None:
        self._records: dict[str, FileReadRecord] = {}

    def record_read(
        self,
        path: str,
        mtime_ns: int,
        partial: bool,
        timestamp: float,
    ) -> None:
        """Record that a file was read."""
        resolved = str(Path(path).resolve())
        self._records[resolved] = FileReadRecord(
            path=resolved,
            mtime_ns=mtime_ns,
            partial=partial,
            timestamp=timestamp,
        )

    def get(self, path: str) -> FileReadRecord | None:
        """Get the read record for a path, or None if never read."""
        resolved = str(Path(path).resolve())
        return self._records.get(resolved)

    def clear(self) -> None:
        """Clear all read records."""
        self._records.clear()


def check_read_before_write(
    file_read_state: FileReadState | None,
    path: str,
) -> str | None:
    """Check if a file was read before attempting to write/edit it.

    Returns an error message string if the guard fails, or None if OK.
    This is a HARD guard: the tool should return this as an error.

    Rules:
    - New file (does not exist on disk): always allowed
    - Existing file, never read: blocked
    - Existing file, read but mtime changed: blocked (stale)
    - Existing file, read, mtime matches: allowed
    """
    resolved = Path(path).resolve()

    if not resolved.exists():
        return None  # new file, OK

    if file_read_state is None:
        return (
            f"Cannot write to '{path}': file has not been read yet. "
            "Use the read tool first to see the current contents."
        )

    record = file_read_state.get(str(resolved))
    if record is None:
        return (
            f"Cannot write to '{path}': file has not been read yet. "
            "Use the read tool first to see the current contents."
        )

    # Check staleness via mtime
    try:
        current_mtime_ns = os.stat(resolved).st_mtime_ns
    except OSError:
        return None  # can't stat, let the write attempt handle it

    if current_mtime_ns != record.mtime_ns:
        return (
            f"Cannot write to '{path}': file was modified since last read. "
            "Read it again to see the current contents before writing."
        )

    return None  # all good


# -- Path Boundary Guard ---------------------------------------------------


class PathBoundaryGuard:
    """Warns or blocks file access outside the working directory.

    First access to a path outside cwd is blocked with a warning.
    If the same path is retried, it is allowed (the model acknowledged
    the warning and chose to proceed).

    Modes:
    - "warn": first attempt blocked with warning, retry allowed
    - "block": always blocked
    - "off": no checks
    """

    def __init__(self, cwd: str | Path, mode: str = "warn") -> None:
        self.cwd = str(Path(cwd).resolve())
        self.mode = mode
        # Paths that have been warned once (allowed on retry)
        self._warned_paths: set[str] = set()

    def check(self, path: str) -> str | None:
        """Check if a path is within the working directory boundary.

        Returns a warning/error message if blocked, or None if allowed.
        """
        if self.mode == "off":
            return None

        resolved = Path(path).resolve()
        cwd = Path(self.cwd)

        # Check if path is under cwd. Use relative_to rather than
        # ``str.startswith(self.cwd + os.sep)``: at a filesystem root
        # ("E:\\" or "/") the naive prefix becomes "E:\\\\" / "//" and
        # every child path is misjudged as outside the workspace.
        # relative_to is also case-insensitive on Windows.
        try:
            resolved.relative_to(cwd)
        except ValueError:
            pass  # outside cwd
        else:
            return None  # inside cwd, always OK

        if self.mode == "block":
            return (
                f"Access denied: '{path}' is outside the working directory "
                f"({self.cwd}). This operation is not allowed."
            )

        # "warn" mode: first attempt is blocked, retry is allowed
        if str(resolved) in self._warned_paths:
            return None  # already warned, allow this time

        self._warned_paths.add(str(resolved))
        return (
            f"Warning: '{path}' is outside the working directory ({self.cwd}). "
            "If this is intentional, retry the same operation to proceed."
        )


# -- Binary File Detection -------------------------------------------------

# File extensions known to be binary
_BINARY_EXTENSIONS: set[str] = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".bmp",
    ".ico",
    ".webp",
    ".svg",
    ".tiff",
    ".tif",
    ".psd",
    ".raw",
    ".heif",
    ".heic",
    ".avif",
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".zip",
    ".tar",
    ".gz",
    ".bz2",
    ".xz",
    ".7z",
    ".rar",
    ".zst",
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".bin",
    ".o",
    ".a",
    ".pyc",
    ".pyo",
    ".class",
    ".whl",
    ".egg",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".eot",
    ".mp3",
    ".mp4",
    ".wav",
    ".flac",
    ".ogg",
    ".avi",
    ".mov",
    ".mkv",
    ".webm",
    ".m4a",
    ".m4v",
    ".aac",
    ".kohakutr",  # KohakuVault session files
}


def is_binary_file(path: str | Path, sample_size: int = 8192) -> bool:
    """Detect if a file is binary.

    Uses two checks:
    1. Extension against a known binary list.
    2. Content sampling: if >10% of bytes in the first ``sample_size``
       bytes are non-printable, the file is binary.

    Returns True if the file appears to be binary.
    """
    p = Path(path)

    # Extension check
    if p.suffix.lower() in _BINARY_EXTENSIONS:
        return True

    # Content sampling
    try:
        with open(p, "rb") as f:
            chunk = f.read(sample_size)
    except OSError:
        return False  # can't read, let the caller handle it

    if not chunk:
        return False  # empty file is not binary

    # Null byte check: binary files contain null bytes, text files don't
    # (even UTF-8 with non-ASCII chars has no null bytes in normal text)
    if b"\x00" in chunk:
        return True

    # High ratio of control characters (excluding common whitespace + ANSI escape)
    # suggests binary. UTF-8 high bytes (0x80+) are NOT counted as control chars.
    control = sum(1 for b in chunk if b < 0x08 or (0x0E <= b <= 0x1F and b != 0x1B))
    return control / len(chunk) > 0.10
