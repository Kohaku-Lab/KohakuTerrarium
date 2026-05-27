"""Canvas-preview metadata helper.

The frontend's Canvas panel watches assistant text and image parts
today; write / edit / multi_edit emit only text status. This helper
builds a small ``canvas_preview`` dict that the file-mutating tools
attach to their ``ToolResult.metadata`` so the frontend can render the
updated file in the canvas without re-reading from disk.

Schema (everything inside ``ToolResult.metadata["canvas_preview"]``)::

    {
        "kind": "write" | "edit" | "multi_edit",
        "file_path": str,           # absolute path
        "lang": str,                # heuristic from file extension
        "content": str | None,      # capped at PREVIEW_MAX_BYTES; None means "too large"
        "bytes": int,               # actual file size (post-write)
        "truncated": bool,          # True iff content was capped
    }

Cap is intentionally modest — the canvas tab is a preview affordance,
not a file viewer. Large files surface ``content=None`` and the
frontend renders a "click to load via /files" stub instead.
"""

from pathlib import Path
from typing import Any

# 256 KiB — the frontend's CodeViewer handles this comfortably; bigger
# files get pulled lazily via the /files API.
PREVIEW_MAX_BYTES: int = 256 * 1024


# Most common extensions → CodeMirror / Monaco language hints. The
# frontend re-maps a few of these (e.g. ``md`` → ``markdown``).  Bare
# table covers >95% of what file-mutating tools touch in practice; the
# fallback ``"text"`` is harmless.
_EXT_TO_LANG: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".vue": "vue",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".scss": "scss",
    ".sass": "sass",
    ".less": "less",
    ".json": "json",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".toml": "toml",
    ".md": "markdown",
    ".markdown": "markdown",
    ".rst": "rst",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    ".fish": "bash",
    ".ps1": "powershell",
    ".rs": "rust",
    ".go": "go",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".java": "java",
    ".kt": "kotlin",
    ".swift": "swift",
    ".rb": "ruby",
    ".php": "php",
    ".sql": "sql",
    ".xml": "xml",
    ".svg": "svg",
}


def lang_for_path(file_path: str | Path) -> str:
    """Map a path's extension to a viewer language hint."""
    return _EXT_TO_LANG.get(Path(str(file_path)).suffix.lower(), "text")


def build_canvas_preview(
    kind: str,
    file_path: str | Path,
    content: str | None,
) -> dict[str, Any]:
    """Build a ``canvas_preview`` metadata dict for a write / edit /
    multi_edit ``ToolResult``.

    ``content`` is the post-write file content. ``None`` is allowed
    (e.g. when the tool only knows the delta and re-reading the file
    would be wasteful) and propagates through as ``content=None,
    truncated=False``. Content exceeding ``PREVIEW_MAX_BYTES`` is
    dropped (set to ``None``) and ``truncated=True`` so the frontend
    can offer a "fetch full content" affordance instead of stuffing
    1 GB into a code bubble.
    """
    path_str = str(file_path)
    lang = lang_for_path(path_str)
    if content is None:
        return {
            "kind": kind,
            "file_path": path_str,
            "lang": lang,
            "content": None,
            "bytes": 0,
            "truncated": False,
        }
    encoded = content.encode("utf-8", errors="replace")
    if len(encoded) > PREVIEW_MAX_BYTES:
        return {
            "kind": kind,
            "file_path": path_str,
            "lang": lang,
            "content": None,
            "bytes": len(encoded),
            "truncated": True,
        }
    return {
        "kind": kind,
        "file_path": path_str,
        "lang": lang,
        "content": content,
        "bytes": len(encoded),
        "truncated": False,
    }
