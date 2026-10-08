"""Local filesystem workspace implementation.

Reads and writes ``<root>/creatures`` and ``<root>/modules`` while enforcing
name and containment checks through ``sanitize_name`` and ``ensure_in_root``.
The public surface implements the ``Workspace`` protocol; manifest, sidecar,
code-generation, and inheritance concerns remain delegated to sibling modules.
"""

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ruamel.yaml.comments import CommentedMap, CommentedSeq

from kohakuterrarium.packages.resolve import resolve_any_path
from kohakuterrarium.packages.walk import package_snapshot
from kohakuterrarium.studio.catalog.catalog_sources import load_workspace_manifest
from kohakuterrarium.studio.catalog.packages_scan import (
    invalidate_scan_caches,
    package_ref,
)
from kohakuterrarium.studio.editors import (
    creatures_crud,
    modules_crud,
    workspace_wiring,
)
from kohakuterrarium.studio.editors.codegen_init import get_codegen
from kohakuterrarium.studio.editors.utils_paths import ensure_in_root, sanitize_name
from kohakuterrarium.studio.editors.workspace_manifest import (
    compute_effective,
    find_module_file,
    load_sidecar_doc,
    modules_summary,
    read_sidecar_schema,
    sync_manifest_entry,
)
from kohakuterrarium.studio.editors.yaml_creature import load_creature_file
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


KNOWN_KINDS = ("tools", "subagents", "triggers", "plugins", "inputs", "outputs")


@dataclass
class LocalWorkspace:
    """Provide filesystem-backed creature and module editing.

    :meth:`open` requires an existing directory. Creature and module
    subdirectories are created lazily by save operations.
    """

    root_path: Path

    @classmethod
    def open(cls, root: str | Path) -> "LocalWorkspace":
        p = Path(root).expanduser().resolve()
        if not p.exists():
            raise FileNotFoundError(f"workspace not found: {p}")
        if not p.is_dir():
            raise NotADirectoryError(str(p))
        return cls(root_path=p)

    @property
    def root(self) -> str:
        return str(self.root_path)

    @property
    def creatures_dir(self) -> Path:
        return self.root_path / "creatures"

    @property
    def modules_dir(self) -> Path:
        return self.root_path / "modules"

    def module_kind_dir(self, kind: str) -> Path:
        if kind not in KNOWN_KINDS:
            raise ValueError(f"unknown module kind: {kind!r}")
        return self.modules_dir / kind

    def ref_prefix(self) -> str | None:
        """The ``@pkg`` reference of the root, or None outside every package."""
        return package_ref(self.root_path)

    def ref_for(self, path: Path, prefix: str | None) -> str:
        """The reference a config uses for ``path``: ``@pkg/rel`` or absolute."""
        if prefix is None:
            return str(path)
        return f"{prefix}/{path.relative_to(self.root_path).as_posix()}"

    def summary(self) -> dict:
        """Everything Studio lists for the workspace.

        Every module file the workspace owns (in ``modules/<kind>/`` or
        declared by its manifest) carries its ``ref`` and ``users``.
        """
        with package_snapshot():
            prefix = self.ref_prefix()
            modules = {
                kind: modules_summary(self, kind, self.list_modules(kind))
                for kind in KNOWN_KINDS
            }
            own = {
                kind: [m for m in entries if m.get("editable") and m.get("path")]
                for kind, entries in modules.items()
            }
            users = workspace_wiring.users_by_file(self, own)
            for kind, entries in own.items():
                for m in entries:
                    key = workspace_wiring.module_file_key(self, kind, m)
                    m["users"] = users[key]
                    m.setdefault("ref", self.ref_for(key[1], prefix))
            return {
                "root": self.root,
                "ref_prefix": prefix,
                "is_project": prefix == "@",
                "creatures": self.list_creatures(),
                "terrariums": self.list_terrariums(),
                "modules": modules,
            }

    def list_terrariums(self) -> list[dict]:
        """Recipe folders under ``terrariums/`` (a ``terrarium.yaml`` each)."""
        folder = self.root_path / "terrariums"
        if not folder.is_dir():
            return []
        prefix = self.ref_prefix()
        declared = {
            str(e.get("path", "")).strip("/"): e
            for e in load_workspace_manifest(self).get("terrariums") or []
            if isinstance(e, dict)
        }
        results: list[dict] = []
        for child in sorted(p for p in folder.iterdir() if p.is_dir()):
            listed = declared.get(f"terrariums/{child.name}", {})
            cfg = next(
                (
                    child / f
                    for f in ("terrarium.yaml", "terrarium.yml")
                    if (child / f).exists()
                ),
                None,
            )
            if cfg is None:
                continue
            try:
                data = load_creature_file(cfg)
            except Exception as e:
                data = {"error": f"parse failed: {e}"}
            body = (
                data.get("terrarium")
                if isinstance(data.get("terrarium"), dict)
                else data
            )
            creatures = body.get("creatures") or []
            results.append(
                {
                    "name": body.get("name") or child.name,
                    "path": str(child),
                    "ref": self.ref_for(child, prefix),
                    "description": body.get("description")
                    or listed.get("description", ""),
                    "creatures": len(creatures) if isinstance(creatures, list) else 0,
                    **({"error": data["error"]} if "error" in data else {}),
                }
            )
        return results

    def list_creatures(self) -> list[dict]:
        if not self.creatures_dir.is_dir():
            return []
        prefix = self.ref_prefix()
        declared = {
            str(e.get("path", "")).strip("/"): e
            for e in load_workspace_manifest(self).get("creatures") or []
            if isinstance(e, dict)
        }
        results: list[dict] = []
        for child in sorted(self.creatures_dir.iterdir()):
            if not child.is_dir():
                continue
            cfg = _find_config_file(child)
            if cfg is None:
                continue
            try:
                data = load_creature_file(cfg)
            except Exception as e:
                logger.warning(
                    "creature config parse failed", path=str(cfg), error=str(e)
                )
                results.append(
                    {
                        "name": child.name,
                        "path": str(child),
                        "ref": self.ref_for(child, prefix),
                        "description": "",
                        "base_config": None,
                        "error": f"parse failed: {e}",
                    }
                )
                continue
            results.append(
                {
                    "name": data.get("name", child.name),
                    "path": str(child),
                    "ref": self.ref_for(child, prefix),
                    "description": data.get("description")
                    or declared.get(f"creatures/{child.name}", {}).get(
                        "description", ""
                    ),
                    "base_config": data.get("base_config"),
                }
            )
        return results

    def load_creature(self, name: str) -> dict:
        name = sanitize_name(name)
        creature_dir = self.creatures_dir / name
        cfg_path = _find_config_file(creature_dir)
        if cfg_path is None:
            raise FileNotFoundError(name)
        data = load_creature_file(cfg_path)
        prompts = _collect_prompts(creature_dir)
        return {
            "name": name,
            "path": str(creature_dir),
            "config": _coerce_plain(data),
            "prompts": prompts,
            "effective": compute_effective(cfg_path, data),
        }

    def scaffold_creature(self, name: str, base: str | None, **seed: Any) -> dict:
        """Create a creature; ``seed`` is starter / description / purpose / model."""
        creatures_crud.scaffold_creature(self.creatures_dir, name, base, **seed)
        invalidate_scan_caches()
        return self.load_creature(name)

    def fork_creature(self, name: str, source: str) -> dict:
        """Copy the creature at ``source`` (a folder or ``@pkg/...``) in as ``name``."""
        creatures_crud.fork_creature(
            self.creatures_dir, name, resolve_any_path(source).expanduser().resolve()
        )
        invalidate_scan_caches()
        return self.load_creature(name)

    def save_creature(self, name: str, body: dict) -> dict:
        creatures_crud.save_creature(self.creatures_dir, name, body)
        invalidate_scan_caches()
        return self.load_creature(name)

    def delete_creature(self, name: str) -> None:
        creatures_crud.delete_creature(self.creatures_dir, name)
        invalidate_scan_caches()

    def read_prompt(self, creature: str, rel: str) -> str:
        creature = sanitize_name(creature)
        creature_dir = self.creatures_dir / creature
        if not creature_dir.is_dir():
            raise FileNotFoundError(creature)
        target = ensure_in_root(creature_dir, rel)
        if not target.exists():
            raise FileNotFoundError(str(target))
        return target.read_text(encoding="utf-8")

    def write_prompt(self, creature: str, rel: str, body: str) -> None:
        creatures_crud.write_prompt(self.creatures_dir, creature, rel, body)

    def list_modules(self, kind: str) -> list[dict]:
        kind_dir = self.module_kind_dir(kind)
        if not kind_dir.is_dir():
            return []
        prefix = self.ref_prefix()
        results: list[dict] = []
        for child in sorted(kind_dir.iterdir()):
            if child.is_file() and child.suffix in (".py", ".yaml", ".yml"):
                results.append(
                    {
                        "kind": kind,
                        "name": child.stem,
                        "path": str(child.relative_to(self.root_path)).replace(
                            "\\", "/"
                        ),
                        "ref": self.ref_for(child, prefix),
                    }
                )
        return results

    def load_module(self, kind: str, name: str) -> dict:
        name = sanitize_name(name)
        path = self._find_module_file(kind, name)
        if path is None:
            raise FileNotFoundError(f"{kind}/{name}")

        raw = path.read_text(encoding="utf-8")
        cg = get_codegen(kind)
        if kind == "plugins":
            sidecar_schema = read_sidecar_schema(path)
            envelope = cg.parse_back(raw, sidecar_schema=sidecar_schema)
        else:
            envelope = cg.parse_back(raw)
        envelope.update(
            {
                "kind": kind,
                "name": name,
                "path": str(path.relative_to(self.root_path)).replace("\\", "/"),
                "raw_source": raw,
            }
        )
        return envelope

    def scaffold_module(
        self,
        kind: str,
        name: str,
        template: str | None,
        plug_into: list[str] | None = None,
    ) -> dict:
        missing = [
            c
            for c in plug_into or []
            if _find_config_file(self.creatures_dir / sanitize_name(c)) is None
        ]
        if missing:
            raise FileNotFoundError(", ".join(missing))
        modules_crud.scaffold_module(self.module_kind_dir(kind), kind, name, template)
        if plug_into:
            self.plug_module(kind, name, plug_into)
        return self.load_module(kind, name)

    def module_wiring(self, kind: str, name: str) -> dict:
        return workspace_wiring.module_wiring(self, kind, name)

    def module_users(self, kind: str, name: str) -> list[str]:
        return workspace_wiring.module_users(self, kind, name)

    def plug_module(self, kind: str, name: str, creatures: list[str]) -> list[str]:
        return workspace_wiring.plug_module(self, kind, name, creatures)

    def unplug_module(self, kind: str, name: str, creatures: list[str]) -> list[str]:
        return workspace_wiring.unplug_module(self, kind, name, creatures)

    def save_module(self, kind: str, name: str, data: dict) -> dict:
        kind_dir = self.module_kind_dir(kind)
        existing = self._find_module_file(kind, sanitize_name(name))
        fallback = kind_dir / f"{sanitize_name(name)}.py"
        modules_crud.save_module(
            kind, name, data, existing_path=existing, fallback_path=fallback
        )
        return self.load_module(kind, name)

    def delete_module(self, kind: str, name: str) -> None:
        path = self._find_module_file(kind, sanitize_name(name))
        modules_crud.delete_module(kind, name, path)

    def load_module_doc(self, kind: str, name: str) -> dict:
        name = sanitize_name(name)
        py_path = self._find_module_file(kind, name)
        if py_path is None:
            raise FileNotFoundError(f"{kind}/{name}")
        return load_sidecar_doc(py_path, self.root_path)

    def save_module_doc(self, kind: str, name: str, content: str) -> dict:
        name = sanitize_name(name)
        py_path = self._find_module_file(kind, name)
        if py_path is None:
            raise FileNotFoundError(f"{kind}/{name}")
        modules_crud.save_module_doc(py_path, content)
        return self.load_module_doc(kind, name)

    def sync_manifest(self, kind: str, name: str) -> dict:
        name = sanitize_name(name)
        py_path = self._find_module_file(kind, name)
        if py_path is None:
            raise FileNotFoundError(f"{kind}/{name}")
        return sync_manifest_entry(self.root_path, kind, name, py_path, KNOWN_KINDS)

    def _find_module_file(self, kind: str, name: str) -> Path | None:
        return find_module_file(
            self.root_path, self.module_kind_dir(kind), kind, name, self
        )


def _find_config_file(creature_dir: Path) -> Path | None:
    for name in ("config.yaml", "config.yml"):
        p = creature_dir / name
        if p.exists():
            return p
    return None


def _collect_prompts(creature_dir: Path) -> dict[str, str]:
    prompts: dict[str, str] = {}
    prompts_dir = creature_dir / "prompts"
    if not prompts_dir.is_dir():
        return prompts
    for p in sorted(prompts_dir.rglob("*")):
        if p.is_file() and p.suffix.lower() in (".md", ".txt"):
            rel = p.relative_to(creature_dir).as_posix()
            try:
                prompts[rel] = p.read_text(encoding="utf-8")
            except Exception:
                continue
    return prompts


def _coerce_plain(obj: Any) -> Any:
    if isinstance(obj, CommentedMap) or isinstance(obj, dict):
        return {k: _coerce_plain(v) for k, v in obj.items()}
    if isinstance(obj, CommentedSeq) or isinstance(obj, list):
        return [_coerce_plain(v) for v in obj]
    return obj


def _rmtree(path: Path) -> None:
    """Recursively delete a workspace path through ``shutil.rmtree``."""
    shutil.rmtree(path)
