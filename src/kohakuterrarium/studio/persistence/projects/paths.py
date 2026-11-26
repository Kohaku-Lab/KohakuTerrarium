"""Project path and node normalisation: the one place a project key is made.

A project is keyed by ``(node, key)``. ``node`` is ``""`` for this host and a
worker's node id otherwise. ``key`` is the folder path with trailing
separators stripped and, for Windows-shaped paths, separators unified and
case folded. Symlinks are never resolved.
"""

import hashlib
import ntpath
import os
import posixpath
import re

LOCAL_NODE = ""
LOCAL_WINDOWS = os.name == "nt"

_LOCAL_NODE_ALIASES = ("", "_host")
_DRIVE_ROOT = re.compile(r"^[A-Za-z]:$")
_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")


def normalize_node(node: str | None) -> str:
    """Return ``""`` for this host (``None``, ``""``, ``"_host"``), else the id."""
    text = str(node or "").strip()
    return LOCAL_NODE if text in _LOCAL_NODE_ALIASES else text


def public_node(node: str) -> str | None:
    """The API form of a normalised node: ``None`` for this host."""
    return node or None


def is_windows_path(path: str, node: str, windows: bool | None = None) -> bool:
    """Whether ``path`` follows Windows rules.

    Local paths follow this host's platform; a remote worker's path is judged
    by its shape (drive letter or leading backslash). ``windows`` forces it.
    """
    if windows is not None:
        return windows
    if node == LOCAL_NODE:
        return LOCAL_WINDOWS
    return bool(_DRIVE_PREFIX.match(path)) or path.startswith("\\")


def clean_path(path: str, node: str, windows: bool | None = None) -> str:
    """Return the display form of a project folder.

    Local paths are user-expanded. Trailing separators are stripped while a
    root (``/``, ``C:\\``) is kept. Windows paths use ``\\`` throughout.
    """
    text = str(path or "").strip()
    if not text:
        return ""
    if node == LOCAL_NODE:
        text = os.path.expanduser(text)
    if is_windows_path(text, node, windows):
        text = text.replace("/", "\\")
        stripped = text.rstrip("\\")
        if not stripped:
            return "\\"
        return stripped + "\\" if _DRIVE_ROOT.match(stripped) else stripped
    return text.rstrip("/") or "/"


def path_key(cleaned: str, node: str, windows: bool | None = None) -> str:
    """Return the comparison key of a cleaned path (case folded on Windows)."""
    if cleaned and is_windows_path(cleaned, node, windows):
        return ntpath.normcase(cleaned)
    return cleaned


def normalize_path(
    path: str, node: str, windows: bool | None = None
) -> tuple[str, str]:
    """Return ``(display path, key)`` for a folder on ``node``."""
    cleaned = clean_path(path, node, windows)
    return cleaned, path_key(cleaned, node, windows)


def ancestors(key: str, node: str, windows: bool | None = None) -> list[str]:
    """Strict ancestors of ``key``, nearest first, ending at its root."""
    win = is_windows_path(key, node, windows)
    module = ntpath if win else posixpath
    out: list[str] = []
    current = key
    while True:
        parent = module.dirname(current)
        if win and parent.endswith("\\") and len(parent) > 1:
            if not _DRIVE_ROOT.match(parent[:-1]):
                parent = parent[:-1]
        if not parent or parent == current:
            return out
        out.append(parent)
        current = parent


def default_name(path: str, node: str, windows: bool | None = None) -> str:
    """The last segment of ``path``; a root names itself."""
    module = ntpath if is_windows_path(path, node, windows) else posixpath
    return module.basename(path) or path


def project_id(node: str, key: str) -> str:
    """Stable id of the project at ``(node, key)``, saved or found."""
    digest = hashlib.sha1(f"{node}\0{key}".encode("utf-8")).hexdigest()
    return "p_" + digest[:12]


__all__ = [
    "LOCAL_NODE",
    "ancestors",
    "clean_path",
    "default_name",
    "is_windows_path",
    "normalize_node",
    "normalize_path",
    "path_key",
    "project_id",
    "public_node",
]
