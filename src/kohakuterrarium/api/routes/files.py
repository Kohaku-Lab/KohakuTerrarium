"""File operations routes - tree browsing, reading, writing for editor mode."""

import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException

from kohakuterrarium.api.schemas import FileDelete, FileMkdir, FileRename, FileWrite

router = APIRouter()

# Extension → language mapping for editor syntax highlighting
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

# Directories/files to skip in tree listing (exact names)
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
    """Validate and resolve a file path."""
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
    """Check if a file/dir name should be skipped in tree listing."""
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


def _build_tree(path: Path, depth: int) -> dict:
    """Recursively build a file tree dict."""
    node = _dir_entry(path)

    if path.is_file():
        try:
            node["size"] = path.stat().st_size
        except OSError:
            node["size"] = 0
        return node

    if depth <= 0:
        node["children"] = []
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
    """Detect language from file extension."""
    # Handle special filenames
    name_lower = path.name.lower()
    if name_lower == "dockerfile":
        return "dockerfile"
    if name_lower == "makefile":
        return "makefile"
    if name_lower in ("cmakelists.txt",):
        return "cmake"

    ext = path.suffix.lower()
    return _EXT_LANG.get(ext, "plaintext")


@router.get("/tree")
async def get_file_tree(root: str, depth: int = 3):
    """Return a nested file tree starting from the given root directory."""
    root_path = _validate_path(root)
    if not root_path.is_dir():
        raise HTTPException(400, f"Not a directory: {root}")
    if depth < 1:
        depth = 1
    if depth > 10:
        depth = 10
    return _build_tree(root_path, depth)


@router.get("/browse")
async def browse_directories(path: str | None = None):
    """Return browsable directories under the local filesystem."""
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


@router.get("/read")
async def read_file(path: str):
    """Read a file and return its content with metadata."""
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


@router.post("/write")
async def write_file(req: FileWrite):
    """Write content to a file, creating parent directories if needed."""
    file_path = _validate_path(req.path)

    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(req.content, encoding="utf-8")
        size = file_path.stat().st_size
    except PermissionError:
        raise HTTPException(400, f"Permission denied: {req.path}")
    except OSError as e:
        raise HTTPException(500, f"Write error: {e}")

    return {"success": True, "size": size}


@router.post("/rename")
async def rename_file(req: FileRename):
    """Rename or move a file/directory."""
    old = _validate_path(req.old_path)
    new = _validate_path(req.new_path)

    if not old.exists():
        raise HTTPException(404, f"Source not found: {req.old_path}")
    if new.exists():
        raise HTTPException(400, f"Destination already exists: {req.new_path}")

    try:
        new.parent.mkdir(parents=True, exist_ok=True)
        old.rename(new)
    except PermissionError:
        raise HTTPException(400, f"Permission denied")
    except OSError as e:
        raise HTTPException(500, f"Rename error: {e}")

    return {"success": True}


@router.post("/delete")
async def delete_file(req: FileDelete):
    """Delete a file or empty directory."""
    target = _validate_path(req.path)

    if not target.exists():
        raise HTTPException(404, f"Not found: {req.path}")

    try:
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
    except PermissionError:
        raise HTTPException(400, f"Permission denied: {req.path}")
    except OSError as e:
        raise HTTPException(500, f"Delete error: {e}")

    return {"success": True}


@router.post("/mkdir")
async def make_directory(req: FileMkdir):
    """Create a directory, including parent directories."""
    dir_path = _validate_path(req.path)

    if dir_path.exists():
        raise HTTPException(400, f"Already exists: {req.path}")

    try:
        dir_path.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        raise HTTPException(400, f"Permission denied: {req.path}")
    except OSError as e:
        raise HTTPException(500, f"Mkdir error: {e}")

    return {"success": True}
