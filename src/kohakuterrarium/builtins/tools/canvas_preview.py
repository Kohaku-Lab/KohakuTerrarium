"""Build bounded metadata snapshots for frontend file previews.

Large content is omitted rather than embedded so consumers can fetch it lazily
without inflating tool results.
"""

from pathlib import Path
from typing import Any

# Larger files are represented by metadata and fetched through the files API.
PREVIEW_MAX_BYTES: int = 256 * 1024


# Unknown extensions deliberately fall back to plain text in ``lang_for_path``.
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
    """Build preview metadata, omitting content that exceeds the size cap."""
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
