"""Workspace Protocol + manifest / sidecar / effective-config helpers.

The Workspace Protocol (consumed by routes via dependency injection)
lives here alongside the manifest-sync, doc-sidecar and post-inheritance
"effective config" computation that ``workspace_fs.LocalWorkspace``
delegates to so that file stays small.
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

    root: str  # display label (usually a path string)

    # Creatures ------------------------------------------------------
    def list_creatures(self) -> list[dict]: ...
    def load_creature(self, name: str) -> dict: ...
    def save_creature(self, name: str, data: dict) -> dict: ...
    def scaffold_creature(self, name: str, base: str | None) -> dict: ...
    def delete_creature(self, name: str) -> None: ...

    # Modules --------------------------------------------------------
    def list_modules(self, kind: str) -> list[dict]: ...
    def load_module(self, kind: str, name: str) -> dict: ...
    def save_module(self, kind: str, name: str, data: dict) -> dict: ...
    def scaffold_module(self, kind: str, name: str, template: str | None) -> dict: ...
    def delete_module(self, kind: str, name: str) -> None: ...

    # Prompts --------------------------------------------------------
    def read_prompt(self, creature: str, rel: str) -> str: ...
    def write_prompt(self, creature: str, rel: str, body: str) -> None: ...


# ----------------------------------------------------------------------
# Effective config
# ----------------------------------------------------------------------


def compute_effective(cfg_path: Path, data: dict) -> dict:
    """Compute the post-inheritance effective config summary.

    Calls the core config loader in a best-effort way — if it
    fails (missing package, broken base ref, …), returns an
    ``error`` key instead of crashing the read.
    """
    try:
        cfg = load_agent_config(cfg_path.parent)
    except Exception as e:
        return {"error": str(e)}

    # Chain reconstruction from raw data (core doesn't expose the
    # chain as a field — we re-walk it for display)
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
        # We don't follow further — just surface the first hop is
        # usually enough for the UI. Phase 4 can extend this if
        # needed.
        break

    tools = [t.name for t in cfg.tools] if cfg.tools else []
    subagents = [s.name for s in cfg.subagents] if cfg.subagents else []
    return {
        "model": cfg.model or cfg.llm_profile or "",
        "tools": tools,
        "subagents": subagents,
        "inheritance_chain": chain,
    }


# ----------------------------------------------------------------------
# Sidecars (per-module .md doc, .schema.json options descriptors)
# ----------------------------------------------------------------------


def load_sidecar_doc(py_path: Path, root_path: Path) -> dict:
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


def save_sidecar_doc(py_path: Path, content: str) -> None:
    """Write the sidecar ``.md`` next to *py_path*."""
    sidecar = py_path.with_suffix(".md")
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(content, encoding="utf-8")


def read_sidecar_schema(py_path: Path) -> list | None:
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


# ----------------------------------------------------------------------
# Manifest sync
# ----------------------------------------------------------------------


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


def resolve_manifest_path(root_path: Path, module: str | None) -> Path | None:
    """Return the on-disk .py file for a dotted ``module:`` ref if it
    lives inside *root_path*; otherwise None.

    Rejects anything outside the root (installed packages, absolute
    paths, parent-escape attempts) — the editor must only ever
    touch files the user already considers part of this workspace.
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
    """Merged module list — files + manifest + packages.

    *workspace_files* is the list of workspace-authored entries the
    caller already produced via ``ws.list_modules(kind)``; we annotate
    them with ``source/editable`` and append manifest + package entries.
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
