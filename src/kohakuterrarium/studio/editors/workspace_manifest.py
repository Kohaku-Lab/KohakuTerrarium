"""Workspace Protocol + manifest / sidecar / effective-config helpers.

Defines the dependency-injected workspace protocol and the manifest, sidecar,
and effective-configuration operations shared by workspace implementations.
"""

import ast
import json
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from kohakuterrarium.core.config import load_agent_config
from kohakuterrarium.studio.catalog.catalog_sources import (
    MANIFEST_KEYS,
    classify_io,
    dedupe_preserve_order,
    load_workspace_manifest,
    package_entries,
    workspace_manifest_entries,
)
from kohakuterrarium.studio.editors.utils_paths import sanitize_name
from kohakuterrarium.studio.editors.yaml_manifest import (
    append_entry,
    ensure_list,
    entry_by_name,
    load_manifest,
    save_manifest,
)


@runtime_checkable
class Workspace(Protocol):
    """Protocol for a studio workspace (FS, remote server, …)."""

    root: str  # Human-readable workspace identity, usually a path.

    def list_creatures(self) -> list[dict]: ...
    def load_creature(self, name: str) -> dict: ...
    def save_creature(self, name: str, data: dict) -> dict: ...
    def scaffold_creature(self, name: str, base: str | None) -> dict: ...
    def delete_creature(self, name: str) -> None: ...

    def list_modules(self, kind: str) -> list[dict]: ...
    def load_module(self, kind: str, name: str) -> dict: ...
    def save_module(self, kind: str, name: str, data: dict) -> dict: ...
    def scaffold_module(self, kind: str, name: str, template: str | None) -> dict: ...
    def delete_module(self, kind: str, name: str) -> None: ...

    def read_prompt(self, creature: str, rel: str) -> str: ...
    def write_prompt(self, creature: str, rel: str, body: str) -> None: ...


def compute_effective(cfg_path: Path, data: dict) -> dict:
    """Return a best-effort summary of the post-inheritance configuration.

    Core loading failures are represented by ``error`` so workspace reads remain
    available when a base package or reference is broken.
    """
    try:
        cfg = load_agent_config(cfg_path.parent)
    except Exception as e:
        return {"error": str(e)}

    # The core config omits lineage, so reconstruct the visible first hop from
    # raw configuration for display.
    chain: list[str] = []
    cur = data
    seen: set[str] = set()
    max_depth = 16
    while max_depth > 0:
        base = cur.get("base_config") if isinstance(cur, dict) else None
        if not base:
            break
        if base in seen:
            break
        seen.add(base)
        chain.append(base)
        # Only the first hop is available without reimplementing core resolution.
        break

    tools = [t.name for t in cfg.tools] if cfg.tools else []
    subagents = [s.name for s in cfg.subagents] if cfg.subagents else []
    return {
        "model": cfg.model or cfg.llm_profile or "",
        "tools": tools,
        "subagents": subagents,
        "inheritance_chain": chain,
    }


def load_sidecar_doc(py_path: Path, root_path: Path) -> dict:
    """Return a module documentation sidecar envelope.

    Missing sidecars produce empty content and ``exists=False`` so callers can
    offer creation without treating absence as an error.
    """
    sidecar = py_path.with_suffix(".md")
    content = sidecar.read_text(encoding="utf-8") if sidecar.exists() else ""
    return {
        "content": content,
        "path": str(sidecar.relative_to(root_path)).replace("\\", "/"),
        "exists": sidecar.exists(),
    }


def save_sidecar_doc(py_path: Path, content: str) -> None:
    """Write the sidecar ``.md`` next to *py_path*."""
    sidecar = py_path.with_suffix(".md")
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(content, encoding="utf-8")


def read_sidecar_schema(py_path: Path) -> list | None:
    """Load a valid list from a module's ``.schema.json`` sidecar.

    Missing, malformed, and non-list sidecars return ``None`` so a later save can
    regenerate them.
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
    """Write optional sidecars supplied by a code-generation module.

    Dot-prefixed suffixes replace the Python suffix; other suffixes append after
    the stem. Missing, failing, or invalid writers are treated as no sidecars.
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


def sync_manifest_entry(
    root_path: Path,
    kind: str,
    name: str,
    py_path: Path,
    known_kinds: tuple[str, ...],
) -> dict:
    """Idempotently add a resolved module to the workspace manifest.

    Existing comments and ordering are preserved. The result reports whether an
    entry was added and returns the effective manifest record.
    """
    name = sanitize_name(name)
    if kind not in known_kinds:
        raise ValueError(f"unknown module kind: {kind!r}")

    manifest_path = root_path / "kohaku.yaml"
    alt = root_path / "kohaku.yml"
    if not manifest_path.exists() and alt.exists():
        manifest_path = alt

    doc = load_manifest(manifest_path)

    # New manifests require minimal package identity before module entries.
    if "name" not in doc:
        doc["name"] = root_path.name
    if "version" not in doc:
        doc["version"] = "0.1.0"

    manifest_key = MANIFEST_KEYS[kind]
    seq = ensure_list(doc, manifest_key)
    dotted = module_dotted_path(root_path, py_path)
    class_name = detect_class_name(py_path, kind)

    # Inputs and outputs share one manifest list, so names are unique only within
    # their I/O classification.
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
    """Convert a workspace-contained Python path to a dotted module path."""
    rel = py_path.relative_to(root)
    parts = list(rel.parts)
    if parts and parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
    return ".".join(parts)


def resolve_manifest_path(root_path: Path, module: str | None) -> Path | None:
    """Resolve a dotted module only when its Python file is inside the workspace.

    Installed packages, absolute references, and parent escapes return ``None``
    because editors may modify only workspace-owned files.
    """
    if not module or not isinstance(module, str):
        return None
    candidate = root_path / (module.replace(".", "/") + ".py")
    try:
        resolved = candidate.resolve()
        root = root_path.resolve()
    except OSError:
        return None
    if not resolved.is_file():
        return None
    if root != resolved and root not in resolved.parents:
        return None
    return resolved


def find_module_file(
    root_path: Path,
    kind_dir: Path,
    kind: str,
    name: str,
    ws: Any,
) -> Path | None:
    """Locate the on-disk file for ``(kind, name)``.

    Search order:
      1. ``<kind_dir>/<name>.py`` (or ``.yaml``/``.yml`` for sub-agents)
      2. ``kohaku.yaml`` manifest entry whose ``name`` matches and whose
         ``module:`` dotted path resolves inside the workspace root.
    """
    candidate = kind_dir / f"{name}.py"
    if candidate.exists():
        return candidate
    if kind == "subagents":
        for ext in (".yaml", ".yml"):
            c = kind_dir / f"{name}{ext}"
            if c.exists():
                return c

    manifest = load_workspace_manifest(ws)
    key = MANIFEST_KEYS.get(kind)
    if key is None:
        return None
    for entry in manifest.get(key) or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("name") != name:
            continue
        if kind in ("inputs", "outputs"):
            want = "input" if kind == "inputs" else "output"
            if classify_io(entry) != want:
                continue
        resolved = resolve_manifest_path(root_path, entry.get("module"))
        if resolved is not None:
            return resolved
    return None


def modules_summary(
    ws: Any,
    kind: str,
    workspace_files: list[dict],
) -> list[dict]:
    """Merge workspace files, manifest records, and package modules.

    Workspace-owned records are marked editable before ordered deduplication.
    """
    merged: list[dict] = []
    for item in workspace_files:
        merged.append({**item, "source": "workspace", "editable": True})

    root_path = getattr(ws, "root_path", None)
    for entry in workspace_manifest_entries(ws, kind):
        path = (
            resolve_manifest_path(root_path, entry.get("module")) if root_path else None
        )
        if path is not None:
            entry = {
                **entry,
                "editable": True,
                "path": str(path.relative_to(root_path)).replace("\\", "/"),
            }
        merged.append(entry)

    merged.extend(package_entries(kind))
    return dedupe_preserve_order(merged)


def detect_class_name(py_path: Path, kind: str) -> str | None:
    """Return the first exported class name when the module shape supports one.

    Sub-agents expose ``SubAgentConfig`` instances, so their manifest entries omit
    ``class`` and rely on loader attribute discovery.
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
