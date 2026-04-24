"""Manifest sync operations for :class:`LocalWorkspace`.

Kept here rather than inline in ``local.py`` so that file stays under
the 600-line soft cap. Callers pass their own workspace state explicitly
— this module doesn't own any.
"""

import ast
from pathlib import Path

from kohakuterrarium.api.studio.catalog_sources import MANIFEST_KEYS, classify_io
from kohakuterrarium.api.studio.utils.paths import sanitize_name
from kohakuterrarium.api.studio.yaml_io.manifest import (
    append_entry,
    ensure_list,
    entry_by_name,
    load_manifest,
    save_manifest,
)


def sync_manifest_entry(
    root_path: Path,
    kind: str,
    name: str,
    py_path: Path,
    known_kinds: tuple[str, ...],
) -> dict:
    """Append ``(kind, name)`` to the workspace's ``kohaku.yaml``.

    Idempotent. The caller (``LocalWorkspace.sync_manifest``) provides
    the already-resolved ``.py`` path so we don't need to redo module
    discovery here. Preserves existing comments via ruamel round-trip.

    Returns ``{ok, added, path, entry}``. ``added=False`` when a prior
    entry with the same ``name`` already sat under the right key.
    """
    name = sanitize_name(name)
    if kind not in known_kinds:
        raise ValueError(f"unknown module kind: {kind!r}")

    manifest_path = root_path / "kohaku.yaml"
    alt = root_path / "kohaku.yml"
    if not manifest_path.exists() and alt.exists():
        manifest_path = alt

    doc = load_manifest(manifest_path)

    # Seed minimal top-level metadata for freshly-created manifests.
    if "name" not in doc:
        doc["name"] = root_path.name
    if "version" not in doc:
        doc["version"] = "0.1.0"

    manifest_key = MANIFEST_KEYS[kind]
    seq = ensure_list(doc, manifest_key)
    dotted = module_dotted_path(root_path, py_path)
    class_name = detect_class_name(py_path, kind)

    # IO entries share a list — de-dupe must respect the input/output
    # classification so "x" as input doesn't shadow "x" as output.
    existing = entry_by_name(seq, name)
    if existing is not None and kind in ("inputs", "outputs"):
        want = "input" if kind == "inputs" else "output"
        if classify_io(existing) != want:
            existing = None

    rel_path = str(manifest_path.relative_to(root_path)).replace("\\", "/")
    if existing is not None:
        return {
            "ok": True,
            "added": False,
            "path": rel_path,
            "entry": dict(existing),
        }

    entry: dict = {"name": name, "module": dotted}
    if class_name is not None:
        entry["class"] = class_name
    append_entry(seq, entry)
    save_manifest(manifest_path, doc)
    return {
        "ok": True,
        "added": True,
        "path": rel_path,
        "entry": dict(entry),
    }


def module_dotted_path(root: Path, py_path: Path) -> str:
    """Convert a workspace-relative .py file path to a dotted module path.

    ``<root>/modules/tools/my_tool.py`` → ``modules.tools.my_tool``.
    ``<root>/kt_template/tools/package_tool.py`` →
    ``kt_template.tools.package_tool``. The caller ensures the file
    is inside *root*.
    """
    rel = py_path.relative_to(root)
    parts = list(rel.parts)
    if parts and parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
    return ".".join(parts)


def detect_class_name(py_path: Path, kind: str) -> str | None:
    """Best-effort AST scan for the primary class exported by the file.

    Sub-agents export a ``SubAgentConfig`` instance rather than a class,
    so we always return ``None`` for them — the manifest entry then
    omits ``class:`` and the framework falls back to attribute scanning
    at load time.
    """
    if kind == "subagents":
        return None
    try:
        tree = ast.parse(py_path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return None
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            return node.name
    return None
