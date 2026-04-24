"""Sidecar helpers for :class:`LocalWorkspace`.

Sidecars live next to a module's ``.py`` file:

* ``<stem>.md`` — skill documentation surfaced at runtime via ``##info##``.
* ``<stem>.schema.json`` — per-key option descriptors for plugins whose
  ``__init__`` takes a single ``options: dict`` blob.

Extracted from ``local.py`` so that file stays under the 600-line cap.
"""

import json
from pathlib import Path
from typing import Any


def load_doc(py_path: Path, root_path: Path) -> dict:
    """Return the sidecar ``.md`` envelope for *py_path*.

    Sidecar is ``<py_path>.with_suffix('.md')``. Empty content when the
    file doesn't exist yet — the caller can treat that as "author it".
    """
    sidecar = py_path.with_suffix(".md")
    content = sidecar.read_text(encoding="utf-8") if sidecar.exists() else ""
    return {
        "content": content,
        "path": str(sidecar.relative_to(root_path)).replace("\\", "/"),
        "exists": sidecar.exists(),
    }


def save_doc(py_path: Path, content: str) -> None:
    """Write the sidecar ``.md`` next to *py_path*."""
    sidecar = py_path.with_suffix(".md")
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(content, encoding="utf-8")


def read_schema(py_path: Path) -> list | None:
    """Load ``<stem>.schema.json`` sitting next to *py_path*.

    Returns the parsed list when the sidecar exists and parses; ``None``
    otherwise. A malformed JSON file is treated as absent rather than
    raising — the author can re-save to regenerate it.
    """
    sidecar = py_path.with_suffix(".schema.json")
    if not sidecar.is_file():
        return None
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, list) else None


def write_codegen_sidecars(cg: Any, form: dict, py_path: Path) -> None:
    """Ask *cg* for any ``{suffix: content}`` sidecars and write them
    next to *py_path*.

    Suffixes starting with ``.`` (e.g. ``.schema.json``) become
    ``py_path.with_suffix(suffix)``; plain suffixes append after the
    stem. Silently no-ops when the codegen module exposes no writer.
    """
    writer = getattr(cg, "sidecar_files", None)
    if writer is None:
        return
    try:
        files = writer(form)
    except Exception:
        files = {}
    if not isinstance(files, dict):
        return
    for suffix, content in files.items():
        if not isinstance(content, str):
            continue
        if suffix.startswith("."):
            target = py_path.with_suffix(suffix)
        else:
            target = py_path.parent / (py_path.stem + "." + suffix.lstrip("."))
        target.write_text(content, encoding="utf-8")
