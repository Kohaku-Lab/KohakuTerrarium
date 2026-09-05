"""Provide route-independent workspace browsing and file operations."""

import mimetypes
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

# Editor language IDs are derived from lowercase filename extensions.
_EXT_LANG: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".md": "markdown",
    ".vue": "vue",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".scss": "scss",
    ".less": "less",
    ".toml": "toml",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".rb": "ruby",
    ".php": "php",
    ".sql": "sql",
    ".xml": "xml",
    ".svg": "xml",
    ".ini": "ini",
    ".cfg": "ini",
    ".txt": "plaintext",
    ".log": "plaintext",
    ".env": "dotenv",
    ".dockerfile": "dockerfile",
    ".r": "r",
    ".lua": "lua",
    ".swift": "swift",
    ".kt": "kotlin",
    ".scala": "scala",
    ".zig": "zig",
}

# Generated and dependency directories are hidden from workspace navigation.
_SKIP_NAMES: set[str] = {
    "__pycache__",
    ".git",
    "node_modules",
    ".venv",
    "venv",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    ".tox",
    ".eggs",
}


def _validate_path(path_str: str) -> Path:
    """Resolve a filesystem path or raise an HTTP-friendly validation error."""
    try:
        return Path(path_str).resolve()
    except (ValueError, OSError) as e:
        raise HTTPException(400, f"Invalid path: {e}")


def _list_browse_roots() -> list[Path]:
    """Return top-level filesystem roots for the current platform."""
    if sys.platform == "win32":
        roots = []
        for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            drive = Path(f"{letter}:/")
            if drive.exists():
                roots.append(drive)
        return roots

    return [Path("/")]


def _parent_directory(path: Path) -> str | None:
    parent = path.parent
    if parent == path:
        return None
    return str(parent)


def _should_skip(name: str) -> bool:
    """Return whether a workspace entry is intentionally hidden."""
    if name in _SKIP_NAMES:
        return True
    if name.endswith(".egg-info"):
        return True
    return False


def _dir_entry(path: Path) -> dict:
    return {
        "name": path.name or str(path),
        "path": str(path),
        "type": "directory" if path.is_dir() else "file",
    }


def _has_visible_children(path: Path) -> bool:
    """Return whether a directory has a visible child without building its subtree."""
    try:
        for entry in path.iterdir():
            if not _should_skip(entry.name):
                return True
    except (PermissionError, OSError):
        return False
    return False


def _build_tree(path: Path, depth: int) -> dict:
    """Build a tree node to the requested depth.

    Directory nodes always advertise ``has_children`` for collapsed rendering.
    ``depth <= 0`` omits ``children`` so clients can fetch that branch lazily.
    """
    node = _dir_entry(path)

    if path.is_file():
        try:
            node["size"] = path.stat().st_size
        except OSError:
            node["size"] = 0
        return node

    # Collapsed directories still need enough state to render an expand control.
    node["has_children"] = _has_visible_children(path)

    if depth <= 0:
        return node

    children = []
    try:
        entries = sorted(path.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
    except PermissionError:
        node["children"] = []
        return node

    for entry in entries:
        if _should_skip(entry.name):
            continue
        children.append(_build_tree(entry, depth - 1))

    node["children"] = children
    return node


def _detect_language(path: Path) -> str:
    """Return the editor language ID inferred from the filename."""
    # Extensionless build files require exact-name handling.
    name_lower = path.name.lower()
    if name_lower == "dockerfile":
        return "dockerfile"
    if name_lower == "makefile":
        return "makefile"
    if name_lower in ("cmakelists.txt",):
        return "cmake"

    ext = path.suffix.lower()
    return _EXT_LANG.get(ext, "plaintext")


async def get_file_tree(root: str, depth: int = 1):
    """Return a lazily expandable tree rooted at an existing directory.

    The default includes immediate children. Callers may request deeper traversal,
    and every directory reports whether a visible child exists.
    """
    root_path = _validate_path(root)
    if not root_path.is_dir():
        raise HTTPException(400, f"Not a directory: {root}")
    if depth < 1:
        depth = 1
    return _build_tree(root_path, depth)


async def browse_directories(path: str | None = None):
    """List visible child directories plus platform roots and parent navigation."""
    roots = _list_browse_roots()
    if path:
        current = _validate_path(path)
        if not current.exists():
            raise HTTPException(404, f"Not found: {path}")
        if not current.is_dir():
            raise HTTPException(400, f"Not a directory: {path}")
        directories = []
        try:
            for entry in sorted(current.iterdir(), key=lambda e: e.name.lower()):
                if not entry.is_dir() or _should_skip(entry.name):
                    continue
                directories.append(_dir_entry(entry))
        except PermissionError:
            directories = []
        return {
            "current": _dir_entry(current),
            "parent": _parent_directory(current),
            "roots": [_dir_entry(root) for root in roots],
            "directories": directories,
        }

    return {
        "current": None,
        "parent": None,
        "roots": [_dir_entry(root) for root in roots],
        "directories": [],
    }


async def read_file_raw(path: str) -> tuple[bytes, str]:
    """Read a file's bytes and guess its media type for direct serving.

    This is how a browser loads a ``file://`` media reference a tool result
    carries; the path policy is the same as :func:`read_file`.
    """
    file_path = _validate_path(path)
    if not file_path.exists():
        raise HTTPException(404, f"File not found: {path}")
    if not file_path.is_file():
        raise HTTPException(400, f"Not a file: {path}")
    try:
        data = file_path.read_bytes()
    except PermissionError:
        raise HTTPException(400, f"Permission denied: {path}")
    except OSError as e:
        raise HTTPException(500, f"Read error: {e}")
    mime, _ = mimetypes.guess_type(file_path.name)
    return data, mime or "application/octet-stream"


async def read_file(path: str):
    """Read a UTF-8 text file with size, modification time, and language metadata."""
    file_path = _validate_path(path)
    if not file_path.exists():
        raise HTTPException(404, f"File not found: {path}")
    if not file_path.is_file():
        raise HTTPException(400, f"Not a file: {path}")

    try:
        stat = file_path.stat()
        content = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise HTTPException(400, f"Cannot read binary file: {path}")
    except PermissionError:
        raise HTTPException(400, f"Permission denied: {path}")
    except OSError as e:
        raise HTTPException(500, f"Read error: {e}")

    modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
    return {
        "path": str(file_path),
        "content": content,
        "size": stat.st_size,
        "modified": modified,
        "language": _detect_language(file_path),
    }


async def write_file(path: str, content: str):
    """Write UTF-8 text, creating missing parent directories."""
    file_path = _validate_path(path)

    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        size = file_path.stat().st_size
    except PermissionError:
        raise HTTPException(400, f"Permission denied: {path}")
    except OSError as e:
        raise HTTPException(500, f"Write error: {e}")

    return {"success": True, "size": size}


async def rename_file(old_path: str, new_path: str):
    """Move a file or directory without overwriting an existing destination."""
    old = _validate_path(old_path)
    new = _validate_path(new_path)

    if not old.exists():
        raise HTTPException(404, f"Source not found: {old_path}")
    if new.exists():
        raise HTTPException(400, f"Destination already exists: {new_path}")

    try:
        new.parent.mkdir(parents=True, exist_ok=True)
        old.rename(new)
    except PermissionError:
        raise HTTPException(400, "Permission denied")
    except OSError as e:
        raise HTTPException(500, f"Rename error: {e}")

    return {"success": True}


async def delete_file(path: str):
    """Delete a file or directory tree."""
    target = _validate_path(path)

    if not target.exists():
        raise HTTPException(404, f"Not found: {path}")

    try:
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
    except PermissionError:
        raise HTTPException(400, f"Permission denied: {path}")
    except OSError as e:
        raise HTTPException(500, f"Delete error: {e}")

    return {"success": True}


async def make_directory(path: str):
    """Create a new directory and any missing parents."""
    dir_path = _validate_path(path)

    if dir_path.exists():
        raise HTTPException(400, f"Already exists: {path}")

    try:
        dir_path.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        raise HTTPException(400, f"Permission denied: {path}")
    except OSError as e:
        raise HTTPException(500, f"Mkdir error: {e}")

    return {"success": True}
