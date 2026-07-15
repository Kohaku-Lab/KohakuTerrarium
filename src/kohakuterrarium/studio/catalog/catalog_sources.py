"""Shared helpers for listing modules from the three catalog sources:
Combines workspace-authored files, workspace manifest declarations, and
installed-package declarations under shared normalization and precedence rules.
Catalog and editor surfaces use these helpers to avoid divergent module lists.
"""

from pathlib import Path
from typing import Any

import yaml

from kohakuterrarium.packages.walk import list_packages

# Inputs and outputs share the manifest's ``io`` slot and are separated by
# declaration metadata; other kinds map directly.
MANIFEST_KEYS: dict[str, str] = {
    "tools": "tools",
    "subagents": "subagents",
    "triggers": "triggers",
    "plugins": "plugins",
    "inputs": "io",
    "outputs": "io",
}


def load_workspace_manifest(ws: Any) -> dict:
    """Return a workspace manifest, treating absence or invalid YAML as empty."""
    if ws is None:
        return {}
    root = getattr(ws, "root_path", None)
    if root is None:
        return {}
    for name in ("kohaku.yaml", "kohaku.yml"):
        manifest = Path(root) / name
        if manifest.is_file():
            try:
                return yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
            except Exception:
                return {}
    return {}


def manifest_entry(raw: dict, *, source: str, entry_type: str) -> dict:
    """Normalize a manifest declaration into the shared catalog shape."""
    return {
        "name": raw.get("name", ""),
        "description": raw.get("description", ""),
        "source": source,
        "type": entry_type,
        "module": raw.get("module"),
        "class_name": raw.get("class") or raw.get("class_name"),
    }


def classify_io(item: dict) -> str:
    """Infer whether a shared ``io`` declaration represents input or output."""
    name = (item.get("name") or "").lower()
    class_name = (item.get("class") or item.get("class_name") or "").lower()
    if "input" in name or class_name.endswith("input") or "input" in class_name:
        return "input"
    if "output" in name or class_name.endswith("output") or "output" in class_name:
        return "output"
    return "unknown"


def workspace_manifest_entries(ws: Any, kind: str) -> list[dict]:
    """Return normalized declarations from the open workspace manifest."""
    key = MANIFEST_KEYS.get(kind)
    if key is None:
        return []
    manifest = load_workspace_manifest(ws)
    raw = manifest.get(key) or []
    out: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        if kind in ("inputs", "outputs"):
            want = "input" if kind == "inputs" else "output"
            if classify_io(item) != want:
                continue
        out.append(
            manifest_entry(item, source="workspace-manifest", entry_type="package")
        )
    return out


def package_entries(kind: str) -> list[dict]:
    """Return normalized declarations from every readable installed package."""
    key = MANIFEST_KEYS.get(kind)
    if key is None:
        return []
    try:
        pkgs = list_packages()
    except Exception:
        pkgs = []
    out: list[dict] = []
    for pkg in pkgs:
        items = pkg.get(key) or []
        pkg_name = pkg.get("name", "?")
        for item in items:
            if not isinstance(item, dict):
                continue
            if kind in ("inputs", "outputs"):
                want = "input" if kind == "inputs" else "output"
                if classify_io(item) != want:
                    continue
            out.append(
                manifest_entry(item, source=f"package:{pkg_name}", entry_type="package")
            )
    return out


def dedupe_preserve_order(entries: list[dict]) -> list[dict]:
    """Keep the first named entry, allowing caller order to define precedence."""
    seen: set[str] = set()
    out: list[dict] = []
    for e in entries:
        key = e.get("name", "")
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out
